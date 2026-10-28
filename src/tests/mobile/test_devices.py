"""
Mobile plan, Phase 1 — the member's own app devices: the API, the Active
Sessions page section, and the admin registration's confidentiality.
"""
import uuid

from django.test import Client, RequestFactory, TestCase, override_settings

from src.models.mobile import MobileAuthTicket, MobileDevice
from src.tests.mobile.sess_helpers import bearer, make_device, make_user

SECRET_FIELDS = {'access_hash', 'refresh_hash', 'prev_refresh_hash', 'alt_refresh_hash',
                 'retired_refresh_hashes', 'auth_fingerprint', 'push_token',
                 'checkin_key_hash'}


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class DevicesApiTests(TestCase):

    def setUp(self):
        self.user = make_user('dev-1')
        self.other = make_user('dev-2')
        self.device, self.access, _ = make_device(self.user, name='My iPhone')
        self.tablet, _, _ = make_device(self.user, platform='android', name='Pixel')
        self.revoked, _, _ = make_device(self.user, name='Lost')
        self.revoked.revoke('test')
        self.others, _, _ = make_device(self.other, name='Theirs')
        self.api = Client()

    def test_list_shows_own_live_devices_and_marks_current(self):
        r = self.api.get('/api/mobile/v1/devices/', **bearer(self.access))
        self.assertEqual(r.status_code, 200)
        devices = {d['public_id']: d for d in r.json()['devices']}
        self.assertEqual(set(devices), {str(self.device.public_id), str(self.tablet.public_id)})
        self.assertTrue(devices[str(self.device.public_id)]['current'])
        self.assertFalse(devices[str(self.tablet.public_id)]['current'])
        self.assertEqual(set(devices[str(self.tablet.public_id)]), {
            'public_id', 'platform', 'device_name', 'app_version', 'created_at',
            'last_seen_at', 'current'})

    def test_revoke_own_device(self):
        r = self.api.post(f'/api/mobile/v1/devices/{self.tablet.public_id}/revoke/', **bearer(self.access))
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['current'])
        self.tablet.refresh_from_db()
        self.assertIsNotNone(self.tablet.revoked_at)

    def test_revoke_current_device_signs_it_out(self):
        r = self.api.post(f'/api/mobile/v1/devices/{self.device.public_id}/revoke/', **bearer(self.access))
        self.assertTrue(r.json()['current'])
        self.assertEqual(self.api.get('/api/mobile/v1/devices/', **bearer(self.access)).status_code, 401)

    def test_other_users_device_is_404(self):
        r = self.api.post(f'/api/mobile/v1/devices/{self.others.public_id}/revoke/', **bearer(self.access))
        self.assertEqual(r.status_code, 404)
        self.others.refresh_from_db()
        self.assertIsNone(self.others.revoked_at)

    def test_unknown_and_already_revoked_are_404(self):
        for pid in (uuid.uuid4(), self.revoked.public_id):
            r = self.api.post(f'/api/mobile/v1/devices/{pid}/revoke/', **bearer(self.access))
            self.assertEqual(r.status_code, 404)

    def test_no_bearer_is_refused(self):
        self.api.force_login(self.user)
        self.assertIn(self.api.get('/api/mobile/v1/devices/').status_code, (401, 403))

    def test_get_on_revoke_is_refused(self):
        r = self.api.get(f'/api/mobile/v1/devices/{self.tablet.public_id}/revoke/', **bearer(self.access))
        self.assertEqual(r.status_code, 405)


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class DevicesWebPageTests(TestCase):

    def setUp(self):
        self.user = make_user('devw-1')
        self.device, _, _ = make_device(self.user, name='My iPhone')
        self.others, _, _ = make_device(make_user('devw-2'), name='Theirs')
        self.web = Client(enforce_csrf_checks=True)
        self.web.force_login(self.user)

    def test_sessions_page_lists_own_devices(self):
        r = self.web.get('/account/sessions/')
        self.assertContains(r, 'Your devices')
        self.assertContains(r, 'My iPhone')
        self.assertNotContains(r, 'Theirs')
        self.assertContains(r, f'/account/devices/{self.device.public_id}/revoke/')

    def test_revoke_needs_csrf(self):
        r = self.web.post(f'/account/devices/{self.device.public_id}/revoke/')
        self.assertEqual(r.status_code, 403)
        self.device.refresh_from_db()
        self.assertIsNone(self.device.revoked_at)

    def test_revoke_with_csrf(self):
        page = self.web.get('/account/sessions/')
        token = page.cookies['csrftoken'].value
        r = self.web.post(f'/account/devices/{self.device.public_id}/revoke/',
                          {'csrfmiddlewaretoken': token})
        self.assertEqual(r.status_code, 302)
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.revoked_at)
        self.assertNotContains(self.web.get('/account/sessions/'), 'My iPhone')

    def test_other_users_device_is_404(self):
        c = Client()
        c.force_login(self.user)
        r = c.post(f'/account/devices/{self.others.public_id}/revoke/')
        self.assertEqual(r.status_code, 404)
        self.others.refresh_from_db()
        self.assertIsNone(self.others.revoked_at)

    def test_get_is_refused(self):
        c = Client()
        c.force_login(self.user)
        self.assertEqual(c.get(f'/account/devices/{self.device.public_id}/revoke/').status_code, 405)


class MobileAdminConfidentialityTests(TestCase):

    def _admin(self, model):
        from src.admin import admin_site
        return admin_site._registry[model]

    def _request(self):
        request = RequestFactory().get('/')
        request.user = make_user('adm-1', is_admin=True)
        return request

    def test_device_admin_hides_every_secret_and_is_view_only(self):
        ma = self._admin(MobileDevice)
        request = self._request()
        self.assertTrue(SECRET_FIELDS <= set(ma.exclude))
        self.assertFalse(SECRET_FIELDS & set(ma.get_fields(request)))
        self.assertFalse(ma.has_add_permission(request))
        self.assertFalse(ma.has_change_permission(request))

    def test_ticket_admin_hides_hash_and_payload(self):
        ma = self._admin(MobileAuthTicket)
        request = self._request()
        self.assertFalse({'token_hash', 'payload'} & set(ma.get_fields(request)))
        self.assertFalse(ma.has_add_permission(request))
        self.assertFalse(ma.has_change_permission(request))

    def test_csv_export_omits_secrets(self):
        from src.admin import export_as_csv
        user = make_user('adm-2')
        make_device(user)
        ma = self._admin(MobileDevice)
        body = export_as_csv(ma, self._request(), MobileDevice.objects.all()).content.decode()
        header = body.splitlines()[0]
        for field in SECRET_FIELDS:
            self.assertNotIn(field, header)

    def test_both_are_in_an_admin_section(self):
        from src.admin_sections import _MODEL_TO_SECTION
        self.assertIn('MobileDevice', _MODEL_TO_SECTION)
        self.assertIn('MobileAuthTicket', _MODEL_TO_SECTION)
