"""
The Security group of Admin v2 in the console look (`admin_console_ui`).

Each page: with the flag on it renders its console template (ac-* markup, the
same forms, fields, URLs and JS hooks); with it off it renders the classic
template exactly as before. Alert and honeypot details open as slide-out
panel fragments behind the same gate as their lists. The console's Actions ▾
endpoints (dismiss alerts, remove allow/block list entries, clear lockouts)
re-check every id. The new templates carry no inline script or style.
"""
import re
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from src.models import (
    ActivityLog, AdminActionLog, CSPViolation, HoneypotAccess, IPBlacklist, IPWhitelist, LoginAlert,
    LoginHistory, LoginLockout, QuarantinedAccount, SecurityNotificationLog,
)
from src.tests.admin_v2.base import ADMIN_ID, AdminV2TestCase, make_user
from src.view import admin_v2

TEMPLATES = Path(settings.BASE_DIR) / 'templates' / 'admin_v2'

# url name -> (console template, classic template, hooks the console page keeps)
PAGES = {
    'admin_v2_security': ('admin_v2/console/security_dashboard.html', 'admin_v2/security_dashboard.html', (
        'name="quarantine_id"', 'value="release"', 'value="clear"', 'value="blacklist"', 'name="lockout_id"',
        'data-confirm="Release quarantine for', 'data-confirm="Blacklist every unique honeypot IP',
    )),
    'admin_v2_security_alerts': ('admin_v2/console/security_alerts.html', 'admin_v2/security_alerts.html', (
        'id="dismiss-all-btn"', 'js-dismiss-alert', 'data-alert-id=', 'name="status"', 'name="severity"',
        'data-ac-panel', 'data-ac-select', 'js/admin_v2/security_alerts.js',
    )),
    'admin_v2_security_notifications': ('admin_v2/console/security_notifications.html',
                                        'admin_v2/security_notifications.html',
                                        ('name="severity"', 'name="event_type"', 'BRUTE_FORCE')),
    'admin_v2_lockouts': ('admin_v2/console/lockouts.html', 'admin_v2/lockouts.html', (
        'value="clear"', 'value="blacklist"', 'value="whitelist_and_clear"', 'value="clear_all_active"',
        'name="lockout_id"', 'name="ip_address"', 'value="clear_selected"', 'data-ac-row-select',
    )),
    'admin_v2_lockdown': ('admin_v2/console/lockdown_control.html', 'admin_v2/lockdown_control.html', (
        'value="activate"', 'name="reason"', 'name="whitelisted_ips"', 'data-confirm="Are you SURE',
    )),
    'admin_v2_quarantine': ('admin_v2/console/quarantine.html', 'admin_v2/quarantine_management.html', (
        'value="quarantine_user"', 'name="user_id"', 'name="ip_address"', 'name="reason"', 'name="expires_at"',
        'value="release"', 'name="quarantine_id"', 'name="release_notes"',
    )),
    'admin_v2_honeypot_logs': ('admin_v2/console/honeypot_logs.html', 'admin_v2/honeypot_logs.html', (
        'id="js-blacklist-all-btn"', 'id="js-clear-all-btn"', 'js-honeypot-row', 'js-delete-log',
        'id="clear-all-form"', 'id="blacklist-all-form"', 'id="clear-ip-form"', 'id="clear-ip-value"',
        'name="endpoint"', 'name="ip"', 'js/admin_v2/security_honeypot.js', 'data-ac-panel',
    )),
    'admin_v2_csp_violations': ('admin_v2/console/csp_violations.html', 'admin_v2/csp_violations.html', (
        'id="js-violation-list"', 'js-violation-row', 'js-dismiss-btn', 'data-directive=', 'data-blocked=',
        'js/admin_v2/security_csp.js',
    )),
    'admin_v2_ip_whitelist': ('admin_v2/console/ip_whitelist.html', 'admin_v2/ip_whitelist.html', (
        'name="ip_address"', 'name="description"', 'data-confirm="Remove 198.51.100.7 from whitelist?"',
    )),
    'admin_v2_ip_blacklist': ('admin_v2/console/ip_blacklist.html', 'admin_v2/ip_blacklist.html', (
        'name="ip_address"', 'name="reason"', 'data-confirm="Remove 203.0.113.66 from blacklist?"',
    )),
    'admin_v2_audit_log': ('admin_v2/console/audit_log.html', 'admin_v2/audit_log.html', (
        'export=csv', 'name="action"', 'name="actor"', 'name="date_from"', 'name="date_to"',
    )),
    'admin_v2_admin_activity': ('admin_v2/console/admin_activity.html', 'admin_v2/admin_activity.html', (
        'name="actor"', 'name="action"', 'name="q"', 'data-autosubmit',
    )),
    'admin_v2_login_history': ('admin_v2/console/login_history.html', 'admin_v2/manage_login_history.html', (
        'name="user"', 'name="suspicious"', 'data-ac-panel',
    )),
}
# URLs every console page must still point at (form actions, links).
URLS = {
    'admin_v2_security': ('admin_v2_quarantine', 'admin_v2_lockouts', 'admin_v2_add_ip_blacklist',
                          'admin_v2_blacklist_all_honeypot_ips'),
    'admin_v2_security_alerts': ('admin_v2_dismiss_all_alerts', 'admin_v2_dismiss_alerts_bulk'),
    'admin_v2_honeypot_logs': ('admin_v2_add_ip_blacklist', 'admin_v2_clear_honeypot_logs',
                               'admin_v2_blacklist_all_honeypot_ips'),
    'admin_v2_csp_violations': ('admin_v2_csp_violation_dismiss',),
    'admin_v2_ip_whitelist': ('admin_v2_add_ip_whitelist', 'admin_v2_remove_ip_whitelist',
                              'admin_v2_remove_ip_whitelist_bulk'),
    'admin_v2_ip_blacklist': ('admin_v2_add_ip_blacklist', 'admin_v2_remove_ip_blacklist',
                              'admin_v2_remove_ip_blacklist_bulk'),
}
BULK = ('admin_v2_security_alerts', 'admin_v2_lockouts', 'admin_v2_ip_whitelist', 'admin_v2_ip_blacklist')
ADMIN_ONLY = ('admin_v2_security', 'admin_v2_security_notifications', 'admin_v2_lockouts', 'admin_v2_lockdown',
              'admin_v2_honeypot_logs', 'admin_v2_csp_violations', 'admin_v2_audit_log')

