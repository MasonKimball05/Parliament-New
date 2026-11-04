"""
Site admin in the app, part two (v4.4.4): src/mobile/views_site_admin_ops.py.

IP allow/block lists, the three admin exports, page visits, trends and
account deletions, each through the website view's own helper (same rows,
same log entries), behind the Site admin gate. Plus the two web fixes made
on the way into the IP helpers (re-adding a removed address; the blacklist's
address check).

    python manage.py test src.tests.mobile.test_site_admin_ops
"""
import os
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import ActivityLog, AdminActionLog, IPBlacklist, IPWhitelist, ParliamentUser, ServicePeriod
from src.models.analytics import PageVisit
from src.models.mobile import MobileDevice
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.view import admin_v2

API = '/api/mobile/v1/'
ADMIN = 'sao-admin'
SECRET = 'the-admin-secret-0452'
PW = 'pw-' + ADMIN

READS = ('admin/ip/whitelist/', 'admin/ip/blacklist/', 'admin/exports/', 'admin/exports/members/',
         'admin/exports/attendance/', 'admin/exports/service-hours/', 'admin/page-visits/', 'admin/trends/',
         'admin/account-deletions/')
WRITES = ('admin/ip/whitelist/add/', 'admin/ip/whitelist/remove/', 'admin/ip/blacklist/add/',
          'admin/ip/blacklist/remove/', 'admin/account-deletions/nobody/restore/',
          'admin/account-deletions/nobody/rebuild/')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        # Not is_admin: none of these website pages checks it, so the app doesn't either.
        self.admin = make_user(ADMIN, is_admin=False, member_type='Officer', email='sao@parliament.test')
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
        return self.api.post(API + path, data or {}, content_type='application/json',
                             **bearer(token or self.token))

    def unlock(self):
        r = self.post('admin/unlock/', {'password': PW, 'secret_key': SECRET})
        self.assertEqual(r.status_code, 200, r.content)

    def code(self, r):
        return r.json().get('error')


class GateTests(_Base):

    def test_not_on_the_allowlist_is_404(self):
        member = make_user('sao-member', is_admin=True)
        _, token, _ = make_device(member)
        for path in READS:
            self.assertEqual(self.get(path, token=token).status_code, 404, path)
        for path in WRITES:
            self.assertEqual(self.post(path, token=token).status_code, 404, path)

    def test_locked_is_403_admin_locked(self):
        for path in READS:
            r = self.get(path)
            self.assertEqual((r.status_code, self.code(r)), (403, 'admin_locked'), path)
        for path in WRITES:
            r = self.post(path)
            self.assertEqual((r.status_code, self.code(r)), (403, 'admin_locked'), path)
        self.assertFalse(ActivityLog.objects.filter(action_type='other', description__contains='exported').exists())


class _Unlocked(_Base):
    def setUp(self):
        super().setUp()
        self.unlock()
        ActivityLog.objects.all().delete()


