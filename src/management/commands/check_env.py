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
        self.stdout.write(self.style.SUCCESS(f"  ✓  {label:<45} {detail}"))
        self.passed.append(label)

    def warn(self, label, detail=''):
        self.stdout.write(self.style.WARNING(f"  ⚠  {label:<45} {detail}"))
        self.warnings.append(label)

    def fail(self, label, detail=''):
        self.stdout.write(self.style.ERROR(f"  ✗  {label:<45} {detail}"))
        self.errors.append(label)

    def section(self, title):
        self.stdout.write(f"\n{self.style.MIGRATE_HEADING(title)}")
        self.stdout.write('  ' + '─' * 60)

    def _mask(self, value, show=4):
        if not value:
            return 'NOT SET'
        s = str(value)
        return '***' if len(s) <= show * 2 else f"{s[:show]}...{s[-show:]}"

    def _env(self, key):
        return os.getenv(key)

    # ------------------------------------------------------------------ checks

    def check_core(self):
        self.section('Core Django')

        key = getattr(settings, 'SECRET_KEY', None)
        if not key:
            self.fail('SECRET_KEY', 'NOT SET — Django will not start')
        elif any(x in key.lower() for x in ('dev-only', 'ci-', 'insecure')):
            self.fail('SECRET_KEY', 'dev/CI placeholder — change for production')
        else:
            self.ok('SECRET_KEY', self._mask(key))

        debug = getattr(settings, 'DEBUG', True)
        if debug:
            self.fail('DEBUG', 'True — must be False in production')
        else:
            self.ok('DEBUG', 'False')

        hosts = getattr(settings, 'ALLOWED_HOSTS', [])
        if not hosts:
            self.fail('ALLOWED_HOSTS', 'empty — Django will reject all requests')
        elif '*' in hosts:
            self.warn('ALLOWED_HOSTS', "contains wildcard '*' — not recommended for production")
        else:
            self.ok('ALLOWED_HOSTS', ', '.join(hosts))

        origins = getattr(settings, 'CSRF_TRUSTED_ORIGINS', [])
        if not origins:
            self.warn('CSRF_TRUSTED_ORIGINS', 'not set — cross-origin POSTs may fail')
        else:
            self.ok('CSRF_TRUSTED_ORIGINS', ', '.join(origins))

        # Functional: verify URL routing works
        try:
            from django.urls import reverse
            reverse('login')
            self.ok('URL routing', 'login URL resolves correctly')
        except Exception as e:
            self.fail('URL routing', f"could not reverse 'login': {e}")

        # Functional: verify middleware all importable
        missing = []
        for mw in getattr(settings, 'MIDDLEWARE', []):
            module_path, _, class_name = mw.rpartition('.')
            try:
                import importlib
                mod = importlib.import_module(module_path)
                getattr(mod, class_name)
            except Exception:
                missing.append(mw)
        if missing:
            self.fail('Middleware imports', f"{len(missing)} failed: {missing[0]}")
        else:
            self.ok('Middleware imports', f"all {len(settings.MIDDLEWARE)} middleware importable")

    def check_database(self):
        self.section('Database')

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

        db = getattr(settings, 'DATABASES', {}).get('default', {})
        sslmode = self._env('DB_SSLMODE') or db.get('OPTIONS', {}).get('sslmode', 'not set')
        if sslmode in ('disable', 'allow'):
            self.warn('DB_SSLMODE', f"{sslmode} — consider 'require' in production")
        else:
            self.ok('DB_SSLMODE', sslmode)

        # Functional: connectivity
        try:
            from django.db import connection
            connection.ensure_connection()
            self.ok('DB connectivity', 'connected successfully')
        except Exception as e:
            self.fail('DB connectivity', str(e)[:80])
            return

        # Functional: core tables exist and are queryable
        try:
            from src.models import ParliamentUser
            count = ParliamentUser.objects.count()
            self.ok('ParliamentUser table', f"accessible — {count} users")
        except Exception as e:
            self.fail('ParliamentUser table', str(e)[:80])

        try:
            from src.models import Legislation
            Legislation.objects.count()
            self.ok('Legislation table', 'accessible')
        except Exception as e:
            self.fail('Legislation table', str(e)[:80])

        # Functional: no unapplied migrations
        try:
            from django.db.migrations.executor import MigrationExecutor
            executor = MigrationExecutor(connection)
            plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
            if plan:
                self.warn('Pending migrations', f"{len(plan)} unapplied — run manage.py migrate")
            else:
                self.ok('Pending migrations', 'none — database is up to date')
        except Exception as e:
            self.warn('Pending migrations', f"could not check: {e}")

    def check_encryption(self):
        self.section('Field-Level Encryption')

        # The environment variable is named ENCRYPTION_KEY; settings.py maps it to
        # CRYPTOGRAPHY_KEY (the name used by encrypted_fields.py and apps.py).
        # Both refer to the same key — they are not separate secrets.
        raw = self._env('ENCRYPTION_KEY')
        if not raw:
            self.fail('ENCRYPTION_KEY (env)', 'NOT SET')
            return
        self.ok('ENCRYPTION_KEY (env)', f"{len(raw)} chars, {self._mask(raw)}")

        crypto_key = getattr(settings, 'CRYPTOGRAPHY_KEY', None)
        if not crypto_key:
            self.fail('settings.CRYPTOGRAPHY_KEY', 'None — restart server to pick up .env changes')
            return
        self.ok('settings.CRYPTOGRAPHY_KEY', f"{len(crypto_key)} bytes")

        # Functional: encrypt/decrypt round-trip
        try:
            from cryptography.fernet import Fernet
            f = Fernet(crypto_key)
            token = f.encrypt(b'parliament-test')
            result = f.decrypt(token)
            assert result == b'parliament-test'
            self.ok('Fernet encrypt/decrypt', 'round-trip successful')
        except Exception as e:
            self.fail('Fernet encrypt/decrypt', f"key is invalid: {e}")
            return

        # Functional: test via an actual model encrypted field
        try:
            from src.models import ParliamentUser
            user = ParliamentUser.objects.first()
            if user:
                # Reading an encrypted field triggers from_db_value — if it works, encryption is wired up
                _ = str(user.username)
                self.ok('Encrypted field read', 'model field decryption working')
            else:
                self.warn('Encrypted field read', 'no users in DB — skipped')
        except Exception as e:
            self.fail('Encrypted field read', str(e)[:80])

    def check_email(self):
        self.section('Email')

        backend = getattr(settings, 'EMAIL_BACKEND', '')
        if 'console' in backend:
            self.warn('EMAIL_BACKEND', 'console — emails print to stdout only')
        elif 'brevo' in backend.lower() or 'anymail' in backend.lower():
            self.ok('EMAIL_BACKEND', 'Anymail/Brevo')
        elif 'smtp' in backend.lower():
            self.ok('EMAIL_BACKEND', 'SMTP')
        else:
            self.warn('EMAIL_BACKEND', backend)

        if 'smtp' in backend.lower():
            for var in ('EMAIL_HOST', 'EMAIL_HOST_USER'):
                val = self._env(var)
                self.ok(var, val) if val else self.warn(var, 'not set')

            pw = self._env('EMAIL_HOST_PASSWORD')
            if not pw:
                self.warn('EMAIL_HOST_PASSWORD', 'not set — SMTP auth will fail')
            else:
                self.ok('EMAIL_HOST_PASSWORD', self._mask(pw))

            # Functional: open SMTP connection
            try:
                from django.core.mail import get_connection
                conn = get_connection()
                conn.open()
                conn.close()
                self.ok('SMTP connectivity', 'connection opened successfully')
            except Exception as e:
                self.fail('SMTP connectivity', str(e)[:80])

        if 'anymail' in backend.lower() or 'brevo' in backend.lower():
            brevo_key = self._env('BREVO_API_KEY') or getattr(settings, 'ANYMAIL', {}).get('BREVO_API_KEY', '')
            if not brevo_key:
                self.fail('BREVO_API_KEY', 'NOT SET — emails will fail')
            else:
                self.ok('BREVO_API_KEY', self._mask(brevo_key))

            # Functional: validate Brevo API key via account endpoint
            try:
                import urllib.request
                import json
                req = urllib.request.Request(
                    'https://api.brevo.com/v3/account',
                    headers={'api-key': brevo_key, 'Accept': 'application/json'}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310  # fixed literal https:// URL, no user-controlled scheme
                    data = json.loads(resp.read())
                    plan = data.get('plan', [{}])
                    email_credits = next((p.get('credits') for p in plan if p.get('type') == 'payAsYouGo'), 'N/A')
                    self.ok('Brevo API key', f"valid — account: {data.get('email', '?')}")
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    self.fail('Brevo API key', 'invalid or revoked (401 Unauthorized)')
                else:
                    self.warn('Brevo API key', f"HTTP {e.code} — could not verify")
            except Exception as e:
                self.warn('Brevo API key', f"could not reach Brevo API: {e}")

        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', '')
        self.ok('DEFAULT_FROM_EMAIL', from_email) if from_email else self.warn('DEFAULT_FROM_EMAIL', 'not set')

        alert_email = getattr(settings, 'SECURITY_ALERT_EMAIL', '')
        if not alert_email:
            self.warn('SECURITY_ALERT_EMAIL', 'not set')
        elif alert_email == from_email:
            self.warn('SECURITY_ALERT_EMAIL', f"{alert_email} — same as DEFAULT_FROM_EMAIL")
        else:
            self.ok('SECURITY_ALERT_EMAIL', alert_email)

    def check_cache_redis(self):
        self.section('Cache / Redis')

        redis_url = self._env('REDIS_URL') or getattr(settings, 'REDIS_URL', '')
        cache_backend = settings.CACHES.get('default', {}).get('BACKEND', '')

        if not redis_url:
            self.warn('REDIS_URL', 'not set — rate limiting will not persist across workers')
        else:
            self.ok('REDIS_URL', self._mask(redis_url, show=20))

        if 'redis' in cache_backend.lower():
            self.ok('Cache backend', 'Redis')
        elif 'locmem' in cache_backend.lower():
            self.warn('Cache backend', 'LocMemCache — per-process, not shared across workers')
        else:
            self.warn('Cache backend', cache_backend)

        # Functional: read/write/delete/ttl
        try:
            from django.core.cache import cache
            import time

            cache.set('_check_env_rw', 'parliament', 10)
            val = cache.get('_check_env_rw')
            if val != 'parliament':
                raise ValueError(f"read-back mismatch: got {val!r}")

            cache.delete('_check_env_rw')
            if cache.get('_check_env_rw') is not None:
                raise ValueError('delete did not work')

            # TTL check — set with 2s expiry, confirm it's there, don't wait
            cache.set('_check_env_ttl', 'x', 2)
            if cache.get('_check_env_ttl') != 'x':
                raise ValueError('TTL-set value not readable immediately')
            cache.delete('_check_env_ttl')

            # Increment (used by rate limiting)
            cache.set('_check_env_inc', 0, 10)
            cache.incr('_check_env_inc')
            if cache.get('_check_env_inc') != 1:
                raise ValueError('incr did not work')
            cache.delete('_check_env_inc')

            self.ok('Cache read/write/delete/incr', 'all operations working')
        except Exception as e:
            self.fail('Cache operations', str(e)[:80])

    def check_2fa(self):
        self.section('Two-Factor Authentication (OTP)')

        issuer = getattr(settings, 'OTP_TOTP_ISSUER', '')
        if not issuer:
            self.warn('OTP_TOTP_ISSUER', 'not set — authenticator apps will show blank issuer')
        else:
            self.ok('OTP_TOTP_ISSUER', issuer)

        for setting in ('REQUIRE_2FA_FOR_ADMINS', 'REQUIRE_2FA_FOR_OFFICERS'):
            val = getattr(settings, setting, None)
            if val is None:
                self.warn(setting, 'not defined')
            elif not val:
                self.warn(setting, 'False — 2FA not enforced')
            else:
                self.ok(setting, 'True')

        # Functional: verify django-otp can generate a TOTP token
        try:
            import binascii, os, time
            from django_otp.oath import TOTP

            key = os.urandom(20)
            totp = TOTP(key=key, step=30, t0=0, digits=6, drift=0)
            token = totp.token()
            assert token is not None and len(str(token)) <= 6
            self.ok('TOTP token generation', 'django-otp working correctly')
        except Exception as e:
            self.fail('TOTP token generation', str(e)[:80])

    def check_security(self):
        self.section('Security Settings')

        debug = getattr(settings, 'DEBUG', True)

        ssl_redirect = getattr(settings, 'SECURE_SSL_REDIRECT', False)
        if not debug and not ssl_redirect:
            self.warn('SECURE_SSL_REDIRECT', 'False — HTTP not redirected to HTTPS')
        elif not debug:
            self.ok('SECURE_SSL_REDIRECT', 'True')

        hsts = getattr(settings, 'SECURE_HSTS_SECONDS', 0)
        if not debug and hsts == 0:
            self.warn('SECURE_HSTS_SECONDS', '0 — HSTS not enabled')
        elif not debug:
            self.ok('SECURE_HSTS_SECONDS', f"{hsts}s ({hsts // 86400} days)")

        for setting, label in (
            ('SESSION_COOKIE_SECURE', 'SESSION_COOKIE_SECURE'),
            ('CSRF_COOKIE_SECURE', 'CSRF_COOKIE_SECURE'),
        ):
            val = getattr(settings, setting, False)
            if not debug and not val:
                self.warn(label, 'False — cookies sent over HTTP')
            elif not debug:
                self.ok(label, 'True')

        admin_key = self._env('ADMIN_V2_SECRET_KEY')
        if not admin_key:
            self.fail('ADMIN_V2_SECRET_KEY', 'NOT SET — Admin v2 will be broken')
        elif len(admin_key) < 16:
            self.warn('ADMIN_V2_SECRET_KEY', f"only {len(admin_key)} chars — too short")
        else:
            self.ok('ADMIN_V2_SECRET_KEY', self._mask(admin_key))

        # Functional: verify honeypot URLs are registered
        try:
            from django.urls import reverse
            reverse('honeypot_wp_admin')
            self.ok('Honeypot URLs', 'registered and resolving')
        except Exception as e:
            self.warn('Honeypot URLs', f"not resolving — {e}")

        # Functional: verify IP blacklist table accessible
        try:
            from src.models import IPBlacklist
            IPBlacklist.objects.count()
            self.ok('IPBlacklist table', 'accessible')
        except Exception as e:
            self.fail('IPBlacklist table', str(e)[:80])

    def check_storage(self):
        self.section('File Storage & Media')

        media_root = getattr(settings, 'MEDIA_ROOT', '')
        if not media_root:
            self.warn('MEDIA_ROOT', 'not set')
        elif not os.path.isdir(media_root):
            self.warn('MEDIA_ROOT', f"{media_root} — directory does not exist")
        else:
            # Functional: actually write and read a test file
            test_path = os.path.join(media_root, '_check_env_test.tmp')
            try:
                with open(test_path, 'w') as f:
                    f.write('ok')
                with open(test_path) as f:
                    assert f.read() == 'ok'
                os.remove(test_path)
                self.ok('MEDIA_ROOT write test', f"{media_root}")
            except Exception as e:
                self.fail('MEDIA_ROOT write test', str(e)[:80])

        log_dir = self._env('LOG_DIR') or str(settings.BASE_DIR / 'logs')
        if not os.path.isdir(log_dir):
            self.warn('LOG_DIR', f"{log_dir} — directory does not exist")
        else:
            # Functional: write and read a test log entry
            test_path = os.path.join(log_dir, '_check_env_test.tmp')
            try:
                with open(test_path, 'w') as f:
                    f.write('ok')
                os.remove(test_path)
                self.ok('LOG_DIR write test', f"{log_dir}")
            except Exception as e:
                self.fail('LOG_DIR write test', str(e)[:80])

        # Functional: check static files have been collected
        static_root = getattr(settings, 'STATIC_ROOT', '')
        if not static_root:
            self.warn('STATIC_ROOT', 'not set')
        elif not os.path.isdir(static_root):
            self.warn('STATIC_ROOT', f"{static_root} — run collectstatic")
        else:
            file_count = sum(len(files) for _, _, files in os.walk(static_root))
            if file_count == 0:
                self.warn('STATIC_ROOT', f"{static_root} — empty, run collectstatic")
            else:
                self.ok('STATIC_ROOT', f"{static_root} ({file_count} files)")

    def check_supply_chain(self):
        """
        Verify self-hosted static assets haven't been tampered with and that
        no external CDN references have crept back into templates.
        """
        self.section('Supply Chain & Asset Integrity')
        import hashlib, json, glob as globmod

        base_dir = settings.BASE_DIR

        # ── 1. Vendor file integrity (SHA-256 vs. manifest) ──────────────
        manifest_path = base_dir / 'static' / 'vendor' / '.integrity.json'
        if not manifest_path.exists():
            self.warn('Asset integrity manifest', 'static/vendor/.integrity.json not found — run check_env --update-hashes')
        else:
            try:
                manifest = json.loads(manifest_path.read_text())
                files = manifest.get('files', {})
                all_ok = True
                for rel_path, meta in files.items():
                    abs_path = base_dir / rel_path
                    if not abs_path.exists():
                        self.fail(f'Asset exists: {rel_path}', 'file not found')
                        all_ok = False
                        continue
                    actual = hashlib.sha256(abs_path.read_bytes()).hexdigest()
                    expected = meta.get('sha256', '')
                    if actual != expected:
                        self.fail(
                            f'Asset hash: {abs_path.name}',
                            f'MISMATCH — expected {expected[:16]}… got {actual[:16]}…'
                        )
                        all_ok = False
                    else:
                        self.ok(f'Asset hash: {abs_path.name}', f'{actual[:16]}… ✓')
                if all_ok:
                    self.ok('All vendor asset hashes', f'{len(files)} files verified')
            except Exception as e:
                self.fail('Asset integrity manifest', str(e)[:80])

        # ── 2. CDN drift detection — no external CDN refs in templates ───
        # All assets are self-hosted as of v3.15.9 (Chart.js was the last CDN
        # holdout), so every known CDN host is now a hard fail. cdnjs was added
        # in v3.15.10 — the CSP-blocked chapter-stats Chart.js came from
        # cdnjs.cloudflare.com and this check never watched that host.
        # Prod CSP is script-src 'self' + nonce, so ANY external script host
        # is both a supply-chain risk and dead-on-arrival in prod anyway.
        cdn_patterns = [
            'cdn.tailwindcss.com', 'play.tailwindcss.com',
            'cdn.quilljs.com', 'unpkg.com/',
            'cdnjs.cloudflare.com', 'cdn.jsdelivr.net',
        ]

        template_dir = base_dir / 'templates'
        html_files = list(template_dir.rglob('*.html'))
        cdn_hits = []
        for f in html_files:
            try:
                content = f.read_text(errors='replace')
            except Exception:
                continue
            for pat in cdn_patterns:
                if pat in content:
                    cdn_hits.append(f'{f.relative_to(base_dir)}: {pat}')

        if cdn_hits:
            for hit in cdn_hits:
                self.fail('CDN ref found', hit)
        else:
            self.ok('No CDN refs in templates', f'scanned {len(html_files)} files')

        # ── 3. Python dependency CVE scan (pip-audit) ────────────────────
        import subprocess, shutil
        pip_audit = shutil.which('pip-audit') or str(base_dir / '.venv' / 'bin' / 'pip-audit')
        if not os.path.exists(pip_audit) and not shutil.which('pip-audit'):
            self.warn('pip-audit CVE scan', 'pip-audit not installed — run: pip install pip-audit')
        else:
            try:
                result = subprocess.run(
                    [pip_audit, '--format', 'json', '--progress-spinner', 'off'],
                    capture_output=True, text=True, timeout=60,
                    cwd=str(base_dir)
                )
                import json as _json
                data = _json.loads(result.stdout or '{}')
                # pip-audit JSON format: {"dependencies": [{"name":..., "vulns":[...]}]}
                deps = data.get('dependencies', [])
                all_vulns = [v for dep in deps for v in dep.get('vulns', [])]
                # Ignore ECHO-only advisories with no standard CVE/fix — treat as warnings
                actionable = [v for v in all_vulns if v.get('fix_versions')
                              and not all('echo' in a.lower() for a in ([v['id']] + v.get('aliases', [])))]
                echo_only  = [v for v in all_vulns if v not in actionable]
                if actionable:
                    pkgs = {dep['name'] for dep in deps
                            for v in dep.get('vulns', []) if v in actionable}
                    self.fail('pip-audit CVE scan',
                              f'{len(actionable)} CVE(s) in {len(pkgs)} package(s) — run pip-audit for details')
                elif echo_only:
                    self.warn('pip-audit CVE scan',
                              f'{len(echo_only)} ECHO-database advisory/advisories (no OSV/PYSEC equivalent)')
                else:
                    self.ok('pip-audit CVE scan', 'no known vulnerabilities')
            except subprocess.TimeoutExpired:
                self.warn('pip-audit CVE scan', 'timed out after 60s')
            except Exception as e:
                self.warn('pip-audit CVE scan', f'could not run: {e}')

    def _update_integrity_manifest(self):
        """Recompute SHA-256 hashes and write static/vendor/.integrity.json."""
        import hashlib, json
        from datetime import date

        base_dir = settings.BASE_DIR
        manifest_path = base_dir / 'static' / 'vendor' / '.integrity.json'

        # Auto-discover everything under static/vendor/ instead of a hardcoded
        # list — the old 3-file list silently left chart.min.js and cropper.*
        # untracked, so the integrity check never watched them, and the
        # documented "regenerate" command couldn't fix it. (v3.15.10,
        # 07-24 report item #3.)
        vendor_dir = base_dir / 'static' / 'vendor'
        tracked = sorted(
            str(p.relative_to(base_dir))
            for p in vendor_dir.rglob('*')
            if p.is_file() and p.name != '.integrity.json'
        )
        # tailwind.css lives outside static/vendor/ but is a locally-built,
        # hash-tracked asset too.
        tracked.append('static/css/tailwind.css')

        files_entry = {}
        import base64
        for rel in tracked:
            p = base_dir / rel
            if not p.exists():
                self.warn(f'Skipped (not found)', rel)
                continue
            data = p.read_bytes()
            sha256 = hashlib.sha256(data).hexdigest()
            sri = 'sha384-' + base64.b64encode(hashlib.sha384(data).digest()).decode()
            # Preserve existing metadata (source, version, note) if present
            try:
                existing = json.loads(manifest_path.read_text()).get('files', {}).get(rel, {})
            except Exception:
                existing = {}
            entry = {k: v for k, v in existing.items() if k not in ('sha256', 'sri', 'size')}
            entry.update({'sha256': sha256, 'sri': sri, 'size': len(data)})
            files_entry[rel] = entry
            self.ok(f'Updated hash: {p.name}', sha256[:16] + '…')

        manifest = {
            '_comment': 'SHA-256 hashes of self-hosted vendor files. Regenerate with: python manage.py check_env --update-hashes',
            '_generated': str(date.today()),
            'files': files_entry,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2) + '\n')
        self.stdout.write(self.style.SUCCESS(f'\n  Manifest written to {manifest_path}\n'))

    # ------------------------------------------------------------------ main

    def add_arguments(self, parser):
        parser.add_argument(
            '--update-hashes',
            action='store_true',
            help='Recompute SHA-256 hashes for vendor files and update static/vendor/.integrity.json',
        )

    def handle(self, *args, **kwargs):
        if kwargs.get('update_hashes'):
            self.section('Updating Asset Integrity Manifest')
            self._update_integrity_manifest()
            return

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
        self.check_supply_chain()

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
                '\n  ✗ CRITICAL issues — fix before running in production:\n'
            ))
            for e in self.errors:
                self.stdout.write(self.style.ERROR(f"    • {e}"))

        if self.warnings:
            self.stdout.write(self.style.WARNING('\n  ⚠ Warnings (review recommended):\n'))
            for w in self.warnings:
                self.stdout.write(self.style.WARNING(f"    • {w}"))

        if not self.errors and not self.warnings:
            self.stdout.write(self.style.SUCCESS('\n  ✓ All checks passed — configuration looks good!\n'))
        elif not self.errors:
            self.stdout.write(self.style.WARNING('\n  ⚠ No critical errors, but review warnings above.\n'))

        self.stdout.write('')
