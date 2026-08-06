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
    # v3.19.0 — announce bills when available_at arrives, not when they are
    # saved. Every minute, same cadence as the open/close tasks, because a bill
    # becoming available is the same kind of scheduled transition.
    {
        'name': 'Notify chapter of newly available legislation',
        'task': 'tasks.notify_available_legislation',
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
    # Vote receipt expiry notices — daily (v3.14.0)
    # -------------------------------------------------------------------------
    {
        'name': 'Notify expired vote receipts',
        'task': 'tasks.notify_expired_vote_receipts',
        'crontab': {'hour': '9', 'minute': '15'},  # 3:15 AM CST daily
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
    # Officer-configured notification schedules — every 15 minutes
    # -------------------------------------------------------------------------
    {
        'name': 'Fire scheduled notifications',
        'task': 'tasks.fire_scheduled_notifications',
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
]


class Command(BaseCommand):
    help = 'Register default Celery Beat periodic task schedules in the database'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='Delete and recreate all managed schedules (use after renaming tasks)',
        )
        parser.add_argument(
            '--prune-orphans',
            action='store_true',
            help='Delete enabled PeriodicTask rows NOT in the managed set '
                 '(stale leftovers from removed features, e.g. the old '
                 '"Prune old auth tokens" / "Send weekly chapter digest" rows). '
                 'Without this flag, orphans are only reported, never deleted.',
        )

    def handle(self, *args, **options):
        reset = options['reset']
        prune = options['prune_orphans']
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

        # --- Orphan reconciliation (v3.15.2) -------------------------------
        # A DEAD orphan is a PeriodicTask whose `task` path is no longer a
        # registered Celery task in the deployed code — beat can never run it,
        # its last_run_at freezes, and it trips the daily digest's "Beat may be
        # down" HIGH check forever (e.g. the v3.5.0 "Prune old auth tokens" row).
        #
        # Criterion is REGISTRATION, not the managed SCHEDULES set: a valid
        # task that's simply seeded elsewhere (e.g. "Send daily honeypot
        # digest") is still registered and must NOT be flagged. Reads the live
        # registry, so it self-corrects to whatever code is actually deployed.
        from celery import current_app
        # Force all task modules to import so the registry is fully populated
        # (a management command isn't a worker — without this, autodiscovered
        # tasks may be absent and every row would look "dead").
        try:
            current_app.loader.import_default_modules()
        except Exception as exc:  # pragma: no cover - defensive
            self.stdout.write(self.style.WARNING(
                f'\nSkipping orphan check — could not load task registry: {exc}'))
            return
        registered = set(current_app.tasks.keys())
        # Safety fuse: if a known-always-present managed task is missing, the
        # registry didn't load — refuse to flag/delete anything rather than
        # risk nuking live schedules on a false negative.
        SENTINEL = 'tasks.send_daily_digest'
        if SENTINEL not in registered:
            self.stdout.write(self.style.WARNING(
                f'\nSkipping orphan check — task registry looks unloaded '
                f'({SENTINEL!r} not registered). No rows touched.'))
            return
        dead = [t for t in PeriodicTask.objects.all()
                if t.task not in registered]
        if dead:
            self.stdout.write(self.style.WARNING(
                f'\n{len(dead)} orphan periodic task(s) point to UNREGISTERED '
                f'tasks (dead — beat cannot run them):'))
            for t in dead:
                last = (t.last_run_at.strftime('%Y-%m-%d %H:%M')
                        if t.last_run_at else 'NEVER')
                self.stdout.write(
                    f'  {"[enabled]" if t.enabled else "[disabled]"} '
                    f'{t.name}  ->  {t.task}  (last run: {last})')
            if prune:
                PeriodicTask.objects.filter(
                    pk__in=[t.pk for t in dead]).delete()
                self.stdout.write(self.style.SUCCESS(
                    f'  Pruned {len(dead)} orphan row(s).'))
            else:
                self.stdout.write(
                    '  Re-run with --prune-orphans to delete these. '
                    '(Verify none are still wanted first.)')
        else:
            self.stdout.write(self.style.SUCCESS(
                '\nNo orphan periodic tasks (all point to registered tasks).'))
