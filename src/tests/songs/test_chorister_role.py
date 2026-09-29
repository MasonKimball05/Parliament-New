"""
v3.35.3 (09-29-26) — an assigned Chorister can manage songs.

`manage_categories` creates the Chorister role with code 'CHOIR', but
`can_manage_songs` checked code 'Chorister' (the role's NAME), so the role
granted nothing to a plain Member.

Run with: python manage.py test src.tests.songs.test_chorister_role
"""
from django.test import TestCase
from django.urls import reverse

from src.models import ParliamentUser, Role
from src.view.songbook import can_manage_songs


class ChoristerRoleTests(TestCase):
    def setUp(self):
        self.member = ParliamentUser.objects.create_user(
            user_id='ch1', name='Choir Member', username='ch1', member_type='Member')
        # Exactly what manage_categories creates.
        self.role, _ = Role.objects.get_or_create(code='CHOIR', defaults={'name': 'Chorister'})

    def test_plain_member_cannot_manage_songs(self):
        self.assertFalse(can_manage_songs(self.member))

    def test_assigned_chorister_can_manage_songs(self):
        self.member.roles.add(self.role)
        self.assertTrue(can_manage_songs(self.member))

    def test_assigned_chorister_reaches_the_add_song_page(self):
        self.member.roles.add(self.role)
        self.client.force_login(self.member)
        r = self.client.get(reverse('song_create'))
        self.assertEqual(r.status_code, 200)
