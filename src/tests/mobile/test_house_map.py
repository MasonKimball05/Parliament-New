"""
v4.4.0 — the House Map in the app (src/mobile/views_house_map.py).

The app must show what ``house_map`` shows: the same houses, counts and
big/little trees (one shared builder, ``house_map_data``), the same
unassigned count, behind the page's toggle and pledge restriction.

Until its route is in src/mobile/urls.py, this module is its own urlconf: the
project's urlconf plus ``house-map/``.

    python manage.py test src.tests.mobile.test_house_map
"""
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import include, path, reverse

from src.mobile import views_house_map as v
from src.models import ParliamentUser
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict, toggle

API = '/api/mobile/v1/'

_routes = [
    path('house-map/', v.HouseMapView.as_view(), name='mobile_house_map'),
]


def _urlpatterns():
    from src import urls as root
    return [path('api/mobile/v1/', include((_routes, 'mobile_hm')))] + root.urlpatterns


urlpatterns = _urlpatterns()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], ROOT_URLCONF=__name__)
class HouseMapViewTests(TestCase):

    def setUp(self):
        cache.clear()
        # A Smith family: a houseless root (implied by his little), his Smith
        # little, a houseless grand-little (follows), and a Knox grand-little
        # (a different house, so he stops the Smith tree).
        self.root = make_user('hm-root', name='Adam Root', member_status='Alumni')
        self.mid = make_user('hm-mid', name='Ben Mid', house='Smith', big_brother=self.root,
                             role_number='101', pledge_class='Founders', pledge_class_greek='Founder')
        self.leaf = make_user('hm-leaf', name='Carl Leaf', big_brother=self.mid)
        self.knox = make_user('hm-knox', name='Dan Knox', house='Knox', big_brother=self.mid)
        self.user = make_user('hm-user', name='Eve User', member_status='Pledge', member_type='Pledge')
        make_user('hm-gone', name='Gone Guy', member_status='Removed')
        _, self.access, _ = make_device(self.user)
        self.api = Client()

    def get(self, access=None):
        return self.api.get(API + 'house-map/', **bearer(access or self.access))

    def test_houses_and_trees(self):
        r = self.get()
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertEqual([h['code'] for h in body['houses']], [c for c, _ in ParliamentUser.HOUSE_CHOICES])
        self.assertEqual(body['web'], {'page': '/house-map/'})
        self.assertFalse(body['can_set_house'])

        smith = next(h for h in body['houses'] if h['code'] == 'Smith')
        self.assertEqual((smith['label'], smith['color']), ('Smith', '#6366f1'))
        self.assertEqual((smith['active'], smith['total']), (1, 3))
        [tree] = smith['trees']
        self.assertEqual((tree['user_id'], tree['member_status']), ('hm-root', 'Alumni'))
        [mid] = tree['littles']
        self.assertEqual(mid['user_id'], 'hm-mid')
        self.assertEqual((mid['role_number'], mid['class_greek']), ('101', 'Founder'))
        self.assertEqual([lb['user_id'] for lb in mid['littles']], ['hm-leaf'])

        knox = next(h for h in body['houses'] if h['code'] == 'Knox')
        self.assertEqual([t['user_id'] for t in knox['trees']], ['hm-knox'])
        gordon = next(h for h in body['houses'] if h['code'] == 'Gordon')
        self.assertEqual((gordon['trees'], gordon['total']), ([], 0))

        # Unassigned: root, leaf and the pledge (not Removed).
        self.assertEqual(body['unassigned_count'], 3)

    def test_same_as_the_web_page(self):
        body = self.get().json()
        web = Client()
        web.force_login(self.user)
        ctx = web.get(reverse('house_map')).context
        self.assertEqual(body['unassigned_count'], ctx['unassigned_count'])
        self.assertEqual([{k: h[k] for k in ('code', 'label', 'total', 'active')} for h in body['houses']],
                         ctx['houses'])
        self.assertEqual([{'code': h['code'], 'trees': h['trees']} for h in body['houses']],
                         ctx['houses_data'])

    def test_house_managers(self):
        officer = make_user('hm-off', member_type='Officer')
        _, access, _ = make_device(officer)
        self.assertTrue(self.get(access).json()['can_set_house'])

    def test_page_toggle_and_pledge_restriction(self):
        toggle('house_map', False)
        r = self.get()
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'page_disabled')
        toggle('house_map', True)
        self.assertEqual(self.get().status_code, 200)

        restrict('house_map', [])
        cache.clear()   # is_allowed caches the restriction list
        r = self.get()
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['error'], 'pledge_restricted')

    def test_needs_a_token(self):
        self.assertEqual(self.api.get(API + 'house-map/').status_code, 401)
