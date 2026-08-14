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
        # v3.17.4: the import above is guarded but the QUERY was not, so on a
        # database where django_celery_beat has not been migrated this command
        # died with a raw traceback — no summary, no exit code, no indication of
        # which check failed. That is the one environment a preflight tool most
        # needs to survive: its whole job is reporting on a broken setup, so it
        # has to fail as a *finding* rather than as a crash.
        # (Found by running `manage.py preflight` for the first time since
        # v3.15.8 built it — against an unmigrated dev database.)
        try:
            rows = {t.name: t for t in PeriodicTask.objects.filter(name__in=expected)}
        except Exception as e:
            self.fail('Celery schedule table',
                      f'could not query PeriodicTask ({type(e).__name__}) — '
                      f'run `migrate` before relying on this check')
            return

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

    # ------------------------------------------------------- cloudflare origin

    def check_cloudflare_origin(self):
        """
        Is the origin reachable without going through Cloudflare? (v3.19.3)

        Why this is worth a preflight check rather than a code comment: since
        v3.18.8 the `CF-Connecting-IP` header decides what the IP blocklist
        blocks, what both per-IP login rate limiters count, what the honeypot
        bans, what the geo gate sees, and what every `ActivityLog`,
        `LoginHistory` and `UserSession` row records. It is an ordinary request
        header. If anything can reach the origin without passing through
        Cloudflare, all of that becomes attacker-selectable — and there is no
        way to determine that from inside Django, because a request that
        bypassed the edge looks entirely normal by the time a view sees it.

        So this probes from outside, which is the only place the question can be
        answered.
        """
        from django.conf import settings

        from src.utils.cloudflare_ranges import (
            GENERATED, cloudflare_networks, declared_range_count,
        )

        behind_cf = getattr(settings, 'BEHIND_CLOUDFLARE', False)
        verifying = getattr(settings, 'CLOUDFLARE_VERIFY_ORIGIN', False)

        if not behind_cf:
            self.ok('Cloudflare origin', 'BEHIND_CLOUDFLARE=False — check not applicable')
            return

        # The range table itself: parsed count must match the declared one, or a
        # malformed entry is silently narrowing verification.
        parsed, declared = len(cloudflare_networks()), declared_range_count()
        if parsed != declared:
            self.fail('Cloudflare ranges',
                      f'{declared - parsed} of {declared} entries failed to parse in '
                      'src/utils/cloudflare_ranges.py — verification is narrower than it looks')
        else:
            self.ok('Cloudflare ranges', f'{parsed} ranges parsed (generated {GENERATED})')

        try:
            from datetime import date
            age_days = (date.today() - date.fromisoformat(GENERATED)).days
            if age_days > 365:
                self.warn('Cloudflare range age',
                          f'{age_days} days old — refresh from cloudflare.com/ips-v4 and '
                          '/ips-v6. A missing range does not admit a forgery; it makes '
                          'audit rows record the edge again for that traffic.')
        except ValueError:
            self.warn('Cloudflare range age', f'GENERATED is not an ISO date: {GENERATED!r}')

        if not self._live_url:
            self.warn('Cloudflare origin',
                      'skipped — pass --live-url to probe whether a forged '
                      'CF-Connecting-IP survives to the application')
            return

        # The probe: send a header no legitimate client sends and see whether the
        # site treats it as our address. `/login/` because it is anonymous, cheap
        # and always present; a GET does not consume a rate-limit bucket (both
        # limiters gate on POST).
        forged = '192.0.2.111'  # TEST-NET-1, RFC 5737 — never a real visitor
        try:
            import requests
            url = self._live_url.rstrip('/') + '/login/'
            r = requests.get(
                url, timeout=10, allow_redirects=False,
                headers={'CF-Connecting-IP': forged},
            )
        except Exception as e:
            self.warn('Cloudflare origin', f'could not reach site: {str(e)[:70]}')
            return

        # Reaching the edge at all means the hostname resolves through Cloudflare,
        # which is necessary but not sufficient — the origin may still answer on
        # its raw IP. Say so rather than implying a clean bill of health.
        via_cf = any(h.lower() in ('cf-ray', 'cf-cache-status') for h in r.headers)

        if not via_cf:
            self.fail('Cloudflare origin',
                      f'{url} answered WITHOUT a CF-Ray header — this request did not '
                      'pass through Cloudflare, so CF-Connecting-IP is client-supplied. '
                      'Restrict the origin at the firewall, or set '
                      'CLOUDFLARE_VERIFY_ORIGIN=True as a stopgap.')
        elif verifying:
            self.ok('Cloudflare origin',
                    'reached via Cloudflare; CLOUDFLARE_VERIFY_ORIGIN=True, so a forged '
                    'header from a direct connection would be ignored and logged')
        else:
            self.warn('Cloudflare origin',
                      'reached via Cloudflare, but CLOUDFLARE_VERIFY_ORIGIN=False — this '
                      'probe cannot tell whether the origin ALSO answers on its raw IP. '
                      'Confirm the firewall, or set CLOUDFLARE_VERIFY_ORIGIN=True and '
                      'watch for FORGED_CF_HEADER in the security log.')

    def check_system_checks(self):
        """
        v3.19.8 — run Django's own system checks and make them GATE the deploy.

        ⚠️ WHY THIS IS HERE AND NOT LEFT TO `manage.py check`. `src.W002` (the
        Kai schema looks unmigrated) and `src.W003` (the release ledger
        disagrees with git) are both things you would want to stop a deploy, and
        both are `Warning`, which stops nothing. `manage.py check` prints them
        and exits 0.

        `src.W003`'s own changelog makes the argument for its existence — *no
        amount of care at authoring time can fix a line whose value does not
        exist until after the writing is over* — and then relies on somebody
        happening to run `manage.py check`. Nobody did: v3.19.7 was committed on
        08-13-26 with its ledger lines still reading "not yet", and the check
        that would have said so sat silent for two days until the nightly review
        ran it by hand.

        **A guard needs a trigger it does not have to be remembered.** This
        command already gates deploys and cron, so it is the trigger that
        already exists.

        The severity mapping is deliberately not 1:1 with Django's: an `Error`
        or `Critical` is an error here, and `src.W002`/`src.W003` are promoted
        to errors because they are release-integrity facts rather than style
        advice. Every other warning stays a warning — promoting all of them
        would make this noisy, and a preflight nobody reads is the failure mode
        it was built to avoid.
        """
        from django.core import checks as dj_checks

        GATING = {'src.W002', 'src.W003'}

        try:
            messages = dj_checks.run_checks()
        except Exception as exc:  # pragma: no cover - a crashing check is itself a finding
            self.fail('System checks', f'could not run: {exc}')
            return

        blocking = [m for m in messages
                    if m.is_serious(dj_checks.ERROR) or m.id in GATING]
        advisory = [m for m in messages if m not in blocking]

        for m in blocking:
            self.fail(f'System check {m.id or ""}'.strip(), str(m.msg).splitlines()[0])
        for m in advisory:
            self.warn(f'System check {m.id or ""}'.strip(), str(m.msg).splitlines()[0])

        if not messages:
            self.ok('System checks', 'no issues (includes the Kai schema and release-ledger gates)')

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
        self.check_cloudflare_origin()   # v3.19.3
        self.check_system_checks()       # v3.19.8 — src.W002/W003 gate the deploy

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
