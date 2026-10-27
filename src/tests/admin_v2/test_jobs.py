"""v3.58.1: overdue scheduled jobs use each task's own schedule."""
from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, IntervalSchedule, PeriodicTask

from src.admin_v2_attention import attention_items
from src.admin_v2_jobs import overdue_tasks, task_status
from src.tests.admin_v2.base import AdminV2TestCase


def interval_task(name, minutes, last_run_ago):
    every = IntervalSchedule.objects.create(every=minutes, period=IntervalSchedule.MINUTES)
    return PeriodicTask.objects.create(name=name, task='x.y', interval=every,
                                       last_run_at=timezone.now() - last_run_ago)


class JobStatusTests(AdminV2TestCase):

    def test_interval_task_on_time_and_overdue(self):
        ok = interval_task('every 15 ok', 15, timedelta(minutes=10))
        late = interval_task('every 15 late', 15, timedelta(hours=2))
        self.assertEqual(task_status(ok)[0], 'ok')
        self.assertEqual(task_status(late)[0], 'overdue')

    def test_weekly_crontab_is_not_stale_after_a_day(self):
        # The old rule called every crontab stale after 25 hours.
        # Last run 3 days ago, on the schedule's own weekday and time, so the
        # next one is 4 days away.
        last = timezone.localtime(timezone.now() - timedelta(days=3)).replace(second=0, microsecond=0)
        weekly = CrontabSchedule.objects.create(
            minute=str(last.minute), hour=str(last.hour), day_of_week=str((last.weekday() + 1) % 7),
            day_of_month='*', month_of_year='*', timezone=str(timezone.get_current_timezone()))
        t = PeriodicTask.objects.create(name='weekly', task='x.y', crontab=weekly, last_run_at=last)
        self.assertEqual(task_status(t)[0], 'ok')
        t.last_run_at = timezone.now() - timedelta(days=20)
        self.assertEqual(task_status(t)[0], 'overdue')

    def test_disabled_tasks_are_not_overdue(self):
        t = interval_task('off', 15, timedelta(days=3))
        t.enabled = False
        t.save()
        self.assertEqual(task_status(t)[0], 'disabled')
        self.assertEqual(overdue_tasks(), [])

    def test_overdue_jobs_and_failed_drill_reach_needs_attention(self):
        from src.models import SecurityNotificationLog
        interval_task('late', 15, timedelta(hours=5))
        SecurityNotificationLog.objects.create(event_type='BACKUP_VERIFY_FAILED', severity='critical', details='x')
        labels = [i['label'] for i in attention_items()]
        self.assertIn('scheduled job(s) overdue', labels)
        self.assertEqual(labels[0], 'backup restore drill failed')

    def test_page_shows_overdue(self):
        interval_task('late job', 15, timedelta(hours=5))
        r = self.client.get(reverse('admin_v2_celery_health'))
        self.assertContains(r, 'Overdue')
