"""
Slating Slate Builder Views

Build and manage the officer slate.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from src.models import (
    SlatingPeriod, SlatingApplication, SlatingPosition,
    Slate, SlateCandidate, SlatingActivity, ParliamentUser
)
from .permissions import slating_chair_required, voting_member_required
from src.models.users import member_defer


@login_required
@slating_chair_required
def build_slate(request, period_id):
    """
    Build the officer slate from applications.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Get or create draft slate
    slate, created = Slate.objects.get_or_create(
        period=period,
        slate_type='draft',
        defaults={
            'name': 'Draft Slate',
            'created_by': request.user,
        }
    )

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'assign':
            return _handle_assign_candidate(request, period, slate, is_runoff=False)
        elif action == 'assign_runoff':
            return _handle_assign_candidate(request, period, slate, is_runoff=True)
        elif action == 'unassign':
            return _handle_unassign_candidate(request, period, slate)
        elif action == 'save_notes':
            return _handle_save_notes(request, slate)

        return redirect('slating_build_slate', period_id=period_id)

    # GET - show slate builder
    positions = period.positions.filter(is_active=True).order_by('display_order')

    # Get current slate assignments keyed by (position_id, is_runoff)
    assignments = {}  # position_id -> {'primary': sc, 'runoff': sc|None}
    for sc in slate.candidates.select_related('application', 'application__applicant').defer(*member_defer('application__applicant')):
        entry = assignments.setdefault(sc.position_id, {'primary': None, 'runoff': None})
        if sc.is_runoff:
            entry['runoff'] = sc
        else:
            entry['primary'] = sc

    # All positions have a primary candidate assigned?
    all_primary_filled = all(
        assignments.get(p.id, {}).get('primary') is not None
        for p in positions
    )

    # v3.17.5: the "N of M positions filled" line in slate_builder.html was
    #
    #   {{ slate.candidates.filter.is_runoff.False.count|default:slate.candidates.count }}
    #
    # which is not a valid expression. Django resolves `candidates.filter` to
    # the bound method, CALLS IT WITH NO ARGUMENTS (returning the unfiltered
    # queryset), then fails to find `.is_runoff` on it — so the whole chain
    # resolved to the empty string and `|default:` silently fell through to
    # `slate.candidates.count`. **It was therefore showing the total candidate
    # count, runoffs included, not the number of primary slots filled** — a
    # wrong number, not merely a slow one, and it cost a COUNT per render.
    #
    # `assignments` is already built from `slate.candidates` above, so both
    # numbers are free.
    primary_filled_count = sum(
        1 for entry in assignments.values() if entry['primary'] is not None
    )

    # Get available applications (any status except draft/withdrawn, not yet slated on THIS slate)
    available_apps = period.applications.exclude(
        status__in=['draft', 'withdrawn']
    ).exclude(
        slate_assignments__slate=slate
    ).select_related('applicant').defer(*member_defer('applicant')).prefetch_related('interviews').order_by('applicant__name')

    # Organize applications by first choice position preference
    apps_by_position = {p.id: [] for p in positions}
    apps_by_position['other'] = []

    for app in available_apps:
        first_choices = app.get_first_choice_positions()
        if first_choices:
            # Place in first matched position bucket
            placed = False
            for first_pref in first_choices:
                if first_pref in apps_by_position:
                    apps_by_position[first_pref].append(app)
                    placed = True
                    break
            if not placed:
                apps_by_position['other'].append(app)
        else:
            apps_by_position['other'].append(app)

    # Get existing slates for reference
    existing_slates = period.slates.exclude(id=slate.id).filter(
        slate_type__in=['primary', 'alternative']
    )

    context = {
        'period': period,
        'slate': slate,
        'positions': positions,
        'primary_filled_count': primary_filled_count,
        'assignments': assignments,
        'apps_by_position': apps_by_position,
        'available_apps': available_apps,
        'existing_slates': existing_slates,
        'all_primary_filled': all_primary_filled,
    }

    return render(request, 'slating/slate_builder.html', context)


