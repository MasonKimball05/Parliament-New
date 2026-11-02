"""
Service hours, batch 5: the app's native versions of the pages it still sent
to the website.

* A member's own submission (``user_view_service_submission``), through
  ``GET service-hours/submissions/<id>/`` (src/mobile/views_service_hours.py).
* Periods, member expectations and service events for the VPP
  (src/mobile/views_service_admin.py), each the web page's helper in
  src/view/service_hours.py: the same rule for who may use them
  (``vpp_required``), the same data, the same checks, and the same side
  effects (pre-approved hours on finalize, reminders re-armed on a date change,
  activity log rows).
* The calendar event's ``service_event`` link for the VPP.

    python manage.py test src.tests.mobile.test_service_hours_batch5
"""
import json
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import (
    ActivityLog, Attendance, Event, ServiceActivity, ServiceEvent, ServiceFieldResponse, ServiceFormField,
    ServiceHoursSubmission, ServiceMemberExpectation, ServicePeriod,
)
from src.models.users import Role
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict
from src.view import service_hours as web

API = '/api/mobile/v1/'


def _local(days, hour, minute=0):
    """An aware datetime ``days`` from today at ``hour`` on the chapter's wall clock."""
    day = timezone.localdate() + timedelta(days=days)
    return timezone.make_aware(datetime(day.year, day.month, day.day, hour, minute))


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        today = timezone.localdate()
        self.period = ServicePeriod.objects.create(
            name='Fall', start_date=today - timedelta(days=30), end_date=today + timedelta(days=30),
            default_hours_required=Decimal('10'))
        self.old = ServicePeriod.objects.create(
            name='Spring', start_date=today - timedelta(days=200), end_date=today - timedelta(days=100),
            default_hours_required=Decimal('5'), is_active=False)
        role = Role.objects.create(name='VP Programming', code='VPP')
        self.vpp = make_user('b5-vpp', name='Vic Vpp', member_type='Officer')
        self.vpp.roles.add(role)
        _, self.access, _ = make_device(self.vpp)
        self.officer = make_user('b5-off', member_type='Officer')
        _, self.officer_access, _ = make_device(self.officer)
        self.amy = make_user('b5-amy', name='Amy Able')
        _, self.amy_access, _ = make_device(self.amy)
        self.bob = make_user('b5-bob', name='Bob Baker')
        self.api = Client()
        self.web = Client()
        self.web.force_login(self.vpp)

    def call(self, method, url, body=None, access=None):
        kw = bearer(access or self.access)
        if body is None:
            return getattr(self.api, method)(API + url, **kw)
        return getattr(self.api, method)(API + url, json.dumps(body), content_type='application/json', **kw)

    def make_event(self, days=3, hour=18, **extra):
        values, errors = web.parse_service_event_form({
            'title': extra.pop('title', 'Park cleanup'), 'date_time': _local(days, hour).isoformat(),
            'period': str(self.period.id), 'hours_awarded': extra.pop('hours', '2'), 'location': 'Park',
            **extra,
        }, creating=True)
        self.assertEqual(errors, [])
        return web.create_service_event_record(values, self.vpp, None)


