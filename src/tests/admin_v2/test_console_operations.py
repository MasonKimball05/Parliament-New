"""
The Operations and Reports pages in the console look (phase 2 of the Admin v2
"Royal console", `admin_console_ui`): System tools, Scheduled jobs, Backups,
Maintenance, Exports, Page visits, Trends, Chapter health, Site map, URL
explorer, and the Admin v2 sign-in.

For each page: the flag on renders admin_v2/console/<page>.html, the flag off
renders the classic template and nothing of the console page; every form the
classic page has (method, action, field names) is still there in the console
look; the page's JS hooks are kept; the new templates have no inline script or
style. The maintenance switches and the sign-in flow are exercised end to end.
"""
import os
import re
from html.parser import HTMLParser
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.cache import cache
from django.test import Client, SimpleTestCase
from django.urls import reverse
from django.utils import timezone
from django_celery_beat.models import IntervalSchedule, PeriodicTask

from src.models import PageVisit
from src.models.webauthn import WebAuthnCredential
from src.models_feature_flags import FeatureFlag, PageToggle, SiteSetting
from src.tests.admin_v2.base import AdminV2TestCase, make_user

CONSOLE = Path(settings.BASE_DIR) / 'templates' / 'admin_v2' / 'console'

#: url name → (console template, classic template, GET params)
PAGES = {
    'admin_v2_system': ('system_tools', {}),
    'admin_v2_celery_health': ('celery_health', {}),
    'admin_v2_backups': ('backups', {}),
    'admin_v2_maintenance': ('maintenance', {}),
    'admin_v2_exports': ('exports', {}),
    'admin_v2_page_visits': ('page_visits', {}),
    'admin_v2_trends': ('trends', {}),
    'admin_v2_health': ('chapter_health', {}),
    'admin_v2_sitemap': ('sitemap', {}),
    'admin_v2_url_explorer': ('url_explorer', {}),
}
#: Extra renders that show a page's other states (confirm cards, drill-down).
STATES = [
    ('admin_v2_system', {'confirm': 'cache', 'scope': 'content'}),
    ('admin_v2_page_visits', {'path': '/home/'}),
    ('admin_v2_page_visits', {'user': 'visit', 'status': 'Active', 'sort': 'users'}),
    ('admin_v2_sitemap', {'node': '/songbook/', 'confirm': 'page', 'target': 'songbook'}),
    ('admin_v2_health', {'days': '60'}),
]


class _Forms(HTMLParser):
    """Collects (method, action, sorted field names) for every <form>, and
    every element id."""

    def __init__(self):
        super().__init__()
        self.forms, self.ids, self._form = [], set(), None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get('id'):
            self.ids.add(a['id'])
        if tag == 'form':
            self._form = [(a.get('method') or 'get').lower(), a.get('action') or '', set()]
        elif self._form is not None and tag in ('input', 'select', 'textarea', 'button') and a.get('name'):
            self._form[2].add(a['name'])

    def handle_endtag(self, tag):
        if tag == 'form' and self._form is not None:
            method, action, names = self._form
            names.discard('csrfmiddlewaretoken')
            self.forms.append((method, action, tuple(sorted(names))))
            self._form = None


def parse(html):
    p = _Forms()
    p.feed(html)
    return p


class OperationsConsoleTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        cache.clear()
        every = IntervalSchedule.objects.create(every=1, period=IntervalSchedule.HOURS)
        self.job = PeriodicTask.objects.create(name='Prune things', task='tasks.prune_expired_login_lockouts',
                                               interval=every, last_run_at=timezone.now())
        self.flag, _ = FeatureFlag.objects.update_or_create(name='committee_system', defaults={
            'display_name': 'Committees', 'description': 'x', 'category': 'core', 'is_enabled': True})
        self.toggle = PageToggle.objects.create(url_name='songbook', display_name='Songbook', is_enabled=True)
        member = make_user('visit-m', name='Visit Member')
        PageVisit.objects.create(user=member, path='/home/', count=4)

    def get(self, name, params=None, console=True):
        self.set_console(console)
        cache.clear()  # the feature-flag context is cached for 60 s
        r = self.client.get(reverse(name), params or {})
        self.assertEqual(r.status_code, 200, name)
        return r

    def test_each_page_on_and_off(self):
        for name, (page, _p) in PAGES.items():
            with self.subTest(page=name):
                on = self.get(name)
                self.assertTemplateUsed(on, f'admin_v2/console/{page}.html')
                self.assertTemplateUsed(on, 'admin_v2/console/base.html')
                html = on.content.decode()
                self.assertIn('class="ac-main admin-v2-content"', html)
                self.assertIn('aria-current="page"', html)  # breadcrumb / sidebar
                off = self.get(name, console=False)
                self.assertTemplateUsed(off, f'admin_v2/{page}.html')
                self.assertTemplateUsed(off, 'admin_v2/base_classic.html')
                self.assertTemplateNotUsed(off, f'admin_v2/console/{page}.html')
                self.assertTemplateNotUsed(off, 'admin_v2/console/base.html')
                self.assertNotIn('ac-op', off.content.decode())
        self.set_console(True)

    def test_off_is_the_classic_render(self):
        """Off, the view passes the classic template exactly the context it
        always did (no console-only keys), so its markup is unchanged."""
        for name, (page, _p) in PAGES.items():
            with self.subTest(page=name):
                off = self.get(name, console=False)
                self.assertNotIn('charts', off.context)
                self.assertNotIn('attendance_bars', off.context)
        self.set_console(True)

    def test_every_classic_form_and_id_is_kept(self):
        renders = [(name, {}) for name in PAGES] + STATES
        for name, params in renders:
            with self.subTest(page=name, params=params):
                classic = parse(self.get(name, params, console=False).content.decode())
                console = parse(self.get(name, params).content.decode())
                frame_forms = {f for f in parse(self.get('admin_v2_dashboard', console=False).content.decode()).forms}
                for form in classic.forms:
                    if form in frame_forms:
                        continue  # the classic layout's own (logout etc.), not the page's
                    self.assertIn(form, console.forms)
                page_ids = {i for i in classic.ids if i.startswith(('sm-', 'setting-', 'confirm-', 'cache', 'jobs',
                                                                    'errors', 'slow', 'inactive', 'passkey'))}
                self.assertLessEqual(page_ids, console.ids)
        self.set_console(True)

    def test_maintenance_switches_work_from_the_console(self):
        maintenance = FeatureFlag.objects.create(name='maintenance_mode', display_name='Maintenance',
                                                 description='x', is_enabled=False)
        setting = SiteSetting.objects.create(key='ops_test_number', display_name='A number', value='3',
                                             setting_type='integer', category='general')
        html = self.get('admin_v2_maintenance').content.decode()
        flag_url = reverse('toggle_feature_flag', args=[self.flag.id])
        self.assertIn(f'action="{flag_url}"', html)
        self.assertIn('aria-label="Turn off Committees"', html)
        self.assertIn(f'action="{reverse("toggle_page", args=[self.toggle.id])}"', html)
        self.assertIn(f'action="{reverse("update_site_setting", args=[setting.id])}"', html)
        self.assertIn('data-confirm="Take the site down for everyone except admins?"', html)
        self.assertIn('Start maintenance', html)
        self.assertEqual(html.count('<input type="hidden" name="next" value="maintenance">'),
                         len(re.findall(r'<form method="post" action="[^"]*(?:toggle|setting)', html)))

        r = self.client.post(flag_url, {'next': 'maintenance'})
        self.assertRedirects(r, reverse('admin_v2_maintenance'), fetch_redirect_response=False)
        self.flag.refresh_from_db()
        self.assertFalse(self.flag.is_enabled)
        self.assertIn('aria-label="Turn on Committees"', self.get('admin_v2_maintenance').content.decode())
        r = self.client.post(reverse('toggle_page', args=[self.toggle.id]), {'next': 'maintenance'})
        self.assertRedirects(r, reverse('admin_v2_maintenance'), fetch_redirect_response=False)
        self.toggle.refresh_from_db()
        self.assertFalse(self.toggle.is_enabled)
        self.client.post(reverse('toggle_feature_flag', args=[maintenance.id]), {'next': 'maintenance'})
        maintenance.refresh_from_db()
        self.assertTrue(maintenance.is_enabled)
        self.assertIn('End maintenance', self.get('admin_v2_maintenance').content.decode())

    def test_system_tools_confirm_flow(self):
        html = self.get('admin_v2_system').content.decode()
        self.assertIn('?confirm=cache&amp;scope=content', html)
        self.assertIn(f'?confirm=job&amp;task={self.job.pk}', html)
        self.assertIn('Not offered here', html)
        html = self.get('admin_v2_system', {'confirm': 'cache', 'scope': 'content'}).content.decode()
        self.assertIn('Clear "Page content"?', html)
        self.assertIn(f'action="{reverse("admin_v2_system_clear_cache")}"', html)
        self.assertIn('<input type="hidden" name="confirm" value="yes">', html)
        html = self.get('admin_v2_system', {'confirm': 'job', 'task': self.job.pk}).content.decode()
        self.assertIn('Run "Prune things" now?', html)
        self.assertIn(f'action="{reverse("admin_v2_system_run_job")}"', html)

    def test_sitemap_hooks(self):
        r = self.get('admin_v2_sitemap')
        html = r.content.decode()
        for hook in ('id="sm-data"', 'id="sm-config"', 'id="sm-canvas"', 'id="sm-list"', 'id="sm-panel"',
                     'id="sm-search"', 'id="sm-search-results"', 'id="sm-show-all"', 'data-sm="expand"',
                     'data-sm="collapse"', 'data-sm="zoom-in"', 'data-sm="zoom-out"', 'data-sm="fit"',
                     'data-sm-zoom', 'data-node-url=', 'data-open-url=', 'data-check-url=',
                     'data-members-url=', 'js/admin_v2/sitemap.js'):
            self.assertIn(hook, html)
        self.assertNotIn('<style', html.split('id="ac-main"')[1])
        html = self.get('admin_v2_sitemap', {'node': '/songbook/', 'confirm': 'page',
                                             'target': 'songbook'}).content.decode()
        self.assertIn('Turn it off', html)
        self.assertIn(reverse('admin_v2_sitemap_page_toggle'), html)

    def test_trends_draws_svg_bars(self):
        r = self.get('admin_v2_trends')
        html = r.content.decode()
        self.assertIn('data-trend="signins"', html)
        self.assertIn('class="ac-chart ac-op-chart"', html)
        self.assertNotIn('chart.min.js', html)
        self.assertNotIn('id="trend-data"', html)
        for chart in r.context['charts']:
            self.assertEqual(len(chart['bars']), 12)
            for bar in chart['bars']:
                self.assertGreaterEqual(bar['y'], 14)
                self.assertLessEqual(round(bar['y'] + bar['h'], 1), 104)
        off = self.get('admin_v2_trends', console=False).content.decode()
        self.assertIn('id="trend-data"', off)  # the classic page keeps Chart.js
        self.set_console(True)

    def test_url_explorer_keeps_the_alpine_component(self):
        html = self.get('admin_v2_url_explorer').content.decode()
        for hook in ('x-data="urlExplorer"', 'x-model="q"', '@click="toggleSection(', 'x-show="isOpen(',
                     'x-show="rowMatches($el)"', 'x-show="noMatches()"', 'data-pattern="'):
            self.assertIn(hook, html)

    def test_page_visits_filters_and_drill(self):
        html = self.get('admin_v2_page_visits').content.decode()
        self.assertIn('/home/', html)
        self.assertIn('?path=/home/&sort=total', html)
        html = self.get('admin_v2_page_visits', {'path': '/home/'}).content.decode()
        self.assertIn('Visit Member', html)
        self.assertIn('<input type="hidden" name="path" value="/home/">', html)

    def test_exports_keep_their_fields(self):
        html = self.get('admin_v2_exports').content.decode()
        for name in ('admin_v2_export_members', 'admin_v2_export_attendance', 'admin_v2_export_service',
                     'export_directory', 'export_calendar_ical'):
            self.assertIn(reverse(name), html)
        for field in ('name="status"', 'name="days"', 'name="period"'):
            self.assertIn(field, html)


