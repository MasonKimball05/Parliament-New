"""
Site admin in the app, parts four and five: src/mobile/views_site_admin_tools.py
and src/mobile/views_site_admin_security.py.

Chapter health, System tools, Scheduled jobs, Backups, the Permissions map, the
Push composer, browser push subscriptions, the Notifications dashboard and
logs, the Security dashboard, Quarantine, Honeypot logs, CSP violations and
the 2FA policy page, each through the website page's own helper, behind the
Site admin gate. No push is ever sent: `send_push_notification.delay` and
Celery's `send_task` are mocked. Plus the website views those helpers were
pulled out of.

    python manage.py test src.tests.mobile.test_site_admin_tools
"""
import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django_celery_beat.models import IntervalSchedule, PeriodicTask

from src.models import (
    ActivityLog, AdminActionLog, CSPViolation, HoneypotAccess, IPBlacklist, NotificationLog,
    PushSubscription, QuarantinedAccount, TwoFactorRequirement,
)
from src.models.mobile import MobileDevice
from src.models_feature_flags import FeatureFlag, SiteSetting
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.view import admin_v2

API = '/api/mobile/v1/'
ADMIN = 'sat-admin'
SECRET = 'the-admin-secret-0454'
PW = 'pw-' + ADMIN
DELAY = 'src.tasks.send_push_notification.delay'
SEND_TASK = 'celery.current_app.send_task'
INSPECT = 'celery.current_app.control.inspect'

READS = ('admin/chapter-health/', 'admin/system/', 'admin/celery/', 'admin/backups/', 'admin/permissions/',
         'admin/push/', 'admin/push-subscriptions/', 'admin/notifications/', 'admin/notification-logs/',
         'admin/notification-logs/1/', 'admin/security/', 'admin/security/quarantine/',
         'admin/security/honeypot/', 'admin/security/csp/', 'admin/two-factor/')
WRITES = ('admin/system/clear-cache/', 'admin/system/run-job/', 'admin/push/preview/', 'admin/push/send/',
          'admin/push-subscriptions/1/delete/', 'admin/push-subscriptions/clear/',
          'admin/security/quarantine/add/', 'admin/security/quarantine/1/release/',
          'admin/security/honeypot/1/delete/', 'admin/security/honeypot/clear/',
          'admin/security/honeypot/blacklist-all/', 'admin/security/csp/dismiss/', 'admin/two-factor/policy/')
ADMIN_ONLY = ('admin/security/', 'admin/security/quarantine/', 'admin/security/honeypot/', 'admin/security/csp/')
ADMIN_ONLY_WRITES = ('admin/security/quarantine/add/', 'admin/security/quarantine/1/release/',
                     'admin/security/honeypot/1/delete/', 'admin/security/honeypot/clear/',
                     'admin/security/honeypot/blacklist-all/', 'admin/security/csp/dismiss/')


def no_workers():
    inspector = mock.Mock()
    inspector.ping.return_value = {}
    return mock.patch(INSPECT, return_value=inspector)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):
    is_admin = True

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.admin = make_user(ADMIN, is_admin=self.is_admin, member_type='Officer', email='sat@parliament.test')
        patcher = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {ADMIN})
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {'ADMIN_V2_SECRET_KEY': SECRET})
        env.start()
        self.addCleanup(env.stop)
        self.device, self.token, _ = make_device(self.admin)
        self.api = Client()

    def get(self, path, token=None):
        return self.api.get(API + path, **bearer(token or self.token))

    def post(self, path, data=None, token=None):
        return self.api.post(API + path, data if data is not None else {}, content_type='application/json',
                             **bearer(token or self.token))

    def code(self, r):
        return r.json().get('error')

    def unlock(self):
        r = self.post('admin/unlock/', {'password': PW, 'secret_key': SECRET})
        self.assertEqual(r.status_code, 200, r.content)
        ActivityLog.objects.all().delete()


