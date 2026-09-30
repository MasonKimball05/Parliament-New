"""
v3.35.3 (09-29-26) — the /admin/backup/ honeypot actually fires.

It was registered after `path('admin/', admin.site.urls)`, so Django admin's
catch-all answered it first (302 to the admin login) and nobody was ever
banned for probing it. Pinned for both URL confs (src.urls is the default
ROOT_URLCONF; Parliament.urls is the alternative).

Run with: python manage.py test src.tests.security.test_admin_backup_honeypot
"""
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import resolve

from src.models import IPBlacklist
from src.view.honeypot import honeypot_admin_backup

SCANNER_IP = '198.51.100.140'


class AdminBackupHoneypotTests(TestCase):
    def test_resolves_to_the_honeypot_in_both_urlconfs(self):
        for urlconf in ('src.urls', 'Parliament.urls'):
            with self.subTest(urlconf=urlconf):
                self.assertIs(resolve('/admin/backup/', urlconf=urlconf).func, honeypot_admin_backup)

    def test_rest_of_the_admin_is_untouched(self):
        for urlconf in ('src.urls', 'Parliament.urls'):
            with self.subTest(urlconf=urlconf):
                self.assertEqual(resolve('/admin/', urlconf=urlconf).app_name, 'admin')

    @override_settings(BEHIND_CLOUDFLARE=False)
    def test_probe_is_banned(self):
        cache.clear()
        r = self.client.get('/admin/backup/', REMOTE_ADDR=SCANNER_IP)
        self.assertEqual(r.status_code, 403)   # the honeypot's fake JSON 403
        self.assertTrue(IPBlacklist.objects.filter(ip_address=SCANNER_IP, is_active=True).exists())