SECRET = 'ops-console-secret-key'
PW = 'pw-for-tests-1'  # base.make_user's password


class LoginConsoleTests(AdminV2TestCase):
    """The sign-in page outside the frame: same fields, same POST, same flow."""

    def setUp(self):
        super().setUp()
        cache.clear()
        env = mock.patch.dict(os.environ, {'ADMIN_V2_SECRET_KEY': SECRET})
        env.start()
        self.addCleanup(env.stop)
        self.web = Client()
        self.web.force_login(self.admin)  # signed in to the site, not yet to Admin v2

    def page(self, console):
        self.set_console(console)
        cache.clear()
        return self.web.get(reverse('admin_v2_login'))

    def test_on_and_off(self):
        on = self.page(True)
        self.assertTemplateUsed(on, 'admin_v2/console/login.html')
        self.assertTemplateNotUsed(on, 'admin_v2/login.html')
        html = on.content.decode()
        self.assertIn('class="ac ac-login"', html)
        self.assertIn('css/admin-console/operations.css', html)
        off = self.page(False)
        self.assertTemplateUsed(off, 'admin_v2/login.html')
        self.assertTemplateNotUsed(off, 'admin_v2/console/login.html')
        self.assertNotIn('ac-login', off.content.decode())
        self.set_console(True)

    def test_same_form(self):
        on = parse(self.page(True).content.decode())
        off = parse(self.page(False).content.decode())
        self.assertIn(('post', '', ('secret_key', 'user_password')), on.forms)
        self.assertEqual([f for f in off.forms if f[2] == ('secret_key', 'user_password')],
                         [f for f in on.forms if f[2] == ('secret_key', 'user_password')])
        self.set_console(True)

    def test_passkey_button_uses_a_static_script(self):
        WebAuthnCredential.objects.create(user=self.admin, credential_id=b'ops-cred', public_key=b'pk', sign_count=0)
        html = self.page(True).content.decode()
        self.assertIn('id="passkey-btn"', html)
        self.assertIn('id="passkey-error"', html)
        self.assertIn(f'data-begin-url="{reverse("admin_v2_passkey_auth_begin")}"', html)
        self.assertIn(f'data-complete-url="{reverse("admin_v2_passkey_auth_complete")}"', html)
        self.assertIn('js/admin_v2/login.js', html)
        body = html.split('class="ac ac-login"')[1].split('</main>')[0]
        for tag in re.findall(r'<script\b[^>]*>', body):
            self.assertIn('src=', tag)

    def test_wrong_and_right_secret(self):
        self.set_console(True)
        r = self.web.post(reverse('admin_v2_login'), {'user_password': PW, 'secret_key': 'nope'})
        self.assertEqual(r.status_code, 200)
        self.assertTemplateUsed(r, 'admin_v2/console/login.html')
        self.assertContains(r, 'Invalid secret key')
        self.assertFalse(self.web.session.get('admin_v2_authenticated'))
        r = self.web.post(reverse('admin_v2_login'), {'user_password': PW, 'secret_key': SECRET})
        self.assertRedirects(r, reverse('admin_v2_dashboard'), fetch_redirect_response=False)
        self.assertTrue(self.web.session['admin_v2_authenticated'])

    def test_gate_unchanged(self):
        anon = Client()
        self.assertRedirects(anon.get(reverse('admin_v2_login')), reverse('login'), fetch_redirect_response=False)
        outsider = Client()
        outsider.force_login(make_user('ops-outsider', is_admin=True))
        self.assertRedirects(outsider.get(reverse('admin_v2_login')), reverse('home'), fetch_redirect_response=False)
        # Already signed in to Admin v2: straight to the console.
        self.assertRedirects(self.client.get(reverse('admin_v2_login')), reverse('admin_v2_dashboard'),
                             fetch_redirect_response=False)