class GateTests(_Base):

    def test_not_on_the_allowlist_is_404(self):
        member = make_user('sat-member', is_admin=True)
        _, token, _ = make_device(member)
        for path in READS:
            self.assertEqual(self.get(path, token=token).status_code, 404, path)
        for path in WRITES:
            self.assertEqual(self.post(path, token=token).status_code, 404, path)

    def test_locked_is_403_and_nothing_happens(self):
        HoneypotAccess.objects.create(endpoint='wp-login.php', ip_address='198.51.100.7')
        for path in READS:
            r = self.get(path)
            self.assertEqual((r.status_code, self.code(r)), (403, 'admin_locked'), path)
        with mock.patch(DELAY) as delay:
            for path in WRITES:
                r = self.post(path, {'confirm': True})
                self.assertEqual((r.status_code, self.code(r)), (403, 'admin_locked'), path)
        delay.assert_not_called()
        self.assertEqual(HoneypotAccess.objects.count(), 1)

    def test_writes_refuse_get(self):
        self.unlock()
        for path in WRITES:
            self.assertEqual(self.get(path).status_code, 405, path)


class NotIsAdminTests(_Base):
    is_admin = False

    def test_security_pages_need_is_admin_as_on_the_website(self):
        self.unlock()
        for path in ADMIN_ONLY:
            r = self.get(path)
            self.assertEqual((r.status_code, self.code(r)), (403, 'admin_required'), path)
        for path in ADMIN_ONLY_WRITES:
            r = self.post(path, {'confirm': True})
            self.assertEqual((r.status_code, self.code(r)), (403, 'admin_required'), path)
        # The rest don't check is_admin on the website, so not here either.
        with no_workers():
            for path in ('admin/system/', 'admin/celery/', 'admin/two-factor/', 'admin/permissions/'):
                self.assertEqual(self.get(path).status_code, 200, path)


class _Unlocked(_Base):
    def setUp(self):
        super().setUp()
        self.unlock()


# ── Reports and tools ────────────────────────────────────────────────────────

class ReportTests(_Unlocked):

    def test_chapter_health(self):
        make_user('sat-quiet', last_login=timezone.now() - timedelta(days=45))
        r = self.get('admin/chapter-health/?days=30')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body['days'], body['day_options']), (30, [30, 60, 90]))
        self.assertIn('sat-quiet', [m['user_id'] for m in body['inactive']['members']])
        for key in ('attendance', 'app', 'pledges', 'service', 'computed_at'):
            self.assertIn(key, body)
        self.assertEqual(self.get('admin/chapter-health/?days=7').json()['days'], 30)

    def test_permissions_map(self):
        r = self.get('admin/permissions/')
        self.assertEqual(r.status_code, 200, r.content)
        sections = {s['key']: s for s in r.json()['sections']}
        allow = [e['member']['user_id'] for g in sections['allowlist']['groups'] for e in g['entries']]
        self.assertEqual(allow, [ADMIN])
        self.assertIn('stale', sections['allowlist']['groups'][0]['entries'][0]['flags'])
        r = self.get('admin/permissions/?q=nobody-matches')
        self.assertEqual(sum(s['n'] for s in r.json()['sections']), 0)

    def test_backups(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, 'parliament_1.dump'), 'wb') as f:
                f.write(b'x' * 10)
            with mock.patch.dict(os.environ, {'PARLIAMENT_BACKUP_DIR': d}):
                body = self.get('admin/backups/').json()
        self.assertEqual((body['file_count'], body['files'][0]['name'], body['files'][0]['size']),
                         (1, 'parliament_1.dump', 10))
        self.assertIsNone(body['error'])
        self.assertIsNone(body['last_ok'])

    def test_celery(self):
        every = IntervalSchedule.objects.create(every=1, period=IntervalSchedule.HOURS)
        PeriodicTask.objects.create(name='Prune', task='tasks.prune_expired_login_lockouts', interval=every)
        with no_workers():
            body = self.get('admin/celery/').json()
        self.assertFalse(body['workers_up'])
        task = next(t for t in body['tasks'] if t['name'] == 'Prune')
        self.assertEqual((task['schedule'], task['refusal']), ('Every 1 hours', None))


