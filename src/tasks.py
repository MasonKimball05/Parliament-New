"""
Celery tasks for Parliament.

All tasks are fire-and-forget unless noted. Periodic tasks are scheduled via
django-celery-beat and stored in the database (manageable from admin-v2).

Task groups:
  Email          — async wrappers around notifications.py send functions
  Vote           — scheduled open/close for chapter + committee legislation
  Announcements  — scheduled publish + email dispatch
  Housekeeping   — session cleanup, digest emails, log maintenance
"""
from celery import shared_task
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# EMAIL TASKS
# Thin async wrappers so email sends never block a gunicorn worker thread.
# =============================================================================

@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='tasks.send_announcement_email')
def send_announcement_email(self, announcement_id, initiated_by_id=None):
    """
    Send announcement notification emails asynchronously.
    Called from manage_announcements.py instead of calling send_announcement_notification() directly.
    """
    try:
        from src.models import Announcement, ParliamentUser
        from src.notifications import send_announcement_notification
        announcement = Announcement.objects.get(pk=announcement_id)
        initiated_by = ParliamentUser.objects.filter(pk=initiated_by_id).first() if initiated_by_id else None
        send_announcement_notification(announcement, initiated_by=initiated_by)
    except Announcement.DoesNotExist:
        logger.warning(f"[tasks] Announcement {announcement_id} no longer exists — skipping email")
    except Exception as exc:
        logger.error(f"[tasks] send_announcement_email failed for id={announcement_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='tasks.send_security_alert_task')
def send_security_alert_task(self, event_type, severity, details, ip_address=None, user_id=None, force_send=False):
    """
    Send a security alert email asynchronously.
    Replaces direct calls to security_notifications.send_security_alert() in hot paths
    (middleware, login view) so attacks don't add email latency to the blocked request.
    """
    try:
        from src.security_notifications import send_security_alert
        from src.models import ParliamentUser
        user = ParliamentUser.objects.filter(pk=user_id).first() if user_id else None
        send_security_alert(
            event_type=event_type,
            severity=severity,
            details=details,
            ip_address=ip_address,
            user=user,
            force_send=force_send,
        )
    except Exception as exc:
        logger.error(f"[tasks] send_security_alert_task failed ({event_type}): {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=120, name='tasks.send_pledge_welcome_task')
def send_pledge_welcome_task(self, user_id, temp_password):
    """Send pledge welcome email asynchronously after account creation."""
    try:
        from src.models import ParliamentUser
        from src.notifications import send_pledge_welcome_email
        user = ParliamentUser.objects.get(pk=user_id)
        send_pledge_welcome_email(user, temp_password)
    except Exception as exc:
        logger.error(f"[tasks] send_pledge_welcome_task failed for user_id={user_id}: {exc}")
        raise self.retry(exc=exc)


# =============================================================================
# VOTE TASKS
# Scheduled via Beat to run every minute and handle auto-open/close of votes
# that have passed their voting_starts_at / voting_ends_at timestamps.
# This replaces the current on-page-load approach in vote_view.py and
# committee/vote.py, so votes open and close on schedule even if no one loads
# the page.
# =============================================================================

@shared_task(name='tasks.auto_open_close_chapter_votes')
def auto_open_close_chapter_votes():
    """
    Open and close chapter legislation votes on schedule.

    - Opens legislation where voting_starts_at has passed but vote is not yet open
    - Closes legislation where voting_ends_at has passed and vote is still open
    """
    from src.models import Legislation, Vote
    now = timezone.now()
    opened = 0
    closed = 0

    # Auto-open: legislation with a voting_starts_at in the past that isn't open yet
    to_open = Legislation.objects.filter(
        voting_closed=False,
        voting_starts_at__isnull=False,
        voting_starts_at__lte=now,
        available_at__isnull=True,  # Not yet "available" (the field used as visible-from)
    ).exclude(status='removed').exclude(status='tabled')

    for leg in to_open:
        if not leg.available_at:
            leg.available_at = leg.voting_starts_at
            leg.save(update_fields=['available_at'])
            opened += 1
            logger.info(f"[tasks] Auto-opened voting on '{leg.title}' (id={leg.id})")

    # Auto-close: legislation past voting_ends_at that is still open
    to_close = Legislation.objects.filter(
        voting_closed=False,
        voting_ends_at__isnull=False,
        voting_ends_at__lte=now,
    )

    for leg in to_close:
        votes = Vote.objects.filter(legislation=leg)
        yes = votes.filter(vote_choice='yes').count()
        no = votes.filter(vote_choice='no').count()
        total = yes + no

        leg.voting_closed = True
        leg.voting_ended_at = leg.voting_ends_at

        if total > 0:
            if leg.vote_mode == 'piecewise':
                leg.passed = yes >= (leg.required_number or 0)
            elif leg.vote_mode == 'plurality':
                options = {opt: votes.filter(vote_choice=opt).count() for opt in (leg.plurality_options or [])}
                leg.passed = max(options.values()) > 0 if options else False
            else:
                yes_pct = (yes / total) * 100
                leg.passed = yes_pct >= int(leg.required_percentage)

            leg.status = 'passed' if leg.passed else 'failed'

        leg.save()
        closed += 1
        result = 'passed' if leg.passed else ('no result — no votes cast' if total == 0 else 'failed')
        logger.info(f"[tasks] Auto-closed voting on '{leg.title}' (id={leg.id}) — {result}")

    if opened or closed:
        logger.info(f"[tasks] auto_open_close_chapter_votes: opened={opened}, closed={closed}")


@shared_task(name='tasks.auto_open_close_committee_votes')
def auto_open_close_committee_votes():
    """
    Close committee legislation votes on schedule (committee votes open manually).
    Mirrors the on-page-load auto-close in committee/vote.py but runs on a timer.
    """
    from src.models import CommitteeLegislation
    from src.view.committee.vote import get_vote_tally
    now = timezone.now()
    closed = 0

    to_close = CommitteeLegislation.objects.filter(
        voting_closed=False,
        voting_ends_at__isnull=False,
        voting_ends_at__lte=now,
    )

    for leg in to_close:
        tally = get_vote_tally(leg)
        total_votes = tally['total']

        leg.voting_closed = True
        leg.voting_ended_at = leg.voting_ends_at

        if total_votes > 0:
            if leg.vote_mode == 'plurality':
                options = {k: v for k, v in tally.items() if k != 'total'}
                leg.passed = max(options.values()) > 0 if options else False
                leg.status = 'passed' if leg.passed else 'draft'
            elif leg.vote_mode == 'piecewise':
                leg.passed = tally.get('yes', 0) >= (leg.required_number or 0)
                leg.status = 'passed' if leg.passed else 'draft'
            else:
                yes = tally.get('yes', 0)
                no = tally.get('no', 0)
                countable = yes + no
                if countable > 0:
                    yes_pct = (yes / countable) * 100
                    leg.passed = yes_pct >= int(leg.required_percentage)
                    leg.status = 'passed' if leg.passed else 'draft'

        leg.save()
        closed += 1
        logger.info(f"[tasks] Auto-closed committee vote on '{leg.title}' (id={leg.id})")

    if closed:
        logger.info(f"[tasks] auto_open_close_committee_votes: closed={closed}")


@shared_task(name='tasks.auto_open_close_slating_votes')
def auto_open_close_slating_votes():
    """
    Open and close slating period voting on schedule.
    SlatingPeriod has voting_open_at and voting_close_at fields.
    """
    from src.models import SlatingPeriod
    now = timezone.now()
    opened = 0
    closed = 0

    # Auto-open: voting_open_at has passed, status is still deliberation
    to_open = SlatingPeriod.objects.filter(
        status='deliberation',
        voting_open_at__isnull=False,
        voting_open_at__lte=now,
    )
    for period in to_open:
        period.status = 'voting_open'
        period.save(update_fields=['status'])
        opened += 1
        logger.info(f"[tasks] Auto-opened slating voting for period id={period.id}")

    # Auto-close: voting_close_at has passed, status is still voting_open
    to_close = SlatingPeriod.objects.filter(
        status='voting_open',
        voting_close_at__isnull=False,
        voting_close_at__lte=now,
    )
    for period in to_close:
        period.status = 'voting_closed'
        period.save(update_fields=['status'])
        closed += 1
        logger.info(f"[tasks] Auto-closed slating voting for period id={period.id}")

    if opened or closed:
        logger.info(f"[tasks] auto_open_close_slating_votes: opened={opened}, closed={closed}")


# =============================================================================
# ANNOUNCEMENT TASKS
# =============================================================================

@shared_task(name='tasks.publish_scheduled_announcements')
def publish_scheduled_announcements():
    """
    Dispatch email notifications for announcements whose publish_at time has
    arrived but whose send_email_on_publish flag is still set (email not yet sent).

    Replaces the cron-based process_scheduled_announcements management command.
    Uses the same email_sent_at / send_email_on_publish fields to avoid duplicates.
    """
    from src.models import Announcement
    from django.db import transaction
    now = timezone.now()

    pending = Announcement.objects.filter(
        is_active=True,
        send_email_on_publish=True,
        email_sent_at__isnull=True,
        publish_at__isnull=False,
        publish_at__lte=now,
    )

    for announcement in pending:
        # Claim the row atomically before queuing — prevents duplicate sends
        # if Beat fires the task twice in quick succession
        with transaction.atomic():
            claimed = Announcement.objects.select_for_update(skip_locked=True).filter(
                pk=announcement.pk,
                send_email_on_publish=True,
                email_sent_at__isnull=True,
            ).first()
            if not claimed:
                continue
            claimed.email_sent_at = now
            claimed.send_email_on_publish = False
            claimed.save(update_fields=['email_sent_at', 'send_email_on_publish'])

        send_announcement_email.delay(announcement.pk)
        logger.info(f"[tasks] Queued email for scheduled announcement id={announcement.pk} '{announcement.title}'")


# =============================================================================
# HOUSEKEEPING TASKS
# =============================================================================

@shared_task(name='tasks.cleanup_expired_sessions')
def cleanup_expired_sessions():
    """
    Remove expired UserSession records. Django's session engine handles its own
    expiry; this task cleans Parliament's UserSession tracking table.
    """
    try:
        from src.models import UserSession
        cutoff = timezone.now() - timezone.timedelta(days=30)
        deleted, _ = UserSession.objects.filter(last_activity__lt=cutoff).delete()
        if deleted:
            logger.info(f"[tasks] cleanup_expired_sessions: removed {deleted} stale UserSession records")
    except Exception as exc:
        logger.error(f"[tasks] cleanup_expired_sessions failed: {exc}")


@shared_task(name='tasks.send_daily_honeypot_digest')
def send_daily_honeypot_digest():
    """
    Send the daily honeypot activity digest email.
    Wraps security_notifications.send_honeypot_digest().
    """
    try:
        from src.security_notifications import send_honeypot_digest
        since = timezone.now() - timezone.timedelta(hours=24)
        send_honeypot_digest(since=since)
    except Exception as exc:
        logger.error(f"[tasks] send_daily_honeypot_digest failed: {exc}")


# ---------------------------------------------------------------------------
# Push Notifications
# ---------------------------------------------------------------------------

@shared_task(name='tasks.send_push_notification', bind=True, max_retries=2, default_retry_delay=30)
def send_push_notification(self, user_id, title, body, url='/home/', tag='parliament'):
    """
    Send a Web Push notification to all active subscriptions for a user.

    Payload shape matches service-worker.js:
      { title, body, url, tag }

    Subscriptions that return 410 Gone are automatically deleted (expired).
    Skips gracefully if VAPID keys are not configured.
    """
    from django.conf import settings
    from src.models import PushSubscription

    vapid_private = getattr(settings, 'VAPID_PRIVATE_KEY', None)
    vapid_claims = getattr(settings, 'VAPID_CLAIMS', None)
    if not vapid_private or not vapid_claims:
        logger.debug('[push] VAPID keys not configured — skipping push')
        return

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.error('[push] pywebpush not installed')
        return

    subscriptions = PushSubscription.objects.filter(user_id=user_id)
    if not subscriptions.exists():
        return

    import json as _json
    payload = _json.dumps({'title': title, 'body': body, 'url': url, 'tag': tag})
    expired_ids = []

    for sub in subscriptions:
        try:
            webpush(
                subscription_info=sub.as_subscription_info(),
                data=payload,
                vapid_private_key=vapid_private,
                vapid_claims=vapid_claims,
            )
            PushSubscription.objects.filter(pk=sub.pk).update(last_used_at=timezone.now())
        except WebPushException as exc:
            response = getattr(exc, 'response', None)
            status = response.status_code if response is not None else None
            if status == 410:
                # Subscription has expired — remove it
                expired_ids.append(sub.pk)
                logger.info(f'[push] subscription expired for user {user_id}, removing')
            else:
                logger.warning(f'[push] WebPushException for user {user_id}: {exc} (status={status})')
        except Exception as exc:
            logger.error(f'[push] unexpected error for user {user_id}: {exc}')

    if expired_ids:
        PushSubscription.objects.filter(pk__in=expired_ids).delete()
