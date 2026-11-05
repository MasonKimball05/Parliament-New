"""
Slating Period Setup Views

Create, edit, and manage slating periods.
"""

from .position_manager import default_slate_positions
from .tallies import open_round, slate_tally
from src.slating_rounds import election_context
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from django.urls import reverse
from src.models import (
    SlatingPeriod, SlatingActivity, Committee, ParliamentUser,
    Slate, SlatingAttendance, SlatingBallot, SlatingVote, SlatingPosition
)
from .permissions import slating_admin_required, slating_chair_required
from src.models.users import member_defer


def check_and_auto_transition_status(period):
    """
    Follow the period's schedule (run on member page loads: the dashboard, the
    app's hub and the setup pages). Returns True if the status changed.

    11-05-26 — the schedule only does what the chair could do, the same way:

    * nominations open / close on their dates, one step at a time;
    * nominations closed → deliberation once the deliberation (or voting) date
      has passed;
    * the FIRST round opens at ``voting_open_at`` through ``open_voting_round``
      (the chair's Open: a full-slate round, notifications), and never again:
      once a round has been opened, deliberation is the chair's (a pause, or
      the next round after a close / void, is never reopened by the clock);
    * an open round closes at ``voting_close_at`` through ``close_round`` (it
      counts the round and elects), but only a round that was open at that
      time, so a later round the chair opens by hand isn't closed at once;
    * results publish at ``results_publish_at`` through ``publish_period_results``
      (notifications, Chapter Documents), unless the chair has unpublished
      them before.

    Each step is taken under the period lock and re-checked inside it, so two
    page loads at once take it once. Before this, the clock moved
    voting_open → voting_closed without counting, reopened paused and failed
    rounds (members voted twice), and published without notifying anyone.
    """
    changed = False
    for _ in range(len(SlatingPeriod.STATUS_CHOICES)):   # catch up, a step at a time
        if _scheduled_step(period, timezone.now()) is None:
            break
        from django.db import transaction
        from src.slating_rounds import RoundError
        try:
            with transaction.atomic():
                locked = SlatingPeriod.objects.select_for_update().get(pk=period.pk)
                step = _scheduled_step(locked, timezone.now())
                if step is None:
                    break
                old_status = locked.status
                _take_scheduled_step(locked, step)
                SlatingActivity.objects.create(
                    period=locked, user=None, action='status_changed',
                    details=f'Auto-transitioned from {old_status} to {locked.status} based on scheduled date')
        except RoundError:
            break   # e.g. no one marked present yet: the chair opens it by hand
        period.refresh_from_db()
        changed = True
    return changed


def _scheduled_step(period, now):
    """The schedule's next step for ``period`` now, or None (no writes)."""
    status = period.status
    if status == 'setup':
        if period.nominations_open_at and period.nominations_open_at <= now:
            return 'nominations_open'
    elif status == 'nominations_open':
        if period.nominations_close_at and period.nominations_close_at <= now:
            return 'nominations_closed'
    elif status == 'nominations_closed':
        due = [d for d in (period.deliberation_start_at, period.voting_open_at) if d]
        if due and min(due) <= now:
            return 'deliberation'
    elif status == 'deliberation':
        if (period.voting_open_at and period.voting_open_at <= now
                and not (period.voting_close_at and period.voting_close_at <= now)
                and period.current_voting_attempt == 0 and not period.rounds.exists()
                and period.slates.filter(is_approved=True, slate_type='primary').exists()):
            return 'open_first_round'
    elif status == 'voting_open':
        if period.voting_close_at and period.voting_close_at <= now and scheduled_close_applies(period):
            return 'close_round'
    elif status == 'voting_closed':
        if (period.results_publish_at and period.results_publish_at <= now
                and not was_unpublished(period)):
            return 'publish'
    return None


def scheduled_close_applies(period):
    """The scheduled close is for the round open at that time, not one opened later by hand."""
    from src.models import SlatingRound
    rnd = SlatingRound.objects.filter(period=period, attempt=period.current_voting_attempt).first()
    return rnd is None or rnd.opened_at is None or rnd.opened_at < period.voting_close_at


def was_unpublished(period):
    return SlatingActivity.objects.filter(
        period=period, action='period_status_changed',
        details__startswith='Status changed from results_published to').exists()


