"""
The console home's Customize (src/admin_console.py `home_layout` /
`save_home_layout`, src/view/admin_v2_console.py
`admin_v2_console_home_widgets`, templates/admin_v2/console/home.html).

Each admin chooses which widgets their console home shows and in what order;
the layout lives in ``UserPreferences.prefs['admin_console']`` next to their
favorites, and the dashboard view skips the queries of hidden widgets.
"""
import re
from unittest import mock

from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from src.admin_console import (
    HOME_KEY, HOME_SEEN_KEY, HOME_WIDGET_KEYS, PREFS_SECTION, home_layout,
)
from src.models import UserPreferences
from src.tests.admin_v2.base import AdminV2TestCase, make_user
from src.view import admin_v2

URL = 'admin_v2_console_home_widgets'


def widget_order(html):
    """Widget keys in the order the home renders them (Totals is the strip)."""
    ids = {'w-health': 'health', 'w-alarms': 'alarms', 'w-attention': 'attention', 'w-signins': 'signins',
           'w-resources': 'resources', 'w-recent': 'recent', 'w-users': 'users', 'w-email': 'email',
           'w-perf': 'performance', 'w-activity': 'activity', 'w-logins': 'logins'}
    grid = html[html.index('class="ac-widgets"'):html.index('id="home-customize"')]
    found = re.findall(r'aria-labelledby="(w-[a-z]+)"|(ac-stat-strip)', grid)
    return [ids[w] if w else 'totals' for w, strip in found]


