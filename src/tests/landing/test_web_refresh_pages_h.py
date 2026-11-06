"""
The website refresh (src/ui_refresh.py) on signing in and account security —
Sign in (with passkey sign-in), password reset (request, sent, set new,
complete), Change Password and the forced change, two-factor (setup, verify,
disable, backup codes, regenerate, recovery), Active Sessions, Delete Account
— and on My Work (/legislation/history/), which the member batch missed.

What must hold:
* the refresh reaches signed-out visitors (by the flag alone, and by the
  ?ui=refresh preview), so the sign-in pages follow it;
* off: the old markup (no page stylesheet, no rf- classes);
* on: the page stylesheet and the page class, with every form, field name,
  autocomplete value, hidden field, error / rate-limit message, CSRF field,
  passkey hook and link the pages had — and signing in, resetting a
  password, changing it and verifying a code still work with it on;
* the 2FA key and QR are rendered exactly as before;
* the templates' switches only add classes or put an include in front of an
  unchanged block (strip them and no `ui_refresh` or `rf-` is left), and the
  new files carry no inline script or style (CSP).
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django_otp.plugins.otp_totp.models import TOTPDevice

from src.models import Announcement, Legislation, ParliamentUser, UserSession
from src.models.announcements import AnnouncementPoll
from src.models_feature_flags import FeatureFlag
from src.tests.security.test_two_factor import generate_totp

PAGE_CSS = 'css/refresh-pages/auth.css'
PASSWORD = 'refresh-pages-h-Secret-12345!'


def make_user(uid, **kwargs):
    defaults = dict(name=f'Member {uid}', username=f'user{uid}', member_type='Member', member_status='Active',
                    email=f'user{uid}@example.test', onboarding_complete=True)
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password(PASSWORD)
    user.save()
    return user


def set_flag(on):
    FeatureFlag.objects.update_or_create(
        name='web_refresh',
        defaults={'display_name': 'Website Refresh', 'description': 'test', 'category': 'ui', 'is_enabled': on},
    )
    cache.clear()


class RefreshBase(TestCase):
    def setUp(self):
        cache.clear()
        self.member = make_user('rph-1', name='Avery Lindqvist')

    def get(self, client, url, status=200):
        response = client.get(url)
        self.assertEqual(response.status_code, status, url)
        return response.content.decode()

    def off_and_on(self, client, url, page_class, hooks, status=200):
        """Off: no stylesheet or page class. On: both. Either way: every hook."""
        html = self.get(client, url, status)
        self.assertNotIn(PAGE_CSS, html)
        self.assertNotIn(page_class, html)
        self.assertNotRegex(html, r'class="[^"]*\brf-auth\b')
        for hook in hooks:
            self.assertIn(hook, html, f'{url} (off): {hook}')
        set_flag(True)
        html = self.get(client, url, status)
        self.assertIn(PAGE_CSS, html)
        self.assertRegex(html, r'class="[^"]*\b%s\b' % re.escape(page_class))
        for hook in hooks:
            self.assertIn(hook, html, f'{url} (on): {hook}')
        set_flag(False)
        return html


# Signed out ------------------------------------------------------------------------

class SignedOutPagesTests(RefreshBase):

    LOGIN_HOOKS = [
        'name="username" id="username" required autocomplete="username"',
        'name="password" id="password" required autocomplete="current-password"',
        'csrfmiddlewaretoken', 'id="passkey-btn"', 'id="passkey-error"',
        'id="theme-toggle"', 'id="theme-toggle-light-icon"', 'id="theme-toggle-dark-icon"',
    ]

    def login_hooks(self):
        return self.LOGIN_HOOKS + [
            reverse('passkey_authenticate_begin'), reverse('passkey_authenticate_complete') + "' + window.location.search",
            reverse('password_reset'), reverse('privacy_policy'), reverse('support'), reverse('terms'),
        ]

    def test_login_follows_the_flag_signed_out(self):
        html = self.off_and_on(Client(), reverse('login'), 'rf-login', self.login_hooks())
        self.assertIn('rf-auth-card', html)
        self.assertIn('rf-auth-secondary', html)  # the passkey button

    def test_login_follows_the_preview_cookie_signed_out(self):
        client = Client()
        html = self.get(client, reverse('login') + '?ui=refresh')
        self.assertIn('rf-auth rf-login', html)
        self.assertIn(PAGE_CSS, html)
        html = self.get(client, reverse('login'))  # the cookie sticks
        self.assertIn('rf-auth rf-login', html)
        html = self.get(client, reverse('login') + '?ui=classic')
        self.assertNotIn('rf-login', html)

    def test_rate_limit_message_is_shown_with_the_refresh_on(self):
        set_flag(True)
        html = self.get(Client(), reverse('login') + '?rl=15')
        self.assertIn('rf-auth-alert is-danger', html)
        self.assertIn('Too many failed login attempts. Please try again in 15 minutes.', html)

    def test_signing_in_works_with_the_refresh_on(self):
        set_flag(True)
        client = Client()
        self.get(client, reverse('login'))
        response = client.post(reverse('login'), {'username': self.member.username, 'password': 'wrong-password'})
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', client.session)
        response = client.post(reverse('login') + '?next=/calendar/',
                               {'username': self.member.username, 'password': PASSWORD})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/calendar/')
        self.assertEqual(client.session['_auth_user_id'], str(self.member.pk))

    def test_password_reset_request_and_sent(self):
        client = Client()
        self.off_and_on(client, reverse('password_reset'), 'rf-reset',
                        ['name="email"', 'id="id_email"', 'type="email"', 'csrfmiddlewaretoken', reverse('login')])
        self.off_and_on(client, reverse('password_reset_done'), 'rf-reset', ['Check Your Email', reverse('login')])
        set_flag(True)
        response = client.post(reverse('password_reset'), {'email': self.member.email})
        self.assertRedirects(response, reverse('password_reset_done'), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)

    def test_password_reset_confirm_and_complete_work_with_the_refresh_on(self):
        client = Client()
        uid = urlsafe_base64_encode(force_bytes(self.member.pk))
        token = default_token_generator.make_token(self.member)
        set_flag(True)
        response = client.get(reverse('password_reset_confirm', args=[uid, token]), follow=True)
        html = response.content.decode()
        for hook in ['name="new_password1"', 'id="id_new_password1"', 'name="new_password2"',
                     'id="id_new_password2"', 'csrfmiddlewaretoken', 'rf-auth rf-reset', PAGE_CSS]:
            self.assertIn(hook, html)
        set_url = response.redirect_chain[-1][0]
        new = 'A-brand-new-Passw0rd!'
        response = client.post(set_url, {'new_password1': new, 'new_password2': new})
        self.assertRedirects(response, reverse('password_reset_complete'), fetch_redirect_response=False)
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password(new))
        html = self.get(client, reverse('password_reset_complete'))
        self.assertIn('rf-auth rf-reset', html)
        self.assertIn(reverse('login'), html)

    def test_password_reset_invalid_link(self):
        html = self.off_and_on(Client(), reverse('password_reset_confirm', args=['MQ', 'bad-token']), 'rf-reset',
                               ['Invalid Reset Link', reverse('password_reset')])
        self.assertIn('rf-auth-alert is-danger', html)


# Signed in: passwords ----------------------------------------------------------------

class PasswordPagesTests(RefreshBase):

    def test_change_password_keeps_its_fields_and_works(self):
        client = Client()
        client.force_login(self.member)
        hooks = ['name="old_password"', 'autocomplete="current-password"', 'name="new_password1"',
                 'autocomplete="new-password"', 'name="new_password2"', 'csrfmiddlewaretoken']
        self.off_and_on(client, reverse('change_password'), 'rf-pwchange', hooks)
        set_flag(True)
        response = client.post(reverse('change_password'), {'old_password': 'not-it', 'new_password1': 'x',
                                                            'new_password2': 'y'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('rf-pwchange', response.content.decode())
        self.member.refresh_from_db()
        self.assertTrue(self.member.check_password(PASSWORD))

    def test_forced_password_change(self):
        self.member.force_password_change = True
        self.member.save()
        client = Client()
        client.force_login(self.member)
        self.off_and_on(client, reverse('forced_password_change'), 'rf-pwforced',
                        ['name="old_password"', 'name="new_password1"', 'name="new_password2"',
                         'csrfmiddlewaretoken', 'Password Requirements', 'At least 9 characters long'])


# Signed in: two-factor -----------------------------------------------------------------

class TwoFactorPagesTests(RefreshBase):

    def enroll(self, user):
        device = TOTPDevice.objects.create(user=user, name='default', confirmed=True)
        return device

    def verified_client(self, user):
        """Signed in and past the second factor (django_otp's session key)."""
        device = self.enroll(user)
        client = Client()
        client.force_login(user)
        session = client.session
        session['otp_device_id'] = device.persistent_id
        session.save()
        return client

    def test_setup_renders_the_key_and_qr_as_before(self):
        from src.view.two_factor import manual_entry_key
        client = Client()
        client.force_login(self.member)
        self.get(client, reverse('two_factor_setup'))  # creates the pending device
        device = TOTPDevice.objects.get(user=self.member, confirmed=False)
        key = manual_entry_key(device)
        hooks = ['src="/accounts/two-factor/qrcode/"', key, f'Account name: {self.member.username}',
                 'name="token"', 'maxlength="6"', 'pattern="[0-9]{6}"', 'autocomplete="one-time-code"',
                 'csrfmiddlewaretoken', reverse('two_factor_dismiss')]
        html = self.off_and_on(client, reverse('two_factor_setup'), 'rf-2fa-setup', hooks)
        # The key's element holds exactly what it held before.
        self.assertRegex(html, r'rf-2fa-key">\s*%s\s*</div>' % re.escape(key))
        self.assertRegex(key, r'^[A-Z2-7]{4}( [A-Z2-7]{1,4})+$')

    def test_setup_email_gate(self):
        self.member.email = ''
        self.member.save()
        client = Client()
        client.force_login(self.member)
        self.off_and_on(client, reverse('two_factor_setup'), 'rf-2fa-setup',
                        ['name="email"', 'type="email"', 'Email Required:', reverse('two_factor_dismiss')])

    def test_verify_keeps_its_hooks_and_a_code_still_verifies(self):
        device = self.enroll(self.member)
        client = Client()
        client.force_login(self.member)
        hooks = ['id="verify-form"', 'id="token"', 'name="token"', 'maxlength="8"', 'id="mode-hint"',
                 'name="remember_device"', 'csrfmiddlewaretoken', reverse('two_factor_recovery_request'),
                 "document.getElementById('token').addEventListener"]
        self.off_and_on(client, reverse('two_factor_verify'), 'rf-2fa-verify', hooks)
        set_flag(True)
        response = client.post(reverse('two_factor_verify'), {'token': generate_totp(device)})
        self.assertEqual(response.status_code, 302)
        self.assertIn('otp_device_id', client.session)

    def test_disable_and_its_rate_limit_message(self):
        client = self.verified_client(self.member)
        self.off_and_on(client, reverse('two_factor_disable'), 'rf-2fa-disable',
                        ['name="password"', 'autocomplete="current-password"', 'csrfmiddlewaretoken',
                         reverse('profile')])
        set_flag(True)
        response = client.post(reverse('two_factor_disable'), {'password': 'wrong'})
        self.assertEqual(response.status_code, 403)
        html = response.content.decode()
        self.assertIn('Incorrect password. Two-Factor Authentication was NOT disabled.', html)
        self.assertIn('rf-2fa-disable', html)
        cache.set(f'cred_change_attempts_{self.member.pk}', 99, 60)
        response = client.post(reverse('two_factor_disable'), {'password': PASSWORD})
        self.assertEqual(response.status_code, 429)
        self.assertIn('Too many failed attempts. Try again in 15 minutes.', response.content.decode())
        self.assertTrue(TOTPDevice.objects.filter(user=self.member).exists())

    def test_backup_codes_reveal_and_regenerate(self):
        client = self.verified_client(self.member)
        set_flag(True)
        session = client.session
        session['new_backup_codes'] = ['3F9A1C2D', 'B71E04AA']
        session.save()
        html = self.get(client, reverse('two_factor_backup_codes_reveal'))
        for hook in ['rf-auth rf-2fa-codes', 'rf-2fa-codes-grid', 'id="codes-block"', '3F9A1C2D', 'B71E04AA',
                     'id="copy-btn"', 'id="js-print-btn"', reverse('home'), PAGE_CSS]:
            self.assertIn(hook, html)
        set_flag(False)
        self.off_and_on(client, reverse('two_factor_regenerate_backup_codes'), 'rf-2fa-regen',
                        ['csrfmiddlewaretoken', 'Generate New Backup Codes', reverse('profile')])

    def test_recovery_request_states(self):
        client = Client()
        client.force_login(self.member)
        self.off_and_on(client, reverse('two_factor_recovery_request'), 'rf-2fa-recovery',
                        ['Send Recovery Email', 'csrfmiddlewaretoken', self.member.email,
                         reverse('two_factor_verify')])
        set_flag(True)
        cache.set(f'2fa_recovery_{self.member.pk}', 99, 60)
        response = client.post(reverse('two_factor_recovery_request'))
        html = response.content.decode()
        self.assertIn('rf-2fa-recovery', html)
        self.assertIn("You've requested too many recovery emails", html)

    def test_recovery_link_invalid(self):
        client = Client()
        client.force_login(self.member)
        self.off_and_on(client, reverse('two_factor_recovery_confirm', args=['MQ', 'bad-token']), 'rf-2fa-recovery',
                        ['Link Expired or Invalid', reverse('two_factor_recovery_request')], status=400)


# Signed in: sessions, delete account ---------------------------------------------------

class AccountPagesTests(RefreshBase):

    def test_sessions_keep_every_script_hook(self):
        client = Client()
        client.force_login(self.member)
        from django.contrib.sessions.backends.db import SessionStore
        keys = []
        for browser, system in [('Safari', 'iOS'), ('Firefox', 'Windows')]:
            store = SessionStore()
            store['x'] = 1
            store.create()
            keys.append(store.session_key)
            UserSession.objects.create(user=self.member, session_key=store.session_key, ip_address='10.0.0.2',
                                       device_type='mobile', browser=browser, operating_system=system)
        hooks = ['id="js-revoke-all-sessions"', 'js-revoke-session', f'data-session-key="{keys[0]}"',
                 f'id="session-{keys[1]}"', 'id="mobile-devices"', "'/account/sessions/revoke-all/'",
                 'function revokeSession(', reverse('preferences'), 'Safari on iOS']
        html = self.off_and_on(client, reverse('session_list'), 'rf-sessions', hooks)
        self.assertIn('rf-sess-list', html)
        self.assertIn('class="mt-10 rf-sess-devices" id="mobile-devices"', html)

    def test_delete_account_keeps_its_form(self):
        client = Client()
        client.force_login(self.member)
        hooks = ['js-vote-form', 'data-confirm="Delete your account?', 'name="password"',
                 'autocomplete="current-password"', 'name="confirm"', 'js-passkey-error', 'csrfmiddlewaretoken',
                 reverse('preferences')]
        self.off_and_on(client, reverse('delete_account'), 'rf-delete-acct', hooks)


# My Work -------------------------------------------------------------------------------------

class MyWorkTests(RefreshBase):

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.passed = Legislation.objects.create(
            title='Kitchen Renovation Fund', description='Fund the kitchen.', posted_by=self.member,
            available_at=now, vote_mode='percentage', required_percentage='67', passed=True,
            voting_closed=True, status='passed', historical_yes_votes=30, historical_no_votes=5)
        self.failed = Legislation.objects.create(
            title='Quiet Hours Amendment', description='Change quiet hours.', posted_by=self.member,
            available_at=now, vote_mode='piecewise', passed=False, voting_closed=True, status='failed',
            admin_note='Bring it back next term.')
        ann = Announcement.objects.create(title='Spring formal planning', content='Vote.', posted_by=self.member)
        self.poll = AnnouncementPoll.objects.create(announcement=ann, title='Formal date preference',
                                                    is_open=True, created_by=self.member)
        from src.models import LegislationDraft
        self.draft = LegislationDraft.objects.create(author=self.member, title='Study Hours Policy')
        self.client = Client()
        self.client.force_login(self.member)

    def test_my_work_off_and_on(self):
        hooks = [
            'id="tab-btn-legislation"', 'id="tab-btn-polls"', 'id="tab-btn-drafts"', 'id="section-legislation"',
            'id="section-polls"', 'id="section-drafts"', 'id="draft-new-toggle"', 'id="draft-new-body"',
            'id="draft-new-chevron"', 'function showTab(', '?status=passed',
            reverse('legislation_detail', args=[self.passed.id]), reverse('submit_new_version', args=[self.passed.id]),
            reverse('reopen_legislation', args=[self.failed.id]), reverse('edit_legislation', args=[self.failed.id]),
            reverse('poll_results', args=[self.poll.announcement.id]),
            reverse('create_or_edit_poll', args=[self.poll.announcement.id]),
            reverse('edit_announcement', args=[self.poll.announcement.id]),
            reverse('create_legislation_draft'), reverse('edit_legislation_draft', args=[self.draft.id]),
            reverse('delete_legislation_draft', args=[self.draft.id]), 'js-confirm-submit',
            'data-confirm-msg="Delete the draft &quot;Study Hours Policy&quot;? This cannot be undone."',
            'csrfmiddlewaretoken', 'Kitchen Renovation Fund', 'Quiet Hours Amendment', 'Formal date preference',
            'Officer note:', 'Bring it back next term.', 'Submit New Version', 'Reopen', 'View Details',
        ]
        html = self.get(self.client, reverse('view_legislation_history'))
        self.assertNotIn('rf-work', html)
        self.assertNotIn(PAGE_CSS, html)
        self.assertIn('<!-- Status Strip -->', html)
        for hook in hooks:
            self.assertIn(hook, html)
        set_flag(True)
        html = self.get(self.client, reverse('view_legislation_history'))
        self.assertIn('rf-mp rf-work"', html)
        self.assertIn(PAGE_CSS, html)
        self.assertNotIn('<!-- Status Strip -->', html)
        for hook in hooks:
            self.assertIn(hook, html)
        for marker in ['rf-card rf-work-list', 'rf-row rf-work-row', '<span class="rf-chip is-success">Passed</span>',
                       '<span class="rf-chip is-danger">Failed</span>', ' rf-work-tabs"', ' rf-work-filters"',
                       'class="rf-work-bar is-passed" max="100"']:
            self.assertIn(marker, html)

    def test_deleting_a_draft_with_the_refresh_on_still_works(self):
        from src.models import LegislationDraft
        set_flag(True)
        self.assertIn(reverse('delete_legislation_draft', args=[self.draft.id]),
                      self.get(self.client, reverse('view_legislation_history') + '?tab=drafts'))
        response = self.client.post(reverse('delete_legislation_draft', args=[self.draft.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(LegislationDraft.objects.filter(pk=self.draft.pk).exists())


# The templates themselves ----------------------------------------------------------------

class OffStateIsTheOldMarkupTests(TestCase):
    """Strip the switches out and nothing of the refresh is left: each one
    either adds classes (`{% if ui_refresh %} rf-…{% endif %}`) or puts an
    include in front of the unchanged old block, closed by `{# /rf #}`."""

    TEMPLATES = ['templates/registration/login.html', 'templates/registration/password_reset.html',
                 'templates/registration/password_reset_done.html',
                 'templates/registration/password_reset_confirm.html',
                 'templates/registration/password_reset_complete.html', 'templates/change_password.html',
                 'templates/forced_password_change.html', 'templates/two_factor/setup.html',
                 'templates/two_factor/verify.html', 'templates/two_factor/disable.html',
                 'templates/two_factor/backup_codes_reveal.html', 'templates/two_factor/regenerate_backup_codes.html',
                 'templates/two_factor/recovery_request.html', 'templates/two_factor/recovery_confirm.html',
                 'templates/two_factor/recovery_confirm_invalid.html', 'templates/account/sessions.html',
                 'templates/account/delete_account.html', 'templates/account/delete_account_done.html',
                 'templates/legislation_history.html']
    NEW = ['templates/my_work_refresh/_bills.html', 'templates/my_work_refresh/_polls.html',
           'templates/my_work_refresh/_drafts.html', 'static/css/refresh-pages/auth.css']
    #: Left alone: the emails go to inboxes, not through base.html.
    UNTOUCHED = ['templates/registration/password_reset_email.html',
                 'templates/registration/password_reset_email.txt']

    @staticmethod
    def strip_switches(text):
        text = re.sub(r'\{% if ui_refresh %\}\{% include "[^"]+"[^%]*%\}\{% else %\}', '', text)
        text = text.replace('{% endif %}{# /rf #}', '')
        return re.sub(r'\{% if ui_refresh %\}( [\w -]+)\{% endif %\}', '', text)

    def test_every_switch_wraps_unchanged_lines(self):
        base = Path(settings.BASE_DIR)
        for name in self.TEMPLATES:
            with self.subTest(template=name):
                text = (base / name).read_text(encoding='utf-8')
                self.assertIn('{% if ui_refresh %}', text)
                stripped = self.strip_switches(text)
                self.assertNotIn('ui_refresh', stripped)
                self.assertNotRegex(stripped, r'\brf-')
                self.assertNotIn('{# /rf #}', stripped)

    def test_switches_only_touch_class_attributes(self):
        base = Path(settings.BASE_DIR)
        for name in self.TEMPLATES:
            with self.subTest(template=name):
                text = (base / name).read_text(encoding='utf-8')
                for match in re.finditer(r'(\w[\w-]*)="[^"]*\{% if ui_refresh %\}', text):
                    self.assertEqual(match.group(1), 'class')

    def test_emails_are_untouched(self):
        base = Path(settings.BASE_DIR)
        for name in self.UNTOUCHED:
            self.assertNotIn('ui_refresh', (base / name).read_text(encoding='utf-8'))

    def test_new_files_exist_and_carry_no_inline_script_or_style(self):
        base = Path(settings.BASE_DIR)
        for name in self.NEW:
            with self.subTest(file=name):
                text = (base / name).read_text(encoding='utf-8')
                if name.endswith('.html'):
                    self.assertNotIn('<script', text)
                    self.assertNotIn('<style', text)
                    self.assertNotIn('style="', text)
                    self.assertNotRegex(text, r'\son[a-z]+=')
                else:
                    css = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
                    self.assertNotRegex(css, r'!\s*important')
                    self.assertNotIn('@import', css)
                    self.assertNotIn('http', css)

    def test_base_links_the_page_stylesheet_only_with_the_switch(self):
        text = (Path(settings.BASE_DIR) / 'templates/base.html').read_text(encoding='utf-8')
        self.assertIn("{% if ui_refresh %}<link rel=\"stylesheet\" href=\"{% static_v '" + PAGE_CSS + "' %}\">{% endif %}",
                      text)