def _take_scheduled_step(period, step):
    """Inside the lock: the step, through the chair's own code."""
    if step == 'open_first_round':
        open_voting_round(period, None, 'deliberation', 'slate', quorum=str(period.quorum or ''))
    elif step == 'close_round':
        from src.slating_rounds import close_round
        close_round(period, None)
    elif step == 'publish':
        from src.view.slating.results import publish_period_results
        # Filed in Chapter Documents under whoever runs the election (a document needs an uploader).
        committee = period.slating_committee
        filer = period.slating_manager or (committee.admin if committee else None) or period.created_by
        publish_period_results(period, filer, save_to_documents=filer is not None)
    else:
        period.status = step
        period.save(update_fields=['status'])


# ── Shared with the app (src/mobile/views_slating_setup.py, 11-04-26) ────────

class PeriodSetupError(Exception):
    """A refusal for the chair: ``code`` for the app, the web's message, and its level."""

    def __init__(self, code, message, level='error'):
        super().__init__(message)
        self.code = code
        self.level = level


#: The schedule fields an edit sets (blank or left out clears one, as the web form does).
PERIOD_DATE_FIELDS = [
    'nominations_open_at', 'nominations_close_at',
    'deliberation_start_at', 'voting_open_at',
    'voting_close_at', 'results_publish_at'
]


def eligible_period_managers():
    """Who the Slating Manager and Add-a-member pickers offer."""
    return ParliamentUser.objects.filter(
        member_status__in=['Active', 'Inactive'],
        member_type__in=['Member', 'Chair', 'Officer']
    ).order_by('name')


def ensure_slating_committee(period):
    """Backfill: auto-create the ad hoc committee if this period predates the feature."""
    if not period.slating_committee_id:
        ad_hoc_committee = Committee.objects.create(
            name=f'Slating — {period.name} [{period.id}]',
            is_slating_committee=True,
            is_ad_hoc=True,
        )
        period.slating_committee = ad_hoc_committee
        period.save(update_fields=['slating_committee'])


def period_committee_members(period):
    """The committee's members, its explicit admin (an FK, not in the M2M) first if he isn't one."""
    committee_members = []
    if period.slating_committee:
        members_qs = list(period.slating_committee.members.all().order_by('name'))
        admin = period.slating_committee.admin
        if admin and not any(m.pk == admin.pk for m in members_qs):
            committee_members = [admin] + members_qs
        else:
            committee_members = members_qs
    return committee_members


def create_slating_period(user, data):
    """
    Create a period from the form's fields (``data.get``), in setup, with its
    ad hoc committee and the configurable values; logged. Raises
    PeriodSetupError for a missing name or term.
    """
    name = data.get('name', '').strip()
    academic_term = data.get('academic_term', '').strip()
    description = data.get('description', '').strip()

    if not name:
        raise PeriodSetupError('invalid_input', 'Period name is required.')

    if not academic_term:
        raise PeriodSetupError('invalid_input', 'Academic term is required.')

    raw_pct = str(data.get('required_approval_percentage') or '').strip()
    if raw_pct and (not raw_pct.isdigit() or not APPROVAL_RANGE[0] <= int(raw_pct) <= APPROVAL_RANGE[1]):
        raise PeriodSetupError('invalid_input', APPROVAL_INVALID)

    # 11-05-26: one transaction (a failure part-way left a period with no
    # committee), and the create form's Slating Manager and schedule are saved
    # (they were dropped).
    from django.db import transaction
    with transaction.atomic():
        return _create_slating_period(user, data, name, academic_term, description)


def _create_slating_period(user, data, name, academic_term, description):
    # Create the period
    period = SlatingPeriod.objects.create(
        name=name,
        academic_term=academic_term,
        description=description,
        created_by=user,
        status='setup'
    )
    manager_id = str(data.get('slating_manager') or '').strip()
    if manager_id:
        period.slating_manager = eligible_period_managers().filter(user_id=manager_id).first()
    _apply_period_dates(period, data)
    period.save()

    # Auto-create an invisible ad hoc committee for this period
    ad_hoc_committee = Committee.objects.create(
        name=f'Slating — {name} [{period.id}]',
        is_slating_committee=True,
        is_ad_hoc=True,
    )
    period.slating_committee = ad_hoc_committee
    period.save(update_fields=['slating_committee'])

    # Set configurable values
    try:
        period.min_gpa_requirement = float(data.get('min_gpa_requirement', 2.50))
        period.gpa_level_2_threshold = float(data.get('gpa_level_2_threshold', 0.20))
        period.required_approval_percentage = int(data.get('required_approval_percentage', 60))
        period.save(update_fields=['min_gpa_requirement', 'gpa_level_2_threshold', 'required_approval_percentage'])
    except (ValueError, TypeError):
        pass
    # v4.4.5: the create form's "Candidates can't vote on their own race" box.
    raw = data.getlist('candidates_cannot_vote') if hasattr(data, 'getlist') else [data.get('candidates_cannot_vote')]
    if any(str(v).lower() in ('on', 'true', '1', 'yes') or v is True for v in raw if v is not None):
        period.extra_settings = {**(period.extra_settings or {}), 'candidates_cannot_vote': True}
        period.save(update_fields=['extra_settings'])

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='period_created',
        details=f'Created slating period: {name}'
    )
    return period


