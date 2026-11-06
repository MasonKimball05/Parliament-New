"""
The Admin v2 console look (`admin_console_ui`, src/admin_console.py).

Covers: the switch (console frame on, classic layout off, both for every
page), the console nav keeping every page and its gate, breadcrumbs,
favorites and recently visited, the slide-out panel fragment (same gate as
the full page), the Members list and its Actions, the console home, search
naming, the seed, and the CSP rules for the new templates.
"""
import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import Client, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from src.admin_console import (
    FLAG, PREFS_SECTION, RECENT_SESSION_KEY, favorite_names, signins_last_7_days,
)
from src.admin_console_icons import ICON_PATHS
from src.admin_v2_nav import (
    CONSOLE_GROUPS, CONSOLE_HOME, CONSOLE_ICONS, NAV, console_page, console_page_names, search_pages,
)
from src.models import LoginAlert, LoginHistory, UserPreferences
from src.models_feature_flags import FeatureFlag
from src.tests.admin_v2.base import AdminV2TestCase, make_user

CONSOLE_TEMPLATES = Path(settings.BASE_DIR) / 'templates' / 'admin_v2' / 'console'


def nav_names():
    return [name for _t, items in NAV for _l, name, _a, _k in items]


class ConsoleNavTests(SimpleTestCase):
    """The console groups NAV's pages differently; it must not lose or add any."""

    def test_every_nav_page_is_in_the_console_exactly_once(self):
        console = console_page_names()
        self.assertEqual(len(console), len(set(console)), 'a page is listed twice')
        self.assertEqual(sorted(console), sorted(nav_names()))
        self.assertEqual(console[0], CONSOLE_HOME)

    def test_every_page_has_a_known_icon(self):
        for name in console_page_names():
            with self.subTest(page=name):
                self.assertIn(CONSOLE_ICONS.get(name), ICON_PATHS)
        for _title, icon, _names in CONSOLE_GROUPS:
            self.assertIn(icon, ICON_PATHS)

    def test_detail_pages_belong_to_their_list(self):
        self.assertEqual(console_page('admin_v2_member'), ('Identity', 'admin_v2_manage_users'))
        self.assertEqual(console_page('admin_v2_email_log_detail'), ('Messaging', 'admin_v2_email_logs'))
        self.assertEqual(console_page(CONSOLE_HOME), (None, CONSOLE_HOME))
        self.assertEqual(console_page('home'), (None, None))

    def test_search_names_pages_the_console_way(self):
        hit = search_pages('members', console=True)[0]
        self.assertEqual((hit['label'], hit['group']), ('Members', 'Identity'))
        self.assertEqual(search_pages('members')[0]['group'], 'People')
        # Either look's group name finds the page.
        self.assertIn('Members', [p['label'] for p in search_pages('identity members')])


class SwitchTests(AdminV2TestCase):

    def test_flag_on_is_the_console_frame(self):
        html = self.client.get(reverse('admin_v2_manage_users')).content.decode()
        self.assertIn('class="ac ac-shell"', html)
        self.assertIn('css/admin-console.css', html)
        for group in ('Identity', 'Security', 'Messaging', 'Operations', 'Reports', 'Developer'):
            self.assertIn(f'<p class="ac-nav-title">{group}</p>', html)
        self.assertRegex(html, r'class="ac-nav-link is-active" aria-current="page">.*<span>Members</span>')
        # The ⌘K palette keeps the ids palette.js reads, and its script.
        for needle in ('id="admin-v2-palette"', 'id="admin-v2-search-input"', 'id="admin-v2-search-results"',
                       'js/admin_v2/palette.js', 'js/admin_v2/console.js', 'js/admin_v2/panel.js'):
            self.assertIn(needle, html)
        self.assertIn(reverse('admin_v2_logout'), html)  # Exit admin
        # The site's own nav and footer give way to the console's top bar.
        self.assertNotIn('id="mobile-menu-button"', html)
        self.assertNotIn('<footer', html)
        self.assertNotIn('onclick=', html)

    def test_flag_off_is_the_classic_layout(self):
        self.set_console(False)
        html = self.client.get(reverse('admin_v2_manage_users')).content.decode()
        self.assertNotIn('ac-shell', html)
        self.assertNotIn('admin-console.css', html)
        self.assertIn('aria-current="page">Members</a>', html)
        self.assertIn('👥 Manage Users', html)  # the classic Members page
        self.assertIn('<footer', html)

    def test_a_missing_row_means_on_and_the_seed_turns_it_on(self):
        self.assertNotIn(FLAG, FeatureFlag.DISABLED_BY_DEFAULT)
        self.assertFalse(FeatureFlag.objects.filter(name=FLAG).exists())
        self.assertTrue(FeatureFlag.is_feature_enabled(FLAG))
        from django.core.management import call_command
        call_command('seed_feature_flags', stdout=mock.MagicMock())
        flag = FeatureFlag.objects.get(name=FLAG)
        self.assertTrue(flag.is_enabled)
        self.assertEqual(flag.category, 'admin')

    def test_every_page_renders_in_both_looks_with_the_same_gate(self):
        """Each console page answers the same either way (no 500 in the frame),
        and a member who isn't on the admin-v2 allowlist is still turned away."""
        outsider = make_user('av2-outsider', is_admin=True)
        other = Client()
        other.force_login(outsider)
        for name in console_page_names():
            url = reverse(name)
            with self.subTest(page=name):
                self.set_console(True)
                on = self.client.get(url)
                self.set_console(False)
                off = self.client.get(url)
                self.assertEqual(on.status_code, off.status_code)
                self.assertLess(on.status_code, 500)
                if name.startswith('admin_v2'):
                    self.assertEqual(on.status_code, 200)
                    self.assertEqual(other.get(url).status_code, 302)
        self.set_console(True)

    def test_unconverted_page_sits_inside_the_frame(self):
        html = self.client.get(reverse('admin_v2_backups')).content.decode()
        self.assertIn('class="ac-main admin-v2-content"', html)
        self.assertIn('<span>Operations</span>', html)  # breadcrumb group
        self.assertIn('aria-current="page">Backups</a>', html)


