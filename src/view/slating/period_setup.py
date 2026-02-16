"""
Slating Period Setup Views

Create, edit, and manage slating periods.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import JsonResponse
from src.models import SlatingPeriod, SlatingActivity, Committee
from .permissions import slating_admin_required, slating_chair_required


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

    # deliberation -> voting_open (if voting_open_at has passed)
    elif period.status == 'deliberation':
        if period.voting_open_at and period.voting_open_at <= now:
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
        committee_id = request.POST.get('slating_committee')

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

        # Assign committee if selected
        if committee_id:
            try:
                committee = Committee.objects.get(id=committee_id)
                period.slating_committee = committee
                period.save()
            except Committee.DoesNotExist:
                pass

        # Set configurable values
        try:
            period.min_gpa_requirement = float(request.POST.get('min_gpa_requirement', 2.50))
            period.gpa_level_2_threshold = float(request.POST.get('gpa_level_2_threshold', 0.20))
            period.required_approval_percentage = int(request.POST.get('required_approval_percentage', 60))
            period.max_slate_voting_attempts = int(request.POST.get('max_slate_voting_attempts', 3))
            period.save()
        except (ValueError, TypeError):
            pass

        # Log activity
        SlatingActivity.objects.create(
            period=period,
            user=request.user,
            action='period_created',
            details=f'Created slating period: {name}',
            ip_address=request.META.get('REMOTE_ADDR')
        )

        messages.success(request, f'Slating period "{name}" created successfully.')
        return redirect('slating_period_setup', period_id=period.id)

    # GET - show create form
    committees = Committee.objects.filter(is_active=True).order_by('name')

    context = {
        'committees': committees,
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

            # Update committee
            committee_id = request.POST.get('slating_committee')
            if committee_id:
                try:
                    period.slating_committee = Committee.objects.get(id=committee_id)
                except Committee.DoesNotExist:
                    pass
            else:
                period.slating_committee = None

            # Update configuration
            try:
                period.min_gpa_requirement = float(request.POST.get('min_gpa_requirement', period.min_gpa_requirement))
                period.gpa_level_2_threshold = float(request.POST.get('gpa_level_2_threshold', period.gpa_level_2_threshold))
                period.required_approval_percentage = int(request.POST.get('required_approval_percentage', period.required_approval_percentage))
                period.max_slate_voting_attempts = int(request.POST.get('max_slate_voting_attempts', period.max_slate_voting_attempts))
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
                        parsed = parse_datetime(value)
                        if parsed:
                            setattr(period, field, parsed)
                    except (ValueError, TypeError):
                        pass
                else:
                    setattr(period, field, None)

            period.save()
            messages.success(request, 'Period settings saved.')

            return redirect('slating_period_setup', period_id=period.id)

        elif action == 'delete':
            # Only site admins can delete periods
            if not request.user.is_admin:
                messages.error(request, 'Only site administrators can delete slating periods.')
                return redirect('slating_period_setup', period_id=period.id)

            period_name = period.name
            period_id = period.id

            # Delete the period (cascades to related objects including activity log)
            period.delete()

            # Log to Django's logging system since SlatingActivity is deleted with the period
            import logging
            logger = logging.getLogger('admin_actions')
            logger.info(
                f"[SLATING] User {request.user.name} ({request.user.user_id}) "
                f"deleted slating period: {period_name} (ID: {period_id}) "
                f"from IP: {request.META.get('REMOTE_ADDR')}"
            )

            messages.success(request, f'Slating period "{period_name}" has been deleted.')
            return redirect('slating_dashboard')

    # GET - show edit form
    committees = Committee.objects.filter(is_active=True).order_by('name')

    # Get counts for display
    position_count = period.positions.filter(is_active=True).count()
    field_count = period.form_fields.filter(is_active=True).count()
    application_count = period.applications.exclude(status='draft').count()

    context = {
        'period': period,
        'committees': committees,
        'is_new': False,
        'position_count': position_count,
        'field_count': field_count,
        'application_count': application_count,
    }

    return render(request, 'slating/period_setup.html', context)


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

    # Validate transitions
    old_status = period.status
    allowed_transitions = {
        'setup': ['nominations_open'],
        'nominations_open': ['nominations_closed'],
        'nominations_closed': ['deliberation', 'nominations_open'],  # Can reopen
        'deliberation': ['voting_open', 'nominations_open'],
        'voting_open': ['voting_closed'],
        'voting_closed': ['results_published', 'voting_open'],  # Can reopen
        'results_published': ['archived'],
        'archived': [],  # No transitions from archived
    }

    if new_status not in allowed_transitions.get(old_status, []):
        # Admins can force any transition
        if not request.user.is_admin:
            messages.error(request, f'Cannot transition from {old_status} to {new_status}.')
            return redirect('slating_period_setup', period_id=period_id)

    # Validation checks before certain transitions
    if new_status == 'nominations_open':
        # Must have at least one position
        if not period.positions.filter(is_active=True).exists():
            messages.error(request, 'Add at least one position before opening nominations.')
            return redirect('slating_period_setup', period_id=period_id)

    if new_status == 'voting_open':
        # Must have an approved slate
        if not period.slates.filter(is_approved=True, slate_type='primary').exists():
            messages.error(request, 'Approve a primary slate before opening voting.')
            return redirect('slating_period_setup', period_id=period_id)
        # Increment voting attempt
        period.current_voting_attempt += 1

    # Update status
    period.status = new_status
    period.save()

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='period_status_changed',
        details=f'Status changed from {old_status} to {new_status}',
        metadata={'old_status': old_status, 'new_status': new_status},
        ip_address=request.META.get('REMOTE_ADDR')
    )

    # Send notifications for key transitions
    if new_status == 'nominations_open':
        _notify_nominations_open(period, request.user)
    elif new_status == 'voting_open':
        _notify_voting_open(period, request.user)
    elif new_status == 'results_published':
        _notify_results_published(period, request.user)

    messages.success(request, f'Period status changed to {period.get_status_display()}.')
    return redirect('slating_period_setup', period_id=period_id)


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