APPROVAL_RANGE = (51, 100)
APPROVAL_INVALID = 'Required approval % must be between 51 and 100.'
APPROVAL_LOCKED = 'Required approval % can’t change once voting has started.'


def approval_locked(period):
    """11-05-26: the approval % is fixed once the first round has been opened."""
    return period.status == 'voting_open' or period.current_voting_attempt > 0 or period.rounds.exists()


def required_approval_from(period, data):
    """
    The posted Required Approval % (11-05-26): a whole number from 51 to 100,
    and unchanged once voting has started (a closed round's pass/fail was
    decided at its threshold). Raises PeriodSetupError; nothing is saved.
    """
    raw = data.get('required_approval_percentage')
    if raw is None or str(raw).strip() == '':
        return period.required_approval_percentage
    raw = str(raw).strip()
    if not raw.isdigit() or not APPROVAL_RANGE[0] <= int(raw) <= APPROVAL_RANGE[1]:
        raise PeriodSetupError('invalid_input', APPROVAL_INVALID)
    value = int(raw)
    if value != period.required_approval_percentage and approval_locked(period):
        raise PeriodSetupError('locked', APPROVAL_LOCKED)
    return value


def save_period_settings(period, user, data):
    """
    The settings form's Save: name, term, description, the slating manager
    (site admins only), the voting and GPA values (left as they were if one
    isn't a number) and the schedule (a blank or missing date clears it).
    """
    required_approval_from(period, data)   # 11-05-26: refused before anything changes
    period.name = data.get('name', period.name).strip()
    period.academic_term = data.get('academic_term', period.academic_term).strip()
    period.description = data.get('description', '').strip()

    # Update slating manager (admin only can change this)
    if user.is_admin:
        manager_id = data.get('slating_manager', '').strip()
        if manager_id:
            try:
                period.slating_manager = ParliamentUser.objects.get(user_id=manager_id)
            except ParliamentUser.DoesNotExist:
                pass
        else:
            period.slating_manager = None

    # Update configuration
    try:
        period.min_gpa_requirement = float(data.get('min_gpa_requirement', period.min_gpa_requirement))
        period.gpa_level_2_threshold = float(data.get('gpa_level_2_threshold', period.gpa_level_2_threshold))
    except (ValueError, TypeError):
        pass
    period.required_approval_percentage = required_approval_from(period, data)

    # v4.4.5: "Candidates can't vote on their own race" (src/slating_ballots.py).
    # Only when the form sends it (an app build without the switch leaves it
    # alone), and never while a vote is open, so the rules can't change mid-round.
    if 'candidates_cannot_vote' in data and period.status != 'voting_open':
        raw = data.getlist('candidates_cannot_vote') if hasattr(data, 'getlist') else [data.get('candidates_cannot_vote')]
        on = any(str(v).lower() in ('on', 'true', '1', 'yes') or v is True for v in raw)
        from src.slating_ballots import CANDIDATES_CANNOT_VOTE
        settings_ = dict(period.extra_settings or {})
        if on:
            settings_[CANDIDATES_CANNOT_VOTE] = True
        else:
            settings_.pop(CANDIDATES_CANNOT_VOTE, None)
        period.extra_settings = settings_

    _apply_period_dates(period, data)
    period.save()


def _apply_period_dates(period, data):
    """The schedule fields from the form (a blank or missing date clears it; a bad one is left as it was)."""
    for field in PERIOD_DATE_FIELDS:
        value = data.get(field)
        if value:
            try:
                from django.utils.dateparse import parse_datetime
                from django.utils import timezone as tz
                parsed = parse_datetime(value)
                if parsed:
                    if tz.is_naive(parsed):
                        parsed = tz.make_aware(parsed)
                    setattr(period, field, parsed)
            except (ValueError, TypeError):
                pass
        else:
            setattr(period, field, None)


MANAGER_HIDES_PERIOD = ('You assigned a slating manager, so this period is now confidential: as a site admin '
                        'you can’t open it unless you manage it or sit on its committee.')


