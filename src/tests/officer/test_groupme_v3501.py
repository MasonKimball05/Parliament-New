"""
v3.50.1 — GroupMe fixes.

  * `deliver()` locked the message with select_for_update() + select_related
    on the nullable `channel` FK. PostgreSQL refuses FOR UPDATE on the nullable
    side of an outer join, so on prod every send raised NotSupportedError and
    nothing was ever posted (the Test button posts directly, so it worked).
    SQLite ignores FOR UPDATE; the existing GroupMe tests only fail on Postgres
    (CI). `test_deliver_query_has_no_outer_join` pins it on any backend.
  * `deliver()` re-checks the audience rule when it posts: a message waiting on
    a retry is skipped if the channel was turned off, the announcement was
    taken down or narrowed, or the event was hidden / has started.
  * Moving an event clears its "reminder sent" marks so push and GroupMe
    reminders go out again for the new time; a reminded event keeps its groups.

Run with: python manage.py test src.tests.officer.test_groupme_v3501
"""
from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from src import groupme
from src.models import Event, GroupMeMessage
from src.tasks import groupme as groupme_tasks
from src.tests.events.test_recurring_series_bulk import _dt, post_data
from src.tests.officer.test_groupme_bot import GroupMeFixture, inline_celery


class DeliverLockTests(GroupMeFixture, TestCase):
    def test_deliver_query_has_no_outer_join(self):
        m = GroupMeMessage.objects.create(channel=self.brothers, channel_name='Brothers',
                                          kind='test', text='hi')
        with CaptureQueriesContext(connection) as ctx:
            self.assertTrue(groupme.deliver(m.pk))
        first = next(q['sql'] for q in ctx.captured_queries
                     if 'src_groupmemessage' in q['sql'].lower() and q['sql'].upper().startswith('SELECT'))
        self.assertNotIn('LEFT OUTER JOIN', first.upper())


class DeliverRecheckTests(GroupMeFixture, TestCase):
    def failed_message(self, **create):
        """An announcement whose first post failed, waiting for a retry."""
        self.api.post_status = 503
        a = self.create(**create)
        self.api.post_status = 202
        m = GroupMeMessage.objects.get(announcement=a)
        self.assertEqual(m.status, 'failed')
        self.api.calls.clear()
        return a, m

    def retry(self, m):
        with inline_celery():
            groupme_tasks.send_groupme_message(m.pk)
        m.refresh_from_db()
        return m

    def test_retry_still_posts_when_nothing_changed(self):
        a, m = self.failed_message(channels=[self.everyone])
        self.assertEqual(self.retry(m).status, 'sent')
        self.assertEqual(len(self.api.posts()), 1)

    def test_channel_turned_off_before_retry(self):
        a, m = self.failed_message(channels=[self.everyone])
        self.everyone.is_active = False
        self.everyone.save()
        self.assertEqual(self.retry(m).status, 'skipped')
        self.assertEqual(self.api.posts(), [])

    def test_announcement_taken_down_before_retry(self):
        a, m = self.failed_message(channels=[self.everyone])
        a.is_active = False
        a.save()
        self.assertEqual(self.retry(m).status, 'skipped')
        self.assertEqual(self.api.posts(), [])

    def test_visible_to_narrowed_before_retry(self):
        a, m = self.failed_message(channels=[self.everyone])
        a.visible_to = ['Member']   # pledges are in that GroupMe
        a.save()
        self.assertEqual(self.retry(m).status, 'skipped')
        self.assertIn("can't see it", m.error)
        self.assertEqual(self.api.posts(), [])

    def test_event_reminder_after_event_started_is_skipped(self):
        e = Event.objects.create(title='Meeting', description='x',
                                 date_time=timezone.now() + timedelta(hours=1), created_by=self.officer)
        m = GroupMeMessage.objects.create(channel=self.everyone, channel_name='x', event=e,
                                          kind='event_reminder', text='⏰', status='failed')
        Event.objects.filter(pk=e.pk).update(date_time=timezone.now() - timedelta(minutes=5))
        self.assertEqual(self.retry(m).status, 'skipped')
        self.assertEqual(self.api.posts(), [])


class MovedEventRemindersTests(GroupMeFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.officer.is_admin = True
        self.officer.save()
        when = timezone.now().replace(second=0, microsecond=0) + timedelta(hours=10)
        self.event = Event.objects.create(
            title='Chapter Meeting', description='x', location='Hall', date_time=when,
            created_by=self.officer, groupme_reminder_hours=24,
            reminder_1_enabled=True, reminder_1_hours_before=24)
        self.event.groupme_channels.set([self.everyone])
        now = timezone.now()
        Event.objects.filter(pk=self.event.pk).update(
            groupme_reminded_at=now, reminder_1_sent_at=now)
        self.event.refresh_from_db()

    def edit(self, **changes):
        r = self.oc.post(reverse('edit_event', args=[self.event.pk]), post_data(self.event, **changes))
        self.assertEqual(r.status_code, 302, r.content[:400])
        self.event.refresh_from_db()

    def test_moving_the_event_resends_reminders_to_the_same_groups(self):
        self.edit(date_time=_dt(self.event.date_time + timedelta(days=2)))
        self.assertIsNone(self.event.groupme_reminded_at)
        self.assertIsNone(self.event.reminder_1_sent_at)
        self.assertEqual(list(self.event.groupme_channels.all()), [self.everyone])

    def test_editing_without_moving_keeps_the_sent_marks(self):
        self.edit(location='Library')
        self.assertIsNotNone(self.event.groupme_reminded_at)
        self.assertIsNotNone(self.event.reminder_1_sent_at)
        self.assertEqual(list(self.event.groupme_channels.all()), [self.everyone])


class OddDigitsTests(GroupMeFixture, TestCase):
    def test_superscript_digits_in_channel_ids_dont_crash(self):
        # '²'.isdigit() is True but int('²') raises; isdecimal() is the safe test.
        data = {'title': 'x', 'content': 'y', 'is_active': 'on', 'groupme_channel_ids': ['²']}
        with inline_celery(), self.captureOnCommitCallbacks(execute=True):
            r = self.oc.post(reverse('create_announcement'), data)
        self.assertIn(r.status_code, (200, 302))
