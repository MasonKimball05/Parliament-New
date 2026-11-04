"""
Site admin log pages in the app (v4.4.4): src/mobile/views_site_admin_logs.py.

Each endpoint runs the website page's own query helper (moved out of the web
view, which now calls it too), behind the Site admin gate (allowlist → 404,
unlock → 403 admin_locked), and sends only what the web page shows.

    python manage.py test src.tests.mobile.test_site_admin_logs
"""
import os
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import (
    ActivityLog, Announcement, AnnouncementEmailLog, AnnouncementEmailRecipient, Event, EventReminderLog,
    EventReminderRecipient, LoginHistory, SecurityNotificationLog,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.view import admin_v2

API = '/api/mobile/v1/'
ADMIN = 'sal-admin'
SECRET = 'the-admin-secret-0452'
PW = 'pw-' + ADMIN

ROUTES = [
    'admin/email-logs/', 'admin/email-logs/{email}/', 'admin/event-reminder-logs/',
    'admin/event-reminder-logs/{reminder}/', 'admin/login-history/', 'admin/admin-activity/',
    'admin/security-notifications/',
]


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.admin = make_user(ADMIN, is_admin=True, member_type='Officer', email='sal@parliament.test')
        patcher = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {ADMIN})
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {'ADMIN_V2_SECRET_KEY': SECRET})
        env.start()
        self.addCleanup(env.stop)
        self.device, self.token, _ = make_device(self.admin)
        self.api = Client()
        self.member = make_user('sal-member', name='Pat Member', email='pat@parliament.test')

    def get(self, path, token=None):
        return self.api.get(API + path, **bearer(token or self.token))

    def unlock(self):
        r = self.api.post(API + 'admin/unlock/', {'password': PW, 'secret_key': SECRET},
                          content_type='application/json', **bearer(self.token))
        self.assertEqual(r.status_code, 200, r.content)

    def web(self):
        """A website client with an admin-v2 session, to check the web pages still render."""
        c = Client()
        c.force_login(self.admin)
        s = c.session
        s['admin_v2_authenticated'] = True
        s['admin_v2_auth_time'] = timezone.now().isoformat()
        s.save()
        return c

    # fixtures

    def email_log(self, **kw):
        a = Announcement.objects.create(title='Formal', content='c', posted_by=self.admin)
        log = AnnouncementEmailLog.objects.create(
            announcement=a, initiated_by=self.admin, visible_to_raw=['Member'], expanded_member_types=['Member'],
            total_active_users=10, users_matching_visibility=8, users_with_valid_email=7, emails_sent=6,
            emails_failed=1, status='partial', console_log='step 1\nstep 2', **kw)
        AnnouncementEmailRecipient.objects.create(
            email_log=log, user=self.member, user_name='Pat Member', user_email='pat@parliament.test',
            user_member_type='Member', user_member_status='Active', status='sent')
        AnnouncementEmailRecipient.objects.create(
            email_log=log, user=None, user_name='Fay Failed', user_email='fay@parliament.test',
            user_member_type='Member', user_member_status='Active', status='failed',
            error_message='one two three four five six seven eight nine ten eleven twelve')
        return log

    def event(self, **kw):
        defaults = dict(title='Chapter Meeting', description='Weekly', location='Room 1',
                        date_time=timezone.now() + timedelta(days=2), is_active=True, created_by=self.admin,
                        reminder_1_enabled=True, reminder_1_hours_before=24, reminder_1_email_enabled=True,
                        reminder_2_enabled=False)
        defaults.update(kw)
        return Event.objects.create(**defaults)

    def reminder_log(self):
        log = EventReminderLog.objects.create(
            event=self.event(), reminder_slot=1, users_eligible=3, users_subscribed=2, notifications_dispatched=2,
            users_with_email=3, emails_dispatched=2)
        EventReminderRecipient.objects.create(reminder_log=log, user=self.member, user_name='Pat Member',
                                              user_member_type='Member', status='dispatched',
                                              email_status='dispatched', viewed_at=timezone.now())
        EventReminderRecipient.objects.create(reminder_log=log, user=None, user_name='Nia NoPush',
                                              user_member_type='Pledge', status='skipped_no_subscription',
                                              email_status='dispatched')
        return log