def manager_hides_period(period, user):
    """True when ``user`` (a site admin) is locked out of ``period`` by its manager (11-05-26)."""
    from .permissions import can_manage_period
    return bool(period.slating_manager_id) and not can_manage_period(user, period)


def add_period_committee_member(period, member_id):
    """Add a member (by user id) to the period's slating committee. Returns him; raises PeriodSetupError."""
    member_id = (member_id or '').strip()
    if period.slating_committee and member_id:
        try:
            member = ParliamentUser.objects.get(user_id=member_id)
        except ParliamentUser.DoesNotExist:
            raise PeriodSetupError('not_found', 'Member not found.')
        # 11-05-26 (the chapter's rule): no candidate on the slating committee.
        from .permissions import applied_cant_join_message, has_live_application
        if has_live_application(member, period):
            raise PeriodSetupError('applied', applied_cant_join_message(member))
        period.slating_committee.members.add(member)
        return member
    raise PeriodSetupError('no_committee', 'No committee assigned to this period.')


def remove_period_committee_member(period, member_id):
    """Remove a member (by user id) from the committee. Returns him, or None if there was nothing to do."""
    member_id = (member_id or '').strip()
    if period.slating_committee and member_id:
        try:
            member = ParliamentUser.objects.get(user_id=member_id)
        except ParliamentUser.DoesNotExist:
            raise PeriodSetupError('not_found', 'Member not found.')
        period.slating_committee.members.remove(member)
        return member
    return None


def delete_slating_period(period, user):
    """Delete a period and everything in it (site admins only), with its ad hoc committee. Returns its name."""
    # Only site admins can delete periods
    if not user.is_admin:
        raise PeriodSetupError('forbidden', 'Only site administrators can delete slating periods.')

    period_name = period.name
    period_id = period.id

    # Delete the auto-created ad hoc committee before deleting the period
    # (SET_NULL on_delete means it survives otherwise)
    ad_hoc = period.slating_committee
    if ad_hoc and ad_hoc.is_ad_hoc:
        ad_hoc_to_delete = ad_hoc
    else:
        ad_hoc_to_delete = None

    # Delete the period (cascades to related objects including activity log)
    period.delete()

    if ad_hoc_to_delete:
        ad_hoc_to_delete.delete()

    # Log to Django's logging system since SlatingActivity is deleted with the period
    import logging
    logger = logging.getLogger('admin_actions')
    logger.info(
        f"[SLATING] User {user.name} ({user.user_id}) "
        f"deleted slating period: {period_name} (ID: {period_id})"
    )
    return period_name


#: 11-05-26: the status changes the chair may make (Change status, web and
#: app). Opening voting is from deliberation (``open_voting_round``); an open
#: round goes back to deliberation only as a pause, and is closed only by
#: Close voting (``close_round``, which counts it). Published results can be
#: unpublished (back to voting_closed) or archived; nothing reopens voting
#: after that, and archived is final.
ALLOWED_STATUS_CHANGES = {
    'setup': ('nominations_open',),
    'nominations_open': ('nominations_closed',),
    'nominations_closed': ('deliberation',),
    'deliberation': ('voting_open',),
    'voting_open': ('deliberation',),
    'voting_closed': ('results_published',),
    'results_published': ('voting_closed', 'archived'),
    'archived': (),
}
STATUS_CHANGE_REFUSALS = {
    ('voting_open', 'voting_closed'): 'Use Close Voting to close the round, so it is counted.',
    ('voting_open', 'results_published'): 'Close voting before publishing the results.',
}


def check_status_change(period, new_status):
    """
    The checks before Change status, in the web's order: a real status, a
    change, and an active position before nominations open. Raises
    PeriodSetupError (``unchanged`` is the web's info, not an error).
    """
    valid_statuses = dict(SlatingPeriod.STATUS_CHOICES).keys()
    if new_status not in valid_statuses:
        raise PeriodSetupError('invalid_status', 'Invalid status.')

    # If same status, no change needed
    if new_status == period.status:
        raise PeriodSetupError('unchanged', 'Status unchanged.', level='info')

    # 11-05-26: only the election's real steps (any status was accepted, so
    # voting could be "closed" without counting, published while open, or
    # reopened after publishing).
    if new_status == 'results_published':
        from src.view.slating.results import publish_refusal
        refusal = publish_refusal(period)
        if refusal:
            raise PeriodSetupError('not_allowed', refusal)
    if new_status not in ALLOWED_STATUS_CHANGES.get(period.status, ()):
        raise PeriodSetupError('not_allowed', STATUS_CHANGE_REFUSALS.get(
            (period.status, new_status),
            f'The election can’t go from {period.get_status_display()} to '
            f'{dict(SlatingPeriod.STATUS_CHOICES)[new_status]}.'))

    # Validation checks before certain transitions
    if new_status == 'nominations_open':
        # Must have at least one position
        if not period.positions.filter(is_active=True).exists():
            raise PeriodSetupError('no_positions', 'Add at least one position before opening nominations.')


