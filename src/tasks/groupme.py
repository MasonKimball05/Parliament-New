"""
v3.49.0 — Celery tasks for the GroupMe bot. The logic is in src/groupme.py;
these are the thin async wrappers plus the sweep.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger('src')

#: Transient GroupMe failures (network, 5xx, 429) retry at 1, 2 and 4 minutes,
#: then the message stays 'failed' with a Retry button on the dashboard.
MAX_RETRIES = 3


@shared_task(name='tasks.dispatch_announcement_to_groupme')
def dispatch_announcement_to_groupme(announcement_id):
    from src.groupme import dispatch_announcement, queue_send
    queue_send(dispatch_announcement(announcement_id))


@shared_task(bind=True, name='tasks.send_groupme_message', max_retries=MAX_RETRIES)
def send_groupme_message(self, message_id):
    from src.groupme import GroupMeError, deliver
    try:
        deliver(message_id)
    except GroupMeError as e:
        if self.request.retries < MAX_RETRIES:
            raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
        logger.warning(f'[groupme] message {message_id} failed after retries: {e}')


def sweep_groupme():
    """
    Run from `publish_scheduled_announcements` (every 5 minutes), so a scheduled
    announcement is echoed within 5 minutes of going live, and anything the
    create-time path couldn't queue (broker down) still goes out.

      1. dispatch every due announcement (published, channels ticked, unclaimed)
         and every event whose GroupMe reminder time has come (v3.50.0);
      2. re-queue messages that never got a first attempt (queued > 2 min ago).
    """
    from src.groupme import due_announcements_queryset, dispatch_announcement, queue_send
    from src.models import GroupMeMessage

    for pk in list(due_announcements_queryset().values_list('pk', flat=True)[:50]):
        queue_send(dispatch_announcement(pk))

    # v3.50.0 — event reminders, same once-only claim.
    from src.groupme import dispatch_event_reminder, due_event_reminders
    for pk in due_event_reminders()[:50]:
        queue_send(dispatch_event_reminder(pk))

    stale = timezone.now() - timedelta(minutes=2)
    stuck = list(
        GroupMeMessage.objects.filter(status='queued', attempts=0, created_at__lt=stale)
        .values_list('pk', flat=True)[:50]
    )
    queue_send(stuck)
