"""
Vote and announcement scheduling tasks.
Vote auto-open/close runs every minute; announcement dispatch runs every 5 minutes.
"""
from celery import shared_task
from django.db import transaction
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


@shared_task(name='tasks.auto_open_close_chapter_votes')
def auto_open_close_chapter_votes():
    """
    Open and close chapter legislation votes on schedule.

    - Opens legislation where voting_starts_at has passed but vote is not yet open
    - Closes legislation where voting_ends_at has passed and vote is still open

    Each close is wrapped in transaction.atomic() with select_for_update() so a
    mid-loop crash cannot leave a bill with voting_closed=True but without the
    passed/status fields set correctly.
    """
    from src.models import Legislation, Vote
    now = timezone.now()
    opened = 0
    closed = 0

    to_open = Legislation.objects.filter(
        voting_closed=False,
        voting_starts_at__isnull=False,
        voting_starts_at__lte=now,
        available_at__isnull=True,
    ).exclude(status='removed').exclude(status='tabled')

    for leg in to_open:
        if not leg.available_at:
            leg.available_at = leg.voting_starts_at
            leg.save(update_fields=['available_at'])
            opened += 1
            logger.info(f"[tasks] Auto-opened voting on '{leg.title}' (id={leg.id})")

    to_close = Legislation.objects.filter(
        voting_closed=False,
        voting_ends_at__isnull=False,
        voting_ends_at__lte=now,
    )

    for leg in to_close:
        with transaction.atomic():
            leg = Legislation.objects.select_for_update().get(pk=leg.pk)
            if leg.voting_closed:
                continue  # Another process beat us to it

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

            leg.save(update_fields=['voting_closed', 'voting_ended_at', 'passed', 'status'])
            closed += 1
            result = 'passed' if leg.passed else ('no result — no votes cast' if total == 0 else 'failed')
            logger.info(f"[tasks] Auto-closed voting on '{leg.title}' (id={leg.id}) — {result}")

    if opened or closed:
        logger.info(f"[tasks] auto_open_close_chapter_votes: opened={opened}, closed={closed}")


@shared_task(name='tasks.auto_open_close_committee_votes')
def auto_open_close_committee_votes():
    """
    Close committee legislation votes on schedule (committee votes open manually).
    State transitions are wrapped in transaction.atomic() with select_for_update()
    so a mid-loop crash cannot leave a bill partially closed.
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
        with transaction.atomic():
            leg = CommitteeLegislation.objects.select_for_update().get(pk=leg.pk)
            if leg.voting_closed:
                continue

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

            leg.save(update_fields=['voting_closed', 'voting_ended_at', 'passed', 'status'])
            closed += 1
            logger.info(f"[tasks] Auto-closed committee vote on '{leg.title}' (id={leg.id})")

    if closed:
        logger.info(f"[tasks] auto_open_close_committee_votes: closed={closed}")


@shared_task(name='tasks.auto_open_close_slating_votes')
def auto_open_close_slating_votes():
    """Open and close slating period voting on schedule."""
    from src.models import SlatingPeriod
    now = timezone.now()
    opened = 0
    closed = 0

    for period in SlatingPeriod.objects.filter(status='deliberation', voting_open_at__isnull=False, voting_open_at__lte=now):
        period.status = 'voting_open'
        period.save(update_fields=['status'])
        opened += 1
        logger.info(f"[tasks] Auto-opened slating voting for period id={period.id}")

    for period in SlatingPeriod.objects.filter(status='voting_open', voting_close_at__isnull=False, voting_close_at__lte=now):
        period.status = 'voting_closed'
        period.save(update_fields=['status'])
        closed += 1
        logger.info(f"[tasks] Auto-closed slating voting for period id={period.id}")

    if opened or closed:
        logger.info(f"[tasks] auto_open_close_slating_votes: opened={opened}, closed={closed}")


@shared_task(name='tasks.publish_scheduled_announcements')
def publish_scheduled_announcements():
    """
    Dispatch email notifications for announcements whose publish_at time has
    arrived but whose send_email_on_publish flag is still set (email not yet sent).
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

        # Import via the package so mocks on src.tasks.send_announcement_email work in tests
        import src.tasks as _tasks
        _tasks.send_announcement_email.delay(announcement.pk)
        logger.info(f"[tasks] Queued email for scheduled announcement id={announcement.pk} '{announcement.title}'")
