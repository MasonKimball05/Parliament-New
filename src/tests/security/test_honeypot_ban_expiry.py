"""
v3.35.2 (09-28-26) — honeypot IP bans expire after HONEYPOT_BAN_DURATION.

The honeypot wrote `IPBlacklist` rows with no `expires_at`, and
`tasks.expire_stale_ip_blacklist_entries` only deactivates rows that HAVE one,
so every honeypot ban was permanent while the log line said "24 hours". That
is how the platform owner's own IPs stayed blocked on 09-27.

Pins:
  * a honeypot hit writes a row that expires HONEYPOT_BAN_DURATION from now;
  * the existing expiry task deactivates it once that time has passed;
  * a manual (admin) ban with no expiry is still left alone by the task.

Run with: python manage.py test src.tests.security.test_honeypot_ban_expiry
"""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from src.models import IPBlacklist
from src.tasks.cleanup import expire_stale_ip_blacklist_entries
from src.view.honeypot import HONEYPOT_BAN_DURATION

SCANNER_IP = '198.51.100.90'


@override_settings(BEHIND_CLOUDFLARE=False)
class HoneypotBanExpiryTests(TestCase):
    def setUp(self):
        cache.clear()

    def _trip(self):
        self.client.get('/wp-login.php', REMOTE_ADDR=SCANNER_IP)
        return IPBlacklist.objects.get(ip_address=SCANNER_IP, is_active=True)

    def test_honeypot_ban_has_an_expiry(self):
        before = timezone.now()
        row = self._trip()
        self.assertIsNotNone(row.expires_at, 'honeypot bans must not be permanent')
        expected = before + timedelta(seconds=HONEYPOT_BAN_DURATION)
        self.assertLess(abs((row.expires_at - expected).total_seconds()), 60)

    def test_expiry_task_lifts_the_ban_once_it_lapses(self):
        row = self._trip()
        IPBlacklist.objects.filter(pk=row.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        expire_stale_ip_blacklist_entries()
        row.refresh_from_db()
        self.assertFalse(row.is_active)

    def test_manual_ban_without_expiry_stays_permanent(self):
        manual = IPBlacklist.objects.create(ip_address='198.51.100.91', reason='manual')
        expire_stale_ip_blacklist_entries()
        manual.refresh_from_db()
        self.assertTrue(manual.is_active)
