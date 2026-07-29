"""
Slating Applications Review Views

Committee review of applications.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Q
from src.models import (
    SlatingPeriod, SlatingApplication, SlatingPosition, SlatingActivity
)
from .permissions import slating_committee_required, slating_chair_required
from src.models.users import member_defer


@login_required
@slating_committee_required
def applications_list(request, period_id):
    """
    View all applications for a period.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Filter options
    status_filter = request.GET.get('status', '')
    position_filter = request.GET.get('position', '')
    gpa_level_filter = request.GET.get('gpa_level', '')
    search = request.GET.get('search', '')

    applications = period.applications.exclude(
        status='draft'
    ).select_related('applicant').defer(*member_defer('applicant')).order_by('-submitted_at')

    # Apply filters
    if status_filter:
        applications = applications.filter(status=status_filter)

    if position_filter:
        # Filter by position preference (handles both legacy list and new tiered dict format)
        pos_id = int(position_filter)
        # Filter applications where position is in any tier
        filtered_apps = []
        for app in applications:
            if pos_id in app.get_preferred_positions():
                filtered_apps.append(app.id)
        applications = applications.filter(id__in=filtered_apps)

    if gpa_level_filter:
        applications = applications.filter(gpa_level=int(gpa_level_filter))

    if search:
        applications = applications.filter(
            Q(applicant__name__icontains=search) |
            Q(applicant__user_id__icontains=search)
        )

    # Get positions for filter dropdown
    positions = period.positions.filter(is_active=True).order_by('display_order')

    # Status counts
    status_counts = {}
    for status, label in SlatingApplication.STATUS_CHOICES:
        if status != 'draft':
            count = period.applications.filter(status=status).count()
            status_counts[status] = {'label': label, 'count': count}

    context = {
        'period': period,
        'applications': applications,
        'positions': positions,
        'status_filter': status_filter,
        'position_filter': position_filter,
        'gpa_level_filter': gpa_level_filter,
        'search': search,
        'status_counts': status_counts,
        'status_choices': [s for s in SlatingApplication.STATUS_CHOICES if s[0] != 'draft'],
        'gpa_level_choices': SlatingApplication.GPA_LEVEL_CHOICES,
    }

    return render(request, 'slating/applications_review.html', context)


@login_required
@slating_committee_required
def application_detail(request, period_id, app_id):
    """
    View a single application in detail.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    application = get_object_or_404(
        SlatingApplication,
        id=app_id,
        period=period
    )

    # Get responses
    responses = application.responses.select_related('field').all()

    # Organize responses by section
    sections = {}
    for response in responses:
        if not response.field.show_in_review and not request.user.is_admin:
            # Skip fields not shown in review (unless admin)
            continue
        section = response.field.section or 'General'
        if section not in sections:
            sections[section] = []
        sections[section].append(response)

    # Sort each section by display order
    for section in sections:
        sections[section].sort(key=lambda r: r.field.display_order)

    # Get positions
    positions = period.positions.filter(is_active=True)
    position_dict = {p.id: p for p in positions}

    # Build tiered position display
    prefs = application.position_preferences or {}

    # Handle legacy list format
    if isinstance(prefs, list):
        prefs = {
            'first_choice': prefs,
            'second_choice': [],
            'third_choice': [],
            'do_not_want': [],
        }

    tiered_positions = {
        'first_choice': [position_dict.get(pid) for pid in prefs.get('first_choice', []) if pid in position_dict],
        'second_choice': [position_dict.get(pid) for pid in prefs.get('second_choice', []) if pid in position_dict],
        'third_choice': [position_dict.get(pid) for pid in prefs.get('third_choice', []) if pid in position_dict],
        'do_not_want': [position_dict.get(pid) for pid in prefs.get('do_not_want', []) if pid in position_dict],
    }

    # Flat list of wanted positions for backward compatibility
    selected_positions = (
        tiered_positions['first_choice'] +
        tiered_positions['second_choice'] +
        tiered_positions['third_choice']
    )

    # Get interviews
    interviews = application.interviews.all().order_by('-scheduled_at')

    # Check if user can edit review
    can_review = request.user.is_admin or (
        period.slating_committee and
        period.slating_committee.is_chair(request.user)
    )

    context = {
        'period': period,
        'application': application,
        'sections': sections,
        'selected_positions': selected_positions,
        'tiered_positions': tiered_positions,
        'interviews': interviews,
        'can_review': can_review,
        'status_choices': SlatingApplication.STATUS_CHOICES,
    }

    return render(request, 'slating/application_detail.html', context)


@login_required
@slating_chair_required
def submit_review(request, period_id, app_id):
    """
    Submit review notes and status update for an application.
    """
    if request.method != 'POST':
        return redirect('slating_app_detail', period_id=period_id, app_id=app_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)
    application = get_object_or_404(SlatingApplication, id=app_id, period=period)

    # Update status
    new_status = request.POST.get('status')
    if new_status and new_status in dict(SlatingApplication.STATUS_CHOICES):
        old_status = application.status
        application.status = new_status

        # Log status change
        if old_status != new_status:
            SlatingActivity.objects.create(
                period=period,
                user=request.user,
                action='application_reviewed',
                details=f'Changed {application.applicant.name} status from {old_status} to {new_status}',
                metadata={'application_id': application.id, 'old_status': old_status, 'new_status': new_status},
                ip_address=request.META.get('REMOTE_ADDR')
            )

    # Update review notes
    review_notes = request.POST.get('review_notes', '').strip()
    if review_notes != application.review_notes:
        application.review_notes = review_notes
        application.reviewer = request.user

    # Verify GPA
    if request.POST.get('verify_gpa') == 'on':
        application.gpa_verified = True
    elif request.POST.get('unverify_gpa') == 'on':
        application.gpa_verified = False

    application.save()

    messages.success(request, 'Application review saved.')
    return redirect('slating_app_detail', period_id=period_id, app_id=app_id)


@login_required
@slating_chair_required
def bulk_update_status(request, period_id):
    """
    Bulk update status for multiple applications.
    """
    if request.method != 'POST':
        return redirect('slating_applications', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    app_ids = request.POST.getlist('application_ids')
    new_status = request.POST.get('status')

    if not app_ids or not new_status:
        messages.error(request, 'No applications selected or status not specified.')
        return redirect('slating_applications', period_id=period_id)

    if new_status not in dict(SlatingApplication.STATUS_CHOICES):
        messages.error(request, 'Invalid status.')
        return redirect('slating_applications', period_id=period_id)

    # Update applications
    updated = SlatingApplication.objects.filter(
        id__in=app_ids,
        period=period
    ).exclude(status='draft').update(status=new_status)

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='application_reviewed',
        details=f'Bulk updated {updated} applications to {new_status}',
        metadata={'application_ids': app_ids, 'new_status': new_status},
        ip_address=request.META.get('REMOTE_ADDR')
    )

    messages.success(request, f'Updated {updated} applications to {new_status}.')
    return redirect('slating_applications', period_id=period_id)
