"""v4.4.15 — routes that answered 500 in the 11-19-26 pentest sweep."""
from django.test import Client, TestCase, override_settings

from src.models import ParliamentUser
from src.tests.mobile.sess_helpers import bearer, make_device, make_user


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class ServerErrorTests(TestCase):
    def test_get_on_the_post_only_excuse_review_is_405(self):
        officer = make_user('v4415-off', member_type='Officer')
        r = Client().get('/api/mobile/v1/officer/excuses/999/', **bearer(make_device(officer)[1]))
        self.assertEqual(r.status_code, 405)

    def _admin(self):
        admin = ParliamentUser.objects.create_user('v4415-adm', 'Admin', 'v4415adm', 'Officer', 'x', is_admin=True)
        c = Client()
        c.force_login(admin)
        return c

    @override_settings(REQUIRE_2FA_FOR_ADMINS=False, REQUIRE_2FA_FOR_OFFICERS=False)
    def test_maintenance_admin_with_a_missing_or_bad_id_does_not_crash(self):
        c = self._admin()
        r = c.get('/admin/src/scheduledmaintenance/')
        self.assertEqual(r.status_code, 200, 'the admin must actually be reachable for this test to mean anything')
        # Django's own "doesn't exist" handling: a redirect to the list with a message.
        for path in ('/admin/src/scheduledmaintenance/x/change/', '/admin/src/scheduledmaintenance/999999/change/'):
            self.assertEqual(c.get(path).status_code, 302, path)
        for path in ('/admin/src/scheduledmaintenance/999999/start/', '/admin/src/scheduledmaintenance/999999/complete/'):
            self.assertEqual(c.get(path).status_code, 404, path)

    @override_settings(REQUIRE_2FA_FOR_ADMINS=False, REQUIRE_2FA_FOR_OFFICERS=False)
    def test_debug_database_info_answers_on_sqlite(self):
        from pathlib import Path
        from unittest import mock
        from django.db import connection
        c = self._admin()
        # On a real SQLite deployment settings.py makes NAME a Path (BASE_DIR / ...);
        # the test database's NAME is a string, so supply the Path the bug needs.
        # The debug API's admin-v2 gate is tested in test_debug_api_gate.py.
        with mock.patch.dict(connection.settings_dict, {'NAME': Path('/tmp/db.sqlite3')}), \
                mock.patch('src.view.admin_v2.admin_v2_fully_authenticated', return_value=True):
            r = c.get('/api/debug/database/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('name', r.json())
