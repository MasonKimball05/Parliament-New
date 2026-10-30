"""
Mobile plan, Phase 3h — officer tools in the app.

Announcements and events go through the web's own forms, an edit from the
app must leave alone what the app doesn't show, and excuses and attendance
follow ``review_excuses`` / ``mark_event_attendance``.

    python manage.py test src.tests.mobile.test_officer
"""
import json
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src.models import Announcement, Attendance, AttendanceExcuse, Committee, Event
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.models import ParliamentUser
from src.tests.mobile.test_home_and_notifications import flag

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        for name in ('attendance_tracking', 'event_attendance', 'excuse_system'):
            flag(name, True)
        self.officer = make_user('off-1', member_type='Officer')
        _, self.access, _ = make_device(self.officer)
        self.member = make_user('mem-1')
        _, self.member_access, _ = make_device(self.member)
        self.api = Client()

    def call(self, method, path, body=None, access=None):
        fn = getattr(self.api, method)
        kw = bearer(access or self.access)
        if body is None:
            return fn(API + path, **kw)
        return fn(API + path, json.dumps(body), content_type='application/json', **kw)


class AccessTests(_Base):

    def test_members_are_refused(self):
        for method, path in (('get', 'officer/'), ('post', 'officer/announcements/'),
                             ('get', 'officer/excuses/'), ('post', 'officer/events/')):
            r = self.call(method, path, {} if method == 'post' else None, access=self.member_access)
            self.assertEqual(r.status_code, 403, path)

    def test_hub(self):
        r = self.call('get', 'officer/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['pending_excuses'], 0)


class AnnouncementTests(_Base):

    def test_create_publish_now_without_email(self):
        with patch('src.notifications.send_announcement_notification') as send:
            r = self.call('post', 'officer/announcements/', {'title': 'Dues', 'content': 'Due Friday.'})
        self.assertEqual(r.status_code, 201, r.content)
        a = Announcement.objects.get(title='Dues')
        self.assertEqual(a.posted_by, self.officer)
        self.assertTrue(a.is_active)
        self.assertIsNone(r.json()['emailed'])
        send.assert_not_called()

    def test_create_with_email_sends_or_waits_for_publish(self):
        with patch('src.notifications.send_announcement_notification', return_value=7) as send:
            r = self.call('post', 'officer/announcements/',
                          {'title': 'Now', 'content': 'x', 'send_email': True})
            self.assertEqual(r.json()['emailed'], 7)
            send.assert_called_once()
            later = (timezone.now() + timedelta(days=1)).isoformat()
            r = self.call('post', 'officer/announcements/',
                          {'title': 'Later', 'content': 'x', 'send_email': True, 'publish_at': later})
            self.assertEqual(r.json()['announcement']['status'], 'scheduled')
            self.assertEqual(send.call_count, 1)
        self.assertTrue(Announcement.objects.get(title='Later').send_email_on_publish)

    def test_recipient_count_matches_the_send(self):
        from src.models import AnnouncementEmailLog
        make_user('mail-p', member_type='Pledge', email='p@example.com')
        make_user('mail-m', member_type='Member', email='m@example.com')
        make_user('mail-c', member_type='Chair', email='c@example.com')
        make_user('mail-x', member_type='Member', email='')                  # no address
        make_user('mail-a', member_type='Member', email='a@example.com', member_status='Alumni')
        for visible_to in ([], ['Pledge'], ['Member']):
            q = '&'.join(f'visible_to={v}' for v in visible_to)
            count = self.call('get', f'officer/announcements/recipients/?{q}').json()['count']
            a = Announcement.objects.create(title='x', content='x', posted_by=self.officer,
                                            visible_to=visible_to or None)
            from src.notifications import send_announcement_notification
            with patch('django.core.mail.EmailMultiAlternatives.send', return_value=1):
                send_announcement_notification(a, initiated_by=self.officer)
            log = AnnouncementEmailLog.objects.filter(announcement=a).latest('id')
            self.assertEqual(count, log.users_with_valid_email, visible_to)
        self.assertEqual(self.call('get', 'officer/announcements/recipients/?visible_to=Pledge').json()['count'], 1)

    def test_invalid_is_explained(self):
        r = self.call('post', 'officer/announcements/', {'title': '', 'content': 'x'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'invalid')
        self.assertIn('required', r.json()['detail'])

    def test_edit_changes_only_what_was_sent_and_delete(self):
        a = Announcement.objects.create(title='Old', content='Body', posted_by=self.officer,
                                        visible_to=['Pledge'], is_active=True)
        r = self.call('patch', f'officer/announcements/{a.id}/', {'title': 'New'})
        self.assertEqual(r.status_code, 200, r.content)
        a.refresh_from_db()
        self.assertEqual((a.title, a.content, a.visible_to, a.is_active), ('New', 'Body', ['Pledge'], True))
        self.call('patch', f'officer/announcements/{a.id}/', {'is_active': False})
        a.refresh_from_db()
        self.assertFalse(a.is_active)
        rows = self.call('get', 'officer/announcements/').json()['announcements']
        self.assertEqual(rows[0]['status'], 'inactive')
        self.assertEqual(self.call('delete', f'officer/announcements/{a.id}/').status_code, 200)
        self.assertFalse(Announcement.objects.filter(pk=a.id).exists())


class EventTests(_Base):

    def test_create_single_event(self):
        when = (timezone.now() + timedelta(days=3)).replace(microsecond=0)
        r = self.call('post', 'officer/events/', {
            'title': 'Chapter', 'description': 'Weekly', 'date_time': when.isoformat(),
            'location': 'House', 'requires_attendance': True, 'allow_excuses': True})
        self.assertEqual(r.status_code, 201, r.content)
        e = Event.objects.get(title='Chapter')
        self.assertEqual((e.date_time, e.created_by, e.requires_attendance, e.is_recurring),
                         (when, self.officer, True, False))

    def test_edit_keeps_settings_the_app_does_not_show(self):
        e = Event.objects.create(title='Formal', description='d', created_by=self.officer,
                                 date_time=timezone.now() + timedelta(days=5),
                                 reminder_2_enabled=True, reminder_2_hours_before=3,
                                 reminder_2_email_enabled=True, visible_to=['Member'])
        r = self.call('patch', f'officer/events/{e.id}/', {'location': 'Ballroom'})
        self.assertEqual(r.status_code, 200, r.content)
        e.refresh_from_db()
        self.assertEqual(e.location, 'Ballroom')
        self.assertEqual((e.reminder_2_enabled, e.reminder_2_hours_before, e.reminder_2_email_enabled),
                         (True, 3, True))
        self.assertEqual(e.visible_to, ['Member'])

    def test_committee_by_code_and_bad_values(self):
        Committee.objects.create(name='Finance', code='FIN', is_active=True)
        e = Event.objects.create(title='Meet', description='d', created_by=self.officer,
                                 date_time=timezone.now() + timedelta(days=5))
        self.call('patch', f'officer/events/{e.id}/', {'committee': 'FIN'})
        e.refresh_from_db()
        self.assertEqual(e.committee.code, 'FIN')
        r = self.call('patch', f'officer/events/{e.id}/', {'reminder_1_hours_before': 500})
        self.assertEqual(r.status_code, 400)
        self.assertIn('168', r.json()['detail'])

    def test_delete_with_scope(self):
        start = timezone.now() + timedelta(days=1)
        root = Event.objects.create(title='Weekly', description='d', created_by=self.officer,
                                    date_time=start, is_recurring=True, recurrence_type='weekly')
        kids = [Event.objects.create(title='Weekly', description='d', created_by=self.officer,
                                     date_time=start + timedelta(weeks=i), parent_event=root)
                for i in (1, 2)]
        detail = self.call('get', f'officer/events/{kids[0].id}/').json()['event']
        self.assertEqual(detail['scope_counts']['following'], 2)
        r = self.call('delete', f'officer/events/{kids[0].id}/?scope=following')
        self.assertEqual(r.json()['deleted'], 2)
        self.assertTrue(Event.objects.filter(pk=root.pk).exists())

    def test_event_detail_flags(self):
        e = Event.objects.create(title='Roll', description='d', created_by=self.officer,
                                 date_time=timezone.now() + timedelta(days=1), requires_attendance=True)
        mine = self.call('get', f'events/{e.id}/').json()['event']
        self.assertTrue(mine['can_manage'] and mine['can_take_attendance'])
        theirs = self.call('get', f'events/{e.id}/', access=self.member_access).json()['event']
        self.assertFalse(theirs['can_manage'] or theirs['can_take_attendance'])


class ExcuseAndAttendanceTests(_Base):

    def setUp(self):
        super().setUp()
        self.event = Event.objects.create(title='Chapter', description='d', created_by=self.officer,
                                          date_time=timezone.now() - timedelta(hours=1),
                                          requires_attendance=True, allow_excuses=True)

    def test_review_excuse(self):
        x = AttendanceExcuse.objects.create(event=self.event, user=self.member, reason='Exam that night')
        body = self.call('get', 'officer/excuses/').json()
        self.assertEqual(body['counts']['pending'], 1)
        self.assertEqual(body['excuses'][0]['member']['user_id'], 'mem-1')
        with patch('src.notifications.notify_excuse_reviewed') as notify:
            r = self.call('post', f'officer/excuses/{x.id}/', {'action': 'approve', 'notes': 'OK'})
        self.assertEqual(r.json()['excuse']['status'], 'approved')
        notify.assert_called_once()
        self.assertEqual(Attendance.objects.get(event=self.event, user=self.member).status, 'excused')
        self.assertEqual(self.call('post', f'officer/excuses/{x.id}/', {'action': 'maybe'}).status_code, 400)

    def test_take_and_finalize_attendance(self):
        other = make_user('mem-2')
        excused = make_user('mem-3')
        AttendanceExcuse.objects.create(event=self.event, user=excused, reason='Sick', status='approved')
        r = self.call('post', f'officer/events/{self.event.id}/attendance/', {
            'statuses': {'mem-1': 'present', 'mem-3': 'absent', 'off-1': 'late'},
            'excused': {'mem-2': 'Family emergency'}})
        self.assertEqual(r.status_code, 200, r.content)
        rows = {m['user_id']: m for m in r.json()['members']}
        self.assertEqual(rows['mem-1']['status'], 'present')
        self.assertEqual(rows['off-1']['status'], 'late')
        self.assertEqual(rows['mem-2']['status'], 'excused')
        self.assertIn('Family emergency', rows['mem-2']['note'])
        self.assertEqual(rows['mem-3']['status'], 'excused')            # approved excuse wins
        self.assertTrue(rows['mem-3']['locked'])
        self.assertFalse(Attendance.objects.filter(event=self.event, user=excused, status='absent').exists())
        r = self.call('post', f'officer/events/{self.event.id}/attendance/', {'finalize': True})
        self.assertTrue(r.json()['event']['finalized'])
        r = self.call('post', f'officer/events/{self.event.id}/attendance/', {'statuses': {'mem-1': 'absent'}})
        self.assertEqual(r.json()['error'], 'finalized')
        self.assertEqual(Attendance.objects.get(event=self.event, user=self.member).status, 'present')
        self.assertIsNotNone(other)

    def test_roster_is_in_roll_call_order(self):
        ParliamentUser.objects.filter(pk='off-1').update(role_number='10', name='Zed Officer')
        ParliamentUser.objects.filter(pk='mem-1').update(role_number='2', name='Amy Member')
        make_user('mem-9', name='Bo Unnumbered')
        make_user('pl-1', name='Al Zulu', member_type='Pledge')
        make_user('pl-2', name='Cy Adams', member_type='Pledge')
        rows = self.call('get', f'officer/events/{self.event.id}/attendance/').json()['members']
        order = [r['user_id'] for r in rows]
        # Roll numbers as numbers (2 before 10), the unnumbered after, then pledges by last name.
        self.assertEqual(order[:2], ['mem-1', 'off-1'])
        self.assertEqual(order[-2:], ['pl-2', 'pl-1'])
        self.assertLess(order.index('mem-9'), order.index('pl-2'))
        first = rows[0]
        self.assertEqual((first['role_number'], first['is_pledge']), ('2', False))
        self.assertTrue(rows[-1]['is_pledge'])

    def test_attendance_needs_the_flags(self):
        flag('event_attendance', False)
        self.assertEqual(self.call('get', f'officer/events/{self.event.id}/attendance/').status_code, 403)


class MobileAppTests(_Base):
    """officer/mobile-app/: the officer portal's Mobile App page for the app."""

    def test_overview_and_places(self):
        from decimal import Decimal
        from src.models import CheckinPlace
        device, _, _ = make_device(self.member)
        device.push_token = 'ExponentPushToken[x]'
        device.save(update_fields=['push_token'])
        body = self.call('get', 'officer/mobile-app/').json()
        rows = {m['user_id']: m for m in body['notifications']['members']}
        self.assertEqual(rows['mem-1']['state'], 'on')
        self.assertEqual(rows['mem-1']['devices'][0]['platform'], 'iOS')
        self.assertIn('places', body['location'])
        self.assertTrue(body['versions']['versions'])

        place = CheckinPlace.objects.create(name='House', latitude=Decimal('1'), longitude=Decimal('2'))
        CheckinPlace.objects.create(name='Library', latitude=Decimal('1'), longitude=Decimal('2'))
        path = f'officer/mobile-app/places/{place.id}/'
        self.assertEqual(self.call('patch', path, {'name': 'Library'}).status_code, 400)
        self.assertEqual(self.call('patch', path, {'name': 'The House'}).json()['name'], 'The House')
        self.assertEqual(self.call('delete', path).status_code, 200)
        self.assertFalse(CheckinPlace.objects.filter(pk=place.pk).exists())

    def test_members_are_refused(self):
        self.assertEqual(self.call('get', 'officer/mobile-app/', access=self.member_access).status_code, 403)


class OfficerToolsTests(_Base):
    """More officer pages in the app (src/mobile/views_officer_tools.py)."""

    def test_attendance_events(self):
        past = Event.objects.create(title='Past', description='d', created_by=self.officer,
                                    date_time=timezone.now() - timedelta(days=1), requires_attendance=True)
        Event.objects.create(title='Soon', description='d', created_by=self.officer,
                             date_time=timezone.now() + timedelta(days=1), requires_attendance=True)
        body = self.call('get', 'officer/attendance-events/').json()
        self.assertEqual([e['title'] for e in body['upcoming']], ['Soon'])
        self.assertEqual(body['past'][0]['id'], past.id)
        self.assertIn('present', body['past'][0]['stats'])
        self.assertEqual(self.call('get', 'officer/attendance-events/', access=self.member_access).status_code, 403)

    def test_stats_and_members(self):
        stats = self.call('get', 'officer/stats/?days=30').json()
        self.assertEqual(stats['days'], 30)
        self.assertIn('attendance', stats)
        self.assertEqual(self.call('get', 'officer/stats/?days=7').json()['days'], 90)    # not an option
        members = {m['user_id']: m for m in self.call('get', 'officer/members/').json()['members']}
        self.assertIn('mem-1', members)
        self.assertIn('last_login', members['mem-1'])

    def test_contact_messages(self):
        from src.models import ContactSubmission
        a = ContactSubmission.objects.create(name='Pat', email='p@example.com', message='Hi')
        ContactSubmission.objects.create(name='Sam', email='s@example.com', message='Hello')
        body = self.call('get', 'officer/contact/').json()
        self.assertEqual(body['unread'], 2)
        self.assertTrue(self.call('post', f'officer/contact/{a.id}/read/', {}).json()['message']['is_read'])
        self.assertEqual(self.call('post', 'officer/contact/read-all/', {}).json()['marked'], 1)
        self.assertEqual(self.call('get', 'officer/contact/', access=self.member_access).status_code, 403)

    def test_reports_include_unpublished(self):
        from django.core.files.base import ContentFile
        from src.models import Committee, CommitteeDocument
        c = Committee.objects.create(name='Finance', code='FIN')
        d = CommitteeDocument(committee=c, title='Budget', uploaded_by=self.officer, document_type='report')
        d.document.save('budget.pdf', ContentFile(b'%PDF-1'), save=True)
        body = self.call('get', 'officer/reports/').json()
        row = body['documents'][0]
        self.assertEqual((row['title'], row['published']), ('Budget', False))
        r = self.call('get', row['file'])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(b''.join(r.streaming_content), b'%PDF-1')
        d.document.delete(save=False)


class AttendanceDashboardTests(_Base):

    def test_dashboard_and_member_history(self):
        e = Event.objects.create(title='Chapter', description='d', created_by=self.officer,
                                 date_time=timezone.now() - timedelta(days=2), requires_attendance=True)
        Attendance.objects.create(event=e, user=self.member, attendance_type='event', status='present')
        AttendanceExcuse.objects.create(event=e, user=self.officer, reason='Exam', status='approved')
        body = self.call('get', 'officer/attendance-dashboard/?range=30').json()
        self.assertEqual(body['range'], '30')
        self.assertEqual(body['chapter']['present'], 1)
        row = next(m for m in body['members'] if m['user_id'] == 'mem-1')
        self.assertEqual((row['present'], row['rate']), (1, 100.0))
        self.assertEqual(self.call('get', 'officer/attendance-dashboard/?range=bogus').json()['range'], '90')
        detail = self.call('get', 'officer/attendance-dashboard/off-1/').json()
        self.assertEqual(detail['history'][0]['status'], 'not_marked')
        self.assertEqual(detail['history'][0]['excuse']['status'], 'approved')
        self.assertEqual(self.call('get', 'officer/attendance-dashboard/', access=self.member_access).status_code, 403)

    def test_website_pages_still_render(self):
        from django.urls import reverse
        self.client.force_login(self.officer)
        self.assertEqual(self.client.get(reverse('attendance_dashboard') + '?range=0').status_code, 200)
        self.assertEqual(self.client.get(reverse('officer_member_attendance', args=['mem-1'])).status_code, 200)
