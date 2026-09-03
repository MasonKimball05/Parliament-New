"""
Chapter minutes can be linked, re-linked, and unlinked from an event AFTER
creation, and the event picker used for that (and for the create-minutes
modal) surfaces events near "now" rather than the farthest-future ones.

v3.29.3 — two things Mason reported while testing:

1. Linking chapter minutes to an event was only possible at creation time
   (`create_chapter_minutes`); there was no way to change it afterward short
   of deleting and recreating the whole minutes session. New
   `update_minutes_event` endpoint fixes this.

2. An event about an hour from starting, visible on the calendar, was not
   showing up in that event picker. Root cause: the picker queried
   `Event.objects.filter(requires_attendance=True).order_by('-date_time')
   [:20]` — no date window, sorted purely by descending date_time. A single
   recurring weekly meeting can pre-generate up to 52 future instances
   (`generate_recurring_events`), so a chapter with one recurring meeting
   already has more than 20 events scheduled further into the future than
   "tonight" — every one of those instances outranked the actual near-term
   event in a `-date_time` sort, pushing it off the list entirely. Reported
   as a timezone ("UTC not CST") issue; it reproduces regardless of
   timezone — `TIME_ZONE`/`USE_TZ` are configured correctly and this is
   purely an ordering/windowing bug. Fixed with `_linkable_events()`, which
   windows to `now +/- 60 days` and sorts by absolute distance from now.
"""
import json
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import ChapterMinutes, Event, ParliamentUser, ActivityLog
from src.view.officer.chapter_minutes import _linkable_events


def _member(user_id, name, member_type='Officer', is_admin=False):
    user = ParliamentUser.objects.create_user(
        user_id=user_id, password='minutes-event-link-pass-12345!',
        name=name, username=user_id.lower().replace('-', '_'),
        member_type=member_type,
    )
    if is_admin:
        user.is_admin = True
        user.save()
    return user


def _event(creator, days_offset=0, title='Chapter Meeting', requires_attendance=True):
    return Event.objects.create(
        title=title, description='x',
        date_time=timezone.now() + timedelta(days=days_offset),
        created_by=creator, requires_attendance=requires_attendance, is_active=True,
    )


class LinkableEventsWindowAndOrderingTests(TestCase):
    """
    `_linkable_events()` — the query used by both the create-minutes modal
    and the edit-page "Link to Event" picker.
    """

    def setUp(self):
        self.officer = _member('LEV-OFF1', 'Officer One')

    def test_event_starting_soon_is_not_buried_by_far_future_recurring_instances(self):
        # Reproduce the exact reported bug: 25 far-future instances of a
        # recurring series (well beyond the old `[:20]` cutoff), plus one
        # ordinary event happening in an hour.
        for i in range(25):
            _event(self.officer, days_offset=30 + i, title=f'Recurring Instance {i}')
        soon_event = _event(self.officer, days_offset=0, title="Tonight's Meeting")
        soon_event.date_time = timezone.now() + timedelta(hours=1)
        soon_event.save()

        results = _linkable_events()

        self.assertIn(soon_event, results)

    def test_events_ordered_by_closeness_to_now_not_raw_chronology(self):
        far_future = _event(self.officer, days_offset=45, title='Far Future')
        near_future = _event(self.officer, days_offset=0, title='Near Future')
        near_future.date_time = timezone.now() + timedelta(hours=2)
        near_future.save()
        recent_past = _event(self.officer, days_offset=0, title='Recent Past')
        recent_past.date_time = timezone.now() - timedelta(hours=1)
        recent_past.save()

        results = _linkable_events()

        # The nearest-to-now event (recent_past, 1hr away) should rank
        # ahead of one that's 2hrs away, which should rank ahead of one
        # 45 days away — regardless of past/future direction.
        self.assertLess(results.index(recent_past), results.index(near_future))
        self.assertLess(results.index(near_future), results.index(far_future))

    def test_events_far_outside_the_window_are_excluded(self):
        far_past = _event(self.officer, days_offset=-90, title='Far Past')
        far_future = _event(self.officer, days_offset=90, title='Far Future')

        results = _linkable_events()

        self.assertNotIn(far_past, results)
        self.assertNotIn(far_future, results)

    def test_events_not_requiring_attendance_are_excluded(self):
        optional_event = _event(self.officer, requires_attendance=False, title='Optional')

        results = _linkable_events()

        self.assertNotIn(optional_event, results)

    def test_respects_limit(self):
        for i in range(30):
            _event(self.officer, days_offset=0, title=f'Event {i}')

        results = _linkable_events(limit=10)

        self.assertEqual(len(results), 10)