# Per-request noise in a rendered page: CSRF tokens (masked anew each render)
# and the CSP nonce.
_NOISE = re.compile(r'(csrfmiddlewaretoken" value=|csrf-token" content=|nonce=|CSRF = )["\'][^"\']*["\']')


def _normalise(html):
    return _NOISE.sub(r'\1"-"', html)


class _SecurityData(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.member = make_user('sec-member', name='Sam Lockedout', email='sam@example.test')
        login = LoginHistory.objects.create(user=self.member, ip_address='198.51.100.20', city='Austin',
                                            region='TX', country='US', device_type='mobile', is_suspicious=True)
        self.alert = LoginAlert.objects.create(
            user=self.member, login_history=login, alert_type='new_location', severity='critical',
            title='Sign-in from a new city', description='Austin, TX for the first time.')
        self.alert_hi = LoginAlert.objects.create(
            user=self.member, alert_type='multiple_failures', severity='high', title='Failed attempts',
            description='Five in a row.')
        self.resolved = LoginAlert.objects.create(
            user=self.member, alert_type='other', severity='low', status='resolved', title='Old one',
            description='Done.', resolution_notes='Checked')
        self.hit = HoneypotAccess.objects.create(
            endpoint='/wp-admin/', ip_address='203.0.113.9', request_method='POST', user_agent='curl/8.0',
            request_body='log=admin', additional_data={'geo': {'country_code': 'US', 'country': 'United States',
                                                               'city': 'Dallas'}})
        self.lockout = LoginLockout.objects.create(ip_address='192.0.2.44', username='sam',
                                                   expires_at=now + timedelta(minutes=10))
        self.lockout2 = LoginLockout.objects.create(ip_address='192.0.2.45', expires_at=now + timedelta(minutes=10))
        self.allow = IPWhitelist.objects.create(ip_address='198.51.100.7', description='Office', added_by=self.admin)
        self.block = IPBlacklist.objects.create(ip_address='203.0.113.66', reason='Scanner', added_by=self.admin)
        QuarantinedAccount.objects.create(user=self.member, ip_address='192.0.2.1', reason='Too many resets',
                                          is_auto=False, quarantined_by=self.admin)
        CSPViolation.objects.create(violated_directive='script-src', blocked_uri='https://evil.example/x.js')
        SecurityNotificationLog.objects.create(event_type='BRUTE_FORCE', severity='critical',
                                               details='Ten failed sign-ins', ip_address='192.0.2.9')
        AdminActionLog.objects.create(actor=self.admin, action='quarantine_set', target_user=self.member,
                                      target_repr='Sam Lockedout', detail='by hand')
        ActivityLog.log_activity(action_type='user_role_changed', user=self.admin,
                                 description='Admin gave Historian to Sam', object_type='ParliamentUser',
                                 object_id=self.member.user_id, object_repr='Sam Lockedout')

    def officer(self):
        """Allowlisted for admin v2, but not is_admin: the admin-only pages refuse."""
        user = make_user('sec-officer', member_type='Officer')
        patcher = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {ADMIN_ID, user.user_id})
        patcher.start()
        self.addCleanup(patcher.stop)
        client = Client()
        client.force_login(user)
        session = client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()
        return client