class CustomizeTests(AdminV2TestCase):

    def save(self, order, show, client=None, **extra):
        return (client or self.client).post(reverse(URL), {'order': order, 'show': show, **extra})

    def home(self):
        r = self.client.get(reverse('admin_v2_dashboard'))
        self.assertEqual(r.status_code, 200)
        return r

    def stored(self, user=None):
        return UserPreferences.objects.get(user=user or self.admin).prefs.get(PREFS_SECTION, {})

    def test_default_layout_is_every_widget_in_default_order(self):
        self.assertEqual(home_layout(self.admin), list(HOME_WIDGET_KEYS))
        r = self.home()
        self.assertEqual(r.context['home']['layout'], list(HOME_WIDGET_KEYS))
        html = r.content.decode()
        self.assertEqual(widget_order(html), list(HOME_WIDGET_KEYS))
        # The button (secondary, not the gold one) and the dialog's form.
        self.assertRegex(html, r'<button type="button" class="ac-btn" data-home-customize[^>]*>.*Customize</button>')
        self.assertIn(f'action="{reverse(URL)}"', html)
        self.assertEqual(html.count('role="switch"'), len(HOME_WIDGET_KEYS))
        self.assertEqual(html.count(' checked>'), len(HOME_WIDGET_KEYS))
        self.assertIn('Reset to default', html)

    def test_hide_show_and_reorder_persist(self):
        order = ['logins', 'alarms', 'totals'] + [k for k in HOME_WIDGET_KEYS if k not in ('logins', 'alarms', 'totals')]
        r = self.save(order, ['logins', 'alarms', 'totals', 'users'])
        self.assertRedirects(r, reverse('admin_v2_dashboard'), fetch_redirect_response=False)
        self.assertEqual(self.stored()[HOME_KEY], ['logins', 'alarms', 'totals', 'users'])
        self.assertEqual(home_layout(self.admin), ['logins', 'alarms', 'totals', 'users'])
        html = self.home().content.decode()
        self.assertEqual(widget_order(html), ['logins', 'alarms', 'totals', 'users'])
        self.assertNotIn('>Service health</h2>', html)
        # The dialog lists the shown ones first (switched on), then the hidden.
        rows = re.findall(r'data-widget="([a-z]+)"', html)
        self.assertEqual(rows[:4], ['logins', 'alarms', 'totals', 'users'])
        self.assertEqual(sorted(rows), sorted(HOME_WIDGET_KEYS))
        self.assertEqual(html.count(' checked>'), 4)
        # Show one again.
        self.save(['health'] + order, ['health', 'logins'])
        self.assertEqual(widget_order(self.home().content.decode()), ['health', 'logins'])

    def test_favorites_survive_and_hiding_everything_is_allowed(self):
        self.client.post(reverse('admin_v2_console_favorite'), {'page': 'admin_v2_backups'})
        self.save(list(HOME_WIDGET_KEYS), [])
        self.assertEqual(self.stored()['favorites'], ['admin_v2_backups'])
        self.assertEqual(home_layout(self.admin), [])
        html = self.home().content.decode()
        self.assertEqual(widget_order(html), [])
        self.assertIn('Every widget is hidden', html)

    def test_reset(self):
        self.client.post(reverse('admin_v2_console_favorite'), {'page': 'admin_v2_backups'})
        self.save(['users'], ['users'])
        r = self.client.post(reverse(URL), {'reset': '1', 'order': ['users'], 'show': ['users']})
        self.assertEqual(r.status_code, 302)
        section = self.stored()
        self.assertNotIn(HOME_KEY, section)
        self.assertNotIn(HOME_SEEN_KEY, section)
        self.assertEqual(section['favorites'], ['admin_v2_backups'])
        self.assertEqual(widget_order(self.home().content.decode()), list(HOME_WIDGET_KEYS))

    def test_junk_input_is_ignored(self):
        self.save(['nope', 'users', 'users', '<script>', 'alarms'], ['alarms', 'users', 'nope', '', 'x' * 500])
        self.assertEqual(self.stored()[HOME_KEY], ['users', 'alarms'])
        # A shown widget missing from `order` goes at the end.
        self.save(['alarms'], ['alarms', 'logins'])
        self.assertEqual(self.stored()[HOME_KEY], ['alarms', 'logins'])
        # Junk already in the stored prefs (hand-edited, or an old key) too.
        prefs = UserPreferences.objects.get(user=self.admin)
        prefs.prefs[PREFS_SECTION][HOME_KEY] = ['gone', 'logins', 7, None, 'logins']
        prefs.prefs[PREFS_SECTION][HOME_SEEN_KEY] = 'not a list'
        prefs.save()
        cache.clear()
        # Unknown keys dropped; every real widget counts as new (not "seen").
        self.assertEqual(home_layout(self.admin), ['logins'] + [k for k in HOME_WIDGET_KEYS if k != 'logins'])
        prefs.prefs[PREFS_SECTION] = 'garbage'
        prefs.save()
        cache.clear()
        self.assertEqual(home_layout(self.admin), list(HOME_WIDGET_KEYS))
        self.assertEqual(self.home().status_code, 200)

    def test_a_widget_added_later_shows_by_default(self):
        self.save(list(HOME_WIDGET_KEYS), ['users', 'alarms'])
        prefs = UserPreferences.objects.get(user=self.admin)
        # As if 'logins' were added after this admin saved.
        prefs.prefs[PREFS_SECTION][HOME_SEEN_KEY] = [k for k in HOME_WIDGET_KEYS if k != 'logins']
        prefs.save()
        cache.clear()
        self.assertEqual(home_layout(self.admin), ['alarms', 'users', 'logins'])

    def test_get_is_refused(self):
        self.assertEqual(self.client.get(reverse(URL)).status_code, 405)
        self.assertFalse(UserPreferences.objects.filter(user=self.admin, prefs__has_key=PREFS_SECTION).exists())

    def test_outsiders_are_refused(self):
        outsider = Client()
        outsider.force_login(make_user('av2-home-out', is_admin=True))  # not allowlisted, no admin-v2 session
        r = self.save(['users'], ['users'], client=outsider)
        self.assertEqual(r.status_code, 302)
        self.assertNotIn(reverse('admin_v2_dashboard'), r['Location'])
        self.assertFalse(UserPreferences.objects.filter(
            user__user_id='av2-home-out', prefs__has_key=PREFS_SECTION).exists())
        anonymous = Client()
        self.assertEqual(self.save(['users'], ['users'], client=anonymous).status_code, 302)

    def test_csrf_is_enforced(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.admin)
        session = strict.session
        session.update({k: v for k, v in self.client.session.items()})
        session.save()
        self.assertEqual(self.save(['users'], ['users'], client=strict).status_code, 403)

    def test_hidden_widgets_skip_their_queries(self):
        self.home()  # warm caches (flags, prefs, seeded defaults)
        with CaptureQueriesContext(connection) as full:
            self.home()
        # Read now: the next request's reset_queries empties the log the
        # context's indices point into.
        full_sql = [q['sql'] for q in full.captured_queries]
        self.save(['alarms'], ['alarms'])
        self.home()
        with mock.patch.object(admin_v2, 'dashboard_celery_summary') as celery, \
                mock.patch.object(admin_v2, 'attention_items') as attention, \
                mock.patch.object(admin_v2, 'get_performance_summary') as performance, \
                mock.patch('src.admin_console.signins_last_7_days') as signins, \
                CaptureQueriesContext(connection) as slim:
            r = self.home()
        for skipped in (celery, attention, performance, signins):
            skipped.assert_not_called()
        slim_sql = [q['sql'] for q in slim.captured_queries]
        for table in ('src_legislation', 'src_event', 'src_mobiledevice', 'src_activitylog'):
            self.assertIn(f'"{table}"', '\n'.join(full_sql), table)
            self.assertNotIn(f'"{table}"', '\n'.join(slim_sql), table)
        self.assertLess(len(slim_sql), len(full_sql) - 8)
        # The alarms (and the bell) still come from the one alert aggregate.
        self.assertEqual(set(r.context['home']['alarms']), {'alarm', 'warning', 'ok'})

    def test_classic_dashboard_ignores_the_layout(self):
        self.save(['alarms'], ['alarms'])
        self.set_console(False)
        r = self.client.get(reverse('admin_v2_dashboard'))
        html = r.content.decode()
        self.assertIn('Control Panel', html)
        self.assertNotIn('home', r.context)
        self.assertNotIn('home-customize', html)
        # Everything the classic page shows is still computed.
        self.assertIn('total', r.context['stats']['legislation'])
        self.assertIn('total', r.context['stats']['events'])
        self.assertIn('percent', r.context['onboarding_stats'])
        self.assertIn('workers_up', r.context['celery_summary'])