def _handle_assign_candidate(request, period, slate, is_runoff=False):
    """Assign a candidate to a position on the slate."""
    position_id = request.POST.get('position_id')
    app_id = request.POST.get('application_id')

    position = get_object_or_404(SlatingPosition, id=position_id, period=period)
    application = get_object_or_404(SlatingApplication, id=app_id, period=period)

    # Remove existing assignment for this slot only
    SlateCandidate.objects.filter(slate=slate, position=position, is_runoff=is_runoff).delete()

    # Create new assignment
    SlateCandidate.objects.create(
        slate=slate,
        position=position,
        application=application,
        is_runoff=is_runoff,
        display_order=position.display_order
    )

    # Update application status
    application.status = 'slated'
    application.save(update_fields=['status'])

    label = 'runoff candidate' if is_runoff else 'primary candidate'
    messages.success(request, f'{application.applicant.name} assigned as {label} for {position.title}.')
    return redirect('slating_build_slate', period_id=period.id)


def _handle_unassign_candidate(request, period, slate):
    """Remove a candidate from a position."""
    assignment_id = request.POST.get('assignment_id')

    assignment = get_object_or_404(SlateCandidate, id=assignment_id, slate=slate)
    applicant_name = assignment.application.applicant.name
    position_title = assignment.position.title

    # Reset application status
    assignment.application.status = 'interviewed'
    assignment.application.save(update_fields=['status'])

    assignment.delete()

    messages.success(request, f'{applicant_name} removed from {position_title}.')
    return redirect('slating_build_slate', period_id=period.id)


def _handle_save_notes(request, slate):
    """Save notes for a slate candidate."""
    assignment_id = request.POST.get('assignment_id')
    notes = request.POST.get('notes', '').strip()

    assignment = get_object_or_404(SlateCandidate, id=assignment_id, slate=slate)
    assignment.notes = notes
    assignment.save(update_fields=['notes'])

    return JsonResponse({'status': 'success'})


@login_required
@slating_chair_required
def approve_slate(request, period_id, slate_id=None):
    """
    Approve a slate for voting.
    """
    if request.method != 'POST':
        return redirect('slating_build_slate', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    if slate_id:
        slate = get_object_or_404(Slate, id=slate_id, period=period)
    else:
        # Get draft slate
        slate = get_object_or_404(Slate, period=period, slate_type='draft')

    # Warn about unfilled positions but allow approval (committee may discuss in person)
    positions = period.positions.filter(is_active=True)
    primary_position_ids = slate.candidates.filter(is_runoff=False).values_list('position_id', flat=True)

    missing_positions = positions.exclude(id__in=primary_position_ids)
    if missing_positions.exists():
        missing_names = ', '.join(p.title for p in missing_positions)
        messages.warning(request, f'Slate approved with {missing_positions.count()} unfilled position(s): {missing_names}. These will be decided in person.')

    # Check if there's already an approved primary slate
    existing_primary = period.slates.filter(
        slate_type='primary',
        is_approved=True
    ).exclude(id=slate.id).first()

    slate_type = request.POST.get('slate_type', 'primary')

    if existing_primary and slate_type == 'primary':
        # Demote existing primary to alternative
        existing_primary.slate_type = 'alternative'
        existing_primary.save(update_fields=['slate_type'])

    # Approve the slate
    slate.slate_type = slate_type
    slate.is_approved = True
    slate.approved_at = timezone.now()
    slate.approved_by = request.user
    slate.name = request.POST.get('name', f'{slate_type.title()} Slate')
    slate.description = request.POST.get('description', '')
    slate.save(update_fields=['slate_type', 'is_approved', 'approved_at', 'approved_by', 'name', 'description'])

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='slate_created',
        details=f'Approved {slate_type} slate: {slate.name}',
        metadata={'slate_id': slate.id, 'slate_type': slate_type},
        ip_address=request.META.get('REMOTE_ADDR')
    )

    messages.success(request, f'Slate "{slate.name}" approved.')
    return redirect('slating_period_setup', period_id=period_id)