class SecurityPagesConsoleTests(_SecurityData):

    def test_console_on(self):
        for name, (console, classic, hooks) in PAGES.items():
            with self.subTest(page=name):
                r = self.client.get(reverse(name))
                self.assertEqual(r.status_code, 200)
                self.assertTemplateUsed(r, console)
                self.assertTemplateNotUsed(r, classic)
                html = r.content.decode()
                self.assertIn('class="ac-card', html)
                self.assertIn('ac-sec-', html)
                self.assertNotIn('container mx-auto px-4 py-8', html)
                for hook in hooks:
                    self.assertIn(hook, html)
                for url_name in URLS.get(name, ()):
                    self.assertIn(reverse(url_name), html)
                # at most one gold primary button
                self.assertLessEqual(html.count('ac-btn-primary'), 1)
                # Actions ▾ only where there is something to do with ticked rows
                self.assertEqual('data-ac-bulk=' in html, name in BULK, 'Actions menu')

    def test_console_off_is_the_classic_page_unchanged(self):
        self.set_console(False)
        for name, (console, classic, _hooks) in PAGES.items():
            with self.subTest(page=name):
                r = self.client.get(reverse(name))
                self.assertEqual(r.status_code, 200)
                self.assertTemplateUsed(r, classic)
                self.assertTemplateUsed(r, 'admin_v2/base_classic.html')
                self.assertFalse([t.name for t in r.templates if t.name and t.name.startswith('admin_v2/console/')])
                html = r.content.decode()
                self.assertNotIn('ac-sec-', html)
                # Byte for byte what the classic template renders for the same context.
                again = render_to_string(classic, r.context[0].flatten(), request=r.wsgi_request)
                self.assertEqual(_normalise(html), _normalise(again))

    def test_classic_templates_are_untouched(self):
        """The classic templates keep their old markup (the flag must stay reversible)."""
        for _console, classic, _hooks in PAGES.values():
            text = (TEMPLATES.parent / classic).read_text()
            with self.subTest(template=classic):
                self.assertNotIn('ac-sec-', text)
                self.assertNotIn('data-ac-', text)

    def test_admin_only_pages_stay_admin_only(self):
        officer = self.officer()
        for console in (True, False):
            self.set_console(console)
            for name in ADMIN_ONLY:
                with self.subTest(page=name, console=console):
                    self.assertEqual(officer.get(reverse(name)).status_code, 403)
        # The others are open to the allowlist, as before.
        self.assertEqual(officer.get(reverse('admin_v2_security_alerts')).status_code, 200)

    def test_anonymous_is_sent_away(self):
        anon = Client()
        for name in PAGES:
            with self.subTest(page=name):
                self.assertEqual(anon.get(reverse(name)).status_code, 302)

    def test_alert_rows_open_in_the_panel(self):
        html = self.client.get(reverse('admin_v2_security_alerts')).content.decode()
        self.assertIn(f'href="{reverse("admin_v2_security_alert", args=[self.alert.pk])}" class="ac-row-link" '
                      f'data-ac-panel', html)
        self.assertIn(reverse('admin_v2_dismiss_alert', args=[self.alert.pk]), html)

    def test_honeypot_rows_open_in_the_panel(self):
        html = self.client.get(reverse('admin_v2_honeypot_logs')).content.decode()
        self.assertIn(f'href="{reverse("admin_v2_honeypot_log", args=[self.hit.pk])}" class="ac-row-link" '
                      f'data-ac-panel', html)
        self.assertIn(reverse('admin_v2_delete_honeypot_log', args=[self.hit.pk]), html)
        r = self.client.get(reverse('admin_v2_honeypot_logs'), {'ip': '203.0.113.9'})
        self.assertContains(r, 'id="js-clear-ip-btn" data-ip="203.0.113.9"')

    def test_lockdown_active_forms(self):
        from src.models import SystemLockdown
        SystemLockdown.get_instance().activate(self.admin, 'Drill', ['192.0.2.1'])
        r = self.client.get(reverse('admin_v2_lockdown'))
        for hook in ('value="deactivate"', 'value="update_whitelist"', 'value="update_message"', 'name="message"',
                     'Lockdown active'):
            self.assertContains(r, hook)
        self.assertContains(self.client.get(reverse('admin_v2_security')), 'Emergency lockdown is active')