class AccessTests(_Base):

    def test_only_the_vpp_and_admins(self):
        se = self.make_event()
        x = ServiceMemberExpectation.objects.create(period=self.period, member=self.amy, expected_hours=1)
        urls = [
            ('get', 'officer/service-hours/periods/'), ('post', 'officer/service-hours/periods/'),
            ('get', f'officer/service-hours/periods/{self.period.id}/'),
            ('post', f'officer/service-hours/periods/{self.period.id}/'),
            ('post', f'officer/service-hours/periods/{self.period.id}/toggle-active/'),
            ('post', f'officer/service-hours/periods/{self.old.id}/delete/'),
            ('get', f'officer/service-hours/periods/{self.period.id}/expectations/'),
            ('post', f'officer/service-hours/periods/{self.period.id}/expectations/'),
            ('delete', f'officer/service-hours/periods/{self.period.id}/expectations/{x.id}/'),
            ('get', 'officer/service-hours/events/'), ('post', 'officer/service-hours/events/'),
            ('get', f'officer/service-hours/events/{se.id}/'), ('post', f'officer/service-hours/events/{se.id}/'),
            ('delete', f'officer/service-hours/events/{se.id}/'),
            ('get', f'officer/service-hours/events/{se.id}/attendance/'),
            ('post', f'officer/service-hours/events/{se.id}/attendance/'),
            ('post', f'officer/service-hours/events/{se.id}/finalize/'),
        ]
        for method, url in urls:
            for access in (self.officer_access, self.amy_access):
                r = self.call(method, url, {} if method == 'post' else None, access=access)
                self.assertEqual((r.status_code, r.json()['error']), (403, 'forbidden'), url)
        # Nothing changed.
        self.assertTrue(ServicePeriod.objects.filter(id=self.old.id).exists())
        self.assertTrue(ServiceMemberExpectation.objects.filter(id=x.id).exists())
        self.assertTrue(ServiceEvent.objects.filter(id=se.id, hours_applied=False).exists())
        admin = make_user('b5-admin', is_admin=True)
        for method, url in urls:
            if method == 'get':
                r = self.call(method, url, access=make_device(admin)[1])
                self.assertEqual(r.status_code, 200, url)

    def test_pledge_restriction_applies(self):
        pledge = make_user('b5-pl', member_type='Pledge', member_status='Pledge', is_admin=True)
        restrict('service_events_list')
        cache.clear()
        r = self.call('get', 'officer/service-hours/events/', access=make_device(pledge)[1])
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))


class PeriodTests(_Base):

    def test_list_matches_the_page(self):
        self.make_event()
        d = self.call('get', 'officer/service-hours/periods/').json()
        page = self.web.get(reverse('manage_service_periods'))
        self.assertEqual([p['id'] for p in d['periods']], [p.id for p in page.context['periods']])
        fall = d['periods'][0]
        self.assertEqual((fall['name'], fall['default_hours_required'], fall['requires_approval'],
                          fall['is_active'], fall['is_current'], fall['service_event_count']),
                         ('Fall', 10.0, True, True, True, 1))
        self.assertFalse(d['periods'][1]['is_active'])

    def test_create_edit_and_validation(self):
        today = timezone.localdate()
        body = {'name': 'Winter', 'start_date': (today + timedelta(days=40)).isoformat(),
                'end_date': (today + timedelta(days=90)).isoformat(), 'default_hours_required': 8,
                'requires_approval': False, 'is_active': True}
        r = self.call('post', 'officer/service-hours/periods/', body)
        self.assertEqual(r.status_code, 201, r.content)
        p = ServicePeriod.objects.get(name='Winter')
        self.assertEqual((p.created_by, p.default_hours_required, p.requires_approval, p.is_active),
                         (self.vpp, Decimal('8'), False, True))
        self.assertEqual(r.json()['message'], 'Created period: Winter')

        r = self.call('post', f'officer/service-hours/periods/{p.id}/', {**body, 'name': 'Winter 2', 'is_active': False})
        self.assertEqual(r.status_code, 200, r.content)
        p.refresh_from_db()
        self.assertEqual((p.name, p.is_active, p.created_by), ('Winter 2', False, self.vpp))

        bad = {**body, 'end_date': body['start_date']}
        r = self.call('post', 'officer/service-hours/periods/', bad)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertIn('End date must be after start date.', r.json()['detail'])
        r = self.call('post', f'officer/service-hours/periods/{p.id}/', {**body, 'name': ''})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))

    def test_toggle(self):
        r = self.call('post', f'officer/service-hours/periods/{self.old.id}/toggle-active/')
        self.assertEqual((r.json()['period']['is_active'], r.json()['message']), (True, 'Spring activated.'))
        r = self.call('post', f'officer/service-hours/periods/{self.old.id}/toggle-active/')
        self.assertEqual(r.json()['message'], 'Spring deactivated.')

    def test_delete_refused_while_a_service_event_counts_toward_it(self):
        self.make_event()
        r = self.call('post', f'officer/service-hours/periods/{self.period.id}/delete/')
        self.assertEqual((r.status_code, r.json()['error']), (400, 'has_service_events'))
        self.assertTrue(ServicePeriod.objects.filter(id=self.period.id).exists())
        # The page refuses it too, instead of a ProtectedError 500.
        page = self.web.post(reverse('manage_service_periods'), {'action': 'delete', 'period_id': self.period.id})
        self.assertEqual(page.status_code, 302)
        self.assertTrue(ServicePeriod.objects.filter(id=self.period.id).exists())

        r = self.call('post', f'officer/service-hours/periods/{self.old.id}/delete/')
        self.assertEqual((r.status_code, r.json()['message']), (200, 'Deleted period: Spring'))
        self.assertFalse(ServicePeriod.objects.filter(id=self.old.id).exists())


