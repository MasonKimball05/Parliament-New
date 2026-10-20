"""
10-20-26 — /api/admin-roles/ is admin-only.

`get_admin_roles` was @login_required only, so any signed-in member (pledges
included) could list which roles grant admin. Its one caller is the Sync
Officer Admins modal on the member list, shown only to admins, so it now uses
`admin_required`: a plain member and a non-admin officer get 403, an admin gets
the list, and a signed-out request goes to the login page.

Run with: python manage.py test src.tests.security.test_admin_roles_api
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import ParliamentUser, Role


class AdminRolesApiTests(TestCase):
    def setUp(self):
        self.url = reverse('get_admin_roles')
        self.member = ParliamentUser.objects.create(user_id='AR-1', username='ar1', name='Member',
                                                    member_type='Member', member_status='Active')
        self.officer = ParliamentUser.objects.create(user_id='AR-2', username='ar2', name='Officer',
                                                     member_type='Officer', member_status='Active')
        self.admin = ParliamentUser.objects.create(user_id='AR-3', username='ar3', name='Admin',
                                                   member_type='Officer', member_status='Active', is_admin=True)
        self.role = Role.objects.create(name='Admin Role Test', code='ART', grants_admin=True)
        Role.objects.create(name='Plain Role Test', code='PRT', grants_admin=False)

    def _c(self, u):
        c = Client(); c.force_login(u); return c

    def test_a_plain_member_is_forbidden(self):
        self.assertEqual(self._c(self.member).get(self.url).status_code, 403)

    def test_a_non_admin_officer_is_forbidden(self):
        self.assertEqual(self._c(self.officer).get(self.url).status_code, 403)

    def test_an_admin_gets_only_the_admin_granting_roles(self):
        r = self._c(self.admin).get(self.url)
        self.assertEqual(r.status_code, 200)
        codes = {role['code'] for role in r.json()['roles']}
        self.assertIn('ART', codes)
        self.assertNotIn('PRT', codes)

    def test_signed_out_redirects_to_login(self):
        r = Client().get(self.url)
        self.assertEqual(r.status_code, 302)
        self.assertIn('login', r['Location'])