class SystemTests(_Unlocked):

    def setUp(self):
        super().setUp()
        every = IntervalSchedule.objects.create(every=1, period=IntervalSchedule.HOURS)
        self.job = PeriodicTask.objects.create(name='Prune things', task='tasks.prune_expired_login_lockouts',
                                               interval=every)
        self.purge = PeriodicTask.objects.create(name='Erase data', task='tasks.purge_deleted_accounts',
                                                 interval=every)
        patcher = mock.patch(SEND_TASK)
        self.send_task = patcher.start()
        self.send_task.return_value.id = 'celery-id-9'
        self.addCleanup(patcher.stop)

    def test_page(self):
        body = self.get('admin/system/').json()
        self.assertIn('settings', [s['key'] for s in body['scopes']])
        self.assertTrue(any(n['label'] == 'Clear everything' for n in body['not_offered']))
        jobs = {j['name']: j for j in body['jobs']}
        self.assertIsNone(jobs['Prune things']['refusal'])
        self.assertIn('Erases data permanently', jobs['Erase data']['refusal'])

    def test_clear_cache(self):
        r = self.post('admin/system/clear-cache/', {'scope': 'settings'})
        self.assertEqual((r.status_code, self.code(r)), (400, 'confirm_required'))
        r = self.post('admin/system/clear-cache/', {'scope': 'everything', 'confirm': True})
        self.assertEqual((r.status_code, self.code(r)), (400, 'invalid_input'))
        cache.set('site_setting:x', {'found': True, 'value': 1}, 60)
        SiteSetting.objects.create(key='x', display_name='X', value='1', default_value='1')
        cache.set('site_setting:x', {'found': True, 'value': 99}, 60)
        r = self.post('admin/system/clear-cache/', {'scope': 'settings', 'confirm': True})
        self.assertEqual(r.json()['level'], 'success', r.content)
        self.assertIsNone(cache.get('site_setting:x'))
        self.assertTrue(ActivityLog.objects.filter(action_type='settings_changed', object_type='Cache',
                                                   object_id='settings').exists())

    def test_run_job(self):
        r = self.post('admin/system/run-job/', {'task': self.job.pk})
        self.assertEqual((r.status_code, self.code(r)), (400, 'confirm_required'))
        self.send_task.assert_not_called()
        r = self.post('admin/system/run-job/', {'task': self.job.pk, 'confirm': True})
        self.assertEqual(r.json()['level'], 'success', r.content)
        self.send_task.assert_called_once()
        self.assertEqual(self.send_task.call_args.args[0], 'tasks.prune_expired_login_lockouts')
        self.assertTrue(ActivityLog.objects.filter(action_type='settings_changed', object_type='PeriodicTask').exists())
        # Once a minute.
        r = self.post('admin/system/run-job/', {'task': self.job.pk, 'confirm': True})
        self.assertEqual(r.json()['level'], 'warning')
        self.assertEqual(self.send_task.call_count, 1)
        r = self.post('admin/system/run-job/', {'task': self.purge.pk, 'confirm': True})
        self.assertEqual((r.status_code, self.code(r)), (400, 'refused'))
        self.assertEqual(self.send_task.call_count, 1)
        self.assertEqual(self.post('admin/system/run-job/', {'task': 99999, 'confirm': True}).status_code, 404)
        r = self.post('admin/system/run-job/', {'task': 'x', 'confirm': True})
        self.assertEqual(self.code(r), 'invalid_input')


# ── Push ─────────────────────────────────────────────────────────────────────

