"""
Calendar access guards.

* `event_signup` / `event_cancel_signup` looked an event up by id with only
  `is_active=True, requires_signup=True`, so any logged-in member could sign
  up for (or cancel on) an event whose `visible_to` excludes them, or an
  archived one, just by POSTing its id. They now 404 exactly like an event
  that doesn't exist.
* `calendar_data_api` did `int(request.GET['year'])` and
  `datetime(year, month, 1)` unguarded — `?month=abc` or `?month=13` was a
  500. It now returns a 400 JSON error.
* `calendar_subscription_feed` is unauthenticated (token in the URL) and
  ignored both the `calendar_subscriptions` feature flag (the views that hand
  out the token are gated by it) and whether the token's owner can still log
  in. A deactivated / quarantined / removed member's old webcal link kept
  serving the chapter calendar indefinitely.
"""
import json
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Event, EventSignup, ParliamentUser
from src.models_calendar_subscription import CalendarSubscription
from src.models_feature_flags import FeatureFlag


def make_member(uid='calguard-member', **kwargs):
    defaults = dict(
        name='Cal Guard Member', username=uid, member_type='Member',
        member_status='Active',
    )
    defaults.update(kwargs)
    return ParliamentUser.objects.create_user(user_id=uid, **defaults)


def make_signup_event(creator, **kwargs):
    defaults = dict(
        title='Signup Event', description='D',
        date_time=timezone.now() + timedelta(days=3),
        is_active=True, requires_signup=True, created_by=creator,
    )
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


class EventSignupVisibilityTests(TestCase):
    def setUp(self):
        self.user = make_member()
        self.client = Client()
        self.client.force_login(self.user)

    def _signup(self, event):
        return self.client.post(reverse('event_signup', args=[event.pk]))

    def _cancel(self, event):
        return self.client.post(reverse('event_cancel_signup', args=[event.pk]))

    def test_visible_event_signup_still_works(self):
        event = make_signup_event(self.user)
        resp = self._signup(event)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(json.loads(resp.content)['success'])

    def test_signup_refused_for_event_not_visible_to_user(self):
        event = make_signup_event(self.user, visible_to=['Advisor'])
        resp = self._signup(event)
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(EventSignup.objects.filter(event=event, user=self.user).exists())

    def test_signup_refused_for_archived_event(self):
        event = make_signup_event(self.user, archived=True)
        resp = self._signup(event)
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(EventSignup.objects.filter(event=event, user=self.user).exists())

    def test_cancel_refused_for_event_not_visible_to_user(self):
        event = make_signup_event(self.user, visible_to=['Advisor'])
        signup = EventSignup.objects.create(event=event, user=self.user)
        resp = self._cancel(event)
        self.assertEqual(resp.status_code, 404)
        signup.refresh_from_db()
        self.assertFalse(signup.is_cancelled)

    def test_cancel_visible_event_still_works(self):
        event = make_signup_event(self.user)
        EventSignup.objects.create(event=event, user=self.user)
        resp = self._cancel(event)
        self.assertEqual(resp.status_code, 200)


class CalendarDataApiInputTests(TestCase):
    def setUp(self):
        self.user = make_member('calguard-api')
        self.client = Client()
        self.client.force_login(self.user)

    def _get(self, **params):
        return self.client.get(reverse('calendar_data_api'), params)

    def test_valid_request_ok(self):
        now = timezone.now()
        resp = self._get(year=now.year, month=now.month)
        self.assertEqual(resp.status_code, 200)

    def test_non_numeric_params_are_400_json(self):
        for params in ({'year': 'abc', 'month': '1'}, {'year': '2026', 'month': 'x'},
                       {'year': '', 'month': ''}):
            with self.subTest(params=params):
                resp = self._get(**params)
                self.assertEqual(resp.status_code, 400)
                self.assertIn('error', json.loads(resp.content))

    def test_out_of_range_month_is_400_json(self):
        for month in ('0', '13', '-1'):
            with self.subTest(month=month):
                resp = self._get(year='2026', month=month)
                self.assertEqual(resp.status_code, 400)
                self.assertIn('error', json.loads(resp.content))

    def test_absurd_year_is_400_json(self):
        resp = self._get(year='99999999', month='1')
        self.assertEqual(resp.status_code, 400)


class CalendarSubscriptionFeedGuardTests(TestCase):
    def setUp(self):
        self.user = make_member('calguard-feed')
        self.sub = CalendarSubscription.get_or_create_for_user(self.user)
        Event.objects.create(
            title='Feed Event', description='D',
            date_time=timezone.now() + timedelta(days=2),
            is_active=True, created_by=self.user,
        )
        self.url = reverse('calendar_subscription_feed', kwargs={'token': self.sub.token})

    def test_feed_served_for_current_member(self):
        resp = Client().get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b'Feed Event', resp.content)

    def test_feed_404_when_feature_flag_disabled(self):
        FeatureFlag.objects.create(
            name='calendar_subscriptions', display_name='Calendar subscriptions',
            description='d', is_enabled=False,
        )
        resp = Client().get(self.url)
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn(b'Feed Event', resp.content)

    def test_feed_404_for_deactivated_user(self):
        ParliamentUser.objects.filter(pk=self.user.pk).update(is_active=False)
        resp = Client().get(self.url)
        self.assertEqual(resp.status_code, 404)

    def test_feed_404_for_quarantined_user(self):
        ParliamentUser.objects.filter(pk=self.user.pk).update(is_quarantined=True)
        resp = Client().get(self.url)
        self.assertEqual(resp.status_code, 404)

    def test_feed_404_for_removed_member(self):
        ParliamentUser.objects.filter(pk=self.user.pk).update(member_status='Removed')
        resp = Client().get(self.url)
        self.assertEqual(resp.status_code, 404)

    def test_feed_still_served_for_alumni(self):
        # Alumni can still log in and view the calendar; their feed stays live.
        ParliamentUser.objects.filter(pk=self.user.pk).update(member_status='Alumni')
        resp = Client().get(self.url)
        self.assertEqual(resp.status_code, 200)