class OperationsTemplateRulesTests(SimpleTestCase):
    """The new templates and the login script follow the prod CSP."""

    TEMPLATES = ['system_tools', 'celery_health', 'backups', 'maintenance', 'exports', 'page_visits',
                 'trends', 'chapter_health', 'sitemap', 'url_explorer', 'login']

    def test_no_inline_script_style_or_handlers(self):
        for page in self.TEMPLATES:
            text = (CONSOLE / f'{page}.html').read_text()
            with self.subTest(template=page):
                self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\sstyle=', text))
                self.assertNotIn('<style', text)
                for tag in re.findall(r'<script\b[^>]*>', text):
                    self.assertIn('src=', tag)
                self.assertIsNone(re.search(r'<[a-zA-Z][^>]*\son[a-z]+=', text))
                self.assertNotIn('/admin-v2/', text)

    def test_classic_templates_do_not_know_the_console(self):
        for page in self.TEMPLATES:
            text = (CONSOLE.parent / f'{page}.html').read_text()
            with self.subTest(template=page):
                self.assertNotIn('admin_console_ui', text)
                self.assertNotIn('ac-op', text)

    def test_page_css_is_token_based(self):
        css = (Path(settings.BASE_DIR) / 'static' / 'css' / 'admin-console' / 'operations.css').read_text()
        self.assertIn('.ac-sitemap .sm-node', css)
        self.assertIn('.ac-login', css)
        js = (Path(settings.BASE_DIR) / 'static' / 'js' / 'admin_v2' / 'login.js').read_text()
        self.assertNotIn('/admin-v2/', js)