class PushTests(_Unlocked):

    def setUp(self):
        super().setUp()
        self.amy = make_user('sat-amy', name='Amy App')
        device, _a, _r = make_device(self.amy)
        MobileDevice.objects.filter(pk=device.pk).update(push_token='ExponentPushToken[sat-amy]')
        self.cal = make_user('sat-cal', name='Cal Nothing')

    def preview(self, **over):
        body = {'audience': 'everyone', 'title': 'Chapter tonight', 'body': 'Meet at 7.', 'link': ''}
        body.update(over)
        return self.post('admin/push/preview/', body)

    def test_page(self):
        body = self.get('admin/push/').json()
        self.assertEqual((body['rate_limit'], body['sends_left'], body['master_on']), (5, 5, True))
        self.assertIn({'value': 'committee', 'label': 'A committee'}, body['audiences'])

    def test_preview_checks_the_form_and_sends_nothing(self):
        with mock.patch(DELAY) as delay:
            r = self.preview(title='')
            self.assertEqual((r.status_code, self.code(r), r.json()['detail']),
                             (400, 'invalid_input', 'Give the push a title.'))
            r = self.preview(link='https://evil.example/')
            self.assertEqual(self.code(r), 'invalid_input')
            r = self.preview(audience='member', user_id='sat-amy')
            p = r.json()['preview']
            self.assertEqual((p['n'], p['names'], p['no_device']), (1, ['Amy App'], 0))
            p = self.preview().json()['preview']
            self.assertGreaterEqual(p['no_device'], 1)   # Cal and the admin have nothing to push to
        delay.assert_not_called()

    def test_send_with_the_token_once(self):
        token = self.preview().json()['preview']['token']
        with mock.patch(DELAY) as delay:
            r = self.post('admin/push/send/', {'token': token})
        self.assertEqual((r.json()['level'], r.json()['n']), ('success', 1), r.content)
        self.assertEqual(delay.call_args.kwargs['user_id'], self.amy.pk)
        self.assertEqual(delay.call_args.kwargs['title'], 'Chapter tonight')
        log = ActivityLog.objects.get(object_type='AdminPush')
        self.assertEqual(log.metadata['n'], 1)
        with mock.patch(DELAY) as delay:
            r = self.post('admin/push/send/', {'token': token})
        self.assertEqual((r.json()['level'], r.json()['message']), ('warning', 'That push was already sent.'))
        delay.assert_not_called()
        self.assertEqual(self.get('admin/push/').json()['sends_left'], 4)
        self.assertEqual(self.get('admin/push/').json()['recent'][0]['title'], 'Chapter tonight')

    def test_send_refusals(self):
        with mock.patch(DELAY) as delay:
            r = self.post('admin/push/send/', {'token': 'forged'})
            self.assertEqual((r.status_code, self.code(r)), (400, 'refused'))
            token = self.preview().json()['preview']['token']
            FeatureFlag.objects.update_or_create(name='push_notifications_enabled',
                                                 defaults={'display_name': 'Push', 'is_enabled': False})
            r = self.post('admin/push/send/', {'token': token})
            self.assertEqual((r.status_code, self.code(r)), (400, 'refused'))
            self.assertIn('switched off', r.json()['detail'])
        delay.assert_not_called()

    def test_another_admins_token_is_refused(self):
        token = self.preview().json()['preview']['token']
        other = make_user('sat-admin2', is_admin=True)
        _, other_token, _ = make_device(other)
        with mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {ADMIN, 'sat-admin2'}):
            self.api.post(API + 'admin/unlock/', {'password': 'pw-sat-admin2', 'secret_key': SECRET},
                          content_type='application/json', **bearer(other_token))
            with mock.patch(DELAY) as delay:
                r = self.post('admin/push/send/', {'token': token}, token=other_token)
        self.assertEqual((r.status_code, self.code(r)), (400, 'refused'), r.content)
        delay.assert_not_called()


class PushSubscriptionTests(_Unlocked):

    def test_list_delete_clear(self):
        amy = make_user('sat-amy', name='Amy')
        a = PushSubscription.objects.create(user=amy, endpoint='https://fcm.example.com/send/abc', p256dh='k',
                                            auth='a', user_agent='Firefox')
        PushSubscription.objects.create(user=amy, endpoint='https://push.example.org/x', p256dh='k', auth='a')
        body = self.get('admin/push-subscriptions/').json()
        self.assertEqual(body['total'], 2)
        self.assertEqual(body['members'][0]['member']['user_id'], 'sat-amy')
        self.assertIn('fcm.example.com', [s['service'] for s in body['members'][0]['subscriptions']])
        self.assertNotIn('endpoint', body['members'][0]['subscriptions'][0])
        r = self.post(f'admin/push-subscriptions/{a.pk}/delete/')
        self.assertEqual(r.json()['message'], f'Deleted push subscription for {amy}.')
        self.assertTrue(ActivityLog.objects.filter(action_type='push_subscription_deleted').exists())
        r = self.post('admin/push-subscriptions/clear/')
        self.assertEqual((r.status_code, self.code(r)), (400, 'confirm_required'))
        self.assertEqual(PushSubscription.objects.count(), 1)
        r = self.post('admin/push-subscriptions/clear/', {'confirm': True})
        self.assertEqual(r.json()['count'], 1)
        self.assertFalse(PushSubscription.objects.exists())
        self.assertTrue(ActivityLog.objects.filter(action_type='push_subscriptions_cleared').exists())