def apply_status_change(period, user, new_status):
    """
    Move the period to ``new_status`` (any but voting_open, which is
    open_voting_round), log it, and announce nominations or results to every
    active member — results are saved to the chapter's documents too.
    Returns the old status.
    """
    old_status = period.status
    if new_status == 'results_published':
        # 11-05-26: one Publish, the results page's (publish_period_results:
        # logged, results_publish_at set, filed in Chapter Documents, members
        # notified), whichever page the chair publishes from.
        from src.view.slating.results import publish_period_results
        publish_period_results(period, user, save_to_documents=True)
        return old_status
    # Update status
    period.status = new_status
    period.save()

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='period_status_changed',
        details=f'Status changed from {old_status} to {new_status}',
        metadata={'old_status': old_status, 'new_status': new_status}
    )

    # Send notifications for key transitions
    if new_status == 'nominations_open':
        _notify_nominations_open(period, user)
    return old_status


@login_required
@slating_admin_required
def create_period(request):
    """
    Create a new slating period. Admin only.
    """
    if request.method == 'POST':
        try:
            period = create_slating_period(request.user, request.POST)
        except PeriodSetupError as e:
            messages.error(request, str(e))
            return redirect('slating_create_period')

        messages.success(request, f'Slating period "{period.name}" created successfully.')
        if manager_hides_period(period, request.user):
            messages.warning(request, MANAGER_HIDES_PERIOD)
            return redirect('slating_dashboard')
        return redirect('slating_period_setup', period_id=period.id)

    # GET - show create form
    eligible_managers = eligible_period_managers()

    context = {
        'eligible_managers': eligible_managers,
        'is_new': True,
    }

    return render(request, 'slating/period_setup.html', context)


