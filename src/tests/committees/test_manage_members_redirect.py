"""
11-06-26: adding or removing a committee member on the website saved the
change and then answered 500 (it redirected to a URL name that doesn't exist).
It now returns to the Manage Members page; an unknown member is a 404.

    python manage.py test src.tests.committees.test_manage_members_redirect
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import Committee
from src.tests.landing.test_web_refresh_pages_c import make_user


class ManageMembersRedirectTests(TestCase):
    def setUp(self):
        self.chair = make_user('mmr-1')
        self.member = make_user('mmr-2')
        self.committee = Committee.objects.create(name='Redirect', code='RDR', is_active=True)
        self.committee.members.add(self.chair)
        self.committee.chairs.add(self.chair)
        self.client = Client()
        self.client.force_login(self.chair)
        self.url = reverse('manage_members', args=[self.committee.code])

    def test_add_returns_to_the_page(self):
        from src.committee_management import MANAGE_ACTIONS
        action = next(a for a in MANAGE_ACTIONS if 'add' in a and 'member' in a)
        r = self.client.post(self.url, {'action': action, 'user_id': self.member.user_id})
        self.assertRedirects(r, self.url, fetch_redirect_response=False)
        self.assertTrue(self.committee.members.filter(pk=self.member.pk).exists())

    def test_unknown_member_is_404(self):
        r = self.client.post(self.url, {'action': 'add_member', 'user_id': 'nobody'})
        self.assertEqual(r.status_code, 404)