class NotificationLogTests(_Unlocked):

    def test_dashboard_logs_and_detail(self):
        sent = NotificationLog.objects.create(notification_type='event_reminder', title='Sent one', message='Hi',
                                              status='sent', recipient_count=3, successful_count=3)
        NotificationLog.objects.create(notification_type='vote_reminder', title='Failed one', message='x',
                                       status='failed', error_message='SMTP down')
        body = self.get('admin/notifications/').json()
        self.assertEqual((body['stats']['logs_today'], body['stats']['failed_today']), (2, 1))
        self.assertEqual(len(body['recent']), 2)
        body = self.get('admin/notification-logs/?status=failed').json()
        self.assertEqual([x['title'] for x in body['logs']], ['Failed one'])
        self.assertEqual(body['stats']['total'], 2)
        body = self.get('admin/notification-logs/?type=event_reminder&from=2000-01-01').json()
        self.assertEqual([x['title'] for x in body['logs']], ['Sent one'])
        r = self.get('admin/notification-logs/?from=yesterday')
        self.assertEqual((r.status_code, self.code(r)), (400, 'invalid_input'))
        r = self.get('admin/notification-logs/?from=2026-13-40')
        self.assertEqual((r.status_code, self.code(r)), (400, 'invalid_input'))
        body = self.get(f'admin/notification-logs/{sent.pk}/').json()
        self.assertEqual((body['message'], body['recipient_count']), ('Hi', 3))
        self.assertEqual(self.get('admin/notification-logs/99999/').status_code, 404)


# ── Security ─────────────────────────────────────────────────────────────────

