"""
Is a scheduled job overdue? (v4.1.0)

Shared by the Scheduled jobs (Celery) page and the dashboard's "Needs
attention". Before, the page treated every crontab task as daily ("stale after
25 hours"), so a weekly task always looked broken and a 15-minute crontab had
to be a day late before anyone saw it. Now each task's own schedule says when
it was next due after its last run; it is overdue once that moment plus a grace
period has passed.

Beat records when it *sent* a task (last_run_at), not whether a worker finished
it, so "overdue" means "beat stopped sending it" (beat down, task disabled by
accident, schedule broken). Worker-side failures are in the server log.
"""
from datetime import timedelta

from django.utils import timezone

GRACE_MIN = timedelta(minutes=10)


def _next_due(task, now):
    """When `task` was next due after its last run (None if it can't be told)."""
    try:
        schedule = task.schedule  # celery schedule / crontab built from the row
    except Exception:
        return None
    last = task.last_run_at
    if last is None:
        return None
    try:
        # Celery measures this from *now*: time left until the first run due
        # after `last` (negative once that moment has passed).
        remaining = schedule.remaining_estimate(last)
    except Exception:
        return None
    return now + remaining


def task_status(task, now=None):
    """('ok' | 'overdue' | 'never' | 'disabled' | 'unknown', next_due or None)."""
    now = now or timezone.now()
    if not task.enabled:
        return 'disabled', None
    if task.last_run_at is None:
        # One-off tasks and brand-new rows haven't run yet; only call a task
        # "never run" once it has existed for a day.
        created = getattr(task, 'date_changed', None)
        if created and now - created < timedelta(days=1):
            return 'unknown', None
        return 'never', None
    due = _next_due(task, now)
    if due is None:
        return 'unknown', None
    period = max(due - task.last_run_at, timedelta(0))
    grace = max(GRACE_MIN, period * 0.25)
    return ('overdue' if now > due + grace else 'ok'), due


def overdue_tasks(now=None):
    from django_celery_beat.models import PeriodicTask

    now = now or timezone.now()
    tasks = PeriodicTask.objects.filter(enabled=True, one_off=False).select_related(
        'interval', 'crontab', 'solar', 'clocked')
    return [t for t in tasks if task_status(t, now)[0] in ('overdue', 'never')]
