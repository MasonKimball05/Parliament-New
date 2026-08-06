"""
Vote and announcement scheduling tasks.
Vote auto-open/close runs every minute; announcement dispatch runs every 5 minutes.
"""
from celery import shared_task
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
import logging
from src.models.users import member_defer

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
    from src.models import Legislation, Vote, ParliamentUser, ActivityLog
    from src.notification_service import notify_users
    from src.utils.vote_events import broadcast_vote_event
    now = timezone.now()
    closed = 0

    # v3.14.0: the old "auto-open" branch filtered available_at__isnull=True,
    # but available_at is a required field — it could never match (dead code,
    # removed). Opening is query-time: legislation appears the moment
    # available_at / voting_starts_at pass, and the vote page's poller +
    # websocket events surface it live.

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
            # Single GROUP BY instead of one COUNT query per choice
            # (was 3 + N_options queries per closing bill — 07-16 review nit).
            choice_counts = {
                row['vote_choice']: row['n']
                for row in votes.values('vote_choice').annotate(n=Count('id'))
            }
            yes = choice_counts.get('yes', 0)
            no = choice_counts.get('no', 0)
            countable = yes + no
            # v3.14.0: plurality ballots aren't yes/no, so the old
            # `total = yes + no` was always 0 for plurality — auto-closed
            # plurality votes never got a result at all.
            total_ballots = sum(choice_counts.values())

            leg.voting_closed = True
            leg.voting_ended_at = leg.voting_ends_at

            if total_ballots > 0:
                if leg.vote_mode == 'piecewise':
                    leg.passed = yes >= (leg.required_number or 0)
                elif leg.vote_mode == 'plurality':
                    # Tie handling matches end_vote: passes only with a
                    # single clear winner (v3.14.0 — a tie used to pass here)
                    options = {opt: choice_counts.get(opt, 0)
                               for opt in (leg.plurality_options or [])}
                    if options:
                        max_count = max(options.values())
                        tied = [o for o, c in options.items() if c == max_count]
                        leg.passed = max_count > 0 and len(tied) == 1
                    else:
                        leg.passed = False
                else:
                    yes_pct = (yes / countable) * 100 if countable else 0
                    leg.passed = yes_pct >= int(leg.required_percentage)

                leg.status = 'passed' if leg.passed else 'failed'

            leg.save(update_fields=['voting_closed', 'voting_ended_at', 'passed', 'status'])
            closed += 1
            result = 'passed' if leg.passed else ('no result — no votes cast' if total_ballots == 0 else 'failed')
            logger.info(f"[tasks] Auto-closed voting on '{leg.title}' (id={leg.id}) — {result}")

        # Parity with end_vote (v3.14.0): audit trail + voter notifications.
        # Outside the atomic block — a notification failure must not roll
        # back the close.
        try:
            ActivityLog.log_activity(
                action_type='vote_ended',
                user=None,
                description=(
                    f'Voting on "{leg.title}" auto-closed at its deadline — '
                    f'{"Passed" if leg.passed else "Did Not Pass"}'
                ),
                object_type='Legislation',
                object_id=leg.id,
                object_repr=leg.title,
                metadata={'auto_closed': True, 'result': result,
                          'vote_mode': leg.vote_mode, 'total_ballots': total_ballots},
            )
            voter_users = ParliamentUser.objects.filter(
                pk__in=votes.values_list('user', flat=True))
            notify_users(
                voter_users,
                'vote_ended',
                f'Vote Ended: {leg.title} — {"Passed" if leg.passed else "Did Not Pass"}',
                link=f'/legislation/detail/{leg.pk}/',
                source_type='Legislation',
                source_id=leg.id,
            )
        except Exception as exc:
            logger.error(f"[tasks] auto-close post-processing failed for leg {leg.id}: {exc}")
        broadcast_vote_event('closed', leg.id)

    if closed:
        logger.info(f"[tasks] auto_open_close_chapter_votes: closed={closed}")


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


@shared_task(name='tasks.notify_expired_vote_receipts')
def notify_expired_vote_receipts():
    """
    v3.14.0 — vote receipts are verifiable for RECEIPT_MAX_AGE_DAYS (~3
    months). Receipts are stateless (regenerated on demand from the ballot
    rows), so "purging" means the Personal tab stops issuing tokens and
    verification refuses old ones — both keyed on the ballot's cast time.
    This daily sweep is the user-facing notice: it notifies members whose
    ballots crossed the expiry line within the last day (the 1-day window
    keeps the task idempotent without storing notification state).
    """
    from src.models import Vote
    from src.notification_service import notify_users
    from src.utils.vote_receipts import RECEIPT_MAX_AGE_DAYS

    now = timezone.now()
    upper = now - timezone.timedelta(days=RECEIPT_MAX_AGE_DAYS)
    lower = upper - timezone.timedelta(days=1)
    crossed = (Vote.objects
               .filter(cast_at__gte=lower, cast_at__lt=upper)
               .select_related('user', 'legislation').defer(*member_defer('user')))

    per_user = {}
    for v in crossed:
        per_user.setdefault(v.user, set()).add(v.legislation.title)

    for user, titles in per_user.items():
        shown = sorted(titles)[:5]
        more = len(titles) - len(shown)
        notify_users(
            [user],
            'system',
            'Vote receipt(s) expired',
            'Receipts for your past vote(s) on {} reached the 3-month limit '
            'and are no longer verifiable.'.format(
                ', '.join(f'"{t}"' for t in shown) + (f' and {more} more' if more > 0 else '')),
            # v3.17.6: was '/passed_legislation/...'. The path was renamed to
            # hyphens in 50ac888 and this string was not, so every receipt-
            # expiry notification carried a dead link.
            link='/passed-legislation/?status=personal',
        )
    if per_user:
        logger.info(f"[tasks] notify_expired_vote_receipts: notified {len(per_user)} member(s)")