@login_required
@slating_chair_required
def slate_preview(request, period_id, slate_id):
    """
    Preview a slate before approval.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    slate = get_object_or_404(Slate, id=slate_id, period=period)

    candidates = slate.candidates.select_related(
        'position', 'application', 'application__applicant'
    ).defer(*member_defer('application__applicant')).order_by('display_order')

    context = {
        'period': period,
        'slate': slate,
        'candidates': candidates,
    }

    return render(request, 'slating/slate_preview.html', context)


@login_required
@voting_member_required
def view_approved_slate(request, period_id):
    """
    Let any voting member see the approved slate once voting is open.
    Available during: voting_open, voting_closed, results_published.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    PUBLIC_STATUSES = ('voting_open', 'voting_closed', 'results_published')
    if period.status not in PUBLIC_STATUSES:
        messages.error(request, 'The slate is not yet publicly available.')
        return redirect('slating_dashboard')

    slate = Slate.objects.filter(
        period=period, is_approved=True, slate_type='primary'
    ).first()

    if not slate:
        messages.error(request, 'No approved slate found.')
        return redirect('slating_dashboard')

    candidates = slate.candidates.select_related(
        'position', 'application', 'application__applicant'
    ).defer(*member_defer('application__applicant')).order_by('display_order')

    context = {
        'period': period,
        'slate': slate,
        'candidates': candidates,
        'public_view': True,
    }

    return render(request, 'slating/slate_preview.html', context)


@login_required
@slating_chair_required
def copy_slate(request, period_id, slate_id):
    """
    Copy a slate to create a new draft.
    """
    if request.method != 'POST':
        return redirect('slating_build_slate', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)
    source_slate = get_object_or_404(Slate, id=slate_id, period=period)

    # Create new draft slate
    new_slate = Slate.objects.create(
        period=period,
        name=f'Copy of {source_slate.name}',
        slate_type='draft',
        description=source_slate.description,
        created_by=request.user
    )

    # Copy candidates
    for candidate in source_slate.candidates.all():
        SlateCandidate.objects.create(
            slate=new_slate,
            position=candidate.position,
            application=candidate.application,
            display_order=candidate.display_order,
            notes=candidate.notes
        )

    messages.success(request, f'Created new draft from "{source_slate.name}".')
    return redirect('slating_build_slate', period_id=period_id)


@login_required
@slating_chair_required
def assign_write_in(request, period_id):
    """
    Assign any active member to a blank position on the approved slate.
    Available during deliberation and voting_open.
    """
    if request.method != 'POST':
        return redirect('slating_period_setup', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    if period.status not in ['deliberation', 'voting_open']:
        messages.error(request, 'Write-in assignments are only allowed during deliberation or voting.')
        return redirect('slating_period_setup', period_id=period_id)

    slate = Slate.objects.filter(period=period, is_approved=True, slate_type='primary').first()
    if not slate:
        messages.error(request, 'No approved primary slate found.')
        return redirect('slating_period_setup', period_id=period_id)

    position_id = request.POST.get('position_id')
    member_id = request.POST.get('member_id')

    position = get_object_or_404(SlatingPosition, id=position_id, period=period)
    member = get_object_or_404(ParliamentUser, user_id=member_id)

    # Only allow assigning to blank primary slots
    if slate.candidates.filter(position=position, is_runoff=False).exists():
        messages.error(request, f'{position.title} already has a primary candidate. Remove them first.')
        return redirect('slating_period_setup', period_id=period_id)

    SlateCandidate.objects.create(
        slate=slate,
        position=position,
        write_in_member=member,
        application=None,
        is_runoff=False,
        display_order=position.display_order
    )

    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='position_added',
        details=f'Write-in: {member.name} assigned to {position.title}',
        ip_address=request.META.get('REMOTE_ADDR')
    )

    messages.success(request, f'{member.name} added as write-in candidate for {position.title}. They will be voted on individually.')
    return redirect('slating_period_setup', period_id=period_id)


@login_required
@slating_chair_required
def remove_write_in(request, period_id):
    """Remove a write-in candidate from a blank position."""
    if request.method != 'POST':
        return redirect('slating_period_setup', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)
    candidate_id = request.POST.get('candidate_id')
    candidate = get_object_or_404(SlateCandidate, id=candidate_id, slate__period=period, write_in_member__isnull=False)

    name = candidate.write_in_member.name
    position_title = candidate.position.title
    candidate.delete()

    messages.success(request, f'{name} removed from {position_title}.')
    return redirect('slating_period_setup', period_id=period_id)


