"""
Management command to verify all critical settings and environment variables.
Run with: python manage.py check_env
"""
from django.core.management.base import BaseCommand
from django.conf import settings
import os


class Command(BaseCommand):
    help = 'Verify all critical settings and environment variables are correctly configured'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.errors = []
        self.warnings = []
        self.passed = []

    # ------------------------------------------------------------------ output

    def ok(self, label, detail=''):
        line = f"  ✓  {label:<45} {detail}"
        self.stdout.write(self.style.SUCCESS(line))
        self.passed.append(label)

    def warn(self, label, detail=''):
        line = f"  ⚠  {label:<45} {detail}"
        self.stdout.write(self.style.WARNING(line))
        self.warnings.append(label)

    def fail(self, label, detail=''):
        line = f"  ✗  {label:<45} {detail}"
        self.stdout.write(self.style.ERROR(line))
        self.errors.append(label)

    def section(self, title):
        self.stdout.write(f"\n{self.style.MIGRATE_HEADING(title)}")
        self.stdout.write('  ' + '─' * 60)

    # ------------------------------------------------------------------ helpers

    def _mask(self, value, show=4):
        if not value:
            return 'NOT SET'
        s = str(value)
        if len(s) <= show * 2:
            return '***'
        return f"{s[:show]}...{s[-show:]}"

    def _env(self, key):
        return os.getenv(key)

    # ------------------------------------------------------------------ checks

    def check_core(self):
        self.section('Core Django')

        # SECRET_KEY
        key = getattr(settings, 'SECRET_KEY', None)
        if not key:
            self.fail('SECRET_KEY', 'NOT SET — Django will not start')
        elif key.startswith('dev-only') or key.startswith('ci-') or 'insecure' in key.lower():
            self.fail('SECRET_KEY', 'using dev/CI placeholder — must be changed for production')
        else:
            self.ok('SECRET_KEY', self._mask(key))

        # DEBUG
        debug = getattr(settings, 'DEBUG', True)
        if debug:
            self.fail('DEBUG', 'True — must be False in production')
        else:
            self.ok('DEBUG', 'False')

        # ALLOWED_HOSTS
        hosts = getattr(settings, 'ALLOWED_HOSTS', [])
        if not hosts:
            self.fail('ALLOWED_HOSTS', 'empty — Django will reject all requests')
        elif '*' in hosts:
            self.warn('ALLOWED_HOSTS', f"contains wildcard '*' — not recommended for production")
        else:
            self.ok('ALLOWED_HOSTS', ', '.join(hosts))

        # CSRF_TRUSTED_ORIGINS
        origins = getattr(settings, 'CSRF_TRUSTED_ORIGINS', [])
        if not origins:
            self.warn('CSRF_TRUSTED_ORIGINS', 'not set — cross-origin POSTs (e.g. from HTTPS) may fail')
        else:
            self.ok('CSRF_TRUSTED_ORIGINS', ', '.join(origins))

    def check_database(self):
        self.section('Database')

        db = getattr(settings, 'DATABASES', {}).get('default', {})
        engine = db.get('ENGINE', '')
        is_postgres = 'postgresql' in engine

        for var in ('DB_NAME', 'DB_USER', 'DB_HOST'):
            val = self._env(var)
            if not val:
                self.fail(var, 'NOT SET')
            else:
                self.ok(var, val)

        pw = self._env('DB_PASSWORD')
        if not pw:
            self.fail('DB_PASSWORD', 'NOT SET')
        else:
            self.ok('DB_PASSWORD', self._mask(pw))

        sslmode = self._env('DB_SSLMODE') or db.get('OPTIONS', {}).get('sslmode', 'not set')
        if sslmode in ('disable', 'allow'):
            self.warn('DB_SSLMODE', f"{sslmode} — consider 'require' or 'verify-full' in production")
        else:
            self.ok('DB_SSLMODE', sslmode)

        # Live connectivity check
        try:
            from django.db import connection
            connection.ensure_connection()
            self.ok('DB connectivity', 'connected successfully')
        except Exception as e:
            self.fail('DB connectivity', str(e)[:80])

    def check_encryption(self):
        self.section('Field-Level Encryption')

        raw = self._env('ENCRYPTION_KEY')
        if not raw:
            self.fail('ENCRYPTION_KEY (env)', 'NOT SET')
            return

        self.ok('ENCRYPTION_KEY (env)', f"{len(raw)} chars, {self._mask(raw)}")

        crypto_key = getattr(settings, 'CRYPTOGRAPHY_KEY', None)
        if not crypto_key:
            self.fail('settings.CRYPTOGRAPHY_KEY', 'None — settings did not load the key (restart server?)')
            return

        self.ok('settings.CRYPTOGRAPHY_KEY', f"{len(crypto_key)} bytes")

        # Validate the key actually works
        try:
            from cryptography.fernet import Fernet
            f = Fernet(crypto_key)
            token = f.encrypt(b'parliament-test')
            f.decrypt(token)
            self.ok('Fernet encrypt/decrypt', 'key is valid and functional')
        except Exception as e:
            self.fail('Fernet encrypt/decrypt', f"key is invalid: {e}")

    def check_email(self):
        self.section('Email')

        backend = getattr(settings, 'EMAIL_BACKEND', '')
        if 'console' in backend:
            self.warn('EMAIL_BACKEND', 'console — emails will only print to stdout, not send')
        elif 'brevo' in backend.lower() or 'anymail' in backend.lower():
            self.ok('EMAIL_BACKEND', backend)
        elif 'smtp' in backend.lower():
            self.ok('EMAIL_BACKEND', backend)
        else:
            self.warn('EMAIL_BACKEND', backend)

        # SMTP credentials (only relevant if using SMTP backend)
        if 'smtp' in backend.lower():
            for var in ('EMAIL_HOST', 'EMAIL_HOST_USER'):
                val = self._env(var)
                if not val:
                    self.warn(var, 'not set')
                else:
                    self.ok(var, val)

            pw = self._env('EMAIL_HOST_PASSWORD')
            if not pw:
                self.warn('EMAIL_HOST_PASSWORD', 'not set — SMTP auth will fail')
            else:
                self.ok('EMAIL_HOST_PASSWORD', self._mask(pw))

        # Anymail / Brevo
        if 'anymail' in backend.lower() or 'brevo' in backend.lower():
            brevo_key = self._env('BREVO_API_KEY') or getattr(settings, 'ANYMAIL', {}).get('BREVO_API_KEY', '')
            if not brevo_key:
                self.fail('BREVO_API_KEY', 'NOT SET — Anymail/Brevo emails will fail')
            else:
                self.ok('BREVO_API_KEY', self._mask(brevo_key))

        # From / alert addresses
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', '')
        if not from_email:
            self.warn('DEFAULT_FROM_EMAIL', 'not set')
        else:
            self.ok('DEFAULT_FROM_EMAIL', from_email)

        alert_email = getattr(settings, 'SECURITY_ALERT_EMAIL', '')
        if not alert_email or alert_email == from_email:
            self.warn('SECURITY_ALERT_EMAIL', f"{alert_email or 'not set'} — same as DEFAULT_FROM_EMAIL or missing")
        else:
            self.ok('SECURITY_ALERT_EMAIL', alert_email)

    def check_cache_redis(self):
        self.section('Cache / Redis')

        redis_url = self._env('REDIS_URL') or getattr(settings, 'REDIS_URL', '')
        cache_backend = settings.CACHES.get('default', {}).get('BACKEND', '')

        if not redis_url:
            self.warn('REDIS_URL', 'not set — using in-memory cache (rate limiting won\'t persist across workers)')
        else:
            self.ok('REDIS_URL', self._mask(redis_url, show=20))

        if 'redis' in cache_backend.lower():
            self.ok('Cache backend', 'Redis')
            # Live connectivity check
            try:
                from django.core.cache import cache
                cache.set('parliament_check_env', 'ok', 5)
                val = cache.get('parliament_check_env')
                if val == 'ok':
                    self.ok('Redis connectivity', 'connected and read/write working')
                else:
                    self.warn('Redis connectivity', 'connected but read-back failed')
            except Exception as e:
                self.fail('Redis connectivity', str(e)[:80])
        elif 'locmem' in cache_backend.lower():
            self.warn('Cache backend', 'LocMemCache — per-process only, not shared across gunicorn workers')
        else:
            self.warn('Cache backend', cache_backend)

    def check_2fa(self):
        self.section('Two-Factor Authentication (OTP)')

        issuer = getattr(settings, 'OTP_TOTP_ISSUER', '')
        if not issuer:
            self.warn('OTP_TOTP_ISSUER', 'not set — authenticator apps will show a blank issuer name')
        else:
            self.ok('OTP_TOTP_ISSUER', issuer)

        for setting in ('REQUIRE_2FA_FOR_ADMINS', 'REQUIRE_2FA_FOR_OFFICERS'):
            val = getattr(settings, setting, None)
            if val is None:
                self.warn(setting, 'not defined in settings')
            elif not val:
                self.warn(setting, 'False — 2FA not enforced')
            else:
                self.ok(setting, 'True')

    def check_security(self):
        self.section('Security Settings')

        debug = getattr(settings, 'DEBUG', True)

        ssl_redirect = getattr(settings, 'SECURE_SSL_REDIRECT', False)
        if not debug and not ssl_redirect:
            self.warn('SECURE_SSL_REDIRECT', 'False — HTTP requests won\'t be redirected to HTTPS')
        elif not debug:
            self.ok('SECURE_SSL_REDIRECT', 'True')

        hsts = getattr(settings, 'SECURE_HSTS_SECONDS', 0)
        if not debug and hsts == 0:
            self.warn('SECURE_HSTS_SECONDS', '0 — HSTS not enabled')
        elif not debug:
            self.ok('SECURE_HSTS_SECONDS', f"{hsts}s ({hsts // 86400} days)")

        session_secure = getattr(settings, 'SESSION_COOKIE_SECURE', False)
        if not debug and not session_secure:
            self.warn('SESSION_COOKIE_SECURE', 'False — session cookies sent over HTTP')
        elif not debug:
            self.ok('SESSION_COOKIE_SECURE', 'True')

        csrf_secure = getattr(settings, 'CSRF_COOKIE_SECURE', False)
        if not debug and not csrf_secure:
            self.warn('CSRF_COOKIE_SECURE', 'False — CSRF cookies sent over HTTP')
        elif not debug:
            self.ok('CSRF_COOKIE_SECURE', 'True')

        admin_key = self._env('ADMIN_V2_SECRET_KEY')
        if not admin_key:
            self.fail('ADMIN_V2_SECRET_KEY', 'NOT SET — Admin v2 access will be broken')
        elif len(admin_key) < 16:
            self.warn('ADMIN_V2_SECRET_KEY', f"only {len(admin_key)} chars — use something longer")
        else:
            self.ok('ADMIN_V2_SECRET_KEY', self._mask(admin_key))

    def check_storage(self):
        self.section('File Storage & Media')

        media_root = getattr(settings, 'MEDIA_ROOT', '')
        if not media_root:
            self.warn('MEDIA_ROOT', 'not set')
        elif not os.path.isdir(media_root):
            self.warn('MEDIA_ROOT', f"{media_root} — directory does not exist")
        else:
            writable = os.access(media_root, os.W_OK)
            if writable:
                self.ok('MEDIA_ROOT', f"{media_root} (writable)")
            else:
                self.fail('MEDIA_ROOT', f"{media_root} — not writable by server process")

        log_dir = self._env('LOG_DIR') or str(settings.BASE_DIR / 'logs')
        if not os.path.isdir(log_dir):
            self.warn('LOG_DIR', f"{log_dir} — directory does not exist, logging will fail")
        else:
            writable = os.access(log_dir, os.W_OK)
            if writable:
                self.ok('LOG_DIR', f"{log_dir} (writable)")
            else:
                self.fail('LOG_DIR', f"{log_dir} — not writable by server process")

    # ------------------------------------------------------------------ main

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n══════════════════════════════════════════════════════════════\n'
            '  Parliament — Settings & Environment Check\n'
            '══════════════════════════════════════════════════════════════'
        ))

        self.check_core()
        self.check_database()
        self.check_encryption()
        self.check_email()
        self.check_cache_redis()
        self.check_2fa()
        self.check_security()
        self.check_storage()

        # ── Summary ──────────────────────────────────────────────────────
        self.stdout.write(f"\n{'─' * 64}")
        total = len(self.passed) + len(self.warnings) + len(self.errors)
        self.stdout.write(
            f"  {self.style.SUCCESS(f'{len(self.passed)} passed')}  "
            f"{self.style.WARNING(f'{len(self.warnings)} warnings')}  "
            f"{self.style.ERROR(f'{len(self.errors)} failed')}  "
            f"({total} checks total)"
        )

        if self.errors:
            self.stdout.write(self.style.ERROR(
                '\n  ✗ CRITICAL issues found — fix these before running in production:\n'
            ))
            for e in self.errors:
                self.stdout.write(self.style.ERROR(f"    • {e}"))

        if self.warnings:
            self.stdout.write(self.style.WARNING(
                '\n  ⚠ Warnings (non-fatal but should be reviewed):\n'
            ))
            for w in self.warnings:
                self.stdout.write(self.style.WARNING(f"    • {w}"))

        if not self.errors and not self.warnings:
            self.stdout.write(self.style.SUCCESS(
                '\n  ✓ All checks passed — configuration looks good!\n'
            ))
        elif not self.errors:
            self.stdout.write(self.style.WARNING(
                '\n  ⚠ No critical errors, but review warnings above.\n'
            ))

        self.stdout.write('')