class IPListTests(_Unlocked):

    def test_whitelist_add_list_remove(self):
        r = self.post('admin/ip/whitelist/add/', {'ip_address': ' 10.1.1.1 ', 'description': 'Office'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['message'], 'IP 10.1.1.1 has been added to whitelist')
        entry = IPWhitelist.objects.get(ip_address='10.1.1.1')
        self.assertEqual((entry.description, entry.added_by, entry.is_active), ('Office', self.admin, True))
        self.assertTrue(ActivityLog.objects.filter(action_type='ip_whitelisted',
                                                   description__contains='added 10.1.1.1 to whitelist: Office'
                                                   ).exists())
        body = self.get('admin/ip/whitelist/').json()
        self.assertEqual([e['ip_address'] for e in body['entries']], ['10.1.1.1'])
        self.assertEqual(body['entries'][0]['added_by']['user_id'], ADMIN)

        r = self.post('admin/ip/whitelist/add/', {'ip_address': '10.1.1.1'})
        self.assertEqual((r.status_code, self.code(r)), (409, 'exists'))
        self.assertEqual(r.json()['detail'], 'IP 10.1.1.1 is already whitelisted')

        r = self.post('admin/ip/whitelist/remove/', {'ip_address': '10.1.1.1'})
        self.assertEqual(r.json()['message'], 'IP 10.1.1.1 has been removed from whitelist (1 entries deactivated)')
        entry.refresh_from_db()
        self.assertFalse(entry.is_active)
        self.assertTrue(ActivityLog.objects.filter(action_type='ip_whitelist_removed').exists())
        self.assertEqual(self.get('admin/ip/whitelist/').json()['entries'], [])

    def test_whitelist_default_note_and_required(self):
        self.post('admin/ip/whitelist/add/', {'ip_address': '10.1.1.2'})
        self.assertEqual(IPWhitelist.objects.get(ip_address='10.1.1.2').description,
                         f'Added by {self.admin.get_display_name()}')
        for path in ('admin/ip/whitelist/add/', 'admin/ip/whitelist/remove/', 'admin/ip/blacklist/add/',
                     'admin/ip/blacklist/remove/'):
            r = self.post(path, {'ip_address': '   '})
            self.assertEqual((r.status_code, self.code(r)), (400, 'required'), path)
        r = self.post('admin/ip/whitelist/add/', {'ip_address': 'x' * 60})
        self.assertEqual((r.status_code, self.code(r)), (400, 'invalid'))

    def test_blacklist_add_sets_cache_and_logs_then_remove_clears(self):
        cache.set('ip_blacklisted_203.0.113.9', False, 60)
        r = self.post('admin/ip/blacklist/add/', {'ip_address': '203.0.113.9', 'reason': 'Scanner'})
        self.assertEqual(r.status_code, 200, r.content)
        entry = IPBlacklist.objects.get(ip_address='203.0.113.9')
        self.assertEqual((entry.reason, entry.added_by), ('Scanner', self.admin))
        self.assertIsNone(cache.get('ip_blacklisted_203.0.113.9'))
        self.assertTrue(cache.get('honeypot_ban_203.0.113.9'))
        self.assertTrue(ActivityLog.objects.filter(action_type='ip_blacklisted',
                                                   description__contains='blacklisted 203.0.113.9: Scanner').exists())
        body = self.get('admin/ip/blacklist/').json()
        self.assertEqual(body['entries'][0]['block_count'], 0)
        self.assertEqual(body['entries'][0]['reason'], 'Scanner')

        r = self.post('admin/ip/blacklist/add/', {'ip_address': '203.0.113.9'})
        self.assertEqual((r.status_code, self.code(r)), (409, 'exists'))

        cache.set('ip_blacklisted_203.0.113.9', True, 60)
        r = self.post('admin/ip/blacklist/remove/', {'ip_address': '203.0.113.9'})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(cache.get('ip_blacklisted_203.0.113.9'))
        entry.refresh_from_db()
        self.assertFalse(entry.is_active)
        self.assertTrue(ActivityLog.objects.filter(action_type='ip_blacklist_removed').exists())

    def test_blacklist_refuses_a_range_or_a_typo(self):
        for bad in ('10.0.0.0/8', '300.1.1.1', 'not-an-ip'):
            r = self.post('admin/ip/blacklist/add/', {'ip_address': bad, 'reason': 'x'})
            self.assertEqual((r.status_code, self.code(r)), (400, 'invalid'), bad)
        self.assertFalse(IPBlacklist.objects.exists())
        self.assertFalse(ActivityLog.objects.filter(action_type='ip_blacklisted').exists())
        r = self.post('admin/ip/blacklist/add/', {'ip_address': '2001:db8::1'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(IPBlacklist.objects.get().reason, 'Suspicious activity')

    def test_a_removed_address_can_be_added_again(self):
        IPBlacklist.objects.create(ip_address='198.51.100.4', reason='old', is_active=False,
                                   expires_at=timezone.now() - timedelta(days=1))
        IPWhitelist.objects.create(ip_address='198.51.100.5', description='old', is_active=False)
        r = self.post('admin/ip/blacklist/add/', {'ip_address': '198.51.100.4', 'reason': 'again'})
        self.assertEqual(r.status_code, 200, r.content)
        b = IPBlacklist.objects.get(ip_address='198.51.100.4')
        self.assertEqual((b.is_active, b.reason, b.added_by, b.expires_at), (True, 'again', self.admin, None))
        r = self.post('admin/ip/whitelist/add/', {'ip_address': '198.51.100.5', 'description': 'back'})
        self.assertEqual(r.status_code, 200, r.content)
        w = IPWhitelist.objects.get(ip_address='198.51.100.5')
        self.assertEqual((w.is_active, w.description), (True, 'back'))


class WebIPListTests(_Base):
    """The website's forms, now through the shared helpers: same messages and rows, plus the two fixes."""

    def setUp(self):
        super().setUp()
        self.web = Client()
        self.web.force_login(self.admin)
        session = self.web.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()

    def messages(self, r):
        return [str(m) for m in r.context['messages']] if r.context else []

    def test_web_add_and_remove(self):
        r = self.web.post(reverse('admin_v2_add_ip_whitelist'), {'ip_address': '10.9.9.9', 'description': 'VPN'},
                          follow=True)
        self.assertIn('IP 10.9.9.9 has been added to whitelist', self.messages(r))
        r = self.web.post(reverse('admin_v2_add_ip_whitelist'), {'ip_address': '10.9.9.9'}, follow=True)
        self.assertIn('IP 10.9.9.9 is already whitelisted', self.messages(r))
        r = self.web.post(reverse('admin_v2_add_ip_blacklist'), {'ip_address': '', 'reason': 'x'}, follow=True)
        self.assertIn('IP address is required', self.messages(r))
        r = self.web.post(reverse('admin_v2_add_ip_blacklist'), {'ip_address': '10.0.0.0/8'}, follow=True)
        self.assertFalse(IPBlacklist.objects.exists())
        r = self.web.post(reverse('admin_v2_add_ip_blacklist'), {'ip_address': '10.8.8.8', 'reason': 'x'},
                          follow=True)
        self.assertIn('IP 10.8.8.8 has been added to blacklist', self.messages(r))
        r = self.web.post(reverse('admin_v2_remove_ip_blacklist'), {'ip_address': '10.8.8.8'}, follow=True)
        self.assertIn('IP 10.8.8.8 has been removed from blacklist (1 entries deactivated)', self.messages(r))
        # Re-adding a removed address was an IntegrityError (unique column); now it goes back on the list.
        r = self.web.post(reverse('admin_v2_add_ip_blacklist'), {'ip_address': '10.8.8.8', 'reason': 'y'})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(IPBlacklist.objects.get(ip_address='10.8.8.8').is_active)
        self.assertEqual(ActivityLog.objects.filter(action_type='ip_blacklisted').count(), 2)


class ExportTests(_Unlocked):

    def setUp(self):
        super().setUp()
        make_user('sao-active', name='Active Person')
        make_user('sao-alum', name='Alum Person', member_status='Alumni')

    def test_members_csv_matches_the_website_and_logs(self):
        r = self.get('admin/exports/members/?status=Active')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r['Content-Type'].startswith('text/csv'))
        self.assertIn('parliament_members_active_', r['Content-Disposition'])
        text = r.content.decode()
        self.assertIn('Active Person', text)
        self.assertNotIn('Alum Person', text)
        self.assertTrue(ActivityLog.objects.filter(action_type='other', user=self.admin,
                                                   description__contains='exported the member roster (Active)'
                                                   ).exists())
        # Same bytes as the website's view for the same parameters (bar the log row).
        web = Client()
        web.force_login(self.admin)
        session = web.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()
        self.assertEqual(web.get(reverse('admin_v2_export_members') + '?status=Active').content, r.content)

    def test_attendance_and_service_csvs(self):
        r = self.get('admin/exports/attendance/?days=30&status=')
        self.assertEqual(r.status_code, 200)
        self.assertIn('parliament_attendance_30d_', r['Content-Disposition'])
        self.assertIn('Alum Person', r.content.decode())
        self.assertTrue(ActivityLog.objects.filter(description__contains='attendance for the last 30 days').exists())
        self.assertEqual(self.get('admin/exports/attendance/?days=junk').status_code, 200)

        period = ServicePeriod.objects.create(name='Fall', start_date=timezone.localdate() - timedelta(days=10),
                                              end_date=timezone.localdate() + timedelta(days=10), is_active=True,
                                              default_hours_required=5)
        r = self.get(f'admin/exports/service-hours/?period={period.pk}')
        self.assertEqual(r.status_code, 200)
        self.assertIn('Fall', r.content.decode())
        self.assertTrue(ActivityLog.objects.filter(description__contains='service hours (Fall)').exists())
        body = self.get('admin/exports/').json()
        self.assertEqual(body['statuses'], ['Active', 'Inactive', 'Alumni'])
        self.assertEqual(body['periods'][0], {'id': period.pk, 'name': 'Fall', 'is_active': True})
        self.assertFalse(body['geo_restricted'])

    def test_an_install_signed_in_from_abroad_gets_no_export(self):
        MobileDevice.objects.filter(pk=self.device.pk).update(
            login_geo_suspicious=True, login_geo={'country': 'France', 'city': 'Paris'})
        with self.assertLogs('admin_actions', 'WARNING') as logs:
            for path in ('admin/exports/members/', 'admin/exports/attendance/', 'admin/exports/service-hours/'):
                r = self.get(path)
                self.assertEqual((r.status_code, self.code(r)), (403, 'geo_restricted'), path)
        self.assertIn('GEO_BLOCK', logs.output[0])
        self.assertIn('Paris, France', logs.output[0])
        self.assertFalse(ActivityLog.objects.filter(description__contains='exported').exists())
        self.assertTrue(self.get('admin/exports/').json()['geo_restricted'])


class PageVisitsAndTrendsTests(_Unlocked):

    def test_page_visits_totals_filters_and_drill_down(self):
        a = make_user('sao-a', name='Alice Example')
        b = make_user('sao-b', name='Bob Example', member_status='Alumni')
        PageVisit.objects.create(user=a, path='/calendar/', count=5)
        PageVisit.objects.create(user=b, path='/calendar/', count=2)
        PageVisit.objects.create(user=b, path='/announcements/', count=9)

        body = self.get('admin/page-visits/').json()
        self.assertEqual(body['pages'][0], {'path': '/announcements/', 'total': 9, 'unique_users': 1})
        self.assertEqual(body['pages'][1], {'path': '/calendar/', 'total': 7, 'unique_users': 2})
        self.assertEqual(body['total'], 2)
        self.assertIsNone(body['matched_users'])

        body = self.get('admin/page-visits/?sort=users').json()
        self.assertEqual(body['pages'][0]['path'], '/calendar/')

        body = self.get('admin/page-visits/?user=alice').json()
        self.assertEqual(body['pages'], [{'path': '/calendar/', 'total': 5, 'unique_users': 1}])
        self.assertEqual(body['matched_users'], [{'user_id': 'sao-a', 'name': a.get_display_name()}])

        body = self.get('admin/page-visits/?status=Alumni').json()
        self.assertEqual({p['path'] for p in body['pages']}, {'/calendar/', '/announcements/'})
        self.assertEqual(self.get('admin/page-visits/?status=Bogus').json()['status'], '')

        body = self.get('admin/page-visits/?path=/calendar/').json()
        self.assertEqual(body['path'], '/calendar/')
        self.assertEqual([(v['member']['user_id'], v['count']) for v in body['visitors']],
                         [('sao-a', 5), ('sao-b', 2)])

    def test_trends(self):
        body = self.get('admin/trends/').json()
        self.assertEqual(len(body['labels']), 12)
        self.assertEqual([s['id'] for s in body['series']], ['signins', 'app', 'failed'])
        app = body['series'][1]
        self.assertEqual(len(app['values']), 12)
        self.assertGreaterEqual(app['latest'], 1)   # this test's own install
        self.assertEqual(app['total'], sum(app['values']))


class AccountDeletionTests(_Unlocked):

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.pending = make_user('sao-pending', name='Pending Person', email='pending@parliament.test')
        ParliamentUser.objects.filter(pk=self.pending.pk).update(
            deletion_requested_at=now - timedelta(days=3), deletion_requested_via='app', is_active=False)
        self.purged = make_user('sao-purged', name='Purged Person', email='')
        ParliamentUser.objects.filter(pk=self.purged.pk).update(
            deletion_requested_at=now - timedelta(days=40), account_deleted_at=now - timedelta(days=10),
            is_active=False)

    def test_list(self):
        body = self.get('admin/account-deletions/').json()
        self.assertEqual(body['purge_days'], 30)
        self.assertEqual([m['user_id'] for m in body['pending']], ['sao-pending'])
        self.assertEqual(body['pending'][0]['requested_via'], 'app')
        self.assertTrue(body['pending'][0]['purge_on'])
        self.assertEqual([m['user_id'] for m in body['purged']], ['sao-purged'])
        self.assertTrue(body['purged'][0]['erased_at'])

    def test_restore(self):
        r = self.post('admin/account-deletions/sao-pending/restore/')
        self.assertEqual(r.status_code, 200, r.content)
        self.pending.refresh_from_db()
        self.assertTrue(self.pending.is_active)
        self.assertIsNone(self.pending.deletion_requested_at)
        self.assertTrue(AdminActionLog.objects.filter(action='account_restored', target_user=self.pending,
                                                      actor=self.admin).exists())
        self.assertEqual(len(mail.outbox), 1)
        r = self.post('admin/account-deletions/sao-pending/restore/')
        self.assertEqual((r.status_code, self.code(r)), (400, 'not_pending'))
        self.assertEqual(self.post('admin/account-deletions/sao-nobody/restore/').status_code, 404)

    def test_rebuild(self):
        r = self.post('admin/account-deletions/sao-purged/rebuild/', {'email': 'bad'})
        self.assertEqual((r.status_code, self.code(r)), (400, 'invalid_email'))
        r = self.post('admin/account-deletions/sao-purged/rebuild/', {'email': 'pending@parliament.test'})
        self.assertEqual((r.status_code, self.code(r)), (400, 'email_taken'))
        r = self.post('admin/account-deletions/sao-pending/rebuild/', {'email': 'new@parliament.test'})
        self.assertEqual((r.status_code, self.code(r)), (400, 'not_purged'))
        r = self.post('admin/account-deletions/sao-purged/rebuild/',
                      {'email': 'Back@Parliament.test', 'username': 'purged-back'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertIn('back@parliament.test', r.json()['message'])
        self.purged.refresh_from_db()
        self.assertEqual((self.purged.email, self.purged.username, self.purged.is_active),
                         ('back@parliament.test', 'purged-back', True))
        self.assertIsNone(self.purged.account_deleted_at)
        self.assertTrue(AdminActionLog.objects.filter(action='account_rebuilt', target_user=self.purged).exists())