class SecurityTests(_Unlocked):

    def test_dashboard(self):
        HoneypotAccess.objects.create(endpoint='wp-admin', ip_address='198.51.100.1')
        CSPViolation.objects.create(violated_directive='script-src', blocked_uri='inline')
        body = self.get('admin/security/').json()
        self.assertEqual((body['counts']['honeypot_24h'], body['counts']['csp_violations']), (1, 1))
        self.assertEqual(body['honeypot'][0]['endpoint'], 'wp-admin')

    @mock.patch('src.security_notifications.alert_account_quarantined')
    def test_quarantine_add_and_release(self, alert):
        bob = make_user('sat-bob', name='Bob')
        r = self.post('admin/security/quarantine/add/', {'user_id': 'sat-bob', 'reason': 'Odd sign-ins',
                                                          'expires_at': '2000-01-01T00:00:00'})
        self.assertEqual((r.status_code, self.code(r)), (400, 'invalid_input'))
        r = self.post('admin/security/quarantine/add/', {'user_id': 'sat-bob', 'reason': 'Odd sign-ins'})
        self.assertEqual(r.json()['message'], 'Quarantined account: Bob', r.content)
        alert.assert_called_once()
        q = QuarantinedAccount.objects.get(user=bob)
        self.assertTrue(AdminActionLog.objects.filter(action='quarantine_set', target_user=bob).exists())
        r = self.post('admin/security/quarantine/add/', {'user_id': 'sat-bob'})
        self.assertEqual(self.code(r), 'invalid_input')
        body = self.get('admin/security/quarantine/').json()
        self.assertEqual([x['member']['user_id'] for x in body['active']], ['sat-bob'])
        r = self.post(f'admin/security/quarantine/{q.pk}/release/', {'notes': 'Checked with him'})
        self.assertEqual(r.json()['message'], 'Released quarantine for Bob')
        q.refresh_from_db()
        self.assertEqual(q.release_notes, 'Checked with him')
        self.assertTrue(AdminActionLog.objects.filter(action='quarantine_lifted', target_user=bob).exists())
        r = self.post(f'admin/security/quarantine/{q.pk}/release/')
        self.assertEqual(self.code(r), 'invalid_input')
        self.assertEqual(self.get('admin/security/quarantine/').json()['released'][0]['id'], q.pk)

    def test_honeypot(self):
        a = HoneypotAccess.objects.create(endpoint='wp-admin', ip_address='198.51.100.1')
        HoneypotAccess.objects.create(endpoint='/.env', ip_address='198.51.100.2')
        HoneypotAccess.objects.create(endpoint='/.env', ip_address='198.51.100.2')
        body = self.get('admin/security/honeypot/?endpoint=env').json()
        self.assertEqual((body['total_filtered'], body['total_all']), (2, 3))
        self.assertEqual(body['top_ips'][0], {'ip_address': '198.51.100.2', 'count': 2, 'blocked': False})
        self.assertEqual(self.post(f'admin/security/honeypot/{a.pk}/delete/').json(), {'ok': True})
        r = self.post('admin/security/honeypot/blacklist-all/')
        self.assertEqual(r.json()['created'], 1)
        self.assertTrue(IPBlacklist.objects.filter(ip_address='198.51.100.2', is_active=True).exists())
        self.assertTrue(cache.get('honeypot_ban_198.51.100.2'))
        self.assertTrue(ActivityLog.objects.filter(action_type='ip_blacklisted').exists())
        self.assertEqual(self.post('admin/security/honeypot/blacklist-all/').json()['created'], 0)
        self.assertTrue(self.get('admin/security/honeypot/').json()['logs'][0]['blocked'])
        r = self.post('admin/security/honeypot/clear/')
        self.assertEqual((r.status_code, self.code(r)), (400, 'confirm_required'))
        r = self.post('admin/security/honeypot/clear/', {'ip_address': '203.0.113.9'})
        self.assertEqual(r.json()['message'], 'No logs found for 203.0.113.9.')
        r = self.post('admin/security/honeypot/clear/', {'confirm': True})
        self.assertEqual(r.json()['deleted'], 2)
        self.assertFalse(HoneypotAccess.objects.exists())

    def test_csp(self):
        for _ in range(2):
            CSPViolation.objects.create(violated_directive='script-src', blocked_uri='inline')
        CSPViolation.objects.create(violated_directive='img-src', blocked_uri='https://x.example')
        body = self.get('admin/security/csp/').json()
        self.assertEqual(body['total_undismissed'], 3)
        self.assertEqual({(g['violated_directive'], g['count']) for g in body['groups']},
                         {('script-src', 2), ('img-src', 1)})
        r = self.post('admin/security/csp/dismiss/', {'violated_directive': 'script-src', 'blocked_uri': 'inline'})
        self.assertEqual(r.json(), {'ok': True, 'dismissed': 2})
        self.assertEqual(CSPViolation.objects.filter(dismissed=True, dismissed_by=self.admin).count(), 2)
        self.assertEqual(len(self.get('admin/security/csp/').json()['groups']), 1)
        self.assertEqual(len(self.get('admin/security/csp/?show_dismissed=1').json()['groups']), 2)
        r = self.post('admin/security/csp/dismiss/', {})
        self.assertEqual(self.code(r), 'invalid_input')


class TwoFactorTests(_Unlocked):

    def test_page_filter_and_policy(self):
        amy = make_user('sat-amy', name='Amy')
        TwoFactorRequirement.objects.create(user=amy, requirement='required', set_by=self.admin)
        body = self.get('admin/two-factor/').json()
        self.assertEqual(body['policy'], 'none')
        self.assertEqual(len(body['policy_options']), 5)
        self.assertEqual(body['stats']['required'], 1)
        rows = {r['member']['user_id']: r for r in body['members']}
        self.assertEqual((rows['sat-amy']['requirement'], rows['sat-amy']['requires_2fa']), ('required', True))
        body = self.get('admin/two-factor/?filter=needs_setup').json()
        self.assertEqual([r['member']['user_id'] for r in body['members']], ['sat-amy'])
        self.assertEqual(self.get('admin/two-factor/?filter=bogus').json()['filter'], 'all')
        r = self.post('admin/two-factor/policy/', {'policy': 'everyone'})
        self.assertEqual((r.status_code, self.code(r)), (400, 'invalid_input'))
        r = self.post('admin/two-factor/policy/', {'policy': 'none'})
        self.assertEqual(r.json(), {'ok': True, 'policy': 'none', 'changed': False})
        self.assertFalse(ActivityLog.objects.filter(object_type='SiteSetting').exists())
        r = self.post('admin/two-factor/policy/', {'policy': 'all_members'})
        self.assertEqual(r.json(), {'ok': True, 'policy': 'all_members', 'changed': True})
        self.assertEqual(SiteSetting.get_setting('2fa_policy_mode'), 'all_members')
        self.assertTrue(ActivityLog.objects.filter(action_type='other', object_repr='2fa_policy_mode=all_members'
                                                   ).exists())
        # This install signed in with a password only, and now needs a second factor: it is signed out
        # (the app warns before the change).
        r = self.get('admin/two-factor/')
        self.assertEqual((r.status_code, self.code(r)), (401, 'signed_out_second_factor_required'))


