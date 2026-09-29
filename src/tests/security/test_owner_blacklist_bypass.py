"""
09-27-26 — the platform owner's logged-in session is not locked out by the IP
blacklist. Everyone else on a blacklisted IP still is.

Mason's sentinel project probed /.env and /.git/config from his own network,
the honeypot banned that IP, and he could not reach the site at all. His call
on scope was "Myself only", so these pin:

  * the owner's authenticated session passes a blacklisted IP;
  * any OTHER logged-in member on the same IP is still blocked;
  * anonymous requests from that IP (incl. /login/) are still blocked, so the
    bypass only helps a session that already exists;
  * the blacklist row itself is left active — the bypass never un-bans;
  * the bypass is logged;
  * a clean IP costs no extra work (the owner check only runs once an IP is
    known to be blacklisted).

Run with: python manage.py test src.tests.security.test_owner_blacklist_bypass
"""
from unittest import mock

from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from src.models import IPBlacklist, ParliamentUser

BLOCKED_IP = '198.51.100.73'
CLEAN_IP = '198.51.100.74'
PASSWORD = 'Owner-Bypass-Test-Pass-9!'


@override_settings(BEHIND_CLOUDFLARE=False)
class OwnerBlacklistBypassTests(TestCase):
    def setUp(self):
        cache.clear()
        IPBlacklist.objects.create(ip_address=BLOCKED_IP, reason='Honeypot trigger: /.env',
                                   is_active=True)
        self.owner = ParliamentUser.objects.create_user(
            '73', 'Owner Person', 'owner', 'Active', password=PASSWORD)
        self.member = ParliamentUser.objects.create_user(
            '74', 'Other Member', 'member', 'Active', password=PASSWORD)

    def _client_for(self, user=None):
        c = Client()
        if user is not None:
            c.force_login(user)
        return c

    def _is_ip_block(self, response):
        return response.status_code == 403 and b'IP address has been blocked' in response.content

    def test_owner_session_passes_a_blacklisted_ip(self):
        r = self._client_for(self.owner).get('/home/', REMOTE_ADDR=BLOCKED_IP)
        self.assertFalse(self._is_ip_block(r), r.status_code)

    def test_another_logged_in_member_on_that_ip_is_still_blocked(self):
        r = self._client_for(self.member).get('/home/', REMOTE_ADDR=BLOCKED_IP)
        self.assertTrue(self._is_ip_block(r))

    def test_anonymous_requests_from_that_ip_are_still_blocked(self):
        c = self._client_for()
        self.assertTrue(self._is_ip_block(c.get('/login/', REMOTE_ADDR=BLOCKED_IP)))
        self.assertTrue(self._is_ip_block(c.post('/login/', {'username': 'owner', 'password': PASSWORD},
                                                  REMOTE_ADDR=BLOCKED_IP)))

    def test_the_bypass_never_unbans_the_ip(self):
        self._client_for(self.owner).get('/home/', REMOTE_ADDR=BLOCKED_IP)
        self.assertTrue(IPBlacklist.objects.get(ip_address=BLOCKED_IP).is_active)
        # And the next anonymous request from it is still refused.
        self.assertTrue(self._is_ip_block(self._client_for().get('/home/', REMOTE_ADDR=BLOCKED_IP)))

    def test_the_bypass_is_logged(self):
        with self.assertLogs('admin_actions', level='WARNING') as logs:
            self._client_for(self.owner).get('/home/', REMOTE_ADDR=BLOCKED_IP)
        self.assertTrue(any('BLACKLISTED_IP_OWNER_BYPASS' in line for line in logs.output), logs.output)

    def test_clean_ip_never_consults_the_owner_check(self):
        with mock.patch('src.middleware.security._is_owner_session') as check:
            self._client_for(self.member).get('/home/', REMOTE_ADDR=CLEAN_IP)
        check.assert_not_called()
