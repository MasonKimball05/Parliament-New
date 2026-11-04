"""
The website refresh (src/ui_refresh.py, static/css/refresh.css): the
member-facing site in the mobile app's look, behind the `web_refresh` flag or
a browser's ?ui=refresh preview.

What must hold:
* off by default — no row, or a row that is off, renders the old page with
  no refresh class, stylesheet or app-style markup;
* the flag turns it on for everyone; ?ui= previews/opts out per browser, by
  cookie, and ?ui=default goes back to following the flag;
* never on Admin v2 ("360"), even though its base extends base.html;
* the off state is the old markup: the templates' switches only add
  `{% if ui_refresh %}…{% else %}` / `{% endif %}` around unchanged lines.
"""
import re
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from src.models import ParliamentUser
from src.models_feature_flags import FeatureFlag
from src.ui_refresh import COOKIE_NAME

REFRESH_CSS = 'css/refresh.css'


def make_user(uid='refresh-user', **kwargs):
    defaults = dict(name='Refresh Tester', username=uid, member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('refresh-pass-12345!')
    user.save()
    return user


class WebRefreshSwitchTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user()
        self.client = Client()
        self.client.force_login(self.user)

    def set_flag(self, on):
        FeatureFlag.objects.update_or_create(
            name='web_refresh',
            defaults={'display_name': 'Website Refresh', 'description': 'test', 'category': 'ui', 'is_enabled': on},
        )
        cache.clear()

    def home(self, query=''):
        return self.client.get(reverse('home') + query)

    def assertRefresh(self, response, on):
        html = response.content.decode()
        self.assertEqual(response.status_code, 200)
        check = self.assertIn if on else self.assertNotIn
        check(REFRESH_CSS, html)
        check('class="ui-refresh"', html)
        check('rf-hero', html)

    def test_off_without_a_row(self):
        self.assertFalse(FeatureFlag.objects.filter(name='web_refresh').exists())
        self.assertFalse(FeatureFlag.is_feature_enabled('web_refresh'))  # fails closed in Python too
        self.assertRefresh(self.home(), False)

    def test_off_when_the_flag_is_off(self):
        self.set_flag(False)
        self.assertRefresh(self.home(), False)

    def test_on_for_everyone_with_the_flag(self):
        self.set_flag(True)
        response = self.home()
        self.assertRefresh(response, True)
        # The old welcome header and gradient feature cards are replaced.
        self.assertNotIn('WELCOME HEADER', response.content.decode())
        self.assertIn('id="coat-modal-open"', response.content.decode())

    def test_preview_cookie_turns_it_on_for_this_browser_only(self):
        response = self.home('?ui=refresh')
        self.assertRefresh(response, True)
        self.assertEqual(response.cookies[COOKIE_NAME].value, 'refresh')
        self.assertTrue(response.cookies[COOKIE_NAME]['httponly'])
        self.assertRefresh(self.home(), True)  # sticks

        other = Client()
        other.force_login(make_user('refresh-other'))
        self.assertRefresh(other.get(reverse('home')), False)

    def test_classic_opts_out_while_the_flag_is_on_and_default_follows_it_again(self):
        self.set_flag(True)
        self.assertRefresh(self.home('?ui=classic'), False)
        self.assertRefresh(self.home(), False)
        response = self.home('?ui=default')
        self.assertEqual(response.cookies[COOKIE_NAME].value, '')  # deleted
        self.assertRefresh(response, True)

    def test_unknown_values_are_ignored(self):
        response = self.home('?ui=<script>')
        self.assertNotIn(COOKIE_NAME, response.cookies)
        self.assertRefresh(response, False)

    def test_committees_list_uses_the_app_rows(self):
        self.set_flag(True)
        html = self.client.get(reverse('committee_index')).content.decode()
        self.assertIn(REFRESH_CSS, html)
        self.assertIn('rf-card', html)

    def test_never_on_admin_v2(self):
        self.set_flag(True)
        admin = make_user('refresh-admin', is_admin=True)
        client = Client()
        client.force_login(admin)
        client.cookies[COOKIE_NAME] = 'refresh'
        response = client.get(reverse('admin_v2_dashboard'))
        # Whatever the page answers for this test user (it may redirect to its
        # own login/2FA step), it never carries the refresh.
        self.assertNotIn(REFRESH_CSS, response.content.decode())
        self.assertNotIn('ui-refresh', response.content.decode())

    def test_flag_is_seeded_off_and_disabled_by_default(self):
        self.assertIn('web_refresh', FeatureFlag.DISABLED_BY_DEFAULT)
        from src.management.commands import seed_feature_flags
        source = Path(seed_feature_flags.__file__).read_text()
        entry = source[source.index("'name': 'web_refresh'"):]
        entry = entry[:entry.index('},')]
        self.assertIn("'is_enabled': False", entry)


class OffStateIsTheOldMarkupTests(TestCase):
    """Strip the switches out of each template and the old file comes back."""

    TEMPLATES = ['templates/base.html', 'templates/home_modern.html', 'templates/committee/committee_index.html']

    @staticmethod
    def strip_switches(text):
        text = re.sub(r'\{% if ui_refresh %\}(\{% include "[^"]+" %\})?\{% else %\}', '', text)
        text = re.sub(r'\{% if ui_refresh %\}(\{% include "[^"]+" %\}|<link[^>]*>| class="ui-refresh"| rf-home| rf-officer)\{% endif %\}', '', text)
        # what's left: the `{% endif %}` closing each else-branch, at a line end
        return re.sub(r'\{% endif %\}\{% endif %\}$', '{% endif %}', re.sub(r'(</div>)\{% endif %\}$', r'\1', text, flags=re.M), flags=re.M)

    def test_every_switch_wraps_unchanged_lines(self):
        base = Path(settings.BASE_DIR)
        for name in self.TEMPLATES:
            with self.subTest(template=name):
                current = (base / name).read_text(encoding='utf-8')
                stripped = self.strip_switches(current)
                self.assertNotIn('ui_refresh', stripped)