# ---------------------------------------------------------------------------
# v3.19.0 — announce legislation when it becomes AVAILABLE, not when it is saved
# ---------------------------------------------------------------------------
#
# WHY: `upload_legislation._notify()` fired the moment the row was saved. For a
# bill dated three weeks out that pushed "New Legislation: …" to every active
# member for something none of them could open, and by the time it *was*
# openable the notification was three weeks stale. Drafts (v3.19.0) make future
# dating the normal case rather than the exception, so the timing had to move.
#
# THE IDEMPOTENCY RULE, and it is the whole design: a row is announced exactly
# once, and the record of that is `Legislation.availability_notified_at`. The
# task claims rows by stamping them inside the same transaction that sends, so
# a beat tick that overlaps the previous one, a worker that dies mid-loop, or a
# manual re-run cannot double-notify. NULL means "not yet announced" — which is
# why migration 0014 backfills every pre-existing row. Without that backfill the
# first tick after deploy would announce the entire historical table.

def announce_legislation_availability(legislation):
    """
    Notify the chapter that one bill is now available, exactly once.

    Returns True if this call sent the notification, False if it was already
    announced. Safe to call directly (the publish view does, for a bill that is
    already available) and from the periodic task below.

    The stamp is written with a CONDITIONAL update rather than a read-then-write:
    `filter(pk=…, availability_notified_at__isnull=True).update(...)` returns the
    number of rows it changed, so two workers racing on the same bill produce one
    winner and one no-op. A `select_for_update()` would also work and is what the
    auto-close loop above uses, but that loop has real work to do inside the
    transaction; here the update IS the claim, so the cheaper form is correct.
    """
    from src.models import Legislation
    from src.notification_service import notify_all_active_members

    claimed = Legislation.objects.filter(
        pk=legislation.pk,
        availability_notified_at__isnull=True,
    ).update(availability_notified_at=timezone.now())

    if not claimed:
        return False

    try:
        notify_all_active_members(
            'legislation_new',
            'New {}: {}'.format(
                'Appointment Vote' if legislation.legislation_type == 'appointment'
                else 'Legislation',
                legislation.title,
            ),
            link='/vote/',
            source_type='Legislation',
            source_id=legislation.id,
        )
    except Exception as e:
        # ⚠️ The stamp is NOT rolled back on a send failure, deliberately.
        #
        # Rolling it back means the next tick retries — and `notify_all_active_
        # members` is not atomic: it creates one Notification row per member, so
        # a failure partway through has already notified some of them. Retrying
        # would notify those members a second time. An unsent announcement is a
        # bill nobody was told about, which is visible on the vote page anyway;
        # a double announcement is a bug members see and report.
        #
        # The log line is the recovery path: it names the bill, and re-announcing
        # is a one-liner in the shell if it matters.
        logger.error(
            '[tasks] announce_legislation_availability: notification failed for '
            'legislation id=%s ("%s") — it is stamped as announced and will NOT '
            'be retried. Re-send manually if needed. %s',
            legislation.pk, legislation.title, e, exc_info=True,
        )
    return True


@shared_task(name='tasks.notify_available_legislation')
def notify_available_legislation():
    """
    Announce every bill whose `available_at` has passed and that has not been
    announced yet. Runs every minute alongside the auto-open/close tasks.
    """
    from src.models import Legislation

    now = timezone.now()

    # ⚠️ THE LOOKBACK WINDOW IS A SAFETY NET, NOT AN OPTIMISATION.
    #
    # Migration 0014 backfills the historical table, so in a correct deploy this
    # filter changes nothing. It exists for the deploy that is NOT correct: if
    # the migration is skipped, or a dump is restored from before it, or someone
    # bulk-inserts bills with `queryset.update()` (which no signal sees), the
    # unbounded version of this query would announce years of legislation to
    # every member within one minute and there would be no undoing it.
    #
    # Seven days is long enough that a worker outage over a weekend still sends,
    # and short enough that a backfill accident stays small. A bill older than
    # this that was genuinely never announced is a manual `announce_...()` call.
    cutoff = now - timezone.timedelta(days=7)

    pending = Legislation.objects.filter(
        available_at__lte=now,
        available_at__gte=cutoff,
        availability_notified_at__isnull=True,
        is_active=True,
    ).exclude(status='removed')

    sent = 0
    for legislation in pending:
        try:
            if announce_legislation_availability(legislation):
                sent += 1
        except Exception as e:
            # One bad row must not stop the rest of the batch.
            logger.error(
                '[tasks] notify_available_legislation: failed on id=%s: %s',
                legislation.pk, e, exc_info=True,
            )

    if sent:
        logger.info('[tasks] notify_available_legislation: announced %s bill(s)', sent)
    return sent
