"""
v3.31.x — the Celery Beat health check inside send_daily_digest
(src/tasks/notifications.py), fixed 09-16-26.

Before the fix, "Beat may be down" fired for ANY enabled task whose
last_run_at was more than a flat 48 hours in the past, regardless of that
task's own schedule. That's correct for a daily-or-more-often task and a
permanent false positive for anything on a longer cadence — both monthly
(day_of_month=1) tasks in setup_celery_schedules.py ('Prune API access
logs (90 days)', 'Prune stale push subscriptions') tripped it every day of
the month except the first two, which is exactly what Mason reported live.

Fixed by asking each task's own `schedule.remaining_estimate(last_run_at)`
(a celery.schedules API, correct for both crontab and interval schedules)
whether it is actually overdue for its NEXT scheduled run, rather than
whether a fixed amount of wall-clock time has passed since its last one.

Covers: a long-cadence task on-schedule is not flagged even though it is
well past 48h since its last run; a long-cadence task that genuinely missed
a run IS flagged; a short-cadence task still gets caught quickly if Beat is
actually down (this must not regress into only catching monthly-scale
outages); a schedule that can't be evaluated falls back to the old flat
window rather than going silent; and the exact production schedule shape
(day_of_month=1) behaves correctly using real calendar math.
"""
import datetime
from unittest import mock

from django.test import TestCase
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

from src.tasks.notifications import send_daily_digest


def flagged_messages(mock_logger):
    """Every [HIGH]-severity 'Beat may be down' message the digest logged."""
    return [
        call.args[0] for call in mock_logger.warning.call_args_list
        if 'Beat may be down' in call.args[0]
    ]


class LongCadenceTaskNotFalselyFlaggedTests(TestCase):
    """The bug as reported: a monthly task, well past 48h since its last
    run, but not actually due yet."""

    def setUp(self):
        self.interval, _ = IntervalSchedule.objects.get_or_create(
            every=30, period=IntervalSchedule.DAYS)

    @mock.patch('src.tasks.notifications.logger')
    def test_a_monthly_cadence_task_on_schedule_is_not_flagged(self, mock_logger):
        # Ran 20 days ago on a 30-day cadence — 10 days from being due, but
        # 20 days > the old flat 48h window that used to flag this.
        PeriodicTask.objects.create(
            name='Monthly task on schedule', task='tasks.check_weak_passwords',
            interval=self.interval, enabled=True,
            last_run_at=timezone.now() - datetime.timedelta(days=20),
        )
        send_daily_digest()
        self.assertEqual(flagged_messages(mock_logger), [])

    @mock.patch('src.tasks.notifications.logger')
    def test_a_monthly_cadence_task_that_missed_its_run_is_flagged(self, mock_logger):
        # 35 days since last run on a 30-day cadence — genuinely missed its
        # next occurrence 5 days ago.
        PeriodicTask.objects.create(
            name='Monthly task overdue', task='tasks.check_weak_passwords',
            interval=self.interval, enabled=True,
            last_run_at=timezone.now() - datetime.timedelta(days=35),
        )
        send_daily_digest()
        messages = flagged_messages(mock_logger)
        self.assertEqual(len(messages), 1)
        self.assertIn('Monthly task overdue', messages[0])


class ShortCadenceTaskStillCaughtTests(TestCase):
    """Regression guard: the fix must not weaken detection for the
    frequent tasks this check was originally built for."""

    def setUp(self):
        self.interval, _ = IntervalSchedule.objects.get_or_create(
            every=1, period=IntervalSchedule.MINUTES)

    @mock.patch('src.tasks.notifications.logger')
    def test_a_1_minute_task_idle_for_3_hours_is_flagged(self, mock_logger):
        PeriodicTask.objects.create(
            name='Vote auto-close', task='tasks.auto_open_close_chapter_votes',
            interval=self.interval, enabled=True,
            last_run_at=timezone.now() - datetime.timedelta(hours=3),
        )
        send_daily_digest()
        messages = flagged_messages(mock_logger)
        self.assertEqual(len(messages), 1)
        self.assertIn('Vote auto-close', messages[0])

    @mock.patch('src.tasks.notifications.logger')
    def test_a_1_minute_task_that_just_ran_is_not_flagged(self, mock_logger):
        PeriodicTask.objects.create(
            name='Vote auto-close', task='tasks.auto_open_close_chapter_votes',
            interval=self.interval, enabled=True,
            last_run_at=timezone.now() - datetime.timedelta(seconds=30),
        )
        send_daily_digest()
        self.assertEqual(flagged_messages(mock_logger), [])

    @mock.patch('src.tasks.notifications.logger')
    def test_within_the_grace_window_is_not_flagged(self, mock_logger):
        # 1 hour overdue on a 1-minute cadence is enormously overdue in
        # relative terms, but OVERDUE_GRACE (2h) exists precisely so a
        # short, ordinary beat hiccup doesn't page anyone — it should only
        # fire once genuinely stuck.
        PeriodicTask.objects.create(
            name='Vote auto-close', task='tasks.auto_open_close_chapter_votes',
            interval=self.interval, enabled=True,
            last_run_at=timezone.now() - datetime.timedelta(hours=1),
        )
        send_daily_digest()
        self.assertEqual(flagged_messages(mock_logger), [])