class SecurityPanelTests(_SecurityData):

    def panels(self):
        return (reverse('admin_v2_security_alert', args=[self.alert.pk]),
                reverse('admin_v2_honeypot_log', args=[self.hit.pk]))

    def test_fragments(self):
        expected = {self.panels()[0]: ('Sign-in from a new city', 'js-dismiss-alert', '198.51.100.20', 'Austin',
                                       'data-ac-tab="signin"', 'id="sa-overview"'),
                    self.panels()[1]: ('/wp-admin/', 'js-delete-log', 'log=admin', 'Dallas',
                                       reverse('admin_v2_add_ip_blacklist'))}
        for url, bits in expected.items():
            with self.subTest(url=url):
                r = self.client.get(url, {'panel': '1'})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r['X-Admin-Panel'], '1')
                html = r.content.decode()
                self.assertNotIn('<html', html)
                self.assertNotIn('<script', html)
                self.assertIn('id="ac-blade-title"', html)
                self.assertIn('data-ac-panel-close', html)
                for bit in bits:
                    self.assertIn(bit, html)

    def test_without_panel_it_is_the_full_page(self):
        for console in (True, False):
            self.set_console(console)
            for url in self.panels():
                with self.subTest(url=url, console=console):
                    r = self.client.get(url)
                    self.assertEqual(r.status_code, 200)
                    self.assertNotIn('X-Admin-Panel', r)
                    self.assertIn('<html', r.content.decode())

    def test_same_gate_as_the_full_page(self):
        for url in self.panels():
            with self.subTest(url=url):
                r = Client().get(url, {'panel': '1'})
                self.assertEqual(r.status_code, 302)
                self.assertNotIn('X-Admin-Panel', r)
                outsider = Client()
                outsider.force_login(make_user(f'sec-out-{url.count("/")}-{len(url)}', is_admin=True))
                r = outsider.get(url, {'panel': '1'})
                self.assertEqual(r.status_code, 302)
                self.assertNotIn('X-Admin-Panel', r)
                stale = Client()
                stale.force_login(self.admin)
                self.assertRedirects(stale.get(url, {'panel': '1'}), reverse('admin_v2_login'),
                                     fetch_redirect_response=False)

    def test_honeypot_panel_is_admin_only_like_its_list(self):
        officer = self.officer()
        self.assertEqual(officer.get(self.panels()[1], {'panel': '1'}).status_code, 403)
        # Alerts are open to the allowlist, like the alert list.
        self.assertEqual(officer.get(self.panels()[0], {'panel': '1'}).status_code, 200)

    def test_unknown_is_404(self):
        for name in ('admin_v2_security_alert', 'admin_v2_honeypot_log'):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(reverse(name, args=[987654]), {'panel': '1'}).status_code, 404)


