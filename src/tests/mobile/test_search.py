"""
v4.4.0 — Search in the app (src/mobile/views_search.py), the website's
``global_search`` page as data.

Both run the page's ``search_everything`` (src/view/global_search.py), so the
tests here check the app gets the same groups, in the page's order, with the
same visibility: officer-only members and election periods, Kai behind its own
permission (and its description behind "details"), chat access, announcement
and event audiences, and committee documents only for that committee. The
page's flag and pledge restriction apply; it has no page toggle.

Until its route is in src/mobile/urls.py, this module is its own urlconf: the
project's urlconf plus ``search/``.

    python manage.py test src.tests.mobile.test_search
"""
import shutil
import tempfile
from datetime import timedelta

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import include, path
from django.utils import timezone

from src.mobile import views_search as v
from src.models import (
    Announcement, ChatChannel, Committee, CommitteeDocument, Event, KaiReport, Legislation, PassedResolution,
    SlatingPeriod,
)
from src.models.kai import KaiMemberPermission
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag, restrict, toggle
from src.view.global_search import search_everything

API = '/api/mobile/v1/'

_routes = [path('search/', v.SearchView.as_view(), name='mobile_search')]


def _urlpatterns():
    from src import urls as root
    return [path('api/mobile/v1/', include((_routes, 'mobile_search')))] + root.urlpatterns


urlpatterns = _urlpatterns()

MEDIA = tempfile.mkdtemp(prefix='search-test-')


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], ROOT_URLCONF=__name__, MEDIA_ROOT=MEDIA)
class _Base(TestCase):

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        cache.clear()
        flag('global_search', True)
        self.user = make_user('gs-1', name='Plain Member')
        self.officer = make_user('gs-off', name='Officer Person', member_type='Officer')
        _, self.access, _ = make_device(self.user)
        _, self.officer_access, _ = make_device(self.officer)
        self.api = Client()

    def search(self, q, access=None):
        return self.api.get(API + 'search/', {'q': q}, **bearer(access or self.access))

    def groups(self, q, access=None):
        r = self.search(q, access)
        self.assertEqual(r.status_code, 200, r.content)
        return {g['key']: g for g in r.json()['groups']}

    def doc(self, title, **kw):
        fields = dict(title=title, uploaded_by=self.officer,
                      document=SimpleUploadedFile(f'{title}.pdf', b'%PDF-1.4 fake', content_type='application/pdf'))
        fields.update(kw)
        return CommitteeDocument.objects.create(**fields)


