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
    Check if the period should auto-transition based on configured dates.
    Returns True if status was changed.
    """
    now = timezone.now()
    changed = False
    new_status = None

    # Only auto-transition forward, never backward
    # setup -> nominations_open (if nominations_open_at has passed)
    if period.status == 'setup':
        if period.nominations_open_at and period.nominations_open_at <= now:
            new_status = 'nominations_open'

    # nominations_open -> nominations_closed (if nominations_close_at has passed)
    elif period.status == 'nominations_open':
        if period.nominations_close_at and period.nominations_close_at <= now:
            new_status = 'nominations_closed'

    # nominations_closed -> voting_open (if voting_open_at has passed AND slate approved)
    elif period.status == 'nominations_closed':
        if period.voting_open_at and period.voting_open_at <= now:
            if period.slates.filter(is_approved=True, slate_type='primary').exists():
                new_status = 'voting_open'

    # deliberation -> voting_open (if voting_open_at has passed AND slate approved)
    elif period.status == 'deliberation':
        if period.voting_open_at and period.voting_open_at <= now:
            if period.slates.filter(is_approved=True, slate_type='primary').exists():
                new_status = 'voting_open'

    # voting_open -> voting_closed (if voting_close_at has passed)
    elif period.status == 'voting_open':
        if period.voting_close_at and period.voting_close_at <= now:
            new_status = 'voting_closed'

    # voting_closed -> results_published (if results_publish_at has passed)
    elif period.status == 'voting_closed':
        if period.results_publish_at and period.results_publish_at <= now:
            new_status = 'results_published'

    if new_status:
        old_status = period.status
        period.status = new_status

        # Increment voting attempt when transitioning to voting_open
        if new_status == 'voting_open':
            period.current_voting_attempt += 1
            period.extra_settings = {k: v for k, v in (period.extra_settings or {}).items()
                                     if k != 'round_ended'}
            period.save(update_fields=['status', 'current_voting_attempt', 'extra_settings'])
            open_round(period)
        else:
            period.save(update_fields=['status'])

        # Log the auto-transition
        SlatingActivity.objects.create(
            period=period,
            user=None,  # System action
            action='status_changed',
            details=f'Auto-transitioned from {old_status} to {new_status} based on scheduled date'
        )
        changed = True

    return changed


@login_required
@slating_admin_required
def create_period(request):
    """
    Create a new slating period. Admin only.
    """
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        academic_term = request.POST.get('academic_term', '').strip()
        description = request.POST.get('description', '').strip()

        if not name:
            messages.error(request, 'Period name is required.')
            return redirect('slating_create_period')

        if not academic_term:
            messages.error(request, 'Academic term is required.')
            return redirect('slating_create_period')

        # Create the period
        period = SlatingPeriod.objects.create(
            name=name,
            academic_term=academic_term,
            description=description,
            created_by=request.user,
            status='setup'
        )

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
            period.min_gpa_requirement = float(request.POST.get('min_gpa_requirement', 2.50))
            period.gpa_level_2_threshold = float(request.POST.get('gpa_level_2_threshold', 0.20))
            period.required_approval_percentage = int(request.POST.get('required_approval_percentage', 60))
            period.save(update_fields=['min_gpa_requirement', 'gpa_level_2_threshold', 'required_approval_percentage'])
        except (ValueError, TypeError):
            pass

        # Log activity
        SlatingActivity.objects.create(
            period=period,
            user=request.user,
            action='period_created',
            details=f'Created slating period: {name}'
        )

        messages.success(request, f'Slating period "{name}" created successfully.')
        return redirect('slating_period_setup', period_id=period.id)

    # GET - show create form
    eligible_managers = ParliamentUser.objects.filter(
        member_status__in=['Active', 'Inactive'],
        member_type__in=['Member', 'Chair', 'Officer']
    ).order_by('name')

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
            # Update basic info
            period.name = request.POST.get('name', period.name).strip()
            period.academic_term = request.POST.get('academic_term', period.academic_term).strip()
            period.description = request.POST.get('description', '').strip()

            # Update slating manager (admin only can change this)
            if request.user.is_admin:
                manager_id = request.POST.get('slating_manager', '').strip()
                if manager_id:
                    try:
                        period.slating_manager = ParliamentUser.objects.get(user_id=manager_id)
                    except ParliamentUser.DoesNotExist:
                        pass
                else:
                    period.slating_manager = None

            # Update configuration
            try:
                period.min_gpa_requirement = float(request.POST.get('min_gpa_requirement', period.min_gpa_requirement))
                period.gpa_level_2_threshold = float(request.POST.get('gpa_level_2_threshold', period.gpa_level_2_threshold))
                period.required_approval_percentage = int(request.POST.get('required_approval_percentage', period.required_approval_percentage))
            except (ValueError, TypeError):
                pass

            # Update dates
            date_fields = [
                'nominations_open_at', 'nominations_close_at',
                'deliberation_start_at', 'voting_open_at',
                'voting_close_at', 'results_publish_at'
            ]
            for field in date_fields:
                value = request.POST.get(field)
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

            period.save()
            messages.success(request, 'Period settings saved.')

            return redirect('slating_period_setup', period_id=period.id)

        elif action == 'add_committee_member':
            member_id = request.POST.get('member_id', '').strip()
            if period.slating_committee and member_id:
                try:
                    member = ParliamentUser.objects.get(user_id=member_id)
                    period.slating_committee.members.add(member)
                    messages.success(request, f'{member.name} added to slating committee.')
                except ParliamentUser.DoesNotExist:
                    messages.error(request, 'Member not found.')
            else:
                messages.error(request, 'No committee assigned to this period.')
            return redirect('slating_period_setup', period_id=period.id)

        elif action == 'remove_committee_member':
            member_id = request.POST.get('member_id', '').strip()
            if period.slating_committee and member_id:
                try:
                    member = ParliamentUser.objects.get(user_id=member_id)
                    period.slating_committee.members.remove(member)
                    messages.success(request, f'{member.name} removed from slating committee.')
                except ParliamentUser.DoesNotExist:
                    messages.error(request, 'Member not found.')
            return redirect('slating_period_setup', period_id=period.id)

        elif action == 'delete':
            # Only site admins can delete periods
            if not request.user.is_admin:
                messages.error(request, 'Only site administrators can delete slating periods.')
                return redirect('slating_period_setup', period_id=period.id)

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
                f"[SLATING] User {request.user.name} ({request.user.user_id}) "
                f"deleted slating period: {period_name} (ID: {period_id})"
            )

            messages.success(request, f'Slating period "{period_name}" has been deleted.')
            return redirect('slating_dashboard')

    # Backfill: auto-create the ad hoc committee if this period predates the feature
    if not period.slating_committee_id:
        ad_hoc_committee = Committee.objects.create(
            name=f'Slating — {period.name} [{period.id}]',
            is_slating_committee=True,
            is_ad_hoc=True,
        )
        period.slating_committee = ad_hoc_committee
        period.save(update_fields=['slating_committee'])

    # GET - show edit form
    eligible_managers = ParliamentUser.objects.filter(
        member_status__in=['Active', 'Inactive'],
        member_type__in=['Member', 'Chair', 'Officer']
    ).order_by('name')

    # Committee members for slating (who can view applications/interviews)
    # Includes the explicit committee admin (stored as FK, not in members M2M)
    committee_members = []
    if period.slating_committee:
        members_qs = list(period.slating_committee.members.all().order_by('name'))
        admin = period.slating_committee.admin
        if admin and not any(m.pk == admin.pk for m in members_qs):
            committee_members = [admin] + members_qs
        else:
            committee_members = members_qs

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

    # Attendance summary for sidebar
    attendance_count = 0
    quorum_met = True
    if period.status in ['deliberation', 'voting_open']:
        attendance_count = SlatingAttendance.objects.filter(period=period).count()
        quorum_met = period.quorum is None or attendance_count >= period.quorum

    # Write-in support: blank positions and active members
    blank_positions = []
    write_in_candidates = []
    active_members = []
    write_in_js_data = '{}'
    if period.status in ['deliberation', 'voting_open']:
        approved_slate = Slate.objects.filter(period=period, is_approved=True, slate_type='primary').first()
        if approved_slate:
            filled_ids = approved_slate.candidates.filter(is_runoff=False, write_in_member__isnull=True).values_list('position_id', flat=True)
            blank_positions = list(period.positions.filter(is_active=True).exclude(id__in=filled_ids))
            write_in_candidates = list(approved_slate.candidates.filter(write_in_member__isnull=False).select_related('position', 'write_in_member').defer(*member_defer('write_in_member')))
            if blank_positions:
                active_members = list(ParliamentUser.objects.filter(
                    member_status__in=['Active', 'Inactive'],
                    member_type__in=['Member', 'Chair', 'Officer']
                ).order_by('name'))

                # Build per-position applicant markers and already-slated set

                # Who is already on the slate in any capacity
                slated_user_ids = []
                for sc in approved_slate.candidates.select_related('application__applicant', 'write_in_member').defer(*member_defer('application__applicant', 'write_in_member')):
                    if sc.application_id:
                        slated_user_ids.append(sc.application.applicant.user_id)
                    elif sc.write_in_member_id:
                        slated_user_ids.append(sc.write_in_member.user_id)

                # Applicants by position (non-withdrawn)
                apps = list(period.applications.exclude(
                    status__in=['draft', 'withdrawn']
                ).select_related('applicant').defer(*member_defer('applicant')))

                pos_applicants = {}
                for pos in blank_positions:
                    entries = []
                    for app in apps:
                        tier = app.get_position_tier(pos.id)
                        if tier and tier != 'do_not_want':
                            entries.append({'id': app.applicant.user_id, 'tier': tier})
                    pos_applicants[str(pos.id)] = entries

                # Rendered into a <script> with |safe, and it carries member names.
                from src.utils.script_json import script_safe_json
                write_in_js_data = script_safe_json({
                    'pos_applicants': pos_applicants,
                    'slated_ids': slated_user_ids,
                    'members': [{'id': m.user_id, 'name': m.name} for m in active_members],
                })
            else:
                write_in_js_data = '{}'

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
        # 10-29-26: positions, rounds and floor nominations (src/slating_rounds.py).
        'election': election,
        'nominee_members': (list(ParliamentUser.objects.filter(
            member_status='Active', member_type__in=['Member', 'Chair', 'Officer']).order_by('name'))
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
            decide_manually(period, request.user, position_id, request.POST.get('choice', ''), request.POST.get('reason'))
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

    valid_statuses = dict(SlatingPeriod.STATUS_CHOICES).keys()
    if new_status not in valid_statuses:
        messages.error(request, 'Invalid status.')
        return redirect('slating_period_setup', period_id=period_id)

    # Track old status for logging
    old_status = period.status

    # If same status, no change needed
    if new_status == old_status:
        messages.info(request, 'Status unchanged.')
        return redirect('slating_period_setup', period_id=period_id)

    # Validation checks before certain transitions
    if new_status == 'nominations_open':
        # Must have at least one position
        if not period.positions.filter(is_active=True).exists():
            messages.error(request, 'Add at least one position before opening nominations.')
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

    # Update status
    period.status = new_status
    period.save()

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='period_status_changed',
        details=f'Status changed from {old_status} to {new_status}',
        metadata={'old_status': old_status, 'new_status': new_status}
    )

    # Send notifications for key transitions
    if new_status == 'nominations_open':
        _notify_nominations_open(period, request.user)
    elif new_status == 'results_published':
        _notify_results_published(period, request.user)
        # Save results to chapter documents
        try:
            from src.view.slating.results import _save_results_to_documents
            _save_results_to_documents(period, request.user)
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(f'Failed to save results to documents: {e}', exc_info=True)

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


def open_voting_round(period, user, old_status, vote_type, positions=None, threshold=None, abstain=None,
                      nominees=None, quorum=''):
    """
    Open chapter voting (the web's Open voting and the app's): the next round,
    or a paused one resumed. 10-29-26 — the round's ballot (full slate, or the
    positions the chair chose with its threshold, abstain option and floor
    nominees) is checked and stored by src/slating_rounds.py.
    Raises RoundError with a message for the chair.
    """
    from src.slating_rounds import RoundError, start_round, validate_round
    if not period.slates.filter(is_approved=True, slate_type='primary').exists():
        raise RoundError('no_slate', 'Approve a primary slate before opening voting.')
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


def _notify_results_published(period, exclude_user):
    """Send notification that results are published."""
    try:
        from src.notification_service import notify_all_active_members
        notify_all_active_members(
            'announcement',
            f'Election Results: {period.name}',
            message='The officer election results have been published.',
            link=f'/slating/period/{period.id}/results/',
            source_type='SlatingPeriod',
            source_id=period.id,
            exclude_user=exclude_user
        )
    except Exception:
        pass
