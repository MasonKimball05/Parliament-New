"""
Archived events are admin-only (v4.1.0, 10-27-26).

The three views used `user_passes_test(lambda u: u.is_admin)`, which answers a
signed-in non-admin with a redirect to the LOGIN page, as if they were signed
out. Found in the mobile app audit: an officer opening /officers/archived-events/
landed on a login screen. They now use `admin_required` like the rest of the
site: 403 "Admins only." for a non-admin, the page for an admin.
"""
from django.test import Client, TestCase
from django.urls import reverse

from src.models import Event, ParliamentUser


def make_user(uid, **kwargs):
    defaults = dict(name=uid, username=uid, member_type='Officer', member_status='Active')
    defaults.update(kwargs)
    return ParliamentUser.objects.create_user(user_id=uid, **defaults)


class ArchivedEventsAccessTests(TestCase):

    def setUp(self):
        self.officer = make_user('arch-officer')
        self.admin = make_user('arch-admin', is_admin=True)
        self.event = Event.objects.create(title='Old', date_time='2026-01-01T12:00:00Z',
                                          created_by=self.admin)

    def client_for(self, user):
        c = Client()
        c.force_login(user)
        return c

    def test_non_admin_gets_403_not_the_login_page(self):
        c = self.client_for(self.officer)
        r = c.get(reverse('view_archived_events'))
        self.assertEqual(r.status_code, 403)
        for name in ('archive_event', 'unarchive_event'):
            with self.subTest(view=name):
                r = c.post(reverse(name, args=[self.event.id]))
                self.assertEqual(r.status_code, 403)
        self.event.refresh_from_db()
        self.assertFalse(self.event.archived)

    def test_admin_can_view_and_archive(self):
        c = self.client_for(self.admin)
        self.assertEqual(c.get(reverse('view_archived_events')).status_code, 200)
        c.post(reverse('archive_event', args=[self.event.id]))
        self.event.refresh_from_db()
        self.assertTrue(self.event.archived)
