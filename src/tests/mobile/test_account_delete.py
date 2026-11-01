"""
Deleting your account from the app (src/mobile/views_account.py), and how a
deleted account shows in the app's directory and search.

Until its routes are in src/mobile/urls.py, this module is its own urlconf:
the project's urlconf plus ``account/delete/`` and its passkey steps.

    python manage.py test src.tests.mobile.test_account_delete
"""
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import include, path
from django.utils import timezone

from src import account_deletion as deletion
from src.mobile import views_account as v
from src.models import ParliamentUser
from src.models.mobile import MobileAuthTicket, MobileDevice
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

API = '/api/mobile/v1/'

_routes = [
    path('account/delete/', v.AccountDeleteView.as_view(), name='mobile_account_delete'),
    path('account/delete/passkey/begin/', v.AccountPasskeyBeginView.as_view(),
         name='mobile_account_delete_passkey_begin'),
    path('account/delete/passkey/complete/', v.AccountPasskeyCompleteView.as_view(),
         name='mobile_account_delete_passkey_complete'),
]


def _urlpatterns():
    from src import urls as root
    return [path('api/mobile/v1/', include((_routes, 'mobile_acct')))] + root.urlpatterns


urlpatterns = _urlpatterns()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], ROOT_URLCONF=__name__)
class AccountDeleteAPITests(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('ma-user', name='Mia App', email='mia@example.com')
        self.device, self.access, _ = make_device(self.user)
        self.other_device, self.other_access, _ = make_device(self.user, name='iPad')
        self.admin = make_user('ma-admin', is_admin=True, member_type='Officer', email='boss@example.com')
        self.api = Client()

    def post(self, body, access=None, url='account/delete/'):
        return self.api.post(API + url, body, content_type='application/json', **bearer(access or self.access))

    def test_wrong_password(self):
        r = self.post({'password': 'wrong'})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'reauth_failed')
        self.assertTrue(ParliamentUser.objects.get(pk='ma-user').is_active)

    def test_no_credential(self):
        r = self.post({})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['error'], 'password_required')

    def test_deletes_and_signs_out_this_device(self):
        mail.outbox = []
        r = self.post({'password': 'pw-ma-user'})
        self.assertEqual(r.status_code, 200, r.content)
        purge = (timezone.localdate() + deletion.PURGE_AFTER).isoformat()
        self.assertEqual(r.json(), {'ok': True, 'purge_on': purge})
        u = ParliamentUser.objects.get(pk='ma-user')
        self.assertEqual(u.deletion_requested_via, 'app')
        self.assertFalse(u.is_active)
        for d in MobileDevice.objects.filter(user=u):
            self.assertIsNotNone(d.revoked_at)
        self.assertEqual(self.api.get(API + 'me/', **bearer(self.access)).status_code, 401)
        self.assertEqual(self.api.get(API + 'me/', **bearer(self.other_access)).status_code, 401)
        self.assertTrue(any(e.to == ['mia@example.com'] for e in mail.outbox))
        self.assertTrue(any('boss@example.com' in e.to for e in mail.outbox))

    def test_passkey_grant(self):
        raw, _ = MobileAuthTicket.issue(MobileAuthTicket.PURPOSE_DELETE_GRANT, user=self.user, device=self.device)
        r = self.post({'reauth_grant': 'not-it'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'reauth_failed'))
        r = self.post({'reauth_grant': raw})
        self.assertEqual(r.status_code, 200, r.content)

    def test_grant_from_another_device_refused(self):
        raw, _ = MobileAuthTicket.issue(MobileAuthTicket.PURPOSE_DELETE_GRANT, user=self.user,
                                        device=self.other_device)
        r = self.post({'reauth_grant': raw})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'reauth_failed'))

    def test_a_vote_grant_cannot_delete_the_account(self):
        raw, _ = MobileAuthTicket.issue(MobileAuthTicket.PURPOSE_VOTE_GRANT, user=self.user, device=self.device)
        r = self.post({'reauth_grant': raw})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'reauth_failed'))

    def test_passkey_begin_not_gated_by_votes_page(self):
        pledge = make_user('ma-pledge', member_type='Pledge', member_status='Active')
        _, access, _ = make_device(pledge)
        r = self.post({}, access=access, url='account/delete/passkey/begin/')
        # No passkeys registered — but not refused as a pledge or by the vote page.
        self.assertEqual((r.status_code, r.json()['error']), (400, 'no_passkeys'))

    def test_admin_refused(self):
        _, access, _ = make_device(self.admin)
        r = self.post({'password': 'pw-ma-admin'}, access=access)
        self.assertEqual((r.status_code, r.json()['error']), (403, 'not_allowed'))
        self.assertTrue(ParliamentUser.objects.get(pk='ma-admin').is_active)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class FormerMemberInTheAppTests(TestCase):

    def setUp(self):
        cache.clear()
        self.viewer = make_user('ma-view', member_type='Officer')
        _, self.access, _ = make_device(self.viewer)
        self.gone = make_user('ma-gone', name='Gus Gone', email='gus@example.com', phone_number='5557777',
                              role_number='55')
        deletion.request_deletion(self.gone, 'web')
        self.api = Client()

    def get(self, url):
        return self.api.get(API + url, **bearer(self.access))

    def test_directory_record_only(self):
        rows = self.get('members/').json()['members']
        self.assertNotIn('ma-gone', [r['user_id'] for r in rows])     # not an active member
        rows = self.get('members/?alumni=1').json()['members']
        row = next(r for r in rows if r['user_id'] == 'ma-gone')
        self.assertEqual((row['member_status'], row['former_member']), ('Former member', True))
        self.assertEqual((row['email'], row['phone'], row['photo']), ('', '', None))
        self.assertEqual(row['role_number'], '55')
        card = self.get('members/ma-gone/').json()['member']
        self.assertEqual((card['email'], card['phone'], card['photo']), ('', '', None))
        self.assertTrue(card['former_member'])
        self.assertEqual(self.get('members/ma-gone/photo/').status_code, 404)