class ExpectationTests(_Base):

    def test_data_matches_the_page(self):
        ServiceMemberExpectation.objects.create(period=self.period, member=self.amy, expected_hours=Decimal('4'),
                                                reason='Injury', created_by=self.vpp)
        make_user('b5-adv', member_type='Advisor')
        d = self.call('get', f'officer/service-hours/periods/{self.period.id}/expectations/').json()
        page = self.web.get(reverse('manage_member_expectations', args=[self.period.id]))
        self.assertEqual([x['id'] for x in d['expectations']], [x.id for x in page.context['expectations']])
        self.assertEqual([m['user_id'] for m in d['available_members']],
                         [m.user_id for m in page.context['available_members']])
        x = d['expectations'][0]
        self.assertEqual((x['member']['name'], x['expected_hours'], x['difference'], x['reason'], x['created_by']),
                         ('Amy Able', 4.0, -6.0, 'Injury', 'Vic Vpp'))
        ids = [m['user_id'] for m in d['available_members']]
        self.assertNotIn('b5-amy', ids)
        self.assertNotIn('b5-adv', ids)
        self.assertIn('b5-bob', ids)

    def test_add_validate_and_remove(self):
        url = f'officer/service-hours/periods/{self.period.id}/expectations/'
        r = self.call('post', url, {'member': 'b5-bob', 'expected_hours': '12.5', 'reason': ' Extra '})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['message'], 'Set 12.5 hours for Bob Baker.')
        x = ServiceMemberExpectation.objects.get(period=self.period, member=self.bob)
        self.assertEqual((x.expected_hours, x.reason, x.created_by), (Decimal('12.5'), 'Extra', self.vpp))

        for body in ({'member': 'b5-bob', 'expected_hours': '3'},      # already has one
                     {'member': 'b5-amy', 'expected_hours': 'lots'},
                     {'member': 'b5-amy', 'expected_hours': '-1'},
                     {'member': 'b5-amy', 'expected_hours': '1000'},
                     {'member': '', 'expected_hours': '2'}):
            r = self.call('post', url, body)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'), body)
        r = self.call('post', url, {'member': 'nobody', 'expected_hours': '2'})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(ServiceMemberExpectation.objects.count(), 1)

        # The page no longer 500s on the same junk.
        page = self.web.post(reverse('manage_member_expectations', args=[self.period.id]),
                             {'action': 'add', 'member': 'b5-amy', 'expected_hours': 'lots'})
        self.assertEqual(page.status_code, 302)

        r = self.call('delete', f'{url}{x.id}/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('They will use the default (10.00 hours).', r.json()['message'])
        self.assertFalse(ServiceMemberExpectation.objects.exists())
        other = ServiceMemberExpectation.objects.create(period=self.old, member=self.amy, expected_hours=1)
        self.assertEqual(self.call('delete', f'{url}{other.id}/').status_code, 404)


class EventTests(_Base):

    def form(self, **extra):
        return {'title': 'Food drive', 'date_time': _local(5, 21).isoformat(), 'location': 'Gym',
                'description': 'Sort cans', 'period': self.period.id, 'hours_awarded': 2.5,
                'reminder_1_enabled': True, 'reminder_1_hours_before': 12,
                'reminder_2_enabled': False, 'email_reminder_enabled': False, **extra}

    def test_create_matches_the_page(self):
        r = self.call('post', 'officer/service-hours/events/', self.form())
        self.assertEqual(r.status_code, 201, r.content)
        se = ServiceEvent.objects.get()
        e = se.event
        self.assertEqual((e.title, e.location, e.created_by, e.requires_attendance, e.allow_excuses,
                          e.reminder_1_enabled, e.reminder_1_hours_before, e.reminder_2_enabled),
                         ('Food drive', 'Gym', self.vpp, True, False, True, 12, False))
        self.assertEqual((se.period, se.hours_awarded, se.created_by), (self.period, Decimal('2.5'), self.vpp))
        self.assertEqual(e.date_time, _local(5, 21))
        self.assertTrue(ActivityLog.objects.filter(
            object_type='ServiceEvent', description__contains='created service event "Food drive"').exists())
        self.assertEqual(r.json()['service_event']['hours_awarded'], 2.5)

        # A naive time is the chapter's wall clock, as the page's datetime-local is.
        naive = _local(6, 19).replace(tzinfo=None).isoformat()
        r = self.call('post', 'officer/service-hours/events/', self.form(title='Naive', date_time=naive))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Event.objects.get(title='Naive').date_time, _local(6, 19))

    def test_create_validation(self):
        cases = [
            ({'title': ''}, 'Title is required.'),
            ({'date_time': ''}, 'Date and time are required.'),
            ({'date_time': 'soon'}, 'Invalid date/time format.'),
            ({'period': ''}, 'Service period is required.'),
            ({'period': 99999}, 'Invalid service period.'),
            ({'hours_awarded': ''}, 'Hours awarded is required.'),
            ({'hours_awarded': 0}, 'Hours awarded must be greater than 0.'),
            ({'hours_awarded': 30}, 'Hours awarded must be 24 or fewer.'),
            ({'hours_awarded': 'two'}, 'Hours awarded must be a valid number.'),
            ({'email_reminder_enabled': True}, 'Email subject is required when email reminder is enabled.'),
        ]
        for extra, message in cases:
            r = self.call('post', 'officer/service-hours/events/', self.form(**extra))
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'), extra)
            self.assertIn(message, r.json()['detail'], extra)
        self.assertFalse(ServiceEvent.objects.exists())
        self.assertFalse(Event.objects.exists())

    def test_list_matches_the_page(self):
        soon = self.make_event(days=1, title='Soon')
        later = self.make_event(days=9, title='Later')
        past = self.make_event(days=-3, title='Past')
        d = self.call('get', 'officer/service-hours/events/').json()
        page = self.web.get(reverse('service_events_list'))
        self.assertEqual([x['id'] for x in d['upcoming']], [x.id for x in page.context['upcoming']])
        self.assertEqual([x['id'] for x in d['upcoming']], [soon.id, later.id])
        self.assertEqual([x['id'] for x in d['past']], [past.id])
        self.assertEqual([p['id'] for p in d['periods']], [self.period.id])   # active ones only

    def test_detail_matches_the_page(self):
        se = self.make_event(days=-1)
        Attendance.objects.create(event=se.event, user=self.amy, attendance_type='event', status='present')
        Attendance.objects.create(event=se.event, user=self.bob, attendance_type='event', status='excused')
        d = self.call('get', f'officer/service-hours/events/{se.id}/').json()
        page = self.web.get(reverse('service_event_detail', args=[se.id]))
        self.assertEqual(d['counts'], {'present': page.context['present_count'], 'absent': page.context['absent_count'],
                                       'excused': page.context['excused_count'],
                                       'unmarked': page.context['unmarked_count']})
        self.assertEqual(d['counts']['present'], 1)
        self.assertEqual(d['can_finalize'], page.context['can_finalize'])
        self.assertTrue(d['can_finalize'])
        self.assertEqual([m['user_id'] for m in d['members']], [m['user'].user_id for m in page.context['member_data']])
        amy = next(m for m in d['members'] if m['user_id'] == 'b5-amy')
        self.assertEqual(amy['status'], 'present')

    def test_edit_rearms_reminders_on_a_new_date(self):
        se = self.make_event(email_reminder_enabled='on', email_reminder_subject='Hi', email_reminder_body='Come')
        e = se.event
        e.reminder_1_sent_at = timezone.now()
        e.save(update_fields=['reminder_1_sent_at'])
        se.email_reminder_sent_at = timezone.now()
        se.save(update_fields=['email_reminder_sent_at'])
        body = self.form(title='Moved', date_time=_local(7, 10).isoformat(), hours_awarded=3,
                         email_reminder_enabled=True, email_reminder_subject='Hi', email_reminder_body='Come')
        r = self.call('post', f'officer/service-hours/events/{se.id}/', body)
        self.assertEqual(r.status_code, 200, r.content)
        se.refresh_from_db()
        e.refresh_from_db()
        self.assertEqual((e.title, e.date_time, se.hours_awarded), ('Moved', _local(7, 10), Decimal('3')))
        self.assertIsNone(e.reminder_1_sent_at)
        self.assertIsNone(se.email_reminder_sent_at)
        self.assertEqual(r.json()['message'], 'Service event "Moved" updated.')

        # Blank date and period keep the event's, as on the page.
        r = self.call('post', f'officer/service-hours/events/{se.id}/', {**body, 'date_time': '', 'period': ''})
        self.assertEqual(r.status_code, 200, r.content)
        se.refresh_from_db()
        self.assertEqual((se.event.date_time, se.period), (_local(7, 10), self.period))

    def test_finalized_events_are_locked(self):
        se = self.make_event(days=-1)
        se.hours_applied = True
        se.save(update_fields=['hours_applied'])
        for method in ('post', 'delete'):
            r = self.call(method, f'officer/service-hours/events/{se.id}/', self.form() if method == 'post' else None)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'already_finalized'))
        self.assertTrue(ServiceEvent.objects.filter(id=se.id).exists())
        d = self.call('get', f'officer/service-hours/events/{se.id}/').json()
        self.assertEqual((d['can_edit'], d['can_finalize']), (False, False))

    def test_delete(self):
        se = self.make_event()
        event_id = se.event_id
        r = self.call('delete', f'officer/service-hours/events/{se.id}/')
        self.assertEqual((r.status_code, r.json()['message']), (200, 'Service event "Park cleanup" deleted.'))
        self.assertFalse(Event.objects.filter(id=event_id).exists())
        self.assertFalse(ServiceEvent.objects.exists())


