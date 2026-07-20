"""
Independent watchdog for the daily site digest (v3.15.2).

Run from SYSTEM cron, NOT Celery beat — the whole point is to catch the case
where Celery itself dies (the 07-15→07-19 incident: the digest stopped and
nothing noticed for four days). The digest writes a `logs/last_digest_sent`
heartbeat only on a successful email, so a stale/missing heartbeat means the
digest didn't go out — whether the worker was down, SMTP failed, or no
recipient was configured.

Behavior when stale (default: older than 26h):
  - prints a WARNING to STDERR and exits 1 → system cron's MAILTO emails you
    via a channel that does NOT depend on Django's SMTP or Celery;
  - also best-effort sends a Django email to SECURITY_ALERT_EMAIL as a second
    channel.
When fresh: silent, exit 0 (cron sends nothing).

Cron (see scripts/crontab.txt):
  0 12 * * * cd /var/www/Parliament-New && source venv/bin/activate && \
      python manage.py check_digest_freshness
"""
import os
import sys
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Alert if the daily digest heartbeat is stale (watchdog).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-age-hours', type=float, default=26.0,
            help='Alert if the last successful digest is older than this '
                 '(default 26h — one day plus slack).')

    def _heartbeat_path(self):
        log_dir = os.path.join(settings.BASE_DIR, os.getenv('LOG_DIR', 'logs'))
        return os.path.join(log_dir, 'last_digest_sent')

    def handle(self, *args, **options):
        max_age = options['max_age_hours']
        path = self._heartbeat_path()
        now = datetime.now(dt_timezone.utc)

        problem = None
        if not os.path.exists(path):
            problem = (f"No digest heartbeat file ({path}). The daily digest "
                       f"has not successfully sent since this watchdog was "
                       f"installed — check Celery worker/beat and mail config.")
        else:
            age_h = (now.timestamp() - os.path.getmtime(path)) / 3600.0
            if age_h > max_age:
                problem = (f"Daily digest is STALE: last successful send was "
                           f"{age_h:.1f}h ago (threshold {max_age:.0f}h). "
                           f"Check parliament-worker / parliament-beat and the "
                           f"mail backend.")

        if not problem:
            # Fresh — stay silent so cron emails nothing.
            return

        # Channel 1: stderr + non-zero exit → system cron MAILTO (independent
        # of Django's SMTP and of Celery).
        self.stderr.write(f"[digest-watchdog] {problem}")

        # Channel 2: best-effort Django email (may itself fail if SMTP is the
        # problem — that's why channel 1 exists).
        try:
            from django.core.mail import send_mail
            to = getattr(settings, 'SECURITY_ALERT_EMAIL', None) or \
                getattr(settings, 'DEFAULT_FROM_EMAIL', None)
            if to:
                send_mail(
                    subject='[Parliament] ⚠ Daily digest watchdog — no recent digest',
                    message=problem,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    recipient_list=[to],
                    fail_silently=True,
                )
        except Exception:
            pass

        sys.exit(1)
