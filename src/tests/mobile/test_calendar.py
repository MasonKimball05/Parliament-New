"""
Mobile plan, Phase 3 — the Calendar endpoints, and Event.updated_at (v3.54.0).

The app must see exactly the events the web calendar shows, sign up through
the same code (lock, waitlist, 404 for hidden events), and honour the same
page toggle and pledge restrictions. The feed part checks that an edit now
reaches subscribed calendars (LAST-MODIFIED / SEQUENCE used to be fixed).

    python manage.py test src.tests.mobile.test_calendar
"""
from datetime import datetime, timedelta
from unittest.mock import patch

import pytz
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from src.event_series import SCOPE_ALL, apply_series_edit
from src.models import AttendanceExcuse, Event, EventSignup
from src.models_calendar_subscription import CalendarSubscription
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag, restrict, toggle

API = '/api/mobile/v1/'


def local(y, m, d, h=12, mi=0):
    return pytz.timezone(settings.TIME_ZONE).localize(datetime(y, m, d, h, mi))


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('cal-1')
        self.device, self.access, _ = make_device(self.user)
        self.api = Client()
        self.soon = timezone.now() + timedelta(days=2)

    def event(self, title, when=None, **kw):
        return Event.objects.create(title=title, description='d', created_by=self.user,
                                    date_time=when or self.soon, **kw)

    def get(self, path, access=None, **params):
        return self.api.get(API + path, params, **bearer(access or self.access))

    def send(self, method, path, access=None):
        return getattr(self.api, method)(API + path, **bearer(access or self.access))

    def titles(self, response):
        return [e['title'] for e in response.json()['events']]


class EventListTests(_Base):

    def test_visible_events_in_the_default_window(self):
        self.event('Chapter')
        self.event('Officers only', visible_to=['Officer'])
        self.event('Inactive', is_active=False)
        self.event('Archived', archived=True)
        self.event('Too far', when=timezone.now() + timedelta(days=60))
        r = self.get('events/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.titles(r), ['Chapter'])

    def test_range_is_inclusive_in_the_chapter_time_zone(self):
        # 23:30 local on the 31st is still the 31st, whatever the UTC date.
        today = timezone.localtime().date()
        last = today + timedelta(days=5)
        self.event('Late', when=local(last.year, last.month, last.day, 23, 30))
        nxt = last + timedelta(days=1)
        self.event('Next day', when=local(nxt.year, nxt.month, nxt.day, 0, 30))
        r = self.get('events/', **{'from': today.isoformat(), 'to': last.isoformat()})
        self.assertEqual(self.titles(r), ['Late'])

    def test_range_ends_at_local_midnight_when_the_clocks_change(self):
        # Fixed dates, so this holds whatever day the suite runs. In Central
        # time, 2026-11-01 is 25 hours long (clocks fall back) and 2027-03-14 is
        # 23 (spring forward); the range must end at the next local midnight.
        real_localtime = timezone.localtime
        fake_now = local(2026, 10, 28)

        def localtime(value=None, *args, **kwargs):
            # "Today" is pinned (the API's window is a year around it); event
            # times still convert for real.
            return fake_now if value is None else real_localtime(value, *args, **kwargs)

        with patch('src.mobile.views_calendar.timezone.localtime', side_effect=localtime):
            self.event('Fall back, 23:30', when=local(2026, 11, 1, 23, 30))
            self.event('Nov 2, 00:30', when=local(2026, 11, 2, 0, 30))
            r = self.get('events/', **{'from': '2026-10-28', 'to': '2026-11-01'})
            self.assertEqual(self.titles(r), ['Fall back, 23:30'])
            self.event('Spring forward, 23:30', when=local(2027, 3, 14, 23, 30))
            self.event('Mar 15, 00:30', when=local(2027, 3, 15, 0, 30))
            r = self.get('events/', **{'from': '2027-03-14', 'to': '2027-03-14'})
            self.assertEqual(self.titles(r), ['Spring forward, 23:30'])

    def test_bad_params(self):
        self.assertEqual(self.get('events/', **{'from': 'nope'}).json()['error'], 'invalid_request')
        today = timezone.localtime().date()
        r = self.get('events/', **{'from': today.isoformat(),
                                   'to': (today - timedelta(days=1)).isoformat()})
        self.assertEqual(r.status_code, 400)
        r = self.get('events/', **{'from': today.isoformat(),
                                   'to': (today + timedelta(days=93)).isoformat()})
        self.assertEqual(r.status_code, 400)

    def test_clamped_to_a_year_either_side(self):
        far = timezone.localtime().date() + timedelta(days=500)
        r = self.get('events/', **{'from': far.isoformat()})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['events'], [])

    def test_signup_and_excuse_state(self):
        e = self.event('Signup', requires_signup=True, max_signups=1, allow_waitlist=True,
                       allow_excuses=True)
        other = make_user('cal-2')
        EventSignup.objects.create(event=e, user=other)
        EventSignup.objects.create(event=e, user=self.user, waitlist_position=1)
        AttendanceExcuse.objects.create(event=e, user=self.user, reason='r')
        item = self.get('events/').json()['events'][0]
        self.assertEqual(item['signup'], {
            'open': True, 'max': 1, 'allow_waitlist': True, 'count': 1, 'waitlist_count': 1,
            'full': True, 'status': 'waitlist', 'waitlist_position': 1})
        self.assertTrue(item['excuse']['can_submit'])
        self.assertEqual(item['excuse']['status'], 'pending')
        local_dt = timezone.localtime(e.date_time)
        self.assertEqual(item['links']['web'],
                         f'/calendar/?year={local_dt.year}&month={local_dt.month}')

    def test_queries_do_not_grow_with_events(self):
        for i in range(3):
            self.event(f'E{i}', requires_signup=True)
        self.get('events/')   # warm flag/toggle caches
        with CaptureQueriesContext(connection) as small:
            self.get('events/')
        for i in range(10):
            e = self.event(f'More{i}', requires_signup=True, allow_excuses=True)
            EventSignup.objects.create(event=e, user=self.user)
        with CaptureQueriesContext(connection) as big:
            r = self.get('events/')
        self.assertEqual(len(r.json()['events']), 13)
        self.assertEqual(len(big.captured_queries), len(small.captured_queries))

    def test_calendar_toggle_off_refuses(self):
        toggle('calendar', False)
        r = self.get('events/')
        self.assertEqual((r.status_code, r.json()['error']), (403, 'page_disabled'))

    def test_pledge_restriction_on_calendar_refuses(self):
        pledge = make_user('P-CAL001', member_type='Pledge')
        _, access = make_device(pledge)[:2]
        restrict('calendar')
        r = self.get('events/', access=access)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))


