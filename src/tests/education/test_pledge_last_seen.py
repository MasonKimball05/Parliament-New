"""
v3.47.0 — "last time a pledge got on the site" on the education dashboard.

Mason, 10-16-26: "Not just entered credentials, the last time they got on."
`last_login` only moves on a password login; `last_seen_at` moves on any
signed-in request (throttled to once per 5 minutes per session) and never on
an admin's "log in as".
"""
from datetime import datetime, time, timedelta
from unittest import mock

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.impersonation import SESSION_ORIGINAL_ID, SESSION_ORIGINAL_NAME
from src.models import ParliamentUser
from src.tests.education._fixtures import EducationFixtureMixin


class LastSeenIsRecordedTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.build()

    def tearDown(self):
        cache.clear()

    def seen(self, user):
        return ParliamentUser.objects.get(pk=user.pk).last_seen_at

    def test_a_signed_in_request_records_it_without_a_new_login(self):
        self.assertIsNone(self.seen(self.pledge))
        self.client.force_login(self.pledge)  # no password typed, like a remembered session
        self.client.get(reverse('my_pledge_tasks'))
        self.assertIsNotNone(self.seen(self.pledge))
        self.assertLess(timezone.now() - self.seen(self.pledge), timedelta(minutes=1))

    def test_an_admin_logged_in_as_the_pledge_does_not_record_it(self):
        self.client.force_login(self.pledge)
        session = self.client.session
        session[SESSION_ORIGINAL_ID] = self.chair.pk
        session[SESSION_ORIGINAL_NAME] = self.chair.name
        session.save()
        self.client.get(reverse('my_pledge_tasks'))
        self.assertIsNone(self.seen(self.pledge))


class DashboardShowsLastSeenTests(EducationFixtureMixin, TestCase):

    def setUp(self):
        cache.clear()
        self.build()
        self.client.force_login(self.chair)

    def tearDown(self):
        cache.clear()

    def html(self):
        response = self.client.get(reverse('education_home', args=[self.committee.code]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def set_seen(self, user, days_ago):
        # v3.48.1 — noon, N calendar days ago in the site's time zone. The
        # dashboard counts calendar days now, so "now minus N x 24 h" would
        # read as N+1 in the minutes after midnight (and across a DST change).
        day = timezone.localdate() - timedelta(days=days_ago)
        ParliamentUser.objects.filter(pk=user.pk).update(
            last_seen_at=timezone.make_aware(datetime.combine(day, time(12))))

    def test_never_on(self):
        self.assertIn('Never on', self.html())

    def test_today_yesterday_and_days_ago(self):
        self.set_seen(self.pledge, 0)
        self.set_seen(self.other_pledge, 1)
        html = self.html()
        self.assertIn('Last on today', html)
        self.assertIn('Last on yesterday', html)
        self.set_seen(self.other_pledge, 10)
        self.assertIn('Last on 10 days ago', self.html())

    def test_late_last_night_reads_yesterday_not_today(self):
        # v3.48.1 — the bug: (now - seen).days counts 24-hour blocks, so a
        # pledge on at 11 PM, looked at by the chair at 8 AM, was "today".
        today = timezone.localdate()
        eight_am = timezone.make_aware(datetime.combine(today, time(8)))
        eleven_pm_last_night = timezone.make_aware(
            datetime.combine(today - timedelta(days=1), time(23)))
        ParliamentUser.objects.filter(pk=self.pledge.pk).update(last_seen_at=eleven_pm_last_night)
        ParliamentUser.objects.filter(pk=self.other_pledge.pk).update(last_seen_at=None)
        with mock.patch('django.utils.timezone.now', return_value=eight_am):
            html = self.html()
        self.assertIn('Last on yesterday', html)
        self.assertNotIn('Last on today', html)

    def test_colour_turns_amber_at_7_days_and_red_at_14(self):
        self.set_seen(self.pledge, 8)
        self.set_seen(self.other_pledge, 20)
        summaries = {ps['pledge'].pk: ps['last_seen_days'] for ps in
                     self.client.get(reverse('education_home', args=[self.committee.code])).context['pledge_summaries']}
        self.assertEqual(summaries[self.pledge.pk], 8)
        self.assertEqual(summaries[self.other_pledge.pk], 20)
        html = self.html()
        self.assertIn('text-amber-600 dark:text-amber-400 font-medium"', html)
        self.assertIn('Last on 20 days ago', html)


class BackfillTests(EducationFixtureMixin, TestCase):

    def test_backfill_takes_the_newer_of_last_login_and_session_activity(self):
        import importlib
        from django.apps import apps
        from src.models import UserSession
        self.build()
        old = timezone.now() - timedelta(days=20)
        newer = timezone.now() - timedelta(days=2)
        ParliamentUser.objects.filter(pk=self.pledge.pk).update(last_login=old)
        s = UserSession.objects.create(user=self.pledge, session_key='k' * 32)
        UserSession.objects.filter(pk=s.pk).update(last_activity=newer)
        ParliamentUser.objects.filter(pk=self.other_pledge.pk).update(last_login=old)
        importlib.import_module('src.migrations.0060_parliamentuser_last_seen_at').backfill(apps, None)
        self.assertEqual(ParliamentUser.objects.get(pk=self.pledge.pk).last_seen_at, newer)
        self.assertEqual(ParliamentUser.objects.get(pk=self.other_pledge.pk).last_seen_at, old)
        self.assertIsNone(ParliamentUser.objects.get(pk=self.brother.pk).last_seen_at)
