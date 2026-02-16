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
    Slate, SlateCandidate, SlatingActivity
)
from .permissions import slating_chair_required


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
            return _handle_assign_candidate(request, period, slate)
        elif action == 'unassign':
            return _handle_unassign_candidate(request, period, slate)
        elif action == 'save_notes':
            return _handle_save_notes(request, slate)

        return redirect('slating_build_slate', period_id=period_id)

    # GET - show slate builder
    positions = period.positions.filter(is_active=True).order_by('display_order')

    # Get current slate assignments
    assignments = {
        sc.position_id: sc
        for sc in slate.candidates.select_related('application', 'application__applicant')
    }

    # Get available applications (any status except draft/withdrawn, not yet slated on THIS slate)
    available_apps = period.applications.exclude(
        status__in=['draft', 'withdrawn']
    ).exclude(
        slate_assignments__slate=slate
    ).select_related('applicant').prefetch_related('interviews').order_by('applicant__name')

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
        'assignments': assignments,
        'apps_by_position': apps_by_position,
        'available_apps': available_apps,
        'existing_slates': existing_slates,
    }

    return render(request, 'slating/slate_builder.html', context)


def _handle_assign_candidate(request, period, slate):
    """Assign a candidate to a position on the slate."""
    position_id = request.POST.get('position_id')
    app_id = request.POST.get('application_id')

    position = get_object_or_404(SlatingPosition, id=position_id, period=period)
    application = get_object_or_404(SlatingApplication, id=app_id, period=period)

    # Remove existing assignment for this position
    SlateCandidate.objects.filter(slate=slate, position=position).delete()

    # Create new assignment
    SlateCandidate.objects.create(
        slate=slate,
        position=position,
        application=application,
        display_order=position.display_order
    )

    # Update application status
    application.status = 'slated'
    application.save()

    messages.success(request, f'{application.applicant.name} assigned to {position.title}.')
    return redirect('slating_build_slate', period_id=period.id)


def _handle_unassign_candidate(request, period, slate):
    """Remove a candidate from a position."""
    assignment_id = request.POST.get('assignment_id')

    assignment = get_object_or_404(SlateCandidate, id=assignment_id, slate=slate)
    applicant_name = assignment.application.applicant.name
    position_title = assignment.position.title

    # Reset application status
    assignment.application.status = 'interviewed'
    assignment.application.save()

    assignment.delete()

    messages.success(request, f'{applicant_name} removed from {position_title}.')
    return redirect('slating_build_slate', period_id=period.id)


def _handle_save_notes(request, slate):
    """Save notes for a slate candidate."""
    assignment_id = request.POST.get('assignment_id')
    notes = request.POST.get('notes', '').strip()

    assignment = get_object_or_404(SlateCandidate, id=assignment_id, slate=slate)
    assignment.notes = notes
    assignment.save()

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

    # Validate slate has all positions filled
    positions = period.positions.filter(is_active=True)
    assignments = slate.candidates.values_list('position_id', flat=True)

    missing_positions = positions.exclude(id__in=assignments)
    if missing_positions.exists():
        missing_names = ', '.join(p.title for p in missing_positions)
        messages.error(request, f'Cannot approve slate. Missing positions: {missing_names}')
        return redirect('slating_build_slate', period_id=period_id)

    # Check if there's already an approved primary slate
    existing_primary = period.slates.filter(
        slate_type='primary',
        is_approved=True
    ).exclude(id=slate.id).first()

    slate_type = request.POST.get('slate_type', 'primary')

    if existing_primary and slate_type == 'primary':
        # Demote existing primary to alternative
        existing_primary.slate_type = 'alternative'
        existing_primary.save()

    # Approve the slate
    slate.slate_type = slate_type
    slate.is_approved = True
    slate.approved_at = timezone.now()
    slate.approved_by = request.user
    slate.name = request.POST.get('name', f'{slate_type.title()} Slate')
    slate.description = request.POST.get('description', '')
    slate.save()

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
    ).order_by('display_order')

    context = {
        'period': period,
        'slate': slate,
        'candidates': candidates,
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
