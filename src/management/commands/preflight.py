"""
Production preflight self-check — the deploy/cron gate check_env can't be.

Runs every check_env check PLUS runtime invariants that have actually bitten
this project before, and (unlike check_env, which is advisory and always exits
0) **exits non-zero when anything fails**, so it can gate a deploy script or
fire a cron MAILTO alert.

    python manage.py preflight                    # full check, exit 1 on failure
    python manage.py preflight --strict           # warnings also fail
    python manage.py preflight --email-on-fail    # email SECURITY_ALERT_EMAIL on failure
    python manage.py preflight --live-url https://am-parliament.org
                                                  # ALSO probe the real site's /media/
                                                  # through nginx+Cloudflare (the
                                                  # v3.14.1 leak was at the nginx
                                                  # layer, invisible to Django)

Intended use (v3.15.8, 07-23 report item #6 — graduation-handoff hardening):
  * last step of the deploy checklist, and/or
  * daily cron: `manage.py preflight --strict --email-on-fail`

New checks beyond check_env:
  1. Celery beat schedules — every managed SCHEDULES row exists and is enabled;
     dead orphans (unregistered task paths) are reported (never deleted here —
     that stays `setup_celery_schedules --prune-orphans`); beat heartbeat
     staleness on the minute-frequency tasks.
  2. Media access gate — an anonymous request to MEDIA_URL must NOT return 200
     (Django-side via test Client; nginx/Cloudflare-side via --live-url).
"""
import sys

from django.conf import settings

from src.management.commands.check_env import Command as CheckEnvCommand


