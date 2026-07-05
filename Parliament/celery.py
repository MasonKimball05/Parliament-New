"""
Celery application configuration for Parliament.

Workers: parliament-worker.service (runs tasks)
Beat:    parliament-beat.service (schedules periodic tasks)

Usage:
  Start worker:  celery -A Parliament worker -l INFO
  Start beat:    celery -A Parliament beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler
"""
import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Parliament.settings')

app = Celery('Parliament')

# Read config from Django settings — all Celery keys start with CELERY_
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks from all installed apps
app.autodiscover_tasks()