class EventDetailTests(_Base):

    def test_visible_event(self):
        e = self.event('Chapter')
        r = self.get(f'events/{e.id}/')
        self.assertEqual(r.json()['event']['title'], 'Chapter')

    def test_hidden_or_archived_is_404(self):
        for e in (self.event('Hidden', visible_to=['Officer']),
                  self.event('Archived', archived=True),
                  self.event('Inactive', is_active=False)):
            r = self.get(f'events/{e.id}/')
            self.assertEqual((r.status_code, r.json()['error']), (404, 'not_found'), e.title)


class EventSignupTests(_Base):

    def test_sign_up_then_cancel(self):
        e = self.event('S', requires_signup=True)
        r = self.send('post', f'events/{e.id}/signup/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual((r.json()['signup_count'], r.json()['user_signed_up']), (1, True))
        r = self.send('delete', f'events/{e.id}/signup/')
        self.assertEqual((r.json()['signup_count'], r.json()['user_signed_up']), (0, False))

    def test_full_goes_to_waitlist_and_cancel_promotes(self):
        e = self.event('S', requires_signup=True, max_signups=1, allow_waitlist=True)
        first = make_user('cal-3')
        _, first_access = make_device(first)[:2]
        self.send('post', f'events/{e.id}/signup/', access=first_access)
        r = self.send('post', f'events/{e.id}/signup/')
        self.assertEqual((r.json()['on_waitlist'], r.json()['waitlist_position']), (True, 1))
        self.send('delete', f'events/{e.id}/signup/', access=first_access)
        mine = EventSignup.objects.get(event=e, user=self.user)
        self.assertIsNone(mine.waitlist_position)

    def test_the_last_slot_can_be_taken(self):
        # v3.54.0 regression: the member's own just-created row was counted as
        # a taken slot, so the Nth sign-up on an N-slot event was waitlisted
        # (or refused as full). Checked through the web button too, which
        # shares the code (src/event_signups.py).
        e = self.event('Two slots', requires_signup=True, max_signups=2, allow_waitlist=True)
        other = make_user('cal-4')
        web = Client()
        web.force_login(other)
        r = web.post(reverse('event_signup', args=[e.id]))
        self.assertEqual((r.json()['on_waitlist'], r.json()['signup_count']), (False, 1))
        r = self.send('post', f'events/{e.id}/signup/')
        self.assertEqual((r.json()['on_waitlist'], r.json()['signup_count'], r.json()['signup_full']),
                         (False, 2, True))
        third = make_user('cal-5')
        _, access = make_device(third)[:2]
        r = self.send('post', f'events/{e.id}/signup/', access=access)
        self.assertEqual((r.json()['on_waitlist'], r.json()['waitlist_position']), (True, 1))

    def test_last_slot_without_waitlist_is_not_refused(self):
        e = self.event('One slot', requires_signup=True, max_signups=1)
        r = self.send('post', f'events/{e.id}/signup/')
        self.assertEqual((r.status_code, r.json()['signup_count']), (200, 1))

    def test_refusals_carry_the_message(self):
        full = self.event('Full', requires_signup=True, max_signups=0)
        r = self.send('post', f'events/{full.id}/signup/')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json(), {'error': 'signup_refused', 'detail': 'This event is full.'})
        closed = self.event('Closed', requires_signup=True, signups_open=False)
        self.assertEqual(self.send('post', f'events/{closed.id}/signup/').json()['detail'],
                         'Sign-ups are closed for this event.')
        r = self.send('delete', f'events/{closed.id}/signup/')
        self.assertEqual(r.json()['detail'], 'You are not signed up for this event.')

    def test_hidden_event_is_404_and_writes_nothing(self):
        e = self.event('Hidden', requires_signup=True, visible_to=['Officer'])
        self.assertEqual(self.send('post', f'events/{e.id}/signup/').status_code, 404)
        self.assertFalse(EventSignup.objects.filter(event=e).exists())

    def test_pledge_restriction_on_the_web_signup_button_applies(self):
        pledge = make_user('P-CAL002', member_type='Pledge')
        _, access = make_device(pledge)[:2]
        e = self.event('S', requires_signup=True)
        restrict('event_signup')
        r = self.send('post', f'events/{e.id}/signup/', access=access)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))
        self.assertFalse(EventSignup.objects.filter(event=e).exists())