class Command(CheckEnvCommand):
    help = (
        'Production preflight: all check_env checks + Celery schedule and '
        'media-gate invariants. Exits 1 on failure (deploy/cron gate).'
    )

    # ------------------------------------------------------------------ args

    def add_arguments(self, parser):
        # NOTE: deliberately does NOT inherit check_env's --update-hashes;
        # preflight is read-only.
        parser.add_argument(
            '--strict', action='store_true',
            help='Treat warnings as failures (exit 1 on any warning).',
        )
        parser.add_argument(
            '--email-on-fail', action='store_true',
            help='Email the failure summary to SECURITY_ALERT_EMAIL.',
        )
        parser.add_argument(
            '--live-url', default='',
            help='Base URL of the live site (e.g. https://am-parliament.org). '
                 'If given, also probes /media/ through the real nginx/Cloudflare '
                 'stack — the layer where the v3.14.1 leak actually lived.',
        )

    # ------------------------------------------------------------------ new checks

    def check_celery_schedules(self):
        self.section('Celery Beat Schedules')
        try:
            from django_celery_beat.models import PeriodicTask
        except Exception as e:
            self.fail('django_celery_beat', f'not importable: {e}')
            return
        try:
            from src.management.commands.setup_celery_schedules import SCHEDULES
        except Exception as e:
            self.warn('Managed schedule specs', f'could not import: {e}')
            return

        expected = {s['name'] for s in SCHEDULES}
        rows = {t.name: t for t in PeriodicTask.objects.filter(name__in=expected)}

        missing = sorted(expected - set(rows))
        if missing:
            self.fail('Managed schedules present',
                      f'{len(missing)} missing (run setup_celery_schedules): '
                      + ', '.join(missing[:3]) + ('…' if len(missing) > 3 else ''))
        else:
            self.ok('Managed schedules present', f'all {len(expected)} rows exist')

        disabled = sorted(n for n, t in rows.items() if not t.enabled)
        if disabled:
            self.fail('Managed schedules enabled',
                      f'{len(disabled)} disabled: ' + ', '.join(disabled[:3]))
        elif rows:
            self.ok('Managed schedules enabled', 'all enabled')

        # Beat heartbeat: the minute-frequency vote tasks should have run
        # recently if beat + worker are alive. Freshly-seeded rows have
        # last_run_at=None — that's a warn (first run pending), not a fail.
        from django.utils import timezone
        run_times = [t.last_run_at for t in rows.values() if t.last_run_at]
        if not run_times:
            self.warn('Beat heartbeat', 'no managed task has ever run '
                      '(fresh seed, or beat/worker never started)')
        else:
            newest = max(run_times)
            age = timezone.now() - newest
            if age.total_seconds() > 2 * 3600:
                self.fail('Beat heartbeat',
                          f'newest last_run_at is {int(age.total_seconds() // 3600)}h old '
                          '— beat or worker looks down')
            else:
                self.ok('Beat heartbeat',
                        f'a managed task ran {int(age.total_seconds() // 60)} min ago')

        # Dead-orphan report (registration-based, same criterion + safety fuse
        # as setup_celery_schedules; report-only here).
        try:
            from celery import current_app
            current_app.loader.import_default_modules()
            registered = set(current_app.tasks.keys())
            if 'tasks.send_daily_digest' not in registered:
                self.warn('Orphan check', 'task registry looks unloaded — skipped')
                return
            dead = sorted(
                t.name for t in PeriodicTask.objects.exclude(task__in=registered)
                if not t.task.startswith('celery.')
            )
            if dead:
                self.warn('Dead orphan schedules',
                          f'{len(dead)} rows point at unregistered tasks '
                          '(setup_celery_schedules --prune-orphans): '
                          + ', '.join(dead[:3]))
            else:
                self.ok('Dead orphan schedules', 'none')
        except Exception as e:
            self.warn('Orphan check', f'skipped: {e}')

    def _client_host(self):
        for h in getattr(settings, 'ALLOWED_HOSTS', []):
            if h and h != '*':
                return h.lstrip('.')
        return 'testserver'

    def check_media_gate(self):
        self.section('Media Access Gate')

        # Prefer probing a real uploaded file so 200-vs-redirect is decisive;
        # fall back to a dummy path (serve_media is @login_required, so the
        # auth redirect fires before any 404).
        probe = '__preflight_probe__.txt'
        try:
            from src.models import CommitteeDocument
            doc = CommitteeDocument.objects.exclude(document='').first()
            if doc:
                probe = doc.document.name
        except Exception:
            pass
        media_url = getattr(settings, 'MEDIA_URL', '/media/')
        path = media_url.rstrip('/') + '/' + probe.lstrip('/')

        # Django-side: anonymous request must not get the file.
        try:
            from django.test import Client
            resp = Client().get(path, secure=True, HTTP_HOST=self._client_host())
            if resp.status_code == 200:
                self.fail('Django media gate',
                          f'anonymous GET {path} returned 200 — media is public!')
            elif resp.status_code in (301, 302) and 'login' in resp.headers.get('Location', ''):
                self.ok('Django media gate', f'anonymous GET redirects to login ({resp.status_code})')
            elif resp.status_code in (301, 302, 401, 403, 404):
                self.ok('Django media gate', f'anonymous GET blocked ({resp.status_code})')
            else:
                self.warn('Django media gate', f'unexpected status {resp.status_code}')
        except Exception as e:
            self.warn('Django media gate', f'could not probe: {str(e)[:70]}')

        # Live-site side (nginx + Cloudflare) — only with --live-url. This is
        # the layer the v3.14.1 leak lived at: a public nginx `location
        # /media/` serves files before Django ever sees the request.
        if not self._live_url:
            self.warn('Live media gate', 'skipped — pass --live-url to probe '
                      'through nginx/Cloudflare (the layer of the v3.14.1 leak)')
            return
        try:
            import requests
            url = self._live_url.rstrip('/') + path
            r = requests.get(url, timeout=10, allow_redirects=False)
            if r.status_code == 200:
                self.fail('Live media gate',
                          f'{url} returned 200 anonymously — nginx is serving '
                          '/media/ publicly (v3.14.1 deploy gate NOT done)')
            elif r.status_code in (301, 302, 401, 403):
                self.ok('Live media gate', f'blocked at the edge ({r.status_code})')
            elif r.status_code == 404:
                self.warn('Live media gate',
                          '404 — ambiguous (file missing vs public-but-absent); '
                          'probe an existing document path to be sure')
            else:
                self.warn('Live media gate', f'unexpected status {r.status_code}')
        except Exception as e:
            self.warn('Live media gate', f'could not reach site: {str(e)[:70]}')

    # ------------------------------------------------------------------ handle

    def handle(self, *args, **options):
        self._live_url = options.get('live_url', '')

        self.stdout.write(self.style.MIGRATE_HEADING(
            '\n══════════════════════════════════════════════════════════════\n'
            '  Parliament — Production Preflight\n'
            '══════════════════════════════════════════════════════════════'
        ))

        # All of check_env's checks…
        self.check_core()
        self.check_database()
        self.check_encryption()
        self.check_email()
        self.check_cache_redis()
        self.check_2fa()
        self.check_security()
        self.check_storage()
        self.check_supply_chain()
        # …plus the preflight-only runtime invariants.
        self.check_celery_schedules()
        self.check_media_gate()

        # Summary + exit semantics (this is the part check_env doesn't have).
        self.stdout.write(f"\n{'─' * 64}")
        total = len(self.passed) + len(self.warnings) + len(self.errors)
        self.stdout.write(
            f"  {self.style.SUCCESS(f'{len(self.passed)} passed')}  "
            f"{self.style.WARNING(f'{len(self.warnings)} warnings')}  "
            f"{self.style.ERROR(f'{len(self.errors)} failed')}  "
            f"({total} checks total)"
        )
        for e in self.errors:
            self.stdout.write(self.style.ERROR(f'    ✗ {e}'))
        if options['strict']:
            for w in self.warnings:
                self.stdout.write(self.style.WARNING(f'    ⚠ {w}'))

        failed = bool(self.errors) or (options['strict'] and bool(self.warnings))

        if failed and options['email_on_fail']:
            self._email_failures(strict=options['strict'])

        if failed:
            self.stdout.write(self.style.ERROR('\n  PREFLIGHT FAILED\n'))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS('\n  PREFLIGHT PASSED\n'))

    def _email_failures(self, strict=False):
        try:
            from django.core.mail import send_mail
            to = getattr(settings, 'SECURITY_ALERT_EMAIL', '') or \
                getattr(settings, 'DEFAULT_FROM_EMAIL', '')
            if not to:
                self.warn('Failure email', 'no SECURITY_ALERT_EMAIL configured')
                return
            lines = [f'FAIL: {e}' for e in self.errors]
            if strict:
                lines += [f'WARN: {w}' for w in self.warnings]
            send_mail(
                subject='[Parliament] PREFLIGHT FAILED',
                message='Production preflight failed:\n\n' + '\n'.join(lines)
                        + '\n\nRun `python manage.py preflight` on the server for detail.',
                from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
                recipient_list=[to],
                fail_silently=False,
            )
            self.stdout.write(f'  Failure summary emailed to {to}')
        except Exception as e:
            self.stdout.write(self.style.WARNING(f'  Could not send failure email: {e}'))