# ── The website views (unchanged behaviour) ──────────────────────────────────

class WebViewsTests(TestCase):

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.admin = make_user('sat-web', is_admin=True, member_type='Officer')
        patcher = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {'sat-web'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client.force_login(self.admin)
        session = self.client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()

    def test_pages_render(self):
        with no_workers():
            for name in ('admin_v2_celery_health', 'admin_v2_backups', 'admin_v2_security', 'admin_v2_quarantine',
                         'admin_v2_honeypot_logs', 'admin_v2_csp_violations', 'admin_v2_notifications',
                         'admin_v2_notification_logs', 'admin_v2_push_subscriptions', 'admin_v2_two_factor'):
                self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    @mock.patch('src.security_notifications.alert_account_quarantined')
    def test_quarantine_actions(self, alert):
        bob = make_user('sat-bob', name='Bob')
        r = self.client.post(reverse('admin_v2_quarantine'), {'action': 'quarantine_user', 'user_id': 'sat-bob',
                                                              'reason': 'Odd'})
        self.assertRedirects(r, reverse('admin_v2_quarantine'), fetch_redirect_response=False)
        q = QuarantinedAccount.objects.get(user=bob)
        self.assertEqual(q.ip_address, '0.0.0.0')  # nosec B104  # the page's placeholder
        alert.assert_called_once()
        self.client.post(reverse('admin_v2_quarantine'), {'action': 'release', 'quarantine_id': q.pk,
                                                          'release_notes': 'ok'})
        q.refresh_from_db()
        self.assertIsNotNone(q.released_at)
        self.assertEqual(AdminActionLog.objects.filter(target_user=bob).count(), 2)

    def test_honeypot_and_csp_actions(self):
        HoneypotAccess.objects.create(endpoint='x', ip_address='198.51.100.5')
        self.client.post(reverse('admin_v2_blacklist_all_honeypot_ips'))
        self.assertTrue(IPBlacklist.objects.filter(ip_address='198.51.100.5').exists())
        self.client.post(reverse('admin_v2_clear_honeypot_logs'), {'ip_address': '198.51.100.5'})
        self.assertFalse(HoneypotAccess.objects.exists())
        CSPViolation.objects.create(violated_directive='script-src', blocked_uri='inline')
        r = self.client.post(reverse('admin_v2_csp_violation_dismiss'),
                             {'violated_directive': 'script-src', 'blocked_uri': 'inline'})
        self.assertEqual(r.json(), {'ok': True, 'dismissed': 1})

    def test_push_subscription_actions(self):
        sub = PushSubscription.objects.create(user=self.admin, endpoint='https://p.example/1', p256dh='k', auth='a')
        PushSubscription.objects.create(user=self.admin, endpoint='https://p.example/2', p256dh='k', auth='a')
        r = self.client.post(reverse('admin_v2_delete_push_subscription', args=[sub.pk]))
        self.assertRedirects(r, reverse('admin_v2_push_subscriptions'), fetch_redirect_response=False)
        r = self.client.post(reverse('admin_v2_clear_push_subscriptions'))
        self.assertRedirects(r, reverse('admin_v2_dashboard'), fetch_redirect_response=False)
        self.assertFalse(PushSubscription.objects.exists())
        self.assertEqual(ActivityLog.objects.filter(action_type__startswith='push_subscription').count(), 2)

    def test_notification_log_filters_and_two_factor_policy(self):
        NotificationLog.objects.create(notification_type='custom', title='A', message='m', status='failed')
        r = self.client.get(reverse('admin_v2_notification_logs') + '?status=failed')
        self.assertEqual(r.context['total_count'], 1)
        r = self.client.get(reverse('admin_v2_two_factor') + '?filter=no_2fa&search=sat')
        self.assertEqual([m['user'].pk for m in r.context['member_data']], ['sat-web'])