class AttendanceTests(_Base):

    def setUp(self):
        super().setUp()
        # 9 PM Central is the next day in UTC: the credited date must be the chapter's.
        self.se = self.make_event(days=-1, hour=21, hours='2')
        self.url = f'officer/service-hours/events/{self.se.id}/attendance/'

    def test_roster_matches_the_page(self):
        Attendance.objects.create(event=self.se.event, user=self.amy, attendance_type='event', status='present')
        d = self.call('get', self.url).json()
        page = self.web.get(reverse('service_event_attendance', args=[self.se.id]))
        self.assertEqual([(m['user_id'], m['present']) for m in d['members']],
                         [(m['user'].user_id, m['present']) for m in page.context['member_data']])
        self.assertEqual((d['present_count'], d['read_only']), (page.context['present_count'], False))

    def test_save_then_finalize_credits_hours(self):
        r = self.call('post', self.url, {'action': 'save', 'present': ['b5-amy', 'b5-bob'],
                                         'hours_override': {'b5-bob': '1.5', 'b5-amy': 'junk'}})
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        self.assertEqual(d['present_count'], 2)
        self.assertEqual(d['message'], 'Attendance saved — 2 member(s) marked present.')
        bob = next(m for m in d['members'] if m['user_id'] == 'b5-bob')
        self.assertEqual(bob['hours_override'], 1.5)
        self.se.refresh_from_db()
        self.assertEqual(self.se.member_hours_override, {'b5-bob': '1.5'})
        self.assertTrue(ActivityLog.objects.filter(action_type='attendance_taken',
                                                   description__contains='(2 present)').exists())

        # Unchecking removes the present mark.
        self.call('post', self.url, {'action': 'save', 'present': ['b5-amy', 'b5-bob', 'b5-vpp'],
                                     'hours_override': {'b5-bob': 1.5}})
        self.call('post', self.url, {'action': 'save', 'present': ['b5-amy', 'b5-bob'],
                                     'hours_override': {'b5-bob': 1.5}})
        self.assertFalse(Attendance.objects.filter(event=self.se.event, user=self.vpp).exists())

        r = self.call('post', self.url, {'action': 'finalize', 'present': ['b5-amy', 'b5-bob'],
                                         'hours_override': {'b5-bob': 1.5}})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['count'], r.json()['read_only']), (2, True))
        self.assertEqual(r.json()['message'], 'Done! 2 members awarded 2.00 hrs toward Fall.')
        self.se.refresh_from_db()
        self.se.event.refresh_from_db()
        self.assertTrue(self.se.hours_applied)
        self.assertEqual((self.se.event.attendance_finalized, self.se.event.finalized_by), (True, self.vpp))
        subs = {s.submitted_by_id: s for s in ServiceHoursSubmission.objects.filter(period=self.period)}
        self.assertEqual(subs['b5-amy'].hours, Decimal('2'))
        self.assertEqual(subs['b5-bob'].hours, Decimal('1.5'))
        self.assertEqual({s.status for s in subs.values()}, {'approved'})
        self.assertEqual({s.reviewed_by for s in subs.values()}, {self.vpp})
        self.assertEqual(subs['b5-amy'].service_date, timezone.localdate() - timedelta(days=1))
        self.assertTrue(ActivityLog.objects.filter(
            description__contains='finalized service event "Park cleanup" — 2 member(s)').exists())

        # Once applied: no second credit, from the app or the page.
        r = self.call('post', self.url, {'action': 'save', 'present': []})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'already_finalized'))
        r = self.call('post', f'officer/service-hours/events/{self.se.id}/finalize/')
        self.assertEqual((r.status_code, r.json()['error']), (400, 'already_finalized'))
        self.web.post(reverse('finalize_service_event', args=[self.se.id]))
        self.assertEqual(ServiceHoursSubmission.objects.count(), 2)

    def test_finalize_needs_someone_present(self):
        Attendance.objects.create(event=self.se.event, user=self.amy, attendance_type='event', status='present')
        r = self.call('post', self.url, {'action': 'finalize', 'present': []})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'none_present'))
        self.se.refresh_from_db()
        self.assertFalse(self.se.hours_applied)
        # Saved first, as the page does: Amy's mark is gone.
        self.assertFalse(Attendance.objects.filter(event=self.se.event, status='present').exists())
        self.assertFalse(ServiceHoursSubmission.objects.exists())

    def test_bad_input(self):
        for body in ({'action': 'nope'}, {'action': 'save', 'present': 'b5-amy'},
                     {'action': 'save', 'present': [], 'hours_override': ['1']}):
            r = self.call('post', self.url, body)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'), body)

    def test_finalize_from_the_detail_screen(self):
        Attendance.objects.create(event=self.se.event, user=self.amy, attendance_type='event', status='present')
        r = self.call('post', f'officer/service-hours/events/{self.se.id}/finalize/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['count'], r.json()['can_finalize'], r.json()['can_edit']), (1, False, False))
        s = ServiceHoursSubmission.objects.get()
        self.assertEqual((s.submitted_by, s.status, s.hours), (self.amy, 'approved', Decimal('2')))

    def test_web_pages_still_render(self):
        for name in ('service_events_list', 'create_service_event', 'manage_service_periods'):
            self.assertEqual(self.web.get(reverse(name)).status_code, 200, name)
        for name in ('service_event_detail', 'service_event_attendance', 'edit_service_event'):
            self.assertEqual(self.web.get(reverse(name, args=[self.se.id])).status_code, 200, name)
        page = self.web.post(reverse('service_event_attendance', args=[self.se.id]),
                             {'action': 'finalize', 'present': ['b5-amy']})
        self.assertEqual(page.status_code, 302)
        self.assertEqual(ServiceHoursSubmission.objects.get().submitted_by, self.amy)


class MemberSubmissionTests(_Base):

    def setUp(self):
        super().setUp()
        self.s = ServiceHoursSubmission.objects.create(
            period=self.period, submitted_by=self.amy, hours=Decimal('3'), service_date=timezone.localdate(),
            organization='Shelter', description='Served dinner', status='rejected', reviewed_by=self.vpp,
            reviewed_at=timezone.now(), reviewer_notes='Need a receipt',
            attachment=SimpleUploadedFile('r.pdf', b'%PDF-1.4', content_type='application/pdf'))
        ServiceActivity.objects.create(submission=self.s, user=self.amy, action='created', details='Submitted')
        ServiceActivity.objects.create(submission=self.s, user=self.vpp, action='rejected', details='Need a receipt')
        field = ServiceFormField.objects.create(field_name='kind', label='Kind', field_type='multiselect',
                                                options=['Food', 'Kids'], display_order=1)
        ServiceFieldResponse.objects.create(submission=self.s, field=field, json_value=['Food', 'Kids'])

    def test_detail_matches_the_page(self):
        r = self.call('get', f'service-hours/submissions/{self.s.id}/', access=self.amy_access)
        self.assertEqual(r.status_code, 200, r.content)
        d = r.json()
        web_client = Client()
        web_client.force_login(self.amy)
        page = web_client.get(reverse('user_view_service_submission', args=[self.s.id]))
        self.assertEqual(d['can_edit'], page.context['can_edit'])
        self.assertEqual([a['action'] for a in d['activity']], [a.action for a in page.context['activity_log']])
        self.assertEqual(d['activity'][0]['user'], 'Vic Vpp')
        self.assertEqual((d['status'], d['status_label'], d['reviewer_notes'], d['reviewed_by']),
                         ('rejected', 'Rejected', 'Need a receipt', 'Vic Vpp'))
        self.assertEqual(d['attachment'], reverse('service_hours_attachment', args=[self.s.id]))
        self.assertEqual(d['answers'], [{'label': 'Kind', 'field_type': 'multiselect', 'value': 'Food, Kids',
                                         'file': None}])
        self.assertEqual(d['web_path'], reverse('user_view_service_submission', args=[self.s.id]))
        # The keys the edit form already reads are unchanged.
        self.assertEqual(d['responses'], {'custom_kind': ['Food', 'Kids']})
        for key in ('id', 'period_id', 'period', 'hours', 'service_date', 'organization', 'description',
                    'attachment_name', 'can_edit'):
            self.assertIn(key, d)

    def test_only_the_member_sees_it(self):
        r = self.call('get', f'service-hours/submissions/{self.s.id}/')
        self.assertEqual(r.status_code, 404)

    def test_pledge_restrictions_follow_the_pages(self):
        pledge = make_user('b5-pl2', member_type='Pledge', member_status='Pledge')
        _, access, _ = make_device(pledge)
        mine = ServiceHoursSubmission.objects.create(
            period=self.period, submitted_by=pledge, hours=Decimal('1'), service_date=timezone.localdate(),
            organization='Org', description='Desc')
        restrict('edit_service_submission')
        cache.clear()
        self.assertEqual(self.call('get', f'service-hours/submissions/{mine.id}/', access=access).status_code, 200)
        r = self.call('post', f'service-hours/submissions/{mine.id}/', {'period': self.period.id}, access=access)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))
        restrict('user_view_service_submission')
        cache.clear()
        r = self.call('get', f'service-hours/submissions/{mine.id}/', access=access)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))


class CalendarLinkTests(_Base):

    def test_vpp_gets_the_service_event(self):
        se = self.make_event()
        d = self.call('get', f'events/{se.event_id}/').json()['event']
        self.assertEqual(d['service_event'], {'id': se.id, 'hours_awarded': 2.0, 'hours_applied': False})
        self.assertTrue(d['is_service_event'])
        d = self.call('get', f'events/{se.event_id}/', access=self.amy_access).json()['event']
        self.assertIsNone(d['service_event'])
        self.assertTrue(d['is_service_event'])
