"""Shared fixtures for the mobile API tests."""
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice
from rest_framework.test import APIClient

from src.models import ParliamentUser

PASSWORD = 'mobile-test-pass-8812!'
API = '/api/mobile/v1/'

FAKE_GEO = {
    'country': 'United States', 'country_code': 'US', 'city': 'Birmingham',
    'region': 'Alabama', 'lat': 33.52, 'lon': -86.80, 'org': '', 'as': '',
}
FAKE_SIGNAL_GEO = {
    'country': 'United States', 'city': 'Birmingham', 'region': 'Alabama',
    'latitude': 33.52, 'longitude': -86.80,
}


def make_user(uid='mob1', username=None, **kwargs):
    username = username or f'{uid}user'
    defaults = dict(name=f'Mobile {uid}', member_type='Member', member_status='Active')
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, username=username, **defaults)
    user.set_password(PASSWORD)
    user.email = f'{username}@parliament.test'
    user.save()
    return user


def totp_device(user):
    return TOTPDevice.objects.create(user=user, name='default', confirmed=True)


def totp_now(device):
    device.refresh_from_db()
    return TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift).token()


def totp_code(device):
    return str(totp_now(device)).zfill(device.digits)


class MobileTestCase(TestCase):
    """Geo lookups mocked (never hit ip-api.com); cache cleared per test."""

    def setUp(self):
        cache.clear()
        p1 = patch('src.geo_utils.get_ip_geo', return_value=FAKE_GEO)
        p2 = patch('src.signals.get_geolocation_from_ip', return_value=FAKE_SIGNAL_GEO)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)
        self.api = APIClient()

    def post(self, path, data=None, **extra):
        return self.api.post(API + path, data or {}, format='json', **extra)

    def login(self, identifier, password=PASSWORD, **more):
        body = {'identifier': identifier, 'password': password, 'platform': 'ios',
                'device_name': 'Test iPhone', 'app_version': '1.0.0', **more}
        return self.post('auth/login/', body)

    def bearer(self, access):
        return {'HTTP_AUTHORIZATION': f'Bearer {access}'}

    def get_me(self, access):
        return self.api.get(API + 'me/', **self.bearer(access))
