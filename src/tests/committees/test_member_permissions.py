"""
Member Permissions (10-29-26): chairs designate who can take minutes, manage
documents and manage members, and the document / member views honour it.

    python manage.py test src.tests.committees.test_member_permissions
"""
from django.test import TestCase, override_settings
from django.urls import reverse

from src.models import Committee, CommitteePermissions
from src.tests.mobile.sess_helpers import make_user


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class MemberPermissionsTests(TestCase):

    def setUp(self):
        self.chair = make_user('mp-chair')
        self.member = make_user('mp-mem')
        self.c = Committee.objects.create(name='Finance', code='FIN')
        self.c.chairs.add(self.chair)
        self.c.members.add(self.member)
        self.url = reverse('committee_member_permissions', args=['FIN'])

    def test_only_chairs_can_designate(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.chair)
        self.assertContains(self.client.get(self.url), 'Manage documents')

    def test_designation_opens_documents_and_members(self):
        self.client.force_login(self.member)
        self.assertNotEqual(self.client.get(reverse('upload_document', args=['FIN'])).status_code, 200)
        self.assertEqual(self.client.get(reverse('manage_members', args=['FIN'])).status_code, 403)

        self.client.force_login(self.chair)
        self.client.post(self.url, {'can_upload_docs_mp-mem': 'on', 'can_manage_members_mp-mem': 'on'})
        perm = CommitteePermissions.objects.get(committee=self.c, user=self.member)
        self.assertEqual((perm.can_upload_docs, perm.can_manage_members, perm.can_take_minutes), (True, True, False))
        self.assertTrue(perm.can_view_docs)

        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse('upload_document', args=['FIN'])).status_code, 200)
        self.assertEqual(self.client.get(reverse('manage_members', args=['FIN'])).status_code, 200)

        self.client.force_login(self.chair)
        self.client.post(self.url, {})      # unticked = taken away
        perm.refresh_from_db()
        self.assertFalse(perm.can_upload_docs)

    def test_minute_taker_sees_and_opens_minutes(self):
        advisor = make_user('mp-adv')
        self.c.advisors.add(advisor)
        minutes = reverse('committee_minutes_list', args=['FIN'])
        self.client.force_login(advisor)
        self.assertEqual(self.client.get(minutes).status_code, 302)      # not a member: sent back
        self.client.force_login(self.chair)
        self.client.post(self.url, {'can_take_minutes_mp-adv': 'on'})
        self.client.force_login(advisor)
        self.assertEqual(self.client.get(minutes).status_code, 200)
        self.client.force_login(self.chair)
        self.client.post(self.url, {'can_take_minutes_mp-mem': 'on'})
        self.client.force_login(self.member)
        self.assertContains(self.client.get(reverse('committee_home', args=['FIN'])), minutes)
