"""
Register default Celery Beat periodic task schedules in the database.

Run once after deploying Celery for the first time (and after new tasks are added):

    python manage.py setup_celery_schedules

Schedules are stored in django_celery_beat's PeriodicTask table so they can be
paused or adjusted from admin-v2 without touching code.

Running this command again is safe — it uses get_or_create so existing schedules
are not overwritten unless --reset is passed.
"""
from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask, IntervalSchedule, CrontabSchedule
import json


SCHEDULES = [
    # -------------------------------------------------------------------------
    # Vote auto-open / close — runs every minute so votes open/close on time
    # -------------------------------------------------------------------------
    {
        'name': 'Auto open/close chapter votes',
        'task': 'tasks.auto_open_close_chapter_votes',
        'interval': {'every': 1, 'period': IntervalSchedule.MINUTES},
    },
    {
        'name': 'Auto open/close committee votes',
        'task': 'tasks.auto_open_close_committee_votes',
        'interval': {'every': 1, 'period': IntervalSchedule.MINUTES},
    },
    {
        'name': 'Auto open/close slating votes',
        'task': 'tasks.auto_open_close_slating_votes',
        'interval': {'every': 1, 'period': IntervalSchedule.MINUTES},
    },

    # -------------------------------------------------------------------------
    # Scheduled announcement emails — every 5 minutes
    # -------------------------------------------------------------------------
    {
        'name': 'Publish scheduled announcements',
        'task': 'tasks.publish_scheduled_announcements',
        'interval': {'every': 5, 'period': IntervalSchedule.MINUTES},
    },

    # -------------------------------------------------------------------------
    # Event reminder push notifications — every 15 minutes
    # -------------------------------------------------------------------------
    {
        'name': 'Send event reminder push notifications',
        'task': 'tasks.send_event_reminder_pushes',
        'interval': {'every': 15, 'period': IntervalSchedule.MINUTES},
    },

    # -------------------------------------------------------------------------
    # Service event email reminders — every 15 minutes
    # -------------------------------------------------------------------------
    {
        'name': 'Send service event email reminders',
        'task': 'tasks.send_service_event_email_reminders',
        'interval': {'every': 15, 'period': IntervalSchedule.MINUTES},
    },

    # -------------------------------------------------------------------------
    # Recruitment RSVP reminders (push + email) — every 15 minutes
    # -------------------------------------------------------------------------
    {
        'name': 'Send recruitment RSVP reminders',
        'task': 'tasks.send_recruitment_rsvp_reminders',
        'interval': {'every': 15, 'period': IntervalSchedule.MINUTES},
    },

    # -------------------------------------------------------------------------
    # Event sign-up announcement emails — every 15 minutes
    # -------------------------------------------------------------------------
    {
        'name': 'Send event sign-up announcement emails',
        'task': 'tasks.send_event_signup_announcements',
        'interval': {'every': 15, 'period': IntervalSchedule.MINUTES},
    },

    # -------------------------------------------------------------------------
    # Housekeeping — daily tasks (3:00–3:30 AM CST = 09:00–09:30 UTC)
    # -------------------------------------------------------------------------
    {
        'name': 'Cleanup expired user sessions',
        'task': 'tasks.cleanup_expired_sessions',
        'crontab': {'hour': '9', 'minute': '0'},   # 3:00 AM CST daily
    },
    {
        'name': 'Prune expired login lockouts',
        'task': 'tasks.prune_expired_login_lockouts',
        'crontab': {'hour': '9', 'minute': '5'},   # 3:05 AM CST daily
    },
    {
        'name': 'Expire stale IP blacklist entries',
        'task': 'tasks.expire_stale_ip_blacklist_entries',
        'crontab': {'hour': '9', 'minute': '10'},  # 3:10 AM CST daily
    },
    {
        'name': 'Auto-release expired quarantines',
        'task': 'tasks.release_expired_quarantines',
        'crontab': {'hour': '9', 'minute': '11'},  # 3:11 AM CST daily
    },
    {
        'name': 'Prune expired chat permissions',
        'task': 'tasks.prune_expired_chat_permissions',
        'crontab': {'hour': '9', 'minute': '12'},  # 3:12 AM CST daily
    },
    {
        'name': 'Notify expiring API tokens',
        'task': 'tasks.notify_expiring_api_tokens',
        'crontab': {'hour': '9', 'minute': '14'},  # 3:14 AM CST daily
    },

    # -------------------------------------------------------------------------
    # Housekeeping — monthly tasks (1st of month, 3:15 AM CST = 09:15 UTC)
    # -------------------------------------------------------------------------
    {
        'name': 'Prune stale push subscriptions',
        'task': 'tasks.prune_stale_push_subscriptions',
        'crontab': {'hour': '9', 'minute': '15', 'day_of_month': '1'},
    },
    {
        'name': 'Prune API access logs (90 days)',
        'task': 'tasks.cleanup_api_access_logs',
        'crontab': {'hour': '9', 'minute': '20', 'day_of_month': '1'},  # 3:20 AM CST, 1st of month
    },

    # -------------------------------------------------------------------------
    # Daily digest — system audit + honeypot activity combined
    # 3:30 AM CST = 09:30 UTC (after all cleanup tasks complete)
    # -------------------------------------------------------------------------
    {
        'name': 'Send daily site digest',
        'task': 'tasks.send_daily_digest',
        'crontab': {'hour': '9', 'minute': '30'},  # 3:30 AM CST daily
    },

    # -------------------------------------------------------------------------
    # Weekly chapter digest — personalised email per member every Sunday morning
    # 8:00 AM CST = 14:00 UTC, Sunday (day_of_week=0)
    # -------------------------------------------------------------------------
    {
        'name': 'Send weekly chapter digest',
        'task': 'tasks.send_weekly_chapter_digest',
        'crontab': {'hour': '14', 'minute': '0', 'day_of_week': '0'},  # 8:00 AM CST Sunday
    },
]