class CalendarSubscriptionTests(_Base):

    def test_returns_the_members_feed(self):
        r = self.get('calendar/subscription/')
        token = CalendarSubscription.objects.get(user=self.user).token
        self.assertEqual(r.json()['webcal_url'], f'webcal://testserver/calendar/feed/{token}/')
        self.assertEqual(self.get('calendar/subscription/').json(), r.json())   # stable

    def test_flag_off(self):
        flag('calendar_subscriptions', False)
        r = self.get('calendar/subscription/')
        self.assertEqual((r.status_code, r.json()['error']), (403, 'feature_disabled'))


class FeedSeesEditsTests(TestCase):
    """v3.54.0 — Event.updated_at drives the feed's LAST-MODIFIED and SEQUENCE."""

    def setUp(self):
        self.user = make_user('cal-feed')
        self.url = reverse('calendar_subscription_feed', kwargs={
            'token': CalendarSubscription.get_or_create_for_user(self.user).token})
        self.e = Event.objects.create(title='Feed', description='d', created_by=self.user,
                                      date_time=timezone.now() + timedelta(days=1))

    def test_unedited_event_is_sequence_zero(self):
        self.e.refresh_from_db()
        Event.objects.filter(pk=self.e.pk).update(updated_at=self.e.created_at)
        self.assertIn(b'SEQUENCE:0', Client().get(self.url).content)

    def test_an_edit_raises_sequence_and_last_modified(self):
        Event.objects.filter(pk=self.e.pk).update(created_at=timezone.now() - timedelta(hours=1))
        self.e.refresh_from_db()
        self.e.title = 'Feed (moved)'
        self.e.save()
        body = Client().get(self.url).content
        self.assertNotIn(b'SEQUENCE:0', body)
        self.assertGreaterEqual(self.e.ical_sequence, 3600)
        stamp = self.e.updated_at.astimezone(pytz.utc).strftime('%Y%m%dT%H%M%S').encode()
        self.assertIn(b'LAST-MODIFIED:' + stamp, body)

    def test_bookkeeping_saves_do_not_count_as_edits(self):
        before = Event.objects.get(pk=self.e.pk).updated_at
        self.e.rsvp_email_sent_at = timezone.now()
        self.e.save(update_fields=['rsvp_email_sent_at'])
        self.assertEqual(Event.objects.get(pk=self.e.pk).updated_at, before)

    def test_series_edits_stamp_every_moved_occurrence(self):
        parent = self.e
        Event.objects.filter(pk=parent.pk).update(is_recurring=True)
        parent.refresh_from_db()
        child = Event.objects.create(title='Feed', description='d', created_by=self.user,
                                     date_time=parent.date_time + timedelta(days=7),
                                     parent_event=parent)
        old = Event.objects.get(pk=child.pk).updated_at
        before = {'date_time': parent.date_time, 'excuse_deadline': parent.excuse_deadline}
        parent.date_time += timedelta(hours=2)
        parent.save()
        apply_series_edit(parent, before, {'date_time'}, SCOPE_ALL)
        self.assertGreater(Event.objects.get(pk=child.pk).updated_at, old)
