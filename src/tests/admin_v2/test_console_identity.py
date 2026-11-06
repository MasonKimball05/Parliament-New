"""
The console look for the Identity pages (phase 2 of the Admin v2 "Royal
console", `admin_console_ui`).

Each page renders its console template with the flag on and its classic
template, unchanged, with the flag off; the console markup keeps every form,
field name, URL and JS hook of the classic page; the API token panel fragment
has the full page's gate; the Actions-menu endpoints added for the console
lists (2FA bulk form post, committees, API tokens) refuse GET and re-check
each id; and the new templates follow the CSP rules (no inline script, style
or handlers).
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import Client, SimpleTestCase
from django.urls import reverse

from src.models import ActivityLog, Committee, RoleHistory, TwoFactorRequirement
from src.models.api import APIAccessLog, APIToken
from src.tests.admin_v2.base import AdminV2TestCase, make_user

CONSOLE = Path(settings.BASE_DIR) / 'templates' / 'admin_v2' / 'console'
OURS = [
    'member.html', 'edit_user_profile.html', 'user_login_security.html', 'two_factor_dashboard.html',
    'permissions.html', 'bulk_members.html', 'bulk_members_confirm.html', 'account_deletions.html',
    'mobile.html', 'api_tokens.html', 'api_token_logs.html', 'api_token_panel.html', 'manage_committees.html',
    '_api_token_buttons.html', '_api_token_status.html', '_api_log_cells.html',
]


class IdentityPagesTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        self.member = make_user('id-member', name='Ivy Member', email='ivy@example.test')
        self.token = APIToken.objects.create(user=self.member, key='k' * 64, name='Sheets sync',
                                             status='active', scopes=['members:read'])
        APIAccessLog.objects.create(token=self.token, user=self.member, endpoint='/api/v1/members/',
                                    method='GET', response_status=200, scopes_used=['members:read'])
        self.pending = APIToken.objects.create(user=self.member, key='p' * 64, name='Widget',
                                               status='pending', scopes=['events:read'])
        self.committee = Committee.objects.create(name='Finance')
        RoleHistory.objects.create(user=self.member, role_name='Treasurer', start_semester='Fall 2025')

    def pages(self):
        """(name, request, classic template, console template, strings the
        console page must keep from the classic one)."""
        uid = self.member.user_id
        return [
            ('member', ('get', reverse('admin_v2_member', args=[uid]), None),
             'admin_v2/member.html', 'admin_v2/console/member.html',
             [reverse('admin_v2_member_sign_out', args=[uid]), reverse('admin_v2_edit_user_profile', args=[uid]),
              reverse('admin_v2_user_login_security', args=[uid]), 'data-confirm=', 'js-check-password']),
            ('edit profile', ('get', reverse('admin_v2_edit_user_profile', args=[uid]), None),
             'admin_v2/edit_user_profile.html', 'admin_v2/console/edit_user_profile.html',
             ['name="action" value="core"', 'name="action" value="extended"', 'value="role_history_add"',
              'value="role_history_delete"', 'value="custom_social_add"', 'value="academic_item_add"',
              'value="initiation_chapter_add"', 'name="pledge_class"', 'name="pledge_class_greek"',
              'list="pledge-class-options"', 'name="big_brother"', 'name="instagram"', 'name="ic_role_number"',
              'js/admin_v2/edit_profile.js']),
            ('login security', ('get', reverse('admin_v2_user_login_security', args=[uid]), None),
             'admin_v2/user_login_security.html', 'admin_v2/console/user_login_security.html',
             [reverse('admin_v2_force_password_reset', args=[uid]), reverse('admin_v2_release_user_lockout', args=[uid]),
              reverse('admin_v2_toggle_watch_flag', args=[uid]), 'id="watch-flag-add-btn"', 'id="watch-flag-add-form"',
              'id="js-pw-random"', 'id="js-pw-custom"', 'id="custom_password_field"', 'name="custom_password"',
              'name="send_email"', 'js-confirm-submit', 'data-confirm-msg=', 'name="activity_category"',
              'name="activity_date"', 'js/admin_v2/login_security.js']),
            ('two-factor', ('get', reverse('admin_v2_two_factor'), None),
             'admin_v2/two_factor_dashboard.html', 'admin_v2/console/two_factor_dashboard.html',
             ['js-policy-radio', 'data-policy-value=', reverse('admin_v2_two_factor_update_policy'),
              'js-open-req-modal', reverse('admin_v2_two_factor_set_requirement', args=[uid]), 'id="requirementModal"',
              'id="reset2FAModal"', 'id="js-save-req"', 'id="js-confirm-reset"', 'name="modalRequirement"',
              'member-checkbox', reverse('admin_v2_two_factor_bulk_action'), 'name="search"', 'name="filter"',
              'js/admin_v2/two_factor.js', 'data-ac-panel']),
            ('permissions', ('get', reverse('admin_v2_permissions'), None),
             'admin_v2/permissions.html', 'admin_v2/console/permissions.html',
             ['name="q"', 'name="flagged"', 'data-autosubmit', 'id="perm-is_admin"']),
            ('bulk edit', ('get', reverse('admin_v2_bulk_members'), None),
             'admin_v2/bulk_members.html', 'admin_v2/console/bulk_members.html',
             ['name="step" value="preview"', 'name="bulk_action"', 'name="value_status"', 'name="value_type"',
              'name="value_role"', 'name="ids"', 'data-check-all="ids"', 'name="pledge_class"', 'name="role"']),
            ('bulk confirm', ('post', reverse('admin_v2_bulk_members'),
                              {'step': 'preview', 'bulk_action': 'set_status', 'value_status': 'Alumni', 'ids': [uid]}),
             'admin_v2/bulk_members_confirm.html', 'admin_v2/console/bulk_members_confirm.html',
             ['name="step" value="apply"', 'name="bulk_action" value="set_status"', 'name="value" value="Alumni"',
              f'name="ids" value="{uid}"', 'data-confirm="Apply this change to 1 member(s)?"']),
            ('account deletions', ('get', reverse('admin_v2_account_deletions'), None),
             'admin_v2/account_deletions.html', 'admin_v2/console/account_deletions.html',
             ['No pending deletions.']),
            ('app installs', ('get', reverse('admin_v2_mobile'), None),
             'admin_v2/mobile.html', 'admin_v2/console/mobile.html', ['Expo access token']),
            ('api tokens', ('get', reverse('admin_api_tokens'), None),
             'admin_v2/api_tokens.html', 'admin_v2/console/api_tokens.html',
             ['id="toggle-rest-api"', 'id="toggle-auto-approve"', 'data-flag="rest_api"', 'js-approve-btn',
              'js-reject-btn', 'js-revoke-btn', 'js-edit-scopes-btn', 'id="approve-modal"', 'id="reject-modal"',
              'id="revoke-modal"', 'id="scopes-modal"', 'js-scope-checkbox', f'id="token-row-{self.token.pk}"',
              reverse('admin_approve_token', args=[self.pending.pk]), reverse('admin_revoke_token', args=[self.token.pk]),
              reverse('admin_update_token_scopes', args=[self.token.pk]), reverse('admin_api_token_logs', args=[self.token.pk]),
              reverse('admin_toggle_api_flag', args=['rest_api']), reverse('admin_api_tokens_bulk'),
              'js/admin_v2/api_tokens.js', 'data-ac-panel']),
            ('api token logs', ('get', reverse('admin_api_token_logs', args=[self.token.pk]), None),
             'admin_v2/api_token_logs.html', 'admin_v2/console/api_token_logs.html',
             ['/api/v1/members/', reverse('admin_api_tokens')]),
            ('committees', ('get', reverse('admin_v2_manage_committees'), None),
             'admin_v2/manage_committees.html', 'admin_v2/console/manage_committees.html',
             [reverse('admin_v2_toggle_committee', args=[self.committee.pk]), reverse('admin_v2_committees_bulk'),
              'data-ac-row-select']),
        ]

    def fetch(self, req):
        method, url, data = req
        return self.client.post(url, data) if method == 'post' else self.client.get(url)

    def test_console_on(self):
        self.set_console(True)
        for name, req, classic, console, hooks in self.pages():
            with self.subTest(page=name):
                r = self.fetch(req)
                self.assertEqual(r.status_code, 200)
                self.assertTemplateUsed(r, console)
                self.assertTemplateNotUsed(r, classic)
                html = r.content.decode()
                self.assertIn('ac-shell', html)
                self.assertIn('ac-card', html)
                self.assertIn('css/admin-console/identity.css', html)
                for hook in hooks:
                    self.assertIn(hook, html)
                self.assertNotIn('onclick=', html)

    def test_console_off_is_the_classic_page(self):
        self.set_console(False)
        for name, req, classic, console, _hooks in self.pages():
            with self.subTest(page=name):
                r = self.fetch(req)
                self.assertEqual(r.status_code, 200)
                self.assertTemplateUsed(r, classic)
                self.assertTemplateNotUsed(r, console)
                html = r.content.decode()
                self.assertNotIn('ac-shell', html)
                self.assertNotIn('ac-id-', html)
                self.assertNotIn('ac-card', html)
                self.assertNotIn('admin-console', html)
        self.set_console(True)

    def test_classic_templates_untouched_by_console_blocks(self):
        """The classic files get no console markup: the switch is in the views."""
        for _n, _r, classic, _c, _h in self.pages():
            text = (Path(settings.BASE_DIR) / 'templates' / classic).read_text()
            with self.subTest(template=classic):
                self.assertNotIn('ac-id-', text)
                self.assertNotIn('admin_console_ui', text)

    def test_member_rows_open_the_panel(self):
        self.set_console(True)
        html = self.client.get(reverse('admin_v2_two_factor')).content.decode()
        self.assertIn(f'href="{reverse("admin_v2_member", args=[self.member.user_id])}" class="ac-row-link" data-ac-panel', html)
        html = self.client.get(reverse('admin_api_tokens')).content.decode()
        self.assertIn(f'href="{reverse("admin_api_token_logs", args=[self.token.pk])}" class="ac-row-link" data-ac-panel', html)

    def test_one_gold_button_at_most(self):
        self.set_console(True)
        for name, req, _classic, _console, _hooks in self.pages():
            with self.subTest(page=name):
                html = self.fetch(req).content.decode()
                self.assertLessEqual(html.count('ac-btn-primary'), 1)

    def test_bulk_edit_header_has_no_stray_actions_menu(self):
        """The view's `actions` (the change choices) must not leak into the list header."""
        self.set_console(True)
        html = self.client.get(reverse('admin_v2_bulk_members')).content.decode()
        self.assertNotIn('data-ac-bulk=', html)


class ApiTokenPanelTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        self.owner = make_user('id-owner', name='Olive Owner')
        self.token = APIToken.objects.create(user=self.owner, key='t' * 64, name='Sheets sync', status='active',
                                             scopes=['members:read'])
        APIAccessLog.objects.create(token=self.token, user=self.owner, endpoint='/api/v1/events/',
                                    method='GET', response_status=200)
        self.url = reverse('admin_api_token_logs', args=[self.token.pk])

    def test_fragment(self):
        r = self.client.get(self.url, {'panel': '1'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['X-Admin-Panel'], '1')
        self.assertEqual(r['Cache-Control'], 'no-store')
        self.assertTemplateUsed(r, 'admin_v2/console/api_token_panel.html')
        html = r.content.decode()
        self.assertNotIn('<html', html)
        self.assertNotIn('<script', html)
        self.assertIn('id="ac-blade-title"', html)
        self.assertIn('Sheets sync', html)
        self.assertIn('/api/v1/events/', html)
        self.assertIn('js-revoke-btn', html)
        self.assertIn(reverse('admin_revoke_token', args=[self.token.pk]), html)

    def test_full_page_without_panel(self):
        r = self.client.get(self.url)
        self.assertNotIn('X-Admin-Panel', r)
        self.assertIn('<html', r.content.decode())

    def test_same_gate_as_the_full_page(self):
        r = Client().get(self.url, {'panel': '1'})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn('X-Admin-Panel', r)
        member = Client()
        member.force_login(make_user('id-plain'))
        for params in ({'panel': '1'}, {}):
            r = member.get(self.url, params)
            self.assertEqual(r.status_code, 403)
            self.assertNotIn('X-Admin-Panel', r)
            self.assertNotIn('Sheets sync', r.content.decode())

    def test_unknown_token_is_404(self):
        self.assertEqual(self.client.get(reverse('admin_api_token_logs', args=[99999]), {'panel': '1'}).status_code, 404)


class IdentityActionsTests(AdminV2TestCase):
    """The POST endpoints behind the console lists' Actions menus."""

    def setUp(self):
        super().setUp()
        self.a = make_user('id-a')
        self.b = make_user('id-b')

    def test_two_factor_bulk_form_post(self):
        r = self.client.post(reverse('admin_v2_two_factor_bulk_action'),
                             {'action': 'require', 'ids': [self.a.user_id, self.b.user_id, 'nobody']})
        self.assertRedirects(r, reverse('admin_v2_two_factor'), fetch_redirect_response=False)
        self.assertEqual(TwoFactorRequirement.objects.filter(requirement='required').count(), 2)
        r = self.client.post(reverse('admin_v2_two_factor_bulk_action'), {'action': 'clear', 'ids': [self.a.user_id]})
        self.assertEqual(TwoFactorRequirement.objects.count(), 1)
        r = self.client.post(reverse('admin_v2_two_factor_bulk_action'), {'action': 'bogus', 'ids': [self.a.user_id]})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(TwoFactorRequirement.objects.count(), 1)

    def test_two_factor_bulk_json_unchanged(self):
        r = self.client.post(reverse('admin_v2_two_factor_bulk_action'),
                             {'action': 'exempt', 'user_ids': [self.a.user_id]}, content_type='application/json')
        self.assertEqual(r.json(), {'success': True, 'count': 1, 'action': 'exempt'})
        r = self.client.post(reverse('admin_v2_two_factor_bulk_action'),
                             {'action': 'nope', 'user_ids': [self.a.user_id]}, content_type='application/json')
        self.assertEqual(r.status_code, 400)

    def test_committees_bulk(self):
        c1 = Committee.objects.create(name='Social')
        c2 = Committee.objects.create(name='Rush')
        url = reverse('admin_v2_committees_bulk')
        self.assertEqual(self.client.get(url).status_code, 405)
        r = self.client.post(url, {'active': '0', 'ids': [c1.pk, c2.pk, 'x']})
        self.assertRedirects(r, reverse('admin_v2_manage_committees'), fetch_redirect_response=False)
        self.assertFalse(Committee.objects.filter(is_active=True).exists())
        self.assertTrue(ActivityLog.objects.filter(action_type='committee_status_changed').exists())
        self.client.post(url, {'active': '1', 'ids': [c2.pk]})
        self.assertEqual(list(Committee.objects.filter(is_active=True)), [c2])
        anon = Client().post(url, {'active': '1', 'ids': [c1.pk]})
        self.assertEqual(anon.status_code, 302)
        self.assertFalse(Committee.objects.get(pk=c1.pk).is_active)

    def test_api_tokens_bulk_rechecks_status(self):
        active = APIToken.objects.create(user=self.a, key='a' * 64, name='A', status='active')
        pending = APIToken.objects.create(user=self.b, key='b' * 64, name='B', status='pending')
        url = reverse('admin_api_tokens_bulk')
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.post(url, {'action': 'revoke', 'ids': [active.pk, pending.pk]})
        active.refresh_from_db(); pending.refresh_from_db()
        self.assertEqual(active.status, 'revoked')
        self.assertEqual(active.revoked_by, self.admin)
        self.assertEqual(pending.status, 'pending')  # not active: skipped
        self.client.post(url, {'action': 'reject', 'ids': [pending.pk, active.pk]})
        active.refresh_from_db(); pending.refresh_from_db()
        self.assertEqual(pending.status, 'rejected')
        self.assertEqual(active.status, 'revoked')
        member = Client()
        member.force_login(make_user('id-nonadmin'))
        other = APIToken.objects.create(user=self.a, key='c' * 64, name='C', status='active')
        self.assertEqual(member.post(url, {'action': 'revoke', 'ids': [other.pk]}).status_code, 403)
        other.refresh_from_db()
        self.assertEqual(other.status, 'active')


class IdentityTemplateRulesTests(SimpleTestCase):
    """Prod CSP: the new templates have no style=, no inline <script>, no on*= handlers."""

    def test_no_inline_style_or_script(self):
        for name in OURS:
            text = (CONSOLE / name).read_text()
            with self.subTest(template=name):
                self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\sstyle=', text))
                for tag in re.findall(r'<script\b[^>]*>', text):
                    self.assertIn('src=', tag)
                self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\bon[a-z]+=', text))
                self.assertNotIn('json_script', text)

    def test_scripts_have_no_hardcoded_admin_paths(self):
        root = reverse('admin_v2_dashboard').strip('/').split('/')[0]  # the admin root segment
        js = Path(settings.BASE_DIR) / 'static' / 'js' / 'admin_v2'
        for name in ('two_factor.js', 'api_tokens.js', 'login_security.js', 'edit_profile.js'):
            with self.subTest(script=name):
                self.assertNotIn(f"'/{root}/", (js / name).read_text())