#: Tables whose numbers only the classic dashboard displays. The classic page
#: must query every one (it renders them all); the console home must not,
#: except the three a console widget reads for its own reason (below).
CLASSIC_ONLY_TABLES = (
    'src_vote', 'src_committeedocument', 'src_committeevote', 'src_announcement',
    'src_quarantinedaccount', 'src_ipblacklist', 'src_honeypotaccess', 'src_securitynotificationlog',
    'src_apitoken', 'src_apiaccesslog', 'src_pushsubscription', 'src_pagevisit',
)
#: Needs attention counts unreleased quarantines and pending API tokens;
#: Service health's Backups row reads restore drills from the security
#: notification log. Hide those two widgets and these go too.
WIDGET_TABLES = {'src_quarantinedaccount': 'attention', 'src_apitoken': 'attention',
                 'src_securitynotificationlog': 'health'}


class ClassicOnlyQueriesTests(AdminV2TestCase):
    """With the console on, the dashboard view skips what only the classic
    page shows; with it off, it computes (and renders) everything as before.

    Measured on this fixture (warm caches): classic 46 queries before and
    after; console home 52 before, 37 after (default layout).

    11-09-26: the worker ping is faked as "no worker answered". The counts
    depended on the machine: with Redis as the broker (a dev machine) the real
    ping raised and the job schedule was never read; on CI (in-memory broker)
    it returned nothing and the schedule was read, one query more (47 != 46).
    Faked, every machine reads the schedule, as production does."""

    def measure(self):
        with mock.patch('celery.app.control.Inspect.ping', return_value={}):
            self.client.get(reverse('admin_v2_dashboard'))  # warm flags, prefs, seeded defaults
            with CaptureQueriesContext(connection) as ctx:
                r = self.client.get(reverse('admin_v2_dashboard'))
        self.assertEqual(r.status_code, 200)
        return r, [q['sql'] for q in ctx.captured_queries]

    def test_console_home_skips_classic_only_queries(self):
        console, console_sql = self.measure()
        console_text = '\n'.join(console_sql)
        for table in CLASSIC_ONLY_TABLES:
            if table not in WIDGET_TABLES:
                with self.subTest(table=table):
                    self.assertNotIn(f'"{table}"', console_text)
        # Without the two widgets that read them, the rest go as well.
        self.client.post(reverse(URL), {'order': list(HOME_WIDGET_KEYS),
                                        'show': [k for k in HOME_WIDGET_KEYS if k not in ('attention', 'health')]})
        _r, slim_sql = self.measure()
        for table in CLASSIC_ONLY_TABLES:
            with self.subTest(table=table, layout='no attention/health'):
                self.assertNotIn(f'"{table}"', '\n'.join(slim_sql))
        # The console still has everything its widgets read.
        self.assertEqual(console.context['feature_flags'], {})
        for key in ('users', 'legislation', 'events', 'committees', 'security', 'communications'):
            self.assertIn(key, console.context['stats'])

        self.set_console(False)
        cache.clear()
        classic, classic_sql = self.measure()
        classic_text = '\n'.join(classic_sql)
        for table in CLASSIC_ONLY_TABLES:
            with self.subTest(table=table, page='classic'):
                self.assertIn(f'"{table}"', classic_text)
        self.assertTrue(classic.context['feature_flags'])
        self.assertTrue(classic.context['stats']['database']['tables'])
        self.assertIn('top_paths', classic.context['page_visit_summary'])
        # 52 -> 37 on the console; the classic page's 46 is untouched (47 and
        # 38 with the schedule read, see the docstring). If a
        # change moves these on purpose, update the numbers (and the docstring).
        self.assertEqual(len(classic_sql), 47, 'the classic dashboard should compute exactly what it did')
        self.assertLessEqual(len(console_sql), 38)
        self.assertLessEqual(len(console_sql), len(classic_sql) - 8)


class ConsoleIconTests(AdminV2TestCase):
    """11-09-26: console_icon passes its path through format_html (escaped)
    instead of mark_safe (CI's bandit scan fails on mark_safe). That's only
    lossless while no path has a character escaping would change."""

    def test_icon_paths_survive_escaping(self):
        from django.utils.html import escape
        from src.admin_console_icons import ICON_PATHS
        from src.templatetags.admin_v2_tags import console_icon
        for name, path in ICON_PATHS.items():
            with self.subTest(icon=name):
                self.assertEqual(escape(path), path)
                self.assertIn(f'd="{path}"', console_icon(name))
