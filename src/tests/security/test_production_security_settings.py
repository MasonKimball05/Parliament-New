"""
v3.25.2 — the production security configuration is executed by zero tests.

⚠️ EVERY TEST RUN THIS PROJECT HAS EVER DONE RAN WITH THE SECURITY SETTINGS OFF.

`Parliament/settings.py` puts the whole HTTPS block behind one condition:

    if not DEBUG:
        SECURE_SSL_REDIRECT = ...
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
        SECURE_HSTS_SECONDS = ...
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True
        SECURE_HSTS_PRELOAD = True

and derives two more from the same flag:

    USE_HTTPS = os.getenv('SECURE_SSL_REDIRECT', 'True') == 'True' and not DEBUG
    SESSION_COOKIE_SECURE = USE_HTTPS
    CSRF_COOKIE_SECURE = USE_HTTPS

`.github/workflows/ci.yml` sets `DJANGO_DEBUG: 'True'`. So in CI, on every
developer machine, and in every auto-run, all seven of those values are absent
or `False`, and **nothing anywhere asserts what they become in production.**
A typo in an environment-variable name, a stray `SECURE_SSL_REDIRECT=False` in
a `.env`, or someone deleting a line from that block would ship silently.

The 2FA flags sitting beside `DJANGO_DEBUG` in `ci.yml` carry a written
justification for being off. `DJANGO_DEBUG` does not, and it is doing far more.

⚠️ WHY THIS IS NOT SIMPLY "RUN THE SUITE WITH DEBUG=False".
Measured 08-24-26: at `DEBUG=False`, `src.test_education_scoring_and_meetings`
alone produces **59 failures and 44 errors**, because `SECURE_SSL_REDIRECT`
turns every plain-HTTP request from the test client into a 301 and the suite is
written against 200s. Flipping the flag is a project, not a line. This asserts
the *values* instead, which is the thing nobody was checking.

⚠️ WHY A CHILD INTERPRETER. Django settings are read once per process, and
`override_settings` cannot re-run a module-level `if not DEBUG:` block. The only
honest way to ask "what does this file produce at DEBUG=False" is to import it
again somewhere else — the same technique, and the same reason, as
`src/test_parallel_reporting.py`'s spawned-worker tests.
"""
import json
import os
import subprocess
import sys

from django.test import SimpleTestCase

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

#: Read the settings module in a fresh interpreter and report what it produced.
#: `django.setup()` rather than a bare import, so that anything that reads
#: settings at app-load time gets a chance to complain.
_PROBE = '''
import json, os, django
os.environ["DJANGO_SETTINGS_MODULE"] = "Parliament.settings"
django.setup()
from django.conf import settings
print("PARLIAMENT_SETTINGS_JSON" + json.dumps({
    "DEBUG": settings.DEBUG,
    "USE_HTTPS": getattr(settings, "USE_HTTPS", None),
    "SECURE_SSL_REDIRECT": getattr(settings, "SECURE_SSL_REDIRECT", None),
    "SECURE_PROXY_SSL_HEADER": list(getattr(settings, "SECURE_PROXY_SSL_HEADER", None) or []),
    "SECURE_HSTS_SECONDS": getattr(settings, "SECURE_HSTS_SECONDS", None),
    "SECURE_HSTS_INCLUDE_SUBDOMAINS": getattr(settings, "SECURE_HSTS_INCLUDE_SUBDOMAINS", None),
    "SECURE_HSTS_PRELOAD": getattr(settings, "SECURE_HSTS_PRELOAD", None),
    "SESSION_COOKIE_SECURE": settings.SESSION_COOKIE_SECURE,
    "CSRF_COOKIE_SECURE": settings.CSRF_COOKIE_SECURE,
    "SESSION_COOKIE_HTTPONLY": settings.SESSION_COOKIE_HTTPONLY,
    "CSRF_COOKIE_HTTPONLY": settings.CSRF_COOKIE_HTTPONLY,
    "SECURE_CONTENT_TYPE_NOSNIFF": settings.SECURE_CONTENT_TYPE_NOSNIFF,
    "X_FRAME_OPTIONS": settings.X_FRAME_OPTIONS,
}))
'''