class GateTests(_Base):

    def paths(self):
        return [p.format(email=1, reminder=1) for p in ROUTES]

    def test_404_for_a_member_not_on_the_allowlist(self):
        other = make_user('sal-other', is_admin=True)
        _, token, _ = make_device(other)
        for path in self.paths():
            self.assertEqual(self.get(path, token=token).status_code, 404, path)

    def test_locked_phone_gets_admin_locked(self):
        for path in self.paths():
            r = self.get(path)
            self.assertEqual(r.status_code, 403, path)
            self.assertEqual(r.json().get('error'), 'admin_locked', path)

    def test_missing_rows_are_404_once_unlocked(self):
        self.unlock()
        self.assertEqual(self.get('admin/email-logs/999/').status_code, 404)
        self.assertEqual(self.get('admin/event-reminder-logs/999/').status_code, 404)

    def test_security_notifications_is_admin_only_as_on_the_website(self):
        self.admin.is_admin = False
        self.admin.save(update_fields=['is_admin'])
        self.unlock()
        r = self.get('admin/security-notifications/')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json().get('error'), 'admin_required')
        self.assertEqual(self.web().get(reverse('admin_v2_security_notifications')).status_code, 403)
        # The other log pages don't check is_admin on the website, so they don't here.
        self.assertEqual(self.get('admin/login-history/').status_code, 200)


class EmailLogTests(_Base):

    def test_list_has_the_pages_rows_and_pending_announcements(self):
        log = self.email_log()
        Announcement.objects.create(title='Waiting', content='c', posted_by=self.admin, send_email_on_publish=True,
                                    visible_to=['Pledge'])
        self.unlock()
        body = self.get('admin/email-logs/').json()
        self.assertEqual([p['title'] for p in body['pending']], ['Waiting'])
        self.assertEqual(body['pending'][0]['visible_to'], ['Pledge'])
        self.assertTrue(body['pending'][0]['is_published'])
        self.assertEqual(len(body['logs']), 1)
        row = body['logs'][0]
        self.assertEqual(row['id'], log.pk)
        self.assertEqual(row['status_label'], 'Partial (Some Failed)')
        self.assertEqual((row['emails_sent'], row['emails_failed']), (6, 1))
        self.assertNotIn('console_log', row)

    def test_list_is_the_newest_50_as_on_the_website(self):
        for _ in range(52):
            self.email_log()
        self.unlock()
        self.assertEqual(len(self.get('admin/email-logs/').json()['logs']), 50)
        self.assertEqual(len(self.web().get(reverse('admin_v2_email_logs')).context['logs']), 50)

    def test_detail_groups_recipients_and_shortens_failure_errors(self):
        log = self.email_log()
        self.unlock()
        body = self.get(f'admin/email-logs/{log.pk}/').json()
        self.assertEqual(body['console_log'], 'step 1\nstep 2')
        self.assertEqual(body['users_with_valid_email'], 7)
        groups = {g['label']: g for g in body['groups']}
        self.assertEqual(set(groups), {'Sent', 'Failed'})
        self.assertNotIn('error', groups['Sent']['recipients'][0])
        failed = groups['Failed']['recipients'][0]
        self.assertEqual(failed['email'], 'fay@parliament.test')
        self.assertEqual(failed['error'], 'one two three four five six seven eight nine ten…')
        # The same grouping as the website's page.
        web = self.web().get(reverse('admin_v2_email_log_detail', args=[log.pk]))
        self.assertEqual(list(web.context['recipients_by_status']), [g['label'] for g in body['groups']])


class EventReminderLogTests(_Base):

    def test_list_and_pending_events(self):
        log = self.reminder_log()
        self.event(title='Later', reminder_2_enabled=True, reminder_2_hours_before=2)
        self.event(title='Past', date_time=timezone.now() - timedelta(days=1))
        self.unlock()
        body = self.get('admin/event-reminder-logs/').json()
        titles = [e['title'] for e in body['pending']]
        self.assertIn('Later', titles)
        self.assertNotIn('Past', titles)
        later = next(e for e in body['pending'] if e['title'] == 'Later')
        self.assertEqual([s['slot'] for s in later['reminders']], [1, 2])
        row = body['logs'][0]
        self.assertEqual((row['id'], row['emails_viewed'], row['slot']), (log.pk, 1, 1))
        self.assertEqual(self.web().get(reverse('admin_v2_event_reminder_logs')).status_code, 200)

    def test_detail_splits_recipients_as_the_website(self):
        log = self.reminder_log()
        self.unlock()
        body = self.get(f'admin/event-reminder-logs/{log.pk}/').json()
        self.assertTrue(body['email_enabled'])
        self.assertEqual([r['name'] for r in body['dispatched']], ['Pat Member'])
        self.assertEqual([r['name'] for r in body['skipped']], ['Nia NoPush'])
        self.assertEqual(body['skipped'][0]['status_label'], 'Skipped — No Push Subscription')
        self.assertEqual([r['name'] for r in body['emails_viewed']], ['Pat Member'])
        self.assertEqual([r['name'] for r in body['emails_not_viewed']], ['Nia NoPush'])
        web = self.web().get(reverse('admin_v2_event_reminder_log_detail', args=[log.pk]))
        self.assertEqual([r.user_name for r in web.context['skipped']], ['Nia NoPush'])