class ScheduleEvaluationFailureFallsBackTests(TestCase):
    """If remaining_estimate() itself blows up, the check must not go
    silent — it falls back to the old flat-48h heuristic rather than
    silently dropping the task."""

    def setUp(self):
        self.interval, _ = IntervalSchedule.objects.get_or_create(
            every=1, period=IntervalSchedule.DAYS)

    @mock.patch('src.tasks.notifications.logger')
    def test_a_schedule_that_raises_falls_back_to_the_flat_window(self, mock_logger):
        PeriodicTask.objects.create(
            name='Broken schedule task', task='tasks.check_weak_passwords',
            interval=self.interval, enabled=True,
            last_run_at=timezone.now() - datetime.timedelta(hours=72),
        )
        with mock.patch(
            'django_celery_beat.models.IntervalSchedule.schedule',
            new_callable=mock.PropertyMock,
            side_effect=RuntimeError('boom'),
        ):
            send_daily_digest()
        messages = flagged_messages(mock_logger)
        self.assertEqual(len(messages), 1)
        self.assertIn('Broken schedule task', messages[0])
        # The exception is surfaced, not swallowed silently.
        error_calls = [c.args[0] for c in mock_logger.error.call_args_list]
        self.assertTrue(any('Broken schedule task' in m for m in error_calls))

    @mock.patch('src.tasks.notifications.logger')
    def test_a_schedule_that_raises_and_is_within_the_flat_window_is_not_flagged(self, mock_logger):
        PeriodicTask.objects.create(
            name='Broken schedule task recent', task='tasks.check_weak_passwords',
            interval=self.interval, enabled=True,
            last_run_at=timezone.now() - datetime.timedelta(hours=1),
        )
        with mock.patch(
            'django_celery_beat.models.IntervalSchedule.schedule',
            new_callable=mock.PropertyMock,
            side_effect=RuntimeError('boom'),
        ):
            send_daily_digest()
        self.assertEqual(flagged_messages(mock_logger), [])


class RealProductionScheduleShapeTests(TestCase):
    """Mirrors the exact schedule shape that triggered the false positive:
    day_of_month=1, using real calendar math rather than a stand-in
    interval, so the fix is proven against the actual crontab type in
    setup_celery_schedules.py and not just against IntervalSchedule."""

    def _most_recent_first_of_month(self, hour, minute):
        now = timezone.now()
        candidate = now.replace(day=1, hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > now:
            # We're on the 1st, before this month's scheduled time — the
            # most recently completed run was last month's.
            last_day_of_prev_month = candidate - datetime.timedelta(days=1)
            candidate = last_day_of_prev_month.replace(hour=hour, minute=minute)
        return candidate

    def setUp(self):
        self.crontab, _ = CrontabSchedule.objects.get_or_create(
            minute='20', hour='9', day_of_week='*', day_of_month='1', month_of_year='*')

    @mock.patch('src.tasks.notifications.logger')
    def test_a_monthly_task_last_run_on_the_1st_is_not_flagged_days_later(self, mock_logger):
        PeriodicTask.objects.create(
            name='Prune API access logs (90 days)', task='tasks.cleanup_api_access_logs',
            crontab=self.crontab, enabled=True,
            last_run_at=self._most_recent_first_of_month(9, 20),
        )
        send_daily_digest()
        self.assertEqual(
            flagged_messages(mock_logger), [],
            'A monthly task that ran on the 1st must not be flagged as "Beat '
            'may be down" on every later day of the month.',
        )

    @mock.patch('src.tasks.notifications.logger')
    def test_a_monthly_task_that_missed_last_months_run_is_flagged(self, mock_logger):
        # Skip back one extra month from the most recent 1st — that run
        # should have happened and didn't.
        missed = self._most_recent_first_of_month(9, 20)
        prior_month_end = missed.replace(day=1) - datetime.timedelta(days=1)
        missed_last_month = prior_month_end.replace(day=1, hour=9, minute=20)
        PeriodicTask.objects.create(
            name='Prune API access logs (90 days)', task='tasks.cleanup_api_access_logs',
            crontab=self.crontab, enabled=True,
            last_run_at=missed_last_month,
        )
        send_daily_digest()
        messages = flagged_messages(mock_logger)
        self.assertEqual(len(messages), 1)
        self.assertIn('Prune API access logs (90 days)', messages[0])