class SecurityBulkActionTests(_SecurityData):

    def test_dismiss_selected_alerts(self):
        url = reverse('admin_v2_dismiss_alerts_bulk')
        self.assertEqual(self.client.get(url).status_code, 405)
        r = self.client.post(url, {'ids': [self.alert.pk, self.resolved.pk, 'x', 99999]})
        self.assertEqual(r.status_code, 302)
        self.alert.refresh_from_db()
        self.alert_hi.refresh_from_db()
        self.resolved.refresh_from_db()
        self.assertEqual((self.alert.status, self.alert.reviewed_by), ('resolved', self.admin))
        self.assertEqual(self.alert_hi.status, 'new')  # not ticked
        self.assertEqual(self.resolved.resolution_notes, 'Checked')  # already closed: left alone
        self.assertTrue(ActivityLog.objects.filter(action_type='security', description__contains='dismissed 1').exists())

    def test_remove_selected_ips(self):
        other = IPWhitelist.objects.create(ip_address='198.51.100.8', description='Home')
        self.client.post(reverse('admin_v2_remove_ip_whitelist_bulk'), {'ids': [self.allow.pk]})
        self.assertFalse(IPWhitelist.objects.get(pk=self.allow.pk).is_active)
        self.assertTrue(IPWhitelist.objects.get(pk=other.pk).is_active)
        self.client.post(reverse('admin_v2_remove_ip_blacklist_bulk'), {'ids': [self.block.pk]})
        self.assertFalse(IPBlacklist.objects.get(pk=self.block.pk).is_active)
        for name in ('admin_v2_remove_ip_whitelist_bulk', 'admin_v2_remove_ip_blacklist_bulk'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 405)
            self.assertEqual(Client().post(reverse(name), {'ids': [other.pk]}).status_code, 302)
        self.assertTrue(IPWhitelist.objects.get(pk=other.pk).is_active)

    def test_clear_selected_lockouts(self):
        r = self.client.post(reverse('admin_v2_lockouts'), {'action': 'clear_selected', 'ids': [self.lockout.pk]})
        self.assertEqual(r.status_code, 302)
        self.lockout.refresh_from_db()
        self.lockout2.refresh_from_db()
        self.assertTrue(self.lockout.is_cleared)
        self.assertFalse(self.lockout2.is_cleared)
        # admins only, like every other lockout action
        self.assertEqual(self.officer().post(reverse('admin_v2_lockouts'),
                                             {'action': 'clear_selected', 'ids': [self.lockout2.pk]}).status_code, 403)
        self.lockout2.refresh_from_db()
        self.assertFalse(self.lockout2.is_cleared)


class SecurityTemplateRulesTests(AdminV2TestCase):
    """The new templates and scripts follow the prod CSP."""

    FILES = [
        'console/security_dashboard.html', 'console/security_alerts.html', 'console/security_alert_panel.html',
        'console/_security_alert_body.html', 'console/_security_severity.html', 'console/_security_lockout_source.html',
        'console/security_notifications.html', 'console/lockouts.html', 'console/lockdown_control.html',
        'console/quarantine.html', 'console/honeypot_logs.html', 'console/honeypot_log_panel.html',
        'console/_honeypot_log_actions.html', 'console/_honeypot_log_body.html', 'console/csp_violations.html',
        'console/ip_whitelist.html', 'console/ip_blacklist.html', 'console/audit_log.html',
        'console/admin_activity.html', 'console/login_history.html', 'security_alert.html', 'honeypot_log.html',
    ]

    def test_no_inline_script_or_style(self):
        for name in self.FILES:
            text = (TEMPLATES / name).read_text()
            with self.subTest(template=name):
                self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\sstyle=', text))
                for tag in re.findall(r'<script\b[^>]*>', text):
                    self.assertIn('src=', tag)
                self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\bon[a-z]+=', text))
                self.assertNotIn('<style', text)

    def test_scripts_exist(self):
        for js in ('security_alerts.js', 'security_honeypot.js', 'security_csp.js'):
            path = Path(settings.BASE_DIR) / 'static' / 'js' / 'admin_v2' / js
            with self.subTest(js=js):
                text = path.read_text()
                self.assertNotIn('innerHTML', text)
                self.assertNotIn('.style.', text)