class LoginHistoryTests(_Base):

    def setUp(self):
        super().setUp()
        LoginHistory.objects.create(user=self.member, ip_address='203.0.113.5', city='Austin', region='TX',
                                    device_type='mobile', is_suspicious=True, user_agent='secret-agent')
        for _ in range(51):
            LoginHistory.objects.create(user=self.admin, ip_address='203.0.113.9', device_type='desktop')

    def test_paged_50_with_the_pages_columns_only(self):
        self.unlock()
        body = self.get('admin/login-history/').json()
        self.assertEqual((body['total'], len(body['logins']), body['has_next']), (52, 50, True))
        self.assertEqual(len(self.get('admin/login-history/?page=2').json()['logins']), 2)
        self.assertEqual(set(body['logins'][0]), {'id', 'member', 'timestamp', 'ip_address', 'city', 'region',
                                                  'device_type', 'is_suspicious'})

    def test_filters(self):
        self.unlock()
        body = self.get('admin/login-history/?suspicious=yes').json()
        self.assertEqual([(r['member'], r['ip_address'], r['city']) for r in body['logins']],
                         [('Pat Member', '203.0.113.5', 'Austin')])
        self.assertEqual(self.get('admin/login-history/?user=pat').json()['total'], 1)
        self.assertEqual(self.get('admin/login-history/?user=nobody-here').json()['total'], 0)
        web = self.web().get(reverse('admin_v2_login_history'), {'user': 'pat'})
        self.assertEqual(web.context['logins'].paginator.count, 1)


class AdminActivityTests(_Base):

    def setUp(self):
        super().setUp()
        ActivityLog.log_activity(action_type='user_role_changed', user=self.admin,
                                 description='Admin gave Historian to sal-member',
                                 object_type='ParliamentUser', object_id='sal-member', object_repr='Pat Member')
        ActivityLog.log_activity(action_type='login', user=self.admin, description='Admin logged in')
        ActivityLog.log_activity(action_type='user_edited', user=self.member, description='Member edited something')

    def test_admin_actions_only_with_filters_on_page_one(self):
        self.unlock()
        body = self.get('admin/admin-activity/').json()
        # (Unlocking writes its own admin_v2_access row.)
        entries = {e['description']: e for e in body['entries']}
        self.assertNotIn('Admin logged in', entries)
        self.assertNotIn('Member edited something', entries)
        role = entries['Admin gave Historian to sal-member']
        self.assertEqual(role['member'], {'user_id': 'sal-member', 'name': 'Pat Member'})
        self.assertIn(ADMIN, [a['user_id'] for a in body['actors']])
        self.assertIn('user_role_changed', [a['code'] for a in body['actions']])
        self.assertNotIn('login', [a['code'] for a in body['actions']])

    def test_filters(self):
        self.unlock()
        self.assertEqual(self.get('admin/admin-activity/?action=user_edited').json()['entries'], [])
        self.assertEqual(self.get('admin/admin-activity/?action=user_role_changed').json()['total'], 1)
        self.assertEqual(self.get('admin/admin-activity/?q=historian').json()['total'], 1)
        self.assertEqual(self.get(f'admin/admin-activity/?actor={self.member.user_id}').json()['total'], 0)
        self.assertEqual(self.web().get(reverse('admin_v2_admin_activity')).status_code, 200)


class SecurityNotificationTests(_Base):

    def setUp(self):
        super().setUp()
        SecurityNotificationLog.objects.create(event_type='BRUTE_FORCE', severity='high', details='5 tries',
                                               ip_address='198.51.100.2', user=self.member,
                                               email_sent=True, email_sent_to='sec@parliament.test')
        SecurityNotificationLog.objects.create(event_type='BACKUP_VERIFY_FAILED', severity='critical',
                                               details='x', email_error='SMTP down')

    def test_list_and_filters(self):
        self.unlock()
        body = self.get('admin/security-notifications/').json()
        self.assertEqual(body['total'], 2)
        self.assertEqual(body['event_types'], ['BACKUP_VERIFY_FAILED', 'BRUTE_FORCE'])
        brute = next(n for n in body['notifications'] if n['event_type'] == 'BRUTE_FORCE')
        self.assertEqual((brute['member'], brute['ip_address'], brute['email_sent_to']),
                         ('Pat Member', '198.51.100.2', 'sec@parliament.test'))
        self.assertEqual(self.get('admin/security-notifications/?severity=critical').json()['total'], 1)
        self.assertEqual(self.get('admin/security-notifications/?event_type=brute').json()['total'], 1)
        web = self.web().get(reverse('admin_v2_security_notifications'), {'severity': 'critical'})
        self.assertEqual(web.context['notifications'].paginator.count, 1)