class BreadcrumbTests(AdminV2TestCase):

    def test_member_page_crumbs(self):
        m = make_user('av2-crumb', name='Casey Crumb')
        html = self.client.get(reverse('admin_v2_member', args=[m.user_id])).content.decode()
        crumbs = html[html.index('class="ac-crumbs"'):html.index('</nav>', html.index('class="ac-crumbs"'))]
        self.assertIn('<span>Identity</span>', crumbs)
        self.assertIn(f'href="{reverse("admin_v2_manage_users")}"', crumbs)
        self.assertIn('<li class="ac-crumb-item">Casey Crumb</li>', crumbs)


class FavoritesAndRecentTests(AdminV2TestCase):

    def fav(self, page, json=True):
        headers = {'HTTP_ACCEPT': 'application/json'} if json else {}
        return self.client.post(reverse('admin_v2_console_favorite'), {'page': page}, **headers)

    def test_pin_and_unpin(self):
        r = self.fav('admin_v2_backups')
        self.assertEqual(r.json(), {'ok': True, 'favorite': True, 'page': 'admin_v2_backups',
                                    'label': 'Backups', 'url': reverse('admin_v2_backups')})
        self.assertEqual(favorite_names(self.admin), ['admin_v2_backups'])
        html = self.client.get(reverse('admin_v2_trends')).content.decode()
        fav_group = html[html.index('data-ac-favorites-group'):html.index('</ul>', html.index('data-ac-favorites-group'))]
        self.assertIn(reverse('admin_v2_backups'), fav_group)
        self.assertFalse(self.fav('admin_v2_backups').json()['favorite'])
        self.assertEqual(favorite_names(self.admin), [])

    def test_without_js_it_redirects_back(self):
        r = self.fav('admin_v2_trends', json=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(favorite_names(self.admin), ['admin_v2_trends'])

    def test_refuses_get_unknown_pages_and_outsiders(self):
        self.assertEqual(self.client.get(reverse('admin_v2_console_favorite')).status_code, 405)
        self.assertEqual(self.fav('home').status_code, 400)
        self.assertEqual(self.fav('admin_v2_member').status_code, 400)  # not a console page
        outsider = Client()
        outsider.force_login(make_user('av2-fav-out', is_admin=True))
        r = outsider.post(reverse('admin_v2_console_favorite'), {'page': 'admin_v2_backups'})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(UserPreferences.objects.filter(
            user__user_id='av2-fav-out', prefs__has_key=PREFS_SECTION).exists())

    def test_saving_preferences_keeps_favorites(self):
        from src.forms.users import UserPreferencesForm
        self.fav('admin_v2_backups')
        prefs = UserPreferences.objects.get(user=self.admin)
        form = UserPreferencesForm(data=dict(UserPreferencesForm(instance=prefs).initial), instance=prefs)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        prefs.refresh_from_db()
        self.assertEqual(prefs.prefs[PREFS_SECTION]['favorites'], ['admin_v2_backups'])

    def test_recently_visited(self):
        for name in ('admin_v2_backups', 'admin_v2_trends', 'admin_v2_backups'):
            self.client.get(reverse(name))
        self.assertEqual(self.client.session[RECENT_SESSION_KEY], ['admin_v2_backups', 'admin_v2_trends'])
        html = self.client.get(reverse('admin_v2_dashboard')).content.decode()
        widget = html[html.index('id="w-recent"'):html.index('</section>', html.index('id="w-recent"'))]
        self.assertIn(reverse('admin_v2_trends'), widget)
        # Home isn't "recent"; detail pages count as their list page.
        m = make_user('av2-recent-m')
        self.client.get(reverse('admin_v2_member', args=[m.user_id]))
        self.assertEqual(self.client.session[RECENT_SESSION_KEY][0], 'admin_v2_manage_users')
        self.assertNotIn(CONSOLE_HOME, self.client.session[RECENT_SESSION_KEY])


class PanelTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        self.member = make_user('av2-panel', name='Avery Chairwell', email='avery@example.test')
        self.url = reverse('admin_v2_member', args=[self.member.user_id])

    def test_fragment(self):
        r = self.client.get(self.url, {'panel': '1'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['X-Admin-Panel'], '1')
        html = r.content.decode()
        self.assertNotIn('<html', html)
        self.assertNotIn('<script', html)
        self.assertIn('id="ac-blade-title"', html)
        self.assertIn('Avery Chairwell', html)
        for tab in ('Overview', 'Security', 'Devices', 'Activity'):
            self.assertIn(f'data-ac-tab="{tab.lower()}">{tab}</button>', html)
        self.assertIn(reverse('admin_v2_member_sign_out', args=[self.member.user_id]), html)
        self.assertIn('data-ac-panel-close', html)

    def test_without_panel_it_is_the_full_page(self):
        r = self.client.get(self.url)
        self.assertNotIn('X-Admin-Panel', r)
        self.assertIn('<html', r.content.decode())

    def test_same_gate_as_the_full_page(self):
        anon = Client()
        r = anon.get(self.url, {'panel': '1'})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn('X-Admin-Panel', r)
        outsider = Client()
        outsider.force_login(make_user('av2-panel-out', is_admin=True))
        r = outsider.get(self.url, {'panel': '1'})
        self.assertEqual(r.status_code, 302)
        self.assertNotIn('Avery', r.content.decode())
        # Allowlisted but without the admin-v2 step: back to its login.
        stale = Client()
        stale.force_login(self.admin)
        r = stale.get(self.url, {'panel': '1'})
        self.assertRedirects(r, reverse('admin_v2_login'), fetch_redirect_response=False)

    def test_missing_member_is_404(self):
        self.assertEqual(self.client.get(reverse('admin_v2_member', args=['nobody']), {'panel': '1'}).status_code, 404)


class MembersListTests(AdminV2TestCase):

    def test_list_frame(self):
        from django_otp.plugins.otp_totp.models import TOTPDevice
        alice = make_user('av2-alice', name='Alice Anderson')
        TOTPDevice.objects.create(user=alice, name='phone', confirmed=True)
        make_user('av2-bob', name='Bob Brown')
        html = self.client.get(reverse('admin_v2_manage_users')).content.decode()
        self.assertIn('<h1>Members <span class="ac-count">(3)</span></h1>', html)
        self.assertIn(f'href="{reverse("admin_v2_member", args=["av2-alice"])}" class="ac-row-link" data-ac-panel', html)
        self.assertIn('data-ac-row-select', html)
        self.assertIn('class="ac-btn ac-btn-primary">Add member</a>', html)
        row = html[html.index('Alice Anderson</a>'):]
        row = row[:row.index('</tr>')]
        self.assertIn('<td>On</td>', row)  # 2FA column
        bob = html[html.index('Bob Brown</a>'):]
        self.assertIn('ac-text-warn">Off', bob[:bob.index('</tr>')])
        # Actions: POST forms to Bulk edit's preview, each with a confirm.
        self.assertEqual(html.count('data-ac-bulk-form'), 4)
        self.assertIn(f'action="{reverse("admin_v2_bulk_members")}" data-ac-bulk-form data-confirm=', html)
        self.assertIn('name="bulk_action" value="set_status"', html)

    def test_filters_and_queries_do_not_scale(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        for i in range(3):
            make_user(f'av2-q{i}')
        self.client.get(reverse('admin_v2_manage_users'))
        with CaptureQueriesContext(connection) as few:
            self.client.get(reverse('admin_v2_manage_users'))
        for i in range(10):
            make_user(f'av2-r{i}')
        with CaptureQueriesContext(connection) as many:
            self.client.get(reverse('admin_v2_manage_users'))
        self.assertEqual(len(many), len(few))
        r = self.client.get(reverse('admin_v2_manage_users'), {'status': 'Alumni'})
        self.assertIn('No members match these filters.', r.content.decode())

    def test_an_action_reaches_the_bulk_preview(self):
        m = make_user('av2-bulk-m')
        r = self.client.post(reverse('admin_v2_bulk_members'), {
            'ids': [m.user_id], 'bulk_action': 'set_status', 'value': 'Alumni'})
        self.assertEqual(r.status_code, 200)
        m.refresh_from_db()
        self.assertEqual(m.member_status, 'Active')  # preview only


class HomeTests(AdminV2TestCase):

    def test_widgets(self):
        r = self.client.get(reverse('admin_v2_dashboard'))
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for title in ('Service health', 'Alarms', 'Needs attention', 'Sign-ins, last 7 days', 'Resources',
                      'Recently visited', 'Users', 'Email &amp; notifications', 'Performance',
                      'Recent activity', 'Recent sign-ins'):
            self.assertIn(f'>{title}</h2>', html)
        for service in ('Website', 'Background jobs', 'Push delivery', 'Backups'):
            self.assertIn(service, html)
        self.assertIn('js/admin_v2/home.js', html)
        self.assertIn(reverse('send_test_announcement_email'), html)
        self.assertIn('lockdown_active', r.context)
        content = html[html.index('class="ac-content"'):html.index('id="ac-panel"')]
        self.assertNotIn('<script nonce', content)  # home.js, not inline

    def test_alarms_count_open_alerts(self):
        u = make_user('av2-alarm-u')
        for sev, status in (('critical', 'new'), ('high', 'investigating'), ('low', 'new'), ('high', 'resolved')):
            LoginAlert.objects.create(user=u, alert_type='new_location', severity=sev, status=status,
                                      title='t', description='d', reviewed_at=timezone.now())
        cache.clear()
        r = self.client.get(reverse('admin_v2_dashboard'))
        self.assertEqual(r.context['home']['alarms'], {'alarm': 2, 'warning': 1, 'ok': 1})
        html = r.content.decode()
        self.assertIn('<span class="ac-bell-count is-alarm">3</span>', html)

    def test_signins_last_7_days(self):
        u = make_user('av2-signin')
        now = timezone.now()
        before = signins_last_7_days()
        LoginHistory.objects.create(user=u, status='success', ip_address='127.0.0.1', timestamp=now)
        LoginHistory.objects.create(user=u, status='failed', ip_address='127.0.0.1', timestamp=now)
        old = LoginHistory.objects.create(user=u, status='success', ip_address='127.0.0.1')
        LoginHistory.objects.filter(pk=old.pk).update(timestamp=now - timezone.timedelta(days=30))
        days = signins_last_7_days()
        self.assertEqual(len(days), 7)
        self.assertEqual(sum(d['success'] for d in days) - sum(d['success'] for d in before), 1)
        self.assertEqual(days[-1]['failed'] - before[-1]['failed'], 1)
        self.assertEqual(days[-1]['h'] + days[-1]['y'], 60)

    def test_classic_dashboard_when_off(self):
        self.set_console(False)
        html = self.client.get(reverse('admin_v2_dashboard')).content.decode()
        self.assertIn('Control Panel', html)
        self.assertNotIn('Service health', html)


class SearchTests(AdminV2TestCase):

    def test_console_groups_in_results(self):
        data = self.client.get(reverse('admin_v2_search'), {'q': 'backups'}).json()
        self.assertEqual(data['pages'][0]['group'], 'Operations')
        start = self.client.get(reverse('admin_v2_search')).json()['pages']
        self.assertEqual([p['group'] for p in start], [t for t, _i, _n in CONSOLE_GROUPS])


class ConsoleTemplateRulesTests(SimpleTestCase):
    """The new templates follow the prod CSP: no style=, no inline <script>, no
    inline handlers. (Site-wide guards cover the last two; style= is ours.)"""

    def files(self):
        yield from CONSOLE_TEMPLATES.glob('*.html')
        yield Path(settings.BASE_DIR) / 'templates' / 'admin_v2' / 'member_panel.html'

    def test_no_inline_style_or_script(self):
        for path in self.files():
            text = path.read_text()
            with self.subTest(template=path.name):
                self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\sstyle=', text))
                for tag in re.findall(r'<script\b[^>]*>', text):
                    self.assertIn('src=', tag)
                self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\bon[a-z]+=', text))