class UpdateMinutesEventTests(TestCase):
    """
    The new edit-time link/re-link/unlink endpoint.
    """

    def setUp(self):
        self.officer = _member('UME-OFF1', 'Officer One')
        self.non_officer = _member('UME-MEM1', 'Member One', member_type='Member')
        self.event_a = _event(self.officer, title='Event A')
        self.event_b = _event(self.officer, title='Event B')
        self.minutes = ChapterMinutes.objects.create(
            title='Test Minutes', date=timezone.localdate(),
            start_time='19:00', created_by=self.officer, status='draft',
        )
        self.url = reverse('update_minutes_event', args=[self.minutes.id])

    def _post(self, event_id):
        return self.client.post(
            self.url, data=json.dumps({'event_id': event_id}),
            content_type='application/json',
        )

    def test_officer_can_link_previously_unlinked_minutes(self):
        self.client.login(username=self.officer.username, password='minutes-event-link-pass-12345!')
        self.assertIsNone(self.minutes.event)

        resp = self._post(self.event_a.id)

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()['success'])
        self.minutes.refresh_from_db()
        self.assertEqual(self.minutes.event_id, self.event_a.id)

    def test_officer_can_relink_to_a_different_event(self):
        self.minutes.event = self.event_a
        self.minutes.save()
        self.client.login(username=self.officer.username, password='minutes-event-link-pass-12345!')

        resp = self._post(self.event_b.id)

        self.assertEqual(resp.status_code, 200)
        self.minutes.refresh_from_db()
        self.assertEqual(self.minutes.event_id, self.event_b.id)

    def test_officer_can_unlink_by_sending_no_event_id(self):
        self.minutes.event = self.event_a
        self.minutes.save()
        self.client.login(username=self.officer.username, password='minutes-event-link-pass-12345!')

        resp = self._post(None)

        self.assertEqual(resp.status_code, 200)
        self.minutes.refresh_from_db()
        self.assertIsNone(self.minutes.event)

    def test_nonexistent_event_id_is_rejected_without_touching_the_link(self):
        self.minutes.event = self.event_a
        self.minutes.save()
        self.client.login(username=self.officer.username, password='minutes-event-link-pass-12345!')

        resp = self._post(999999)

        self.assertEqual(resp.status_code, 400)
        self.minutes.refresh_from_db()
        self.assertEqual(self.minutes.event_id, self.event_a.id)

    def test_non_officer_cannot_update_the_link(self):
        self.client.login(username=self.non_officer.username, password='minutes-event-link-pass-12345!')

        resp = self._post(self.event_a.id)

        self.assertNotEqual(resp.status_code, 200)
        self.minutes.refresh_from_db()
        self.assertIsNone(self.minutes.event)

    def test_logged_out_user_cannot_update_the_link(self):
        resp = self._post(self.event_a.id)

        self.assertNotEqual(resp.status_code, 200)

    def test_linking_published_minutes_tracks_edit_metadata(self):
        self.minutes.status = 'published'
        self.minutes.save()
        self.client.login(username=self.officer.username, password='minutes-event-link-pass-12345!')

        self._post(self.event_a.id)

        self.minutes.refresh_from_db()
        self.assertTrue(self.minutes.edited_after_publish)
        self.assertEqual(self.minutes.last_edit_by_id, self.officer.pk)
        self.assertIsNotNone(self.minutes.last_edit_at)

    def test_linking_draft_minutes_does_not_flag_it_as_edited_after_publish(self):
        self.client.login(username=self.officer.username, password='minutes-event-link-pass-12345!')

        self._post(self.event_a.id)

        self.minutes.refresh_from_db()
        self.assertFalse(self.minutes.edited_after_publish)

    def test_link_change_is_activity_logged(self):
        self.client.login(username=self.officer.username, password='minutes-event-link-pass-12345!')

        self._post(self.event_a.id)

        log = ActivityLog.objects.filter(
            object_type='ChapterMinutes', object_id=self.minutes.id,
        ).order_by('-timestamp').first()
        self.assertIsNotNone(log)
        self.assertIn(self.event_a.title, log.description)

    def test_committee_minutes_are_not_touched_by_the_chapter_endpoint(self):
        # Sanity: the endpoint's get_object_or_404 filters committee__isnull=True,
        # so a committee-minutes id should 404 rather than silently succeed.
        from src.models import Committee
        committee = Committee.objects.create(name='Test Committee', code='TESTCMT1')
        committee_minutes = ChapterMinutes.objects.create(
            title='Committee Minutes', date=timezone.localdate(),
            start_time='19:00', created_by=self.officer, status='draft',
            committee=committee,
        )
        self.client.login(username=self.officer.username, password='minutes-event-link-pass-12345!')

        resp = self.client.post(
            reverse('update_minutes_event', args=[committee_minutes.id]),
            data=json.dumps({'event_id': self.event_a.id}),
            content_type='application/json',
        )

        self.assertEqual(resp.status_code, 404)


class EditChapterMinutesPageShowsLinkControlTests(TestCase):
    """The edit page itself renders the new link-to-event control."""

    def setUp(self):
        self.officer = _member('ECM-OFF1', 'Officer One')
        self.minutes = ChapterMinutes.objects.create(
            title='Test Minutes', date=timezone.localdate(),
            start_time='19:00', created_by=self.officer, status='draft',
        )
        self.client.login(username=self.officer.username, password='minutes-event-link-pass-12345!')

    def test_link_event_control_present_when_unlinked(self):
        resp = self.client.get(reverse('edit_chapter_minutes', args=[self.minutes.id]))
        self.assertContains(resp, 'js-open-link-event-modal')
        self.assertContains(resp, 'Link to event')

    def test_link_event_control_shows_current_event_when_linked(self):
        event = _event(self.officer, title='Linked Event Title')
        self.minutes.event = event
        self.minutes.save()

        resp = self.client.get(reverse('edit_chapter_minutes', args=[self.minutes.id]))

        self.assertContains(resp, 'Linked Event Title')
