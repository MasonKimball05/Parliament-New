"""
Celery application configuration for Parliament.

v3.45.5: ONE service, parliament-worker.service, runs both the worker and the
beat scheduler (`celery worker -B`). There used to be a separate
parliament-beat.service; it was a whole second copy of Django (~115 MB on a
1 GB server) whose only job is to put scheduled tasks on the queue. Run
embedded, beat is a small process forked from the worker that shares its
memory. This is safe because there is exactly one worker. If a second worker
is ever added, only ONE of them may have -B, or every periodic task runs twice.

Usage:
  celery -A Parliament worker -B -l INFO --concurrency=1 \
      --scheduler django_celery_beat.schedulers:DatabaseScheduler
"""
import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Parliament.settings')

# v3.45.5 — Celery's Django integration runs Django's system checks when a
# worker starts. The URL check imports the URLconf, which imports every view
# module and the libraries they use (webauthn, qrcode, bleach, icalendar,
# DRF, ...): ~30 MB that the worker and beat then keep for their whole life,
# though they never serve a page. The checks still run where they matter:
# `manage.py check`, `migrate` and `preflight` at deploy.
# Set CELERY_SKIP_CHECKS= (empty) in the environment to run them again.
os.environ.setdefault('CELERY_SKIP_CHECKS', '1')

app = Celery('Parliament')

# Read config from Django settings — all Celery keys start with CELERY_
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks from all installed apps
app.autodiscover_tasks()