@login_required
@slating_chair_required
def edit_period(request, period_id):
    """
    Edit an existing slating period. Chair or admin.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Check for automatic status transitions based on dates
    if check_and_auto_transition_status(period):
        messages.info(request, f'Status automatically updated to "{period.get_status_display()}" based on scheduled date.')

    if request.method == 'POST':
        action = request.POST.get('action', 'save')

        if action == 'save':
            try:
                save_period_settings(period, request.user, request.POST)
            except PeriodSetupError as e:
                messages.error(request, str(e))
                return redirect('slating_period_setup', period_id=period.id)
            messages.success(request, 'Period settings saved.')

            return redirect('slating_period_setup', period_id=period.id)

        elif action == 'add_committee_member':
            try:
                member = add_period_committee_member(period, request.POST.get('member_id', ''))
                messages.success(request, f'{member.name} added to slating committee.')
            except PeriodSetupError as e:
                messages.error(request, str(e))
            return redirect('slating_period_setup', period_id=period.id)

        elif action == 'remove_committee_member':
            try:
                member = remove_period_committee_member(period, request.POST.get('member_id', ''))
                if member:
                    messages.success(request, f'{member.name} removed from slating committee.')
            except PeriodSetupError as e:
                messages.error(request, str(e))
            return redirect('slating_period_setup', period_id=period.id)

        elif action == 'delete':
            try:
                period_name = delete_slating_period(period, request.user)
            except PeriodSetupError as e:
                messages.error(request, str(e))
                return redirect('slating_period_setup', period_id=period.id)

            messages.success(request, f'Slating period "{period_name}" has been deleted.')
            return redirect('slating_dashboard')

    # Backfill: auto-create the ad hoc committee if this period predates the feature
    ensure_slating_committee(period)

    # GET - show edit form
    eligible_managers = eligible_period_managers()

    # Committee members for slating (who can view applications/interviews)
    # Includes the explicit committee admin (stored as FK, not in members M2M)
    committee_members = period_committee_members(period)

    # Get counts for display
    position_count = period.positions.filter(is_active=True).count()
    field_count = period.form_fields.filter(is_active=True).count()
    application_count = period.applications.exclude(status='draft').count()

    # Get live voting stats if voting is open or closed
    vote_stats = None
    if period.status in ['voting_open', 'voting_closed', 'results_published']:
        from collections import Counter

        slate = Slate.objects.filter(
            period=period,
            is_approved=True,
            slate_type='primary'
        ).first()

        if slate:
            current_attempt = period.current_voting_attempt

            if period.vote_type == 'individual':
                # Individual position voting stats
                # v4.0.0: this round only (rounds are kept now), plus paper.
                ind_ballots = SlatingBallot.objects.filter(period=period, vote_type='individual',
                                                           voting_attempt=current_attempt)
                total_ballots = ind_ballots.values('voter').distinct().count()

                # 10-29-26: this round's ballot positions, run-offs and floor
                # nominees included (the same rows as the round's snapshot).
                from .tallies import round_results
                position_breakdown = [{
                    'position': row['position'],
                    'candidate_name': row['candidate'],
                    'approve': row['approve'],
                    'reject': row['reject'],
                    'abstain': row['abstain'],
                    'paper': 0,
                    'approval_percentage': row['percentage'] or 0,
                } for row in round_results(period, current_attempt, slate, 'individual')['positions']]

                vote_stats = {
                    'vote_type': 'individual',
                    'total_ballots': total_ballots,
                    'position_breakdown': position_breakdown,
                }

            else:
                # Full slate vote stats
                ballots = SlatingBallot.objects.filter(
                    period=period,
                    voting_attempt=current_attempt,
                    vote_type='slate'
                )
                votes = SlatingVote.objects.filter(
                    period=period,
                    slate=slate,
                    voting_attempt=current_attempt
                )

                # v4.0.0: online + paper ballots, one grouped count.
                t = slate_tally(period, slate, current_attempt)
                approve_count, reject_count, abstain_count = t['approve'], t['reject'], t['abstain']
                total_votes = t['total']
                counted_votes = approve_count + reject_count

                # Rejection analysis
                rejection_votes = votes.filter(vote_choice='reject').exclude(rejected_positions=[])
                position_counts = Counter()
                for vote in rejection_votes:
                    for pos_id in vote.rejected_positions:
                        position_counts[pos_id] += 1

                rejection_breakdown = []
                if position_counts:
                    positions = {p.id: p for p in SlatingPosition.objects.filter(id__in=position_counts.keys())}
                    candidates = {c.position_id: c for c in slate.candidates.select_related('application__applicant', 'write_in_member').defer(*member_defer('application__applicant', 'write_in_member'))}
                    for pos_id, count in position_counts.most_common():
                        pos = positions.get(pos_id)
                        candidate = candidates.get(pos_id)
                        if pos:
                            rejection_breakdown.append({
                                'position': pos.title,
                                'candidate_name': candidate.candidate_name if candidate else 'Unknown',
                                'count': count,
                            })

                vote_stats = {
                    'vote_type': 'slate',
                    'total_ballots': ballots.count(),
                    'paper_ballots': t['ballots_paper'],
                    'paper_votes': sum(t['paper'].values()),
                    'approve': approve_count,
                    'reject': reject_count,
                    'abstain': abstain_count,
                    'total_votes': total_votes,
                    'approval_percentage': (approve_count / counted_votes * 100) if counted_votes > 0 else 0,
                    'rejection_breakdown': rejection_breakdown,
                }

    # 11-05-26: "N of M present have voted" — this round, any kind, paper included.
    if vote_stats is not None and period.status == 'voting_open':
        from .results import round_participation
        vote_stats['participation'] = round_participation(period)

    # Attendance summary for sidebar
    attendance_count = 0
    quorum_met = True
    if period.status in ['deliberation', 'voting_open']:
        attendance_count = SlatingAttendance.objects.filter(period=period).count()
        quorum_met = period.quorum is None or attendance_count >= period.quorum

    # Write-in support: blank positions and active members (11-04-26: shared
    # with the app's Blank positions screen, slate_builder.write_in_options)
    from .slate_builder import write_in_options
    write_ins = write_in_options(period)
    blank_positions = write_ins['blank_positions']
    write_in_candidates = write_ins['write_in_candidates']
    active_members = write_ins['active_members']
    write_in_js_data = '{}'
    if blank_positions:
        # Rendered into a <script> with |safe, and it carries member names.
        from src.utils.script_json import script_safe_json
        write_in_js_data = script_safe_json({
            'pos_applicants': write_ins['pos_applicants'],
            'slated_ids': write_ins['slated_ids'],
            'members': [{'id': m.user_id, 'name': m.name} for m in active_members],
        })

    election = election_context(period)
    context = {
        'period': period,
        'is_new': False,
        'position_count': position_count,
        'field_count': field_count,
        'application_count': application_count,
        'vote_stats': vote_stats,
        'blank_positions': blank_positions,
        'write_in_candidates': write_in_candidates,
        'active_members': active_members,
        'attendance_count': attendance_count,
        'quorum_met': quorum_met,
        'eligible_managers': eligible_managers,
        'committee_members': committee_members,
        'write_in_js_data': write_in_js_data,
        # v4.0.0: the "+ Default Positions" modal lists exactly what the
        # button adds (it was a hardcoded list that had drifted).
        'default_positions': default_slate_positions(),
        'candidates_cannot_vote': bool((period.extra_settings or {}).get('candidates_cannot_vote')),
        'approval_locked': approval_locked(period),
        # 10-29-26: positions, rounds and floor nominations (src/slating_rounds.py).
        'election': election,
        # 11-05-26: the applicants' rule (pledges may run; no advisors).
        'nominee_members': (list(ParliamentUser.objects.filter(
            member_status='Active').exclude(member_type='Advisor').order_by('name'))
            if any(s.kind == 'nominations' for s in election['states']) else []),
    }

    return render(request, 'slating/period_setup.html', context)


@login_required
@slating_chair_required
def position_action(request, period_id):
    """
    The chair's per-position controls (10-29-26): floor nominations on/off,
    nominees (add, accepted, remove), and recording what the chapter decided
    for a position (a tie, a tabled run-off) or leaving it unfilled.
    """
    from src.slating_rounds import (
        RoundError, add_nominee, decide_manually, remove_nominee, set_nominee_accepted, set_open_nominations,
    )
    period = get_object_or_404(SlatingPeriod, id=period_id)
    if request.method != 'POST':
        return redirect('slating_period_setup', period_id=period_id)
    action = request.POST.get('action')
    pid = request.POST.get('position_id', '')
    position_id = int(pid) if pid.isdigit() else None
    nid = request.POST.get('nominee_id', '')
    nominee_id = int(nid) if nid.isdigit() else None
    try:
        if action in ('nominations_on', 'nominations_off'):
            set_open_nominations(period, request.user, position_id, action == 'nominations_on')
        elif action == 'add_nominee':
            member = ParliamentUser.objects.filter(user_id=request.POST.get('member_id'), member_status='Active').first()
            if member is None:
                raise RoundError('not_found', 'Choose an active member.')
            add_nominee(period, request.user, position_id, member, accepted=request.POST.get('accepted') == 'on')
        elif action in ('accept_nominee', 'unaccept_nominee'):
            set_nominee_accepted(period, nominee_id, action == 'accept_nominee')
        elif action == 'remove_nominee':
            remove_nominee(period, nominee_id)
        elif action == 'decide':
            decide_manually(period, request.user, position_id, request.POST.get('choice', ''), request.POST.get('reason'),
                            override=request.POST.get('override') == '1')
        else:
            raise RoundError('invalid', 'Unknown action.')
    except RoundError as e:
        messages.error(request, str(e))
    else:
        messages.success(request, 'Saved.')
    return redirect(f"{reverse('slating_period_setup', args=[period_id])}#positions")


@login_required
@slating_chair_required
def change_period_status(request, period_id):
    """
    Change the status of a slating period.
    """
    if request.method != 'POST':
        return redirect('slating_period_setup', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)
    new_status = request.POST.get('status')

    # Track old status for logging
    old_status = period.status

    try:
        check_status_change(period, new_status)
    except PeriodSetupError as e:
        getattr(messages, e.level)(request, str(e))
        return redirect('slating_period_setup', period_id=period_id)

    if new_status == 'voting_open':
        # 10-29-26: one copy, shared with the app (open_voting_round below).
        from src.slating_rounds import RoundError
        try:
            open_voting_round(
                period, request.user, old_status, request.POST.get('vote_type', 'slate'),
                request.POST.getlist('ballot_positions'), request.POST.get('threshold') or None,
                (request.POST.get('abstain') == 'on') if request.POST.get('abstain_set') else None,
                {k[len('nominees_'):]: request.POST.getlist(k) for k in request.POST if k.startswith('nominees_')},
                request.POST.get('quorum', ''))
        except RoundError as e:
            messages.error(request, str(e))
            return redirect('slating_period_setup', period_id=period_id)
        messages.success(request, f'Period status changed to {period.get_status_display()}.')
        return redirect('slating_period_setup', period_id=period_id)

    apply_status_change(period, request.user, new_status)

    # Pausing voting sends the chair back to the vote page (they can reopen from there)
    if old_status == 'voting_open' and new_status == 'deliberation':
        messages.info(request, 'Voting paused. You can reopen it below.')
        return redirect('slating_vote', period_id=period_id)

    # Redirect results-related transitions to the results page
    if new_status == 'results_published' or old_status == 'results_published':
        messages.success(request, f'Period status changed to {period.get_status_display()}.')
        return redirect('slating_results', period_id=period_id)

    messages.success(request, f'Period status changed to {period.get_status_display()}.')
    return redirect('slating_period_setup', period_id=period_id)


NOBODY_PRESENT = 'No one is marked present. Mark attendance before opening voting.'


def open_voting_round(period, user, old_status, vote_type, positions=None, threshold=None, abstain=None,
                      nominees=None, quorum=''):
    """
    Open chapter voting (the web's Open voting and the app's): the next round,
    or a paused one resumed. 10-29-26 — the round's ballot (full slate, or the
    positions the chair chose with its threshold, abstain option and floor
    nominees) is checked and stored by src/slating_rounds.py.
    Raises RoundError with a message for the chair.
    """
    from django.db import transaction
    from src.slating_rounds import RoundError, lock_period, start_round, validate_round
    with transaction.atomic():
        # 11-05-26: the period lock first (src/slating_rounds.lock_period), as
        # close_round and every ballot take it, and the status re-read inside it.
        lock_period(period)
        if period.status != old_status or period.status != 'deliberation':
            raise RoundError('not_deliberation', 'Voting can be opened from deliberation only.')
        if not period.slates.filter(is_approved=True, slate_type='primary').exists():
            raise RoundError('no_slate', 'Approve a primary slate before opening voting.')
        # 11-05-26: a round with no one marked present can't be voted in.
        if not SlatingAttendance.objects.filter(period=period).exists():
            raise RoundError('nobody_present', NOBODY_PRESENT)
        if vote_type in dict(SlatingPeriod.VOTE_TYPE_CHOICES):
            period.vote_type = vote_type
        quorum = str(quorum or '').strip()
        period.quorum = int(quorum) if quorum.isdigit() and int(quorum) > 0 else None
        # Only start a new round when opening fresh, not when resuming a pause.
        # v4.0.0: a round that was CLOSED (failed) or VOIDED also starts a
        # new one (`round_ended`, set by close_voting / reset_votes). Before,
        # any deliberation → voting_open with attempt > 0 counted as a pause,
        # so a failed round's ballots blocked members from voting again.
        settings_ = dict(period.extra_settings or {})
        resuming_paused = (old_status == 'deliberation' and period.current_voting_attempt > 0
                           and not settings_.get('round_ended'))
        config = None
        if not resuming_paused:
            config = validate_round(period, period.vote_type, positions, threshold, abstain, nominees)
            period.current_voting_attempt += 1
            settings_.pop('round_ended', None)
            period.extra_settings = settings_
            # Earlier rounds' votes are KEPT (Mason: keep the history) — they
            # belong to their round, and counts are per round now.
        period.status = 'voting_open'
        period.save()
        open_round(period)   # v4.0.0: the round's history record
        if config is not None:
            start_round(period, config)
        SlatingActivity.objects.create(
            period=period, user=user, action='period_status_changed',
            details=f'Status changed from {old_status} to voting_open',
            metadata={'old_status': old_status, 'new_status': 'voting_open',
                      'round': period.current_voting_attempt, 'ballot': config or {}})
    _notify_voting_open(period, user)


def _notify_nominations_open(period, exclude_user):
    """Send notification that nominations are open."""
    try:
        from src.notification_service import notify_all_active_members
        notify_all_active_members(
            'announcement',
            f'Officer Applications Open: {period.name}',
            message=f'Applications for {period.name} are now open. Submit your interest to run for office.',
            link=f'/slating/period/{period.id}/apply/',
            source_type='SlatingPeriod',
            source_id=period.id,
            exclude_user=exclude_user
        )
    except Exception:
        pass  # Don't fail if notifications fail


def _notify_voting_open(period, exclude_user):
    """Send notification that voting is open."""
    try:
        from src.notification_service import notify_all_active_members
        notify_all_active_members(
            'announcement',
            f'Voting Open: {period.name}',
            message='Chapter voting on the officer slate is now open. Cast your vote!',
            link=f'/slating/period/{period.id}/vote/',
            source_type='SlatingPeriod',
            source_id=period.id,
            exclude_user=exclude_user
        )
    except Exception:
        pass
