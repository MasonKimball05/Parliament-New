"""
v3.15.2 — digest freshness watchdog (check_digest_freshness).

The watchdog must stay SILENT when the digest heartbeat is fresh and RAISE
(stderr + exit 1) when it's missing or stale — that non-zero exit is what
makes system cron email an alert independent of Celery and Django's SMTP.
"""
import os
import tempfile
import time
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

_TMP = tempfile.mkdtemp(prefix='parliament-test-hb-')


@override_settings(BASE_DIR=_TMP)
class DigestWatchdogTests(SimpleTestCase):
    """
    ⚠️ v3.21.5 — `override_settings(BASE_DIR=…)` WAS NOT ENOUGH, AND THE WAY IT
    FAILED IS WORTH KEEPING.

    The heartbeat path is `os.path.join(BASE_DIR, os.getenv('LOG_DIR', 'logs'))`,
    and `os.path.join` throws away everything to the left of an absolute
    component. CI sets `LOG_DIR: /tmp`. So the override was silently discarded,
    the command looked in `/tmp/last_digest_sent`, and three of these four tests
    failed **in CI and nowhere else** — one of the two reasons the Django CI
    workflow had never once passed.

    The rule: **a test that overrides a setting has not pinned the value if the
    code also reads the environment.** Pin both. `mock.patch.dict` restores the
    variable (including deleting one this process did not have) on exit.
    """

    def setUp(self):
        log_dir = mock.patch.dict(os.environ, {'LOG_DIR': 'logs'})
        log_dir.start()
        self.addCleanup(log_dir.stop)

        os.makedirs(os.path.join(_TMP, 'logs'), exist_ok=True)
        self.hb = os.path.join(_TMP, 'logs', 'last_digest_sent')

    def tearDown(self):
        if os.path.exists(self.hb):
            os.remove(self.hb)

    def _run(self, **kw):
        out, err = StringIO(), StringIO()
        code = 0
        try:
            call_command('check_digest_freshness', stdout=out, stderr=err, **kw)
        except SystemExit as e:
            code = e.code
        return code, err.getvalue()

    def test_fresh_heartbeat_is_silent(self):
        with open(self.hb, 'w') as f:
            f.write('now')
        code, err = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(err.strip(), '')

    def test_missing_heartbeat_alerts(self):
        code, err = self._run()
        self.assertEqual(code, 1)
        self.assertIn('digest-watchdog', err)
        self.assertIn('has not successfully sent', err)

    def test_stale_heartbeat_alerts(self):
        with open(self.hb, 'w') as f:
            f.write('old')
        old = time.time() - 40 * 3600  # 40h ago
        os.utime(self.hb, (old, old))
        code, err = self._run()
        self.assertEqual(code, 1)
        self.assertIn('STALE', err)

    def test_threshold_respected(self):
        with open(self.hb, 'w') as f:
            f.write('x')
        old = time.time() - 10 * 3600  # 10h ago
        os.utime(self.hb, (old, old))
        # 10h < default 26h → silent; but with --max-age-hours 6 → alert
        self.assertEqual(self._run()[0], 0)
        self.assertEqual(self._run(max_age_hours=6)[0], 1)