class Command(BaseCommand):
    help = 'Register default Celery Beat periodic task schedules in the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete and recreate all managed schedules (use after renaming tasks)',
        )

    def handle(self, *args, **options):
        reset = options['reset']
        created = 0
        skipped = 0

        for spec in SCHEDULES:
            task_name = spec['name']

            if reset:
                PeriodicTask.objects.filter(name=task_name).delete()

            # Build the schedule object
            if 'interval' in spec:
                iv = spec['interval']
                schedule, _ = IntervalSchedule.objects.get_or_create(
                    every=iv['every'],
                    period=iv['period'],
                )
                defaults = {
                    'task': spec['task'],
                    'interval': schedule,
                    'crontab': None,
                    'args': json.dumps([]),
                    'enabled': True,
                }
            else:
                ct = spec['crontab']
                schedule, _ = CrontabSchedule.objects.get_or_create(
                    minute=ct.get('minute', '*'),
                    hour=ct.get('hour', '*'),
                    day_of_week=ct.get('day_of_week', '*'),
                    day_of_month=ct.get('day_of_month', '*'),
                    month_of_year=ct.get('month_of_year', '*'),
                )
                defaults = {
                    'task': spec['task'],
                    'crontab': schedule,
                    'interval': None,
                    'args': json.dumps([]),
                    'enabled': True,
                }

            _, was_created = PeriodicTask.objects.get_or_create(
                name=task_name,
                defaults=defaults,
            )

            if was_created:
                self.stdout.write(self.style.SUCCESS(f'  Created: {task_name}'))
                created += 1
            else:
                self.stdout.write(f'  Already exists (skipped): {task_name}')
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f'\nDone. Created {created}, skipped {skipped}.'
        ))
        if skipped and not reset:
            self.stdout.write('  Run with --reset to force-recreate existing schedules.')