def _settings_at(debug):
    """Import `Parliament.settings` in a fresh interpreter with DEBUG forced."""
    environment = {
        **os.environ,
        'DJANGO_DEBUG': 'True' if debug else 'False',
        # settings refuses to load in production mode without one, and CI and
        # the local `.env` both provide it; supply a fallback so this test is
        # about the security block rather than about key management.
        'ENCRYPTION_KEY': os.environ.get(
            'ENCRYPTION_KEY', 'HjGdIfMrGRWLLYtZQPm6NyLyyYl9_Cpco4telPFcAh8='),
        'DB_BACKEND': 'sqlite',
        'REDIS_URL': '',
    }
    completed = subprocess.run(
        [sys.executable, '-c', _PROBE],
        cwd=_REPO_ROOT, env={**environment, 'PYTHONPATH': _REPO_ROOT},
        capture_output=True, text=True, timeout=180,
    )
    marker = 'PARLIAMENT_SETTINGS_JSON'
    if marker not in completed.stdout:
        raise AssertionError(
            f'the settings probe did not report (exit {completed.returncode})\n'
            f'--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}')
    return json.loads(completed.stdout.split(marker, 1)[1].splitlines()[0])


class ProductionSecuritySettingsTests(SimpleTestCase):
    """What `Parliament/settings.py` produces when `DEBUG` is off."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.production = _settings_at(debug=False)

    def test_the_probe_actually_reached_production_mode(self):
        """
        ⚠️ FIRST, because every assertion below is meaningless if the child
        silently ran in debug mode — which is precisely the failure this whole
        file exists to rule out, one level up.
        """
        self.assertIs(self.production['DEBUG'], False)

    def test_http_is_redirected_to_https(self):
        self.assertIs(self.production['SECURE_SSL_REDIRECT'], True)

    def test_the_proxy_header_is_trusted_for_scheme(self):
        """
        Without this, `request.is_secure()` is always False behind nginx and
        `SECURE_SSL_REDIRECT` becomes an infinite redirect loop.
        """
        self.assertEqual(self.production['SECURE_PROXY_SSL_HEADER'],
                         ['HTTP_X_FORWARDED_PROTO', 'https'])

    def test_hsts_is_on_for_at_least_a_year_including_subdomains_and_preload(self):
        self.assertGreaterEqual(self.production['SECURE_HSTS_SECONDS'], 31536000)
        self.assertIs(self.production['SECURE_HSTS_INCLUDE_SUBDOMAINS'], True)
        self.assertIs(self.production['SECURE_HSTS_PRELOAD'], True)

    def test_the_session_and_csrf_cookies_are_https_only(self):
        self.assertIs(self.production['SESSION_COOKIE_SECURE'], True)
        self.assertIs(self.production['CSRF_COOKIE_SECURE'], True)

    def test_the_settings_that_do_not_depend_on_debug_are_on_in_both_modes(self):
        """
        These are outside the `if not DEBUG:` block and are asserted here so
        that moving one *into* it — which would silently disable it everywhere
        this suite runs — shows up as a failure rather than as nothing.
        """
        for value in (self.production, _settings_at(debug=True)):
            self.assertIs(value['SESSION_COOKIE_HTTPONLY'], True)
            self.assertIs(value['CSRF_COOKIE_HTTPONLY'], True)
            self.assertIs(value['SECURE_CONTENT_TYPE_NOSNIFF'], True)
            self.assertEqual(value['X_FRAME_OPTIONS'], 'SAMEORIGIN')


class TheseValuesAreNotOnInTheSuiteTests(SimpleTestCase):
    """
    ⚠️ THE CONTROL, AND IT IS THE FINDING.

    If the assertions above passed in debug mode too, they would be measuring
    a constant rather than the production branch. They do not: this records, as
    an executable fact, that **the configuration the class above checks is
    switched off in every run of this suite** — which is why it took until
    08-24-26 for anyone to notice nothing was checking it.

    When somebody eventually makes the suite `SECURE_SSL_REDIRECT`-safe and
    flips `DJANGO_DEBUG` in CI, this test goes red and should be deleted. That
    is the intended ending.

    ⚠️ **DO NOT ASSERT `settings.DEBUG` HERE — IT IS A LIAR IN THIS CONTEXT.**
    Django's `setup_test_environment()` sets `settings.DEBUG = False` for every
    test run, *after* the settings module has already been evaluated. So inside
    a test `settings.DEBUG` reads `False` while the `if not DEBUG:` block was
    skipped, and reading it tells you nothing about which branch ran. The first
    draft of this test asserted `settings.DEBUG is True` and failed for exactly
    that reason. **The derived values are the evidence; the flag is not.**
    """

    def test_the_ambient_test_settings_have_the_https_block_off(self):
        from django.conf import settings

        message = ('the suite now runs with the production security block on '
                   '— good; see this class\'s docstring, it should be deleted.')
        self.assertIs(settings.SESSION_COOKIE_SECURE, False, message)
        self.assertIs(settings.CSRF_COOKIE_SECURE, False, message)
        self.assertFalse(getattr(settings, 'SECURE_SSL_REDIRECT', False), message)
        self.assertFalse(getattr(settings, 'SECURE_HSTS_SECONDS', 0), message)
