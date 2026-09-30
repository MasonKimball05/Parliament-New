"""
v3.36.0 (09-29-26) — bulk edit and delete for recurring event series.

Before: every edit and delete touched one row, so changing a weekly meeting
meant editing each occurrence, and deleting the series' first (root) event
CASCADE-deleted every occurrence, including past ones with attendance.

Pins (src/event_series.py):
  * scope "this" is the old single-event behaviour;
  * "following" / "all" copy only the CHANGED fields, move date/time by the
    same delta (excuse deadlines too), and never touch finalized events;
  * "all" leaves past events alone;
  * deleting the root promotes the earliest survivor instead of cascading;
  * generated occurrences now inherit the committee and sign-up settings.

Run with: python manage.py test src.tests.events.test_recurring_series_bulk
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.event_series import affected_events, scope_counts
from src.models import Committee, Event, ParliamentUser
from src.view.officer.manage_events import generate_recurring_events

BOOL_FIELDS = ('is_active', 'requires_attendance', 'allow_excuses', 'requires_signup',
               'signups_open', 'allow_waitlist', 'rsvp_email_enabled', 'is_recurring',
               'reminder_1_enabled', 'reminder_1_email_enabled',
               'reminder_2_enabled', 'reminder_2_email_enabled')


def _dt(value):
    return timezone.localtime(value).strftime('%Y-%m-%dT%H:%M') if value else ''


def post_data(event, **changes):
    data = {
        'title': event.title, 'description': event.description, 'location': event.location,
        'date_time': _dt(event.date_time), 'excuse_deadline': _dt(event.excuse_deadline),
        'recurrence_type': event.recurrence_type, 'recurrence_interval': event.recurrence_interval,
        'recurrence_unit': event.recurrence_unit,
        'recurrence_end_date': event.recurrence_end_date.isoformat() if event.recurrence_end_date else '',
        'reminder_1_hours_before': event.reminder_1_hours_before,
        'reminder_2_hours_before': event.reminder_2_hours_before,
        'max_signups': event.max_signups or '',
        'committee': event.committee_id or '',
    }
    for f in BOOL_FIELDS:
        if getattr(event, f):
            data[f] = 'on'
    for k, v in changes.items():
        if v is False:
            data.pop(k, None)
        else:
            data[k] = v
    return data


class SeriesFixture(TestCase):
    def setUp(self):
        self.officer = ParliamentUser.objects.create_user(
            user_id='series-officer', name='Series Officer', username='series-officer',
            member_type='Officer', is_admin=True)
        self.client.force_login(self.officer)
        now = timezone.now().replace(second=0, microsecond=0)
        # +1h so no occurrence lands exactly on 'now' (it would turn past mid-test).
        start = now - timedelta(weeks=2) + timedelta(hours=1)
        self.root = Event.objects.create(
            title='Chapter Meeting', description='Weekly', location='Hall',
            date_time=start, created_by=self.officer, is_recurring=True,
            recurrence_type='weekly', requires_attendance=True,
            excuse_deadline=start - timedelta(hours=2))
        self.occ = []
        for w in range(1, 6):   # -1 week (past, finalized), then 4 upcoming
            self.occ.append(Event.objects.create(
                title='Chapter Meeting', description='Weekly', location='Hall',
                date_time=start + timedelta(weeks=w), created_by=self.officer,
                parent_event=self.root, requires_attendance=True,
                excuse_deadline=start + timedelta(weeks=w) - timedelta(hours=2)))
        self.past = self.occ[0]
        self.past.attendance_finalized = True
        self.past.save()
        self.upcoming = self.occ[1:]
        assert all(e.date_time > now for e in self.upcoming)

    def reload(self):
        self.root.refresh_from_db()
        for e in self.occ:
            e.refresh_from_db()


class ScopeTests(SeriesFixture):
    def test_counts(self):
        first = self.upcoming[0]
        counts = scope_counts(first)
        self.assertEqual(counts['this'], 1)
        self.assertEqual(counts['following'], len(self.upcoming))
        self.assertEqual(counts['all'], len(self.upcoming))

    def test_not_in_a_series_has_no_counts(self):
        lone = Event.objects.create(title='Social', description='x', date_time=timezone.now(),
                                    created_by=self.officer)
        self.assertIsNone(scope_counts(lone))

    def test_following_excludes_finalized_and_earlier(self):
        ids = set(affected_events(self.upcoming[1], 'following').values_list('pk', flat=True))
        self.assertEqual(ids, {e.pk for e in self.upcoming[1:]})


class BulkEditTests(SeriesFixture):
    def test_this_only_is_the_old_behaviour(self):
        target = self.upcoming[0]
        self.client.post(reverse('edit_event', args=[target.pk]),
                         post_data(target, title='Special Meeting', scope='this'))
        self.reload()
        self.assertEqual(Event.objects.filter(title='Special Meeting').count(), 1)

    def test_all_changes_every_upcoming_event_but_not_the_past(self):
        target = self.upcoming[1]
        r = self.client.post(reverse('edit_event', args=[target.pk]),
                             post_data(target, location='Library', scope='all'))
        self.assertEqual(r.status_code, 302)
        self.reload()
        for e in self.upcoming:
            self.assertEqual(e.location, 'Library')
        self.assertEqual(self.root.location, 'Hall')      # past
        self.assertEqual(self.past.location, 'Hall')      # past + finalized

    def test_only_changed_fields_propagate(self):
        # A one-off tweak on a later occurrence survives a series-wide title change.
        later = self.upcoming[-1]
        later.location = 'Off-site'
        later.save()
        target = self.upcoming[0]
        self.client.post(reverse('edit_event', args=[target.pk]),
                         post_data(target, title='Chapter Meeting (new)', scope='following'))
        later.refresh_from_db()
        self.assertEqual(later.title, 'Chapter Meeting (new)')
        self.assertEqual(later.location, 'Off-site')

    def test_time_change_moves_everything_by_the_same_delta(self):
        target = self.upcoming[0]
        originals = {e.pk: (e.date_time, e.excuse_deadline) for e in self.upcoming}
        self.client.post(reverse('edit_event', args=[target.pk]),
                         post_data(target, date_time=_dt(target.date_time + timedelta(hours=1)),
                                   excuse_deadline=_dt(target.excuse_deadline + timedelta(hours=1)),
                                   scope='following'))
        for e in self.upcoming:
            e.refresh_from_db()
            self.assertEqual(e.date_time, originals[e.pk][0] + timedelta(hours=1))
            self.assertEqual(e.excuse_deadline, originals[e.pk][1] + timedelta(hours=1))

    def test_following_pivots_on_the_time_before_the_move(self):
        # v3.37.1: "following" used the event's NEW time, so a move of a week
        # or more skipped the next occurrence (later) or dragged in the
        # previous one (earlier).
        target = self.upcoming[1]
        originals = {e.pk: e.date_time for e in self.occ}
        self.client.post(reverse('edit_event', args=[target.pk]),
                         post_data(target, date_time=_dt(target.date_time + timedelta(days=8)),
                                   excuse_deadline=_dt(target.excuse_deadline + timedelta(days=8)),
                                   scope='following'))
        self.reload()
        self.assertEqual(self.upcoming[0].date_time, originals[self.upcoming[0].pk])
        for e in self.upcoming[1:]:
            self.assertEqual(e.date_time, originals[e.pk] + timedelta(days=8))

    def test_following_moved_earlier_leaves_earlier_events_alone(self):
        target = self.upcoming[2]
        originals = {e.pk: e.date_time for e in self.occ}
        self.client.post(reverse('edit_event', args=[target.pk]),
                         post_data(target, date_time=_dt(target.date_time - timedelta(days=7)),
                                   excuse_deadline=_dt(target.excuse_deadline - timedelta(days=7)),
                                   scope='following'))
        self.reload()
        for e in self.upcoming[:2]:
            self.assertEqual(e.date_time, originals[e.pk])
        for e in self.upcoming[2:]:
            self.assertEqual(e.date_time, originals[e.pk] - timedelta(days=7))

    def test_excuse_deadline_keeps_its_distance_before_each_event(self):
        target = self.upcoming[0]
        self.client.post(reverse('edit_event', args=[target.pk]),
                         post_data(target, excuse_deadline=_dt(target.date_time - timedelta(days=1)),
                                   scope='all'))
        for e in self.upcoming:
            e.refresh_from_db()
            self.assertEqual(e.date_time - e.excuse_deadline, timedelta(days=1))

    def test_finalized_event_is_never_bulk_edited(self):
        self.upcoming[1].attendance_finalized = True
        self.upcoming[1].save()
        target = self.upcoming[0]
        self.client.post(reverse('edit_event', args=[target.pk]),
                         post_data(target, title='Renamed', scope='following'))
        self.upcoming[1].refresh_from_db()
        self.assertEqual(self.upcoming[1].title, 'Chapter Meeting')

    def test_bad_scope_falls_back_to_this(self):
        target = self.upcoming[0]
        self.client.post(reverse('edit_event', args=[target.pk]),
                         post_data(target, title='Only Me', scope='everything'))
        self.assertEqual(Event.objects.filter(title='Only Me').count(), 1)

    def test_scope_picker_renders_for_series_only(self):
        r = self.client.get(reverse('edit_event', args=[self.upcoming[0].pk]) + '?scope=all')
        self.assertContains(r, 'name="scope" value="all" class="mt-1" checked')
        lone = Event.objects.create(title='Social', description='x', date_time=timezone.now(),
                                    created_by=self.officer)
        r = self.client.get(reverse('edit_event', args=[lone.pk]))
        self.assertNotContains(r, 'name="scope"')


class BulkDeleteTests(SeriesFixture):
    def test_this_only(self):
        target = self.upcoming[0]
        self.client.post(reverse('delete_event', args=[target.pk]), {'scope': 'this'})
        self.assertFalse(Event.objects.filter(pk=target.pk).exists())
        self.assertEqual(Event.objects.count(), 5)

    def test_following_deletes_the_tail_and_ends_the_series(self):
        split = self.upcoming[1]
        self.client.post(reverse('delete_event', args=[split.pk]), {'scope': 'following'})
        remaining = set(Event.objects.values_list('pk', flat=True))
        self.assertEqual(remaining, {self.root.pk, self.past.pk, self.upcoming[0].pk})
        self.root.refresh_from_db()
        self.assertEqual(self.root.recurrence_end_date,
                         timezone.localtime(self.upcoming[0].date_time).date())

    def test_all_keeps_past_and_finalized(self):
        self.client.post(reverse('delete_event', args=[self.upcoming[0].pk]), {'scope': 'all'})
        remaining = set(Event.objects.values_list('pk', flat=True))
        self.assertEqual(remaining, {self.root.pk, self.past.pk})

    def test_deleting_the_root_alone_no_longer_cascades(self):
        # The old bug: deleting the first event deleted the whole series.
        self.client.post(reverse('delete_event', args=[self.root.pk]), {'scope': 'this'})
        self.assertEqual(Event.objects.count(), 5)
        new_root = Event.objects.get(pk=self.past.pk)          # earliest survivor
        self.assertTrue(new_root.is_recurring)
        self.assertIsNone(new_root.parent_event_id)
        self.assertEqual(new_root.recurrence_type, 'weekly')
        self.assertEqual(Event.objects.filter(parent_event=new_root).count(), 4)

    def test_non_series_delete_unchanged(self):
        lone = Event.objects.create(title='Social', description='x', date_time=timezone.now(),
                                    created_by=self.officer)
        self.client.post(reverse('delete_event', args=[lone.pk]))
        self.assertFalse(Event.objects.filter(pk=lone.pk).exists())


class SeriesHeaderTests(SeriesFixture):
    def test_series_view_links_bulk_actions_to_next_upcoming(self):
        r = self.client.get(reverse('manage_events') + f'?series={self.root.pk}')
        nxt = self.upcoming[0]
        self.assertContains(r, reverse('edit_event', args=[nxt.pk]) + '?scope=all')
        self.assertContains(r, reverse('delete_event', args=[nxt.pk]) + '?scope=all')


class GenerationCopiesCommitteeTests(TestCase):
    def test_occurrences_inherit_committee_and_signup_settings(self):
        officer = ParliamentUser.objects.create_user(
            user_id='gen-officer', name='Gen', username='gen-officer', member_type='Officer')
        committee = Committee.objects.create(code='PROGRAM', name='Programming')
        parent = Event.objects.create(
            title='Committee Meeting', description='x', created_by=officer,
            date_time=timezone.now() + timedelta(days=1), is_recurring=True,
            recurrence_type='weekly', committee=committee,
            requires_signup=True, max_signups=12, allow_waitlist=True, rsvp_email_enabled=True,
            recurrence_end_date=(timezone.now() + timedelta(weeks=4)).date())
        instances = generate_recurring_events(parent)
        self.assertTrue(instances)
        for i in instances:
            self.assertEqual(i.committee_id, committee.pk)
            self.assertTrue(i.requires_signup)
            self.assertEqual(i.max_signups, 12)
            self.assertTrue(i.allow_waitlist)
            self.assertFalse(i.rsvp_email_enabled)