class SearchTests(_Base):

    def test_empty_query_returns_nothing(self):
        r = self.search('   ')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'query': '', 'total_count': 0, 'groups': [], 'web': {'page': '/search/'}})

    def test_too_long_is_invalid_input(self):
        r = self.search('x' * (v.MAX_QUERY + 1))
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))

    def test_groups_in_the_pages_order_with_targets(self):
        leg = Legislation.objects.create(title='Zebra budget', description='About zebras.', posted_by=self.officer,
                                         available_at=timezone.now() - timedelta(days=1))
        ann = Announcement.objects.create(title='Zebra party', content='Bring zebras.', posted_by=self.officer)
        ev = Event.objects.create(title='Zebra meeting', description='d', created_by=self.officer,
                                  location='Zoo', date_time=timezone.now() + timedelta(days=2))
        res = PassedResolution.objects.create(title='Zebra act', description='d', date_passed=timezone.now().date())
        doc = self.doc('Zebra guide', published_to_chapter=True)
        chan = ChatChannel.objects.create(name='Zebra chat', access_type='open', created_by=self.officer)

        r = self.search(' zebra ')
        body = r.json()
        self.assertEqual(body['query'], 'zebra')
        keys = [g['key'] for g in body['groups']]
        order = [k for k, _ in v.GROUPS]
        self.assertEqual(keys, sorted(keys, key=order.index))
        self.assertTrue({'legislation', 'announcements', 'events', 'passed_resolutions',
                         'chapter_documents', 'chat_channels'} <= set(keys))
        self.assertEqual(body['total_count'], sum(len(g['items']) for g in body['groups']))
        g = {x['key']: x for x in body['groups']}

        self.assertEqual(g['legislation']['title'], 'Active Legislation')
        self.assertEqual(g['legislation']['items'][0]['target'],
                         {'url': f'/legislation/{leg.id}/', 'screen': 'vote', 'id': leg.id})
        self.assertEqual(g['announcements']['items'][0]['target'],
                         {'url': '/announcements/', 'screen': 'announcements', 'id': ann.id})
        self.assertEqual(g['events']['items'][0]['target'], {'url': '/calendar/', 'screen': 'calendar', 'id': ev.id})
        self.assertEqual(g['events']['items'][0]['location'], 'Zoo')
        self.assertEqual(g['passed_resolutions']['items'][0]['id'], res.id)
        self.assertEqual(g['chat_channels']['items'][0]['target'], {'url': f'/chat/{chan.id}/', 'screen': 'chat',
                                                                    'id': chan.id})
        d = g['chapter_documents']['items'][0]
        self.assertEqual(d['document']['file'], f'documents/{doc.id}/file/')
        self.assertTrue(d['document']['file_name'].endswith('.pdf'))

    def test_pages_match_keywords(self):
        g = self.groups('calendar')
        names = [p['name'] for p in g['pages']['items']]
        self.assertIn('Event Calendar', names)
        cal = next(p for p in g['pages']['items'] if p['name'] == 'Event Calendar')
        self.assertEqual(cal['target']['screen'], 'calendar')
        # Officer-only pages are only for officers, as on the page.
        self.assertNotIn('pages', self.groups('attendance'))
        self.assertIn('Take Attendance',
                      [p['name'] for p in self.groups('attendance', self.officer_access)['pages']['items']])

    def test_members_and_election_periods_are_officer_only(self):
        make_user('gs-target', name='Quillon Searchable', role_number='173')
        SlatingPeriod.objects.create(name='Quillon election', description='', created_by=self.officer)
        self.assertNotIn('users', self.groups('Quillon'))
        self.assertNotIn('slating_periods', self.groups('Quillon'))
        g = self.groups('Quillon', self.officer_access)
        self.assertEqual(g['users']['items'][0]['user_id'], 'gs-target')
        self.assertEqual(g['slating_periods']['title'], 'Election Periods')
        # Searching by roll number finds him (v3.25.0).
        self.assertEqual(self.groups('173', self.officer_access)['users']['items'][0]['user_id'], 'gs-target')

    def test_audiences_and_chat_access(self):
        Announcement.objects.create(title='Yonder officers', content='c', posted_by=self.officer,
                                    visible_to=['Officer'])
        Event.objects.create(title='Yonder retreat', description='d', created_by=self.officer,
                             date_time=timezone.now() + timedelta(days=2), visible_to=['Officer'])
        ChatChannel.objects.create(name='Yonder private', access_type='restricted', created_by=self.officer)
        g = self.groups('Yonder')
        self.assertNotIn('announcements', g)
        self.assertNotIn('events', g)
        self.assertNotIn('chat_channels', g)
        g = self.groups('Yonder', self.officer_access)
        self.assertIn('announcements', g)
        self.assertIn('events', g)

    def test_committee_documents_only_for_its_members(self):
        comm = Committee.objects.create(name='Finance', code='FIN', is_active=True)
        doc = self.doc('Xylo ledger', committee=comm)
        self.assertNotIn('committee_documents', self.groups('Xylo'))
        comm.members.add(self.user)
        item = self.groups('Xylo')['committee_documents']['items'][0]
        self.assertEqual(item['committee'], {'code': 'FIN', 'name': 'Finance'})
        self.assertEqual(item['document']['file'], f'committees/FIN/documents/{doc.id}/file/')
        self.assertEqual(item['target']['url'], '/committee/FIN/documents/')

    def test_kai_reports_need_permission_and_details(self):
        flag('kai_reports', True)
        kai = Committee.objects.create(name='Kai', code='KAI', is_active=True, is_kai_committee=True)
        KaiReport.objects.create(title='Wombat matter', category='behavioral', description='The allegation',
                                 submitted_by=self.officer)
        self.assertNotIn('kai_reports', self.groups('Wombat'))
        perm = KaiMemberPermission.objects.create(committee=kai, user=self.user, can_view_report_list=True,
                                                  can_view_report_details=False)
        item = self.groups('Wombat')['kai_reports']['items'][0]
        self.assertIsNone(item['description'])
        # List-only reviewers don't match on the body either.
        self.assertNotIn('kai_reports', self.groups('allegation'))
        perm.can_view_report_details = True
        perm.save()
        cache.clear()
        self.assertEqual(self.groups('Wombat')['kai_reports']['items'][0]['description'], 'The allegation')
        flag('kai_reports', False)
        cache.clear()
        self.assertNotIn('kai_reports', self.groups('Wombat'))

    def test_same_results_as_the_page(self):
        Legislation.objects.create(title='Vortex bill', description='d', posted_by=self.officer,
                                   available_at=timezone.now())
        Announcement.objects.create(title='Vortex news', content='c', posted_by=self.officer)
        make_user('gs-vortex', name='Vortex Man')
        for user, access in ((self.user, self.access), (self.officer, self.officer_access)):
            results, _ = search_everything('vortex', user)
            body = self.search('vortex', access).json()
            self.assertEqual([g['key'] for g in body['groups']], [k for k, _ in v.GROUPS if results.get(k)])
            self.assertEqual({g['key']: len(g['items']) for g in body['groups']},
                             {k: len(x) for k, x in results.items()})
        # And the website still renders its page from the same helper.
        web = Client()
        web.force_login(self.officer)
        r = web.get('/search/', {'q': 'vortex'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Vortex bill')
        self.assertContains(r, 'Vortex Man')


class GateTests(_Base):

    def test_sign_in_required(self):
        self.assertEqual(self.api.get(API + 'search/', {'q': 'x'}).status_code, 401)

    def test_feature_flag(self):
        flag('global_search', False)
        cache.clear()
        r = self.search('home')
        self.assertEqual((r.status_code, r.json()['error']), (403, 'feature_disabled'))

    def test_pledge_restriction_but_no_page_toggle(self):
        pledge = make_user('gs-pl', member_type='Pledge')
        _, pledge_access, _ = make_device(pledge)
        restrict('global_search', phases=())
        r = self.search('home', pledge_access)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'pledge_restricted'))
        toggle('global_search', False)
        self.assertEqual(self.search('home').status_code, 200)
