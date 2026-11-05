"""v4.4.4 admin-v2 site map: gate, tree shape, introspection, "who can open", the confirmed switches."""
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone

from src.models import ActivityLog, Committee, CommitteePermissions, SlatingPeriod
from src.models.education import PledgePageRestriction
from src.models_feature_flags import FeatureFlag, PageToggle
from src.tests.admin_v2.base import AdminV2TestCase, make_user
from src.view import admin_v2_sitemap as sm


def node(node_id):
    n = sm.find_node(node_id)
    assert n is not None, f'no node {node_id}'
    return n


class SitemapGateTests(AdminV2TestCase):

    def test_not_allowlisted_is_refused(self):
        other = make_user('sm-other', is_admin=True)
        c = Client()
        c.force_login(other)
        s = c.session
        s['admin_v2_authenticated'] = True
        s['admin_v2_auth_time'] = timezone.now().isoformat()
        s.save()
        for name in ('admin_v2_sitemap', 'admin_v2_sitemap_node', 'admin_v2_sitemap_check', 'admin_v2_sitemap_members'):
            r = c.get(reverse(name))
            self.assertRedirects(r, reverse('home'), fetch_redirect_response=False, msg_prefix=name)
        r = c.post(reverse('admin_v2_sitemap_page_toggle'), {'confirm': 'yes', 'target': 'home'})
        self.assertRedirects(r, reverse('home'), fetch_redirect_response=False)

    def test_needs_the_admin_v2_step_up(self):
        c = Client()
        c.force_login(self.admin)
        r = c.get(reverse('admin_v2_sitemap'))
        self.assertRedirects(r, reverse('admin_v2_login'), fetch_redirect_response=False)

    def test_page_renders_with_tree_script_and_nav(self):
        r = self.client.get(reverse('admin_v2_sitemap'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'id="sm-data"')
        self.assertContains(r, 'js/admin_v2/sitemap.js')
        self.assertContains(r, f'href="{reverse("admin_v2_sitemap")}"')
        self.assertContains(r, 'aria-current="page"')
        self.assertNotContains(r, '<script>')  # CSP: no un-nonced inline script


class SitemapTreeTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        self.exec = Committee.objects.create(name='Sitemap Exec', code='SMEXEC')
        self.bylaws = Committee.objects.create(name='Sitemap Bylaws', code='SMBYL')
        self.period = SlatingPeriod.objects.create(name='SM Fall', academic_term='Fall')

    def test_committees_nest_under_committees_with_their_pages(self):
        home = node('/committee/SMEXEC/')
        self.assertEqual(home['parent'], '/committees/')
        self.assertEqual(home['label'], 'Sitemap Exec')
        self.assertEqual(home['name'], 'committee_home')
        # v4.4.5: the folder above (/committee/) now redirects to Committees, so
        # it is no longer flagged, and committees still nest under Committees.
        self.assertEqual(home['missing'], '')
        for sub in ('minutes/', 'documents/', 'attendance/'):
            self.assertEqual(node(f'/committee/SMEXEC/{sub}')['parent'], '/committee/SMEXEC/', sub)
        self.assertEqual(node('/committee/SMBYL/minutes/')['parent'], '/committee/SMBYL/')
        self.assertEqual(node('/committees/')['parent'], '')
        self.assertEqual(node('/home/')['parent'], '')  # Home is a top-level page, not everyone's parent

    def test_feature_pages_only_under_committees_of_that_kind(self):
        # The education/recruitment/Kai views 404 for any other committee
        # (get_object_or_404(..., is_education_committee=True) and so on), so
        # Exec and Bylaws must not list them; the flagged committees do.
        Committee.objects.create(name='Sitemap Education', code='SMEDU', is_education_committee=True)
        Committee.objects.create(name='Sitemap Recruitment', code='SMREC', is_recruitment_committee=True)
        Committee.objects.create(name='Sitemap Kai', code='SMKAI', is_kai_committee=True)
        cache.clear()
        ids = {n['id'] for n in sm.build_tree()['nodes']}
        for page in ('education/', 'education-permissions/', 'recruitment/', 'recruitment-permissions/',
                     'kai-permissions/'):
            self.assertNotIn(f'/committee/SMEXEC/{page}', ids, page)
            self.assertNotIn(f'/committee/SMBYL/{page}', ids, page)
        self.assertIn('/committee/SMEDU/education/', ids)
        self.assertIn('/committee/SMEDU/education-permissions/', ids)
        self.assertNotIn('/committee/SMEDU/recruitment/', ids)
        self.assertIn('/committee/SMREC/recruitment/', ids)
        self.assertIn('/committee/SMREC/recruitment-permissions/', ids)
        self.assertIn('/committee/SMKAI/kai-permissions/', ids)
        self.assertNotIn('/committee/SMKAI/education/', ids)
        # Pages every committee has stay under every committee.
        for code in ('SMEXEC', 'SMEDU', 'SMKAI'):
            self.assertIn(f'/committee/{code}/minutes/', ids)

    def test_parameters_expand_to_real_rows_only(self):
        ids = {n['id'] for n in sm.build_tree()['nodes']}
        self.assertNotIn('/committee/<code>/', ids)
        self.assertIn(f'/slating/period/{self.period.pk}/', ids)
        self.assertIn('/committee/SMEXEC/minutes/<minutes_id>/edit/', ids)  # other parameters stay templated

    def test_api_mobile_and_actions_are_classified_for_hiding(self):
        data = sm.build_tree()
        kinds = {r['name']: r['kind'] for r in data['routes']}
        self.assertEqual(kinds['committee_index'], sm.KIND_PAGE)
        self.assertEqual(kinds['committee_detail_api'], sm.KIND_API)
        self.assertEqual(kinds['toggle_page'], sm.KIND_ACTION)
        self.assertEqual(kinds['delete_committee'], sm.KIND_ACTION)  # @require_POST
        self.assertTrue(any(k == sm.KIND_MOBILE for k in kinds.values()))
        traps = [n for n, k in kinds.items() if n and n.startswith('honeypot_')]
        self.assertTrue(traps)
        self.assertEqual({kinds[n] for n in traps}, {sm.KIND_TRAP})
        trap = next(n for n in sm.build_tree()['nodes'] if data['routes'][n['r']]['name'] == traps[0])
        self.assertEqual(sm.node_details(sm.find_node(trap['id']))['open_url'], '')  # opening one bans your IP
        self.assertGreater(data['django_admin'], 0)
        self.assertFalse(any(r['path'].startswith('/admin/src/') and r['name'].startswith('src_') for r in data['routes']))
        # The page only shows "page" by default; the payload carries the kinds for the toggle.
        payload = sm.client_payload(data)
        self.assertEqual({k['key'] for k in payload['kinds']}, set(sm.KIND_LABELS))

    def test_client_payload_rebuilds_every_id(self):
        data = sm.build_tree()
        payload = sm.client_payload(data)
        nodes = payload['nodes']
        cache = {}

        def resolve(i):
            if i not in cache:
                n = nodes[i]
                cache[i] = n['i'] if 'i' in n else resolve(n['p']) + n['s']
            return cache[i]
        self.assertEqual([resolve(i) for i in range(len(nodes))], [n['id'] for n in data['nodes']])

    def test_linked_from_comes_from_templates(self):
        info = sm.link_info(node('/committees/'))
        self.assertIn('home', [l['name'] for l in info['linked_from']])
        to = sm.link_info(node('/committee/SMEXEC/minutes/'))['links_to']
        back = next(l for l in to if l['name'] == 'committee_home')
        self.assertEqual(back['id'], '/committee/SMEXEC/')  # same committee preferred

    def test_tree_query_count_does_not_grow_with_committees(self):
        def count():
            from django.core.cache import cache
            cache.clear()
            with CaptureQueriesContext(connection) as ctx:
                sm.build_tree()
            return len(ctx.captured_queries)
        before = count()
        for i in range(8):
            Committee.objects.create(name=f'SM Extra {i}', code=f'SMX{i}')
        self.assertEqual(count(), before)
        self.assertLessEqual(before, 3)

    def test_tree_is_cached(self):
        sm.build_tree()
        with CaptureQueriesContext(connection) as ctx:
            sm.build_tree()
        self.assertEqual(len(ctx.captured_queries), 0)


class SitemapIntrospectionTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        Committee.objects.create(name='Sitemap Exec', code='SMEXEC')

    def layers(self, node_id):
        return [(l['name'], l['args'], l['type']) for l in sm.node_details(node(node_id))['layers']]

    def test_committee_home_guards(self):
        self.assertEqual(self.layers('/committee/SMEXEC/'), [
            ('login_required', '', 'guard'),
            ('require_page_enabled', 'committee_home', 'guard'),
            ('require_feature_flag', 'committee_system', 'guard'),
        ])
        d = sm.node_details(node('/committee/SMEXEC/'))
        self.assertEqual(d['body_checks'], ["if not has_access -> redirect('committee_index')"])
        self.assertEqual(d['view'], 'src.view.committee.committee_home.committee_home')
        self.assertEqual(d['committee']['code'], 'SMEXEC')
        self.assertTrue(any('member-permissions' in e['url'] for e in d['committee']['editors']))
        self.assertEqual(d['open_url'], '/committee/SMEXEC/')

    def test_officer_page_and_admin_v2_page(self):
        names = [n for n, _a, _t in self.layers('/committees/manage/')]
        self.assertEqual(names, ['login_required', 'officer_required', 'require_feature_flag'])
        self.assertEqual(self.layers('/admin-v2/permissions/'), [('require_admin_v2_auth', '', 'guard')])
        post_only = self.layers('/committees/<committee_id>/delete/')
        self.assertIn(('require_http_methods', 'POST', 'info'), post_only)

    def test_page_switch_state_and_unenforced_row(self):
        PageToggle.objects.create(url_name='committee_home', display_name='Committee home', is_enabled=False)
        PageToggle.objects.create(url_name='committee_index', display_name='Committees', is_enabled=True)
        d = sm.node_details(node('/committee/SMEXEC/'))
        self.assertEqual(d['page_switches'], [{
            'url_name': 'committee_home', 'exists': True, 'enabled': False, 'display': 'Committee home',
            'enforced': True, 'last': ''}])
        # A row on a page whose view has @require_page_enabled is enforced; the
        # committee minutes list has no decorator, so a row there would not be.
        PageToggle.objects.create(url_name='committee_minutes_list', display_name='Minutes')
        d = sm.node_details(node('/committee/SMEXEC/minutes/'))
        self.assertEqual([(s['url_name'], s['enforced']) for s in d['page_switches']], [('committee_minutes_list', False)])

    def test_pledge_restriction_shown(self):
        PledgePageRestriction.objects.create(url_name='songbook', allowed_phases=['2'])
        d = sm.node_details(node('/songbook/'))
        self.assertEqual(d['pledge']['row']['phases'], ['2'])
        self.assertTrue(sm.node_details(node('/home/'))['pledge']['always_allowed'])

    def test_node_endpoint(self):
        r = self.client.get(reverse('admin_v2_sitemap_node'), {'id': '/committee/SMEXEC/'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['name'], 'committee_home')
        self.assertEqual(self.client.get(reverse('admin_v2_sitemap_node'), {'id': 'no-such-node'}).status_code, 404)


class WhoCanOpenTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        FeatureFlag.objects.update_or_create(name='committee_system', defaults={
            'display_name': 'Committees', 'description': 'x', 'is_enabled': True})
        self.committee = Committee.objects.create(name='Sitemap Exec', code='SMEXEC')
        self.officer = make_user('sm-officer', member_type='Officer')
        self.plain = make_user('sm-plain')
        self.pledge = make_user('sm-pledge', member_type='Pledge')

    def check(self, node_id, member):
        r = self.client.get(reverse('admin_v2_sitemap_check'), {'id': node_id, 'member': member.user_id})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_officer_allowed_member_denied(self):
        yes = self.check('/committees/manage/', self.officer)
        self.assertEqual(yes['verdict'], 'yes', yes)
        no = self.check('/committees/manage/', self.plain)
        self.assertEqual(no['verdict'], 'no', no)
        self.assertIn('Officers and chairs', no['reason'])
        self.assertEqual([s['result'] for s in no['steps']], ['pass', 'deny', 'pass'])

    def test_admin_v2_allowlist(self):
        self.assertEqual(self.check('/admin-v2/permissions/', self.admin)['verdict'], 'yes')
        no = self.check('/admin-v2/permissions/', self.officer)
        self.assertEqual(no['verdict'], 'no')
        self.assertIn('Redirects to', no['reason'])

    def test_page_switch_off_denies(self):
        PageToggle.objects.create(url_name='committee_home', display_name='Committee home', is_enabled=False)
        d = self.check('/committee/SMEXEC/', self.officer)
        self.assertEqual(d['verdict'], 'no')
        self.assertIn('Page switch', d['reason'])

    def test_pledge_restriction_denies_a_pledge_only(self):
        PledgePageRestriction.objects.create(url_name='songbook', allowed_phases=[])
        self.assertEqual(self.check('/songbook/', self.pledge)['verdict'], 'no')
        self.assertNotEqual(self.check('/songbook/', self.plain)['verdict'], 'no')
        self.assertEqual(self.check('/home/', self.pledge)['verdict'] in ('yes', 'guards'), True)

    def test_committee_page_reports_standing_and_body_checks(self):
        self.committee.chairs.add(self.plain)
        d = self.check('/committee/SMEXEC/', self.plain)
        self.assertEqual(d['verdict'], 'guards')  # the decorators pass; the view's own has_access check decides
        self.assertIn('has_access', d['reason'])
        self.assertTrue(any('not evaluated' in n.lower() or "isn't evaluated" in n for n in d['notes']))
        self.assertIn(['Chair', True], d['standing']['facts'])

    def test_evaluation_has_no_side_effects(self):
        """committee_home creates a CommitteePermissions row when it runs; the check must not run it."""
        perms, logs = CommitteePermissions.objects.count(), ActivityLog.objects.count()
        for member in (self.officer, self.plain, self.pledge):
            self.check('/committee/SMEXEC/', member)
            self.check('/committee/SMEXEC/minutes/', member)
            self.check('/committees/manage/', member)
        self.assertEqual(CommitteePermissions.objects.count(), perms)
        self.assertEqual(ActivityLog.objects.count(), logs)

    def test_member_search(self):
        r = self.client.get(reverse('admin_v2_sitemap_members'), {'q': 'sm-off'})
        self.assertEqual([m['id'] for m in r.json()['members']], ['sm-officer'])


class SitemapChangeTests(AdminV2TestCase):

    def setUp(self):
        super().setUp()
        self.toggle = PageToggle.objects.create(url_name='songbook', display_name='Songbook', is_enabled=True)
        self.flag, _ = FeatureFlag.objects.update_or_create(name='committee_system', defaults={
            'display_name': 'Committees', 'description': 'x', 'is_enabled': True})

    def test_actions_are_post_only(self):
        for name in ('admin_v2_sitemap_page_toggle', 'admin_v2_sitemap_feature_flag', 'admin_v2_sitemap_pledge_restriction'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 405, name)

    def test_confirm_panel_shows_without_changing(self):
        r = self.client.get(reverse('admin_v2_sitemap'), {'node': '/songbook/', 'confirm': 'page', 'target': 'songbook'})
        self.assertContains(r, 'Turn it off')
        self.assertContains(r, reverse('admin_v2_sitemap_page_toggle'))
        self.toggle.refresh_from_db()
        self.assertTrue(self.toggle.is_enabled)

    def test_unconfirmed_post_changes_nothing(self):
        r = self.client.post(reverse('admin_v2_sitemap_page_toggle'), {'target': 'songbook', 'node': '/songbook/'})
        self.assertEqual(r.status_code, 302)
        self.toggle.refresh_from_db()
        self.assertTrue(self.toggle.is_enabled)
        self.assertFalse(ActivityLog.objects.filter(action_type='page_toggle').exists())

    def test_page_toggle_confirmed_and_logged(self):
        r = self.client.post(reverse('admin_v2_sitemap_page_toggle'),
                             {'target': 'songbook', 'node': '/songbook/', 'confirm': 'yes'})
        self.assertRedirects(r, reverse('admin_v2_sitemap') + '?node=%2Fsongbook%2F', fetch_redirect_response=False)
        self.toggle.refresh_from_db()
        self.assertFalse(self.toggle.is_enabled)
        log = ActivityLog.objects.get(action_type='page_toggle')
        self.assertEqual(log.user_id, self.admin.pk)

    def test_feature_flag_confirmed_and_logged(self):
        self.client.post(reverse('admin_v2_sitemap_feature_flag'),
                         {'target': 'committee_system', 'node': '/committees/', 'confirm': 'yes'})
        self.flag.refresh_from_db()
        self.assertFalse(self.flag.is_enabled)
        self.assertTrue(ActivityLog.objects.filter(action_type='feature_flag_toggle').exists())

    def test_pledge_restriction_set_and_remove_logged(self):
        url = reverse('admin_v2_sitemap_pledge_restriction')
        self.client.post(url, {'target': 'songbook', 'node': '/songbook/', 'action': 'set', 'phases': ['2', '3'],
                               'confirm': 'yes'})
        row = PledgePageRestriction.objects.get(url_name='songbook')
        self.assertEqual(row.allowed_phases, ['2', '3'])
        self.assertEqual(row.updated_by_id, self.admin.pk)
        self.client.post(url, {'target': 'songbook', 'node': '/songbook/', 'action': 'remove', 'confirm': 'yes'})
        self.assertFalse(PledgePageRestriction.objects.filter(url_name='songbook').exists())
        logs = ActivityLog.objects.filter(action_type='settings_changed', object_type='PledgePageRestriction')
        self.assertEqual(logs.count(), 2)

    def test_pledge_restriction_refuses_always_allowed_pages(self):
        self.client.post(reverse('admin_v2_sitemap_pledge_restriction'),
                         {'target': 'login', 'action': 'set', 'phases': [], 'confirm': 'yes'})
        self.assertFalse(PledgePageRestriction.objects.filter(url_name='login').exists())
        self.assertFalse(ActivityLog.objects.filter(object_type='PledgePageRestriction').exists())



class SitemapOpenAndReturnTests(AdminV2TestCase):
    """v4.4.4: "Open page" opens in the same tab with a "Back to the site map" bar."""

    def test_open_sets_the_return_bar_and_back_goes_to_the_node(self):
        r = self.client.get(reverse('admin_v2_sitemap_open'), {'node': '/songbook/'})
        self.assertRedirects(r, '/songbook/', fetch_redirect_response=False)
        self.assertEqual(self.client.session['admin_v2_sitemap_return']['node'], '/songbook/')
        page = self.client.get('/songbook/')
        self.assertContains(page, 'Opened from the Site map')
        self.assertContains(page, reverse('admin_v2_sitemap_return'))
        back = self.client.get(reverse('admin_v2_sitemap_return'))
        self.assertRedirects(back, reverse('admin_v2_sitemap') + '?node=%2Fsongbook%2F', fetch_redirect_response=False)
        self.assertNotIn('admin_v2_sitemap_return', self.client.session)
        self.assertNotContains(self.client.get('/songbook/'), 'Opened from the Site map')

    def test_close_keeps_you_on_the_page(self):
        self.client.get(reverse('admin_v2_sitemap_open'), {'node': '/songbook/'})
        r = self.client.post(reverse('admin_v2_sitemap_return'), {'close': '1', 'next': '/songbook/'})
        self.assertRedirects(r, '/songbook/', fetch_redirect_response=False)
        self.assertNotIn('admin_v2_sitemap_return', self.client.session)
        r = self.client.post(reverse('admin_v2_sitemap_return'), {'close': '1', 'next': 'https://evil.example/'})
        self.assertRedirects(r, reverse('home'), fetch_redirect_response=False)

    def test_going_back_to_the_map_clears_the_bar(self):
        self.client.get(reverse('admin_v2_sitemap_open'), {'node': '/songbook/'})
        self.client.get(reverse('admin_v2_sitemap'))
        self.assertNotIn('admin_v2_sitemap_return', self.client.session)

    def test_unopenable_nodes_go_back_to_the_map(self):
        for node_id in ('no-such-node', '/committee/<code>/minutes/<minutes_id>/edit/'):
            r = self.client.get(reverse('admin_v2_sitemap_open'), {'node': node_id})
            self.assertEqual(r.status_code, 302)
            self.assertTrue(r['Location'].startswith(reverse('admin_v2_sitemap')), node_id)
            self.assertNotIn('admin_v2_sitemap_return', self.client.session)

    def test_open_is_gated(self):
        from unittest import mock
        from src.view import admin_v2
        with mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', set()):
            r = self.client.get(reverse('admin_v2_sitemap_open'), {'node': '/songbook/'})
        self.assertNotEqual(r['Location'], '/songbook/')
        self.assertNotIn('admin_v2_sitemap_return', self.client.session)


class SitemapClassificationTests(AdminV2TestCase):
    """GET routes with side effects are actions, not pages (no "Open page")."""

    def test_side_effect_routes_are_not_pages(self):
        routes = {r['name']: r for r in sm.build_tree()['routes']}
        for name in ('track_email_view', 'track_event_reminder_email_view', 'login-as', 'return_to_original_user'):
            self.assertEqual(routes[name]['kind'], sm.KIND_ACTION, name)
        self.assertEqual(routes['site_monitor_static_assets']['kind'], sm.KIND_API)


class SitemapGapFlagTests(AdminV2TestCase):
    """Only gaps a member could reach by shortening an address are flagged."""

    def setUp(self):
        super().setUp()
        Committee.objects.create(name='Sitemap Exec', code='SMEXEC')
        cache.clear()

    def test_parameter_and_machine_gaps_are_not_flagged(self):
        nodes = {n['id']: n for n in sm.build_tree()['nodes']}
        card = next(n for i, n in nodes.items() if n['path'] == '/directory/<user_id>/card/')
        self.assertEqual(card['m'], '')                                   # /directory/<user_id>/ has a parameter
        self.assertFalse([n for n in nodes.values() if n['m'].startswith(('/api/', '/admin/'))])
        self.assertEqual(nodes['/committee/SMEXEC/']['m'], '')            # /committee/ redirects since v4.4.5
        self.assertEqual(nodes['/committee/SMEXEC/']['p'], '/committees/')
        legis = [n for n in nodes.values() if n['m'] and n['m'].strip('/').split('/')[0] == 'feedback']
        self.assertTrue(legis)                                            # a remaining folder a member can reach

    def test_json_views_are_not_pages(self):
        routes = {r['name']: r for r in sm.build_tree()['routes']}
        self.assertEqual(routes['profile_card']['kind'], sm.KIND_API)
