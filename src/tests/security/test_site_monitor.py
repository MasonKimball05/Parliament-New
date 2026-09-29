"""
v3.35.3 (09-29-26) — the owner's site monitor is not banned by the honeypot.

go-sentinel probes /.env and /.git/config to prove they are not public. A
request carrying the right `X-Site-Monitor-Token` gets the ordinary 404: no
ban (cache or IPBlacklist), no HoneypotAccess row. Everything else about the
honeypot is unchanged, and the token grants nothing beyond that.

Run with: python manage.py test src.tests.security.test_site_monitor
"""
from django.core.cache import cache
from django.core.checks import run_checks
from django.test import TestCase, override_settings

from src.models import HoneypotAccess, IPBlacklist

TOKEN = 'm' * 40
MONITOR_IP = '198.51.100.120'


@override_settings(BEHIND_CLOUDFLARE=False, SITE_MONITOR_TOKEN=TOKEN)
class SiteMonitorHoneypotTests(TestCase):
    def setUp(self):
        cache.clear()

    def _get(self, path, token=None, ip=MONITOR_IP):
        extra = {'REMOTE_ADDR': ip}
        if token is not None:
            extra['HTTP_X_SITE_MONITOR_TOKEN'] = token
        return self.client.get(path, **extra)

    def assertNotBanned(self, ip=MONITOR_IP):
        self.assertFalse(IPBlacklist.objects.filter(ip_address=ip).exists())
        self.assertIsNone(cache.get(f'honeypot_ban_{ip}'))
        self.assertFalse(HoneypotAccess.objects.filter(ip_address=ip).exists())

    def test_monitor_gets_a_plain_404_on_trap_paths_and_is_not_banned(self):
        # (Not /admin/backup/: Django admin's catch-all answers it first.)
        for path in ('/.env', '/.git/config', '/.git/HEAD', '/wp-login.php'):
            with self.subTest(path=path):
                r = self._get(path, TOKEN)
                self.assertEqual(r.status_code, 404)
                # Not the honeypot's fake bodies, which the monitor would
                # (rightly) report as a leak.
                self.assertNotIn(b'HONEYPOT_DETECTED', r.content)
                self.assertNotIn(b'[core]', r.content)
        self.assertNotBanned()
        # ...and the site still works for it afterwards.
        self.assertNotEqual(self._get('/').status_code, 403)

    def test_monitor_is_answered_404_even_if_its_ip_has_a_cached_honeypot_ban(self):
        cache.set(f'honeypot_ban_{MONITOR_IP}', True, 60)
        self.assertEqual(self._get('/.env', TOKEN).status_code, 404)

    def test_wrong_token_is_treated_as_a_scanner(self):
        r = self._get('/.env', 'x' * 40)
        self.assertEqual(r.status_code, 200)   # the fake .env
        self.assertTrue(IPBlacklist.objects.filter(ip_address=MONITOR_IP, is_active=True).exists())

    def test_no_token_is_treated_as_a_scanner(self):
        self._get('/.env')
        self.assertTrue(IPBlacklist.objects.filter(ip_address=MONITOR_IP, is_active=True).exists())

    def test_token_does_not_lift_an_existing_blacklist_ban(self):
        IPBlacklist.objects.create(ip_address=MONITOR_IP, reason='manual')
        self.assertEqual(self._get('/', TOKEN).status_code, 403)

    @override_settings(SITE_MONITOR_TOKEN='')
    def test_unset_token_disables_the_bypass(self):
        self._get('/.env', '')
        self.assertTrue(IPBlacklist.objects.filter(ip_address=MONITOR_IP).exists())

    @override_settings(SITE_MONITOR_TOKEN='short')
    def test_short_token_is_ignored_and_never_logged(self):
        self._get('/.env', 'short')
        self.assertTrue(IPBlacklist.objects.filter(ip_address=MONITOR_IP).exists())
        row = HoneypotAccess.objects.get(ip_address=MONITOR_IP)
        self.assertNotIn('HTTP_X_SITE_MONITOR_TOKEN', row.additional_data['headers'])


class SiteMonitorCheckTests(TestCase):
    def _w005(self):
        return [m for m in run_checks() if m.id == 'src.W005']

    @override_settings(SITE_MONITOR_TOKEN='too-short')
    def test_short_token_warns(self):
        self.assertEqual(len(self._w005()), 1)

    @override_settings(SITE_MONITOR_TOKEN=TOKEN)
    def test_good_token_is_quiet(self):
        self.assertEqual(self._w005(), [])

    @override_settings(SITE_MONITOR_TOKEN='')
    def test_unset_is_quiet(self):
        self.assertEqual(self._w005(), [])
