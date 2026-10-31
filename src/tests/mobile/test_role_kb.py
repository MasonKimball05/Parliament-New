"""
v4.4.0 — the Role Knowledge Base in the app (src/mobile/views_role_kb.py).

The app must show what ``role_knowledge_base_index`` and
``role_knowledge_base`` show (one shared builder each), to the same people
(any signed-in member; pledge restriction only, no page toggle), and must
not create a ``RoleKnowledgeBase`` row on read.

Until its routes are in src/mobile/urls.py, this module is its own urlconf:
the project's urlconf plus ``role-knowledge-base/``.

    python manage.py test src.tests.mobile.test_role_kb
"""
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import include, path, reverse

from src.mobile import views_role_kb as v
from src.models import Role, RoleKnowledgeBase, RoleKnowledgeBaseRevision
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict, toggle

API = '/api/mobile/v1/'

_routes = [
    path('role-knowledge-base/', v.RoleKnowledgeBaseIndexView.as_view(), name='mobile_role_kb_index'),
    path('role-knowledge-base/<int:role_id>/', v.RoleKnowledgeBaseView.as_view(), name='mobile_role_kb'),
]


def _urlpatterns():
    from src import urls as root
    return [path('api/mobile/v1/', include((_routes, 'mobile_rkb')))] + root.urlpatterns


urlpatterns = _urlpatterns()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], ROOT_URLCONF=__name__)
class RoleKnowledgeBaseViewTests(TestCase):

    def setUp(self):
        cache.clear()
        self.written = Role.objects.create(name='Zz Treasurer', code='ZZT')
        self.empty = Role.objects.create(name='Zz Scribe', code='ZZS')
        self.officer = make_user('rkb-off', name='Olive Officer', member_type='Officer')
        self.officer.roles.add(self.written)
        gone = make_user('rkb-alum', name='Alan Alum', member_status='Alumni')
        gone.roles.add(self.written)
        kb = RoleKnowledgeBase.objects.create(role=self.written)
        RoleKnowledgeBaseRevision.objects.create(knowledge_base=kb, content='old notes', edited_by=self.officer)
        self.rev = RoleKnowledgeBaseRevision.objects.create(
            knowledge_base=kb, edited_by=self.officer,
            content='Pay the dues by Friday.\r\nCheck https://example.com/bank\r\n\r\n\r\nKeep receipts.')
        self.user = make_user('rkb-user', member_status='Active', member_type='Pledge')
        _, self.access, _ = make_device(self.user)
        self.api = Client()

    def get(self, suffix='', access=None, **params):
        return self.api.get(API + 'role-knowledge-base/' + suffix, params, **bearer(access or self.access))

    def test_index(self):
        r = self.get()
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual(body['web'], {'page': '/role-knowledge-base/'})
        self.assertEqual(body['total_roles'], Role.objects.count())
        rows = {row['name']: row for row in body['roles']}
        self.assertEqual(rows['Zz Treasurer'], {
            'id': self.written.id, 'name': 'Zz Treasurer', 'holders': ['Olive Officer'], 'has_content': True})
        self.assertEqual(rows['Zz Scribe']['holders'], [])
        self.assertFalse(rows['Zz Scribe']['has_content'])
        self.assertEqual([row['name'] for row in body['roles']], sorted(rows))

    def test_search_matches_current_text_only(self):
        self.assertEqual([row['id'] for row in self.get(q='  RECEIPTS ').json()['roles']], [self.written.id])
        self.assertEqual(self.get(q='receipts').json()['query'], 'receipts')
        self.assertEqual(self.get(q='old notes').json()['roles'], [])   # a past revision
        self.assertEqual([row['id'] for row in self.get(q='olive').json()['roles']], [self.written.id])

    def test_index_same_as_the_web_page(self):
        web = Client()
        web.force_login(self.user)
        for q in ('', 'zz'):
            ctx = web.get(reverse('role_knowledge_base_index'), {'q': q}).context
            body = self.get(q=q).json()
            self.assertEqual(body['total_roles'], ctx['total_roles'])
            self.assertEqual(body['roles'], [
                {'id': d['role'].id, 'name': d['role'].name, 'holders': d['holders'],
                 'has_content': d['has_content']} for d in ctx['roles_data']])

    def test_page(self):
        r = self.get(f'{self.written.id}/')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual((body['id'], body['name']), (self.written.id, 'Zz Treasurer'))
        self.assertEqual(body['revision_count'], 2)
        self.assertFalse(body['can_edit'])
        cur = body['current']
        self.assertEqual(cur['content'], self.rev.content)
        self.assertEqual(cur['paragraphs'], ['Pay the dues by Friday.\nCheck https://example.com/bank', 'Keep receipts.'])
        self.assertEqual(cur['edited_by'], 'Olive Officer')
        self.assertEqual(body['web'], {
            'page': f'/officers/roles/{self.written.id}/knowledge/',
            'history': f'/officers/roles/{self.written.id}/knowledge/history/',
            'index': '/role-knowledge-base/',
        })

        web = Client()
        web.force_login(self.user)
        ctx = web.get(reverse('role_knowledge_base', kwargs={'role_id': self.written.id})).context
        self.assertEqual(ctx['current_revision'], self.rev)
        self.assertEqual((ctx['revision_count'], ctx['can_edit']), (2, False))

    def test_empty_page_creates_no_row(self):
        body = self.get(f'{self.empty.id}/').json()
        self.assertIsNone(body['current'])
        self.assertEqual(body['revision_count'], 0)
        self.assertFalse(RoleKnowledgeBase.objects.filter(role=self.empty).exists())

    def test_officers_and_chairs_can_edit(self):
        for uid, kind in (('rkb-o2', 'Officer'), ('rkb-c2', 'Chair')):
            _, access, _ = make_device(make_user(uid, member_type=kind))
            self.assertTrue(self.get(f'{self.empty.id}/', access=access).json()['can_edit'])

    def test_unknown_role(self):
        r = self.get('999999/')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()['error'], 'not_found')

    def test_no_toggle_but_pledge_restriction(self):
        toggle('role_knowledge_base_index', False)
        toggle('role_knowledge_base', False)
        self.assertEqual(self.get().status_code, 200)   # the site ignores toggles here too
        self.assertEqual(self.get(f'{self.written.id}/').status_code, 200)

        restrict('role_knowledge_base_index', [])
        restrict('role_knowledge_base', [])
        cache.clear()   # is_allowed caches the restriction list
        for suffix in ('', f'{self.written.id}/'):
            r = self.get(suffix)
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.json()['error'], 'pledge_restricted')

    def test_needs_a_token(self):
        self.assertEqual(self.api.get(API + 'role-knowledge-base/').status_code, 401)
        self.assertEqual(self.api.get(API + f'role-knowledge-base/{self.written.id}/').status_code, 401)
