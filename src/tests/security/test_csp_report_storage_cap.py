"""
v3.46.2 — `/csp-report/` stops storing reports past a per-IP and a site-wide
cap per ten-minute window, and still answers 204.

Before: every accepted POST to this public, CSRF-exempt endpoint wrote one
`CSPViolation` row and one `security` log line, and nothing deletes those rows.
"""
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings

from src.models import CSPViolation
from src.view import csp_report as view

REPORT = json.dumps({'csp-report': {
    'blocked-uri': 'https://evil.example/x.js',
    'violated-directive': 'script-src',
    'document-uri': 'https://am-parliament.org/home/',
}})


@override_settings(BEHIND_CLOUDFLARE=False)
class CspReportStorageCapTests(TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def post(self, ip='198.51.100.7'):
        return self.client.post('/csp-report/', REPORT, content_type='application/csp-report',
                                REMOTE_ADDR=ip)

    def test_reports_under_the_cap_are_stored(self):
        for _ in range(3):
            self.assertEqual(self.post().status_code, 204)
        self.assertEqual(CSPViolation.objects.count(), 3)

    def test_one_ip_past_its_cap_is_acknowledged_but_not_stored(self):
        with patch.object(view, '_PER_IP_CAP', 5):
            codes = {self.post().status_code for _ in range(12)}
        self.assertEqual(codes, {204})
        self.assertEqual(CSPViolation.objects.count(), 5)

    def test_one_ip_over_its_cap_does_not_block_another(self):
        with patch.object(view, '_PER_IP_CAP', 2):
            for _ in range(5):
                self.post(ip='198.51.100.7')
            self.post(ip='203.0.113.9')
        self.assertEqual(CSPViolation.objects.filter(ip_address='203.0.113.9').count(), 1)

    def test_rotating_addresses_hits_the_site_wide_cap(self):
        with patch.object(view, '_GLOBAL_CAP', 4):
            for i in range(10):
                self.assertEqual(self.post(ip=f'203.0.113.{i + 1}').status_code, 204)
        self.assertEqual(CSPViolation.objects.count(), 4)

    def test_dropped_reports_are_not_logged_one_line_each(self):
        with patch.object(view, '_PER_IP_CAP', 2), self.assertLogs('security', 'INFO') as logs:
            for _ in range(10):
                self.post()
        violation_lines = [m for m in logs.output if 'CSP violation |' in m]
        cap_lines = [m for m in logs.output if 'cap reached' in m]
        self.assertEqual(len(violation_lines), 2)
        self.assertEqual(len(cap_lines), 1)

    def test_a_cache_failure_still_stores_the_report(self):
        with patch.object(view.cache, 'add', side_effect=ConnectionError('redis down')):
            self.assertEqual(self.post().status_code, 204)
        self.assertEqual(CSPViolation.objects.count(), 1)