@login_required
@slating_chair_required
def edit_approved_slate(request, period_id):
    """
    Let chairs/admins replace any candidate on the approved slate.
    Available during voting_open or paused voting (deliberation + attempt > 0).
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    voting_paused = period.status == 'deliberation' and period.current_voting_attempt > 0
    if period.status not in ('voting_open',) and not voting_paused:
        messages.error(request, 'Slate editing is only available during voting.')
        return redirect('slating_period_setup', period_id=period_id)

    slate = Slate.objects.filter(period=period, is_approved=True, slate_type='primary').first()
    if not slate:
        messages.error(request, 'No approved primary slate found.')
        return redirect('slating_period_setup', period_id=period_id)

    if request.method == 'POST':
        position_id = request.POST.get('position_id')
        candidate_type = request.POST.get('candidate_type')  # 'applicant' or 'write_in'
        application_id = request.POST.get('application_id')
        member_id = request.POST.get('member_id')

        position = get_object_or_404(SlatingPosition, id=position_id, period=period)

        # Remove existing primary candidate for this position
        old_candidate = slate.candidates.filter(position=position, is_runoff=False).first()
        old_name = old_candidate.candidate_name if old_candidate else '(vacant)'
        if old_candidate:
            old_candidate.delete()

        # Assign new candidate
        if candidate_type == 'applicant' and application_id:
            application = get_object_or_404(SlatingApplication, id=application_id, period=period)
            SlateCandidate.objects.create(
                slate=slate,
                position=position,
                application=application,
                write_in_member=None,
                is_runoff=False,
                display_order=position.display_order,
            )
            new_name = application.applicant.name
        elif candidate_type == 'write_in' and member_id:
            member = get_object_or_404(ParliamentUser, user_id=member_id)
            SlateCandidate.objects.create(
                slate=slate,
                position=position,
                application=None,
                write_in_member=member,
                is_runoff=False,
                display_order=position.display_order,
            )
            new_name = member.name
        else:
            messages.error(request, 'Invalid replacement — select an applicant or a member.')
            return redirect('slating_edit_approved_slate', period_id=period_id)

        SlatingActivity.objects.create(
            period=period,
            user=request.user,
            action='slate_edited',
            details=f'{position.title}: replaced {old_name} with {new_name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )

        messages.success(request, f'{position.title} updated: {old_name} → {new_name}.')
        return redirect('slating_edit_approved_slate', period_id=period_id)

    # GET — build context
    positions = period.positions.filter(is_active=True).order_by('display_order')
    candidate_map = {
        sc.position_id: sc
        for sc in slate.candidates.filter(is_runoff=False).select_related(
            'position', 'application__applicant', 'write_in_member'
        ).defer(*member_defer('application__applicant', 'write_in_member'))
    }

    # All non-withdrawn applications for this period
    applications = list(
        period.applications.exclude(status__in=['draft', 'withdrawn'])
        .select_related('applicant').defer(*member_defer('applicant'))
        .order_by('applicant__name')
    )

    # All active/inactive members for write-in
    active_members = list(
        ParliamentUser.objects.filter(
            member_status__in=['Active', 'Inactive'],
            member_type__in=['Member', 'Chair', 'Officer']
        ).order_by('name')
    )

    rows = []
    for pos in positions:
        current = candidate_map.get(pos.id)
        rows.append({
            'position': pos,
            'current': current,
        })

    context = {
        'period': period,
        'slate': slate,
        'rows': rows,
        'applications': applications,
        'active_members': active_members,
        'voting_paused': voting_paused,
    }
    return render(request, 'slating/edit_approved_slate.html', context)


@login_required
@slating_chair_required
def manual_results(request, period_id):
    """
    Bypass voting entirely — record who won each position and optionally
    log vote counts (overall or per-position). Moves period to voting_closed.
    Available from any pre-voting-closed status.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Not available once results are published or archived
    if period.status in ['voting_closed', 'results_published', 'archived']:
        messages.error(request, 'Results have already been recorded.')
        return redirect('slating_period_setup', period_id=period_id)

    positions = period.positions.filter(is_active=True).order_by('display_order')
    all_members = list(
        ParliamentUser.objects.filter(
            member_status__in=['Active', 'Inactive'],
            member_type__in=['Member', 'Chair', 'Officer']
        ).order_by('name')
    )

    if request.method == 'POST':
        vote_mode = request.POST.get('vote_mode', 'none')  # none | overall | per_position

        # Get or create the primary slate
        slate, _ = Slate.objects.get_or_create(
            period=period,
            slate_type='primary',
            defaults={'name': f'{period.name} Slate', 'created_by': request.user}
        )
        # Ensure it is approved
        if not slate.is_approved:
            slate.is_approved = True
            slate.approved_at = timezone.now()
            slate.approved_by = request.user
            slate.save(update_fields=['is_approved', 'approved_at', 'approved_by'])

        errors = []
        assignments = []  # (position, member) tuples to create

        for pos in positions:
            member_id = request.POST.get(f'winner_{pos.id}')
            if not member_id:
                errors.append(f'Select a winner for {pos.title}.')
                continue
            try:
                member = ParliamentUser.objects.get(pk=member_id)
            except ParliamentUser.DoesNotExist:
                errors.append(f'Invalid member for {pos.title}.')
                continue
            assignments.append((pos, member))

        if errors:
            for e in errors:
                messages.error(request, e)
            return redirect('slating_manual_results', period_id=period_id)

        # Overall vote counts (optional)
        overall_approve = overall_reject = overall_abstain = None
        if vote_mode == 'overall':
            try:
                overall_approve = int(request.POST.get('overall_approve', 0))
                overall_reject = int(request.POST.get('overall_reject', 0))
                overall_abstain = int(request.POST.get('overall_abstain', 0))
            except (ValueError, TypeError):
                pass

        # Build slate candidates and record results
        for pos, member in assignments:
            # Remove any existing primary candidate for this position
            SlateCandidate.objects.filter(slate=slate, position=pos, is_runoff=False).delete()

            approve = reject = abstain = 0
            passed = None

            if vote_mode == 'per_position':
                try:
                    approve = int(request.POST.get(f'approve_{pos.id}', 0))
                    reject = int(request.POST.get(f'reject_{pos.id}', 0))
                    abstain = int(request.POST.get(f'abstain_{pos.id}', 0))
                except (ValueError, TypeError):
                    pass
                counted = approve + reject
                if counted > 0:
                    passed = (approve / counted * 100) >= period.required_approval_percentage
            elif vote_mode == 'overall' and overall_approve is not None:
                approve = overall_approve
                reject = overall_reject
                abstain = overall_abstain
                counted = approve + reject
                if counted > 0:
                    passed = (approve / counted * 100) >= period.required_approval_percentage

            SlateCandidate.objects.create(
                slate=slate,
                position=pos,
                write_in_member=member,
                display_order=pos.display_order,
                individual_passed=passed if vote_mode != 'none' else True,
                individual_votes_for=approve,
                individual_votes_against=reject,
            )

        # Update slate-level vote totals for overall mode
        if vote_mode == 'overall' and overall_approve is not None:
            counted = overall_approve + overall_reject
            slate.approval_votes = overall_approve
            slate.rejection_votes = overall_reject
            slate.abstain_votes = overall_abstain
            slate.total_votes = overall_approve + overall_reject + overall_abstain
            slate.approval_percentage = (overall_approve / counted * 100) if counted > 0 else None
            slate.passed = (overall_approve / counted * 100) >= period.required_approval_percentage if counted > 0 else None
            slate.save(update_fields=['approval_votes', 'rejection_votes', 'abstain_votes', 'total_votes', 'approval_percentage', 'passed'])

        period.status = 'voting_closed'
        period.vote_type = 'individual' if vote_mode == 'per_position' else period.vote_type
        period.save(update_fields=['status', 'vote_type'])

        SlatingActivity.objects.create(
            period=period,
            user=request.user,
            action='voting_closed',
            details=f'Manual results recorded by {request.user.name}. Vote mode: {vote_mode}.',
            metadata={'manual': True, 'vote_mode': vote_mode},
            ip_address=request.META.get('REMOTE_ADDR')
        )

        messages.success(request, 'Results recorded. You can now publish them.')
        return redirect('slating_results', period_id=period_id)

    # GET
    rows = [{'position': pos} for pos in positions]
    context = {
        'period': period,
        'rows': rows,
        'all_members': all_members,
    }
    return render(request, 'slating/manual_results.html', context)
