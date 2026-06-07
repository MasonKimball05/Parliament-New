"""
Officer Transition View

Allows admin to trigger the officer transition after election results are published.
This transfers roles from outgoing officers to newly elected officers.
Supports both auto-fill from slate results and manual entry/overrides.
Can be executed immediately or scheduled for a future date.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.timezone import localtime

from src.models import (
    SlatingPeriod, Slate, SlateCandidate, ParliamentUser, Role,
    SlatingPosition, SlatingActivity
)
from src.decorators import admin_required


@login_required
@admin_required
def transition_officers(request, period_id):
    """
    Transition officer roles based on election results.
    This view shows a preview with editable assignments and requires confirmation.
    Supports immediate execution or scheduling for a future date.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Verify period is in correct state
    if period.status != 'results_published':
        messages.error(request, 'Officer transition can only be performed after results are published.')
        return redirect('slating_results', period_id=period.id)

    # Check if transition already completed
    if period.officer_transition_completed:
        messages.info(request, f'Officer transition was already completed on {localtime(period.officer_transition_completed_at).strftime("%B %d, %Y at %I:%M %p %Z")}.')
        return redirect('slating_results', period_id=period.id)

    # Get all positions and potential candidates
    positions = period.positions.filter(is_active=True).select_related('role').order_by('display_order')

    # Get all active members who could be assigned
    all_members = ParliamentUser.objects.filter(
        member_status='Active'
    ).order_by('name')

    # Build the auto-filled assignments from slate
    auto_assignments = build_auto_assignments(period)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'confirm':
            # Get the effective date
            effective_date_str = request.POST.get('effective_date')
            effective_time_str = request.POST.get('effective_time', '00:00')

            # Build transition data to save
            transition_data = {}
            for position in positions:
                enabled = request.POST.get(f'enable_{position.id}') == 'on'
                member_id = request.POST.get(f'member_{position.id}')
                if enabled and member_id:
                    transition_data[str(position.id)] = int(member_id)

            if not transition_data:
                messages.error(request, 'No positions selected for transition.')
                return redirect('slating_transition', period_id=period.id)

            # Determine if immediate or scheduled
            if effective_date_str:
                # Parse the datetime
                try:
                    effective_datetime = parse_datetime(f"{effective_date_str}T{effective_time_str}")
                    if effective_datetime and effective_datetime > timezone.now():
                        # Schedule for later
                        period.officer_transition_at = effective_datetime
                        period.officer_transition_data = transition_data
                        period.save(update_fields=['officer_transition_at', 'officer_transition_data'])

                        SlatingActivity.objects.create(
                            period=period,
                            user=request.user,
                            action='transition_scheduled',
                            details=f'Officer transition scheduled for {localtime(effective_datetime).strftime("%B %d, %Y at %I:%M %p %Z")}',
                            metadata={'transition_data': transition_data},
                            ip_address=request.META.get('REMOTE_ADDR')
                        )

                        messages.success(
                            request,
                            f'Officer transition scheduled for {localtime(effective_datetime).strftime("%B %d, %Y at %I:%M %p %Z")}.'
                        )
                        return redirect('slating_results', period_id=period.id)
                except (ValueError, TypeError):
                    pass

            # Execute immediately
            results = execute_transition(period, transition_data, request.user)

            if results['errors']:
                for error in results['errors']:
                    messages.error(request, error)

            added_count = len(results.get('added', []))
            removed_count = len(results.get('removed', []))

            if added_count > 0 or removed_count > 0:
                messages.success(
                    request,
                    f'Officer transition complete: {added_count} roles added, {removed_count} removed.'
                )

            return redirect('slating_results', period_id=period.id)

        elif action == 'cancel_scheduled':
            # Cancel a scheduled transition
            period.officer_transition_at = None
            period.officer_transition_data = {}
            period.save(update_fields=['officer_transition_at', 'officer_transition_data'])

            SlatingActivity.objects.create(
                period=period,
                user=request.user,
                action='transition_cancelled',
                details='Scheduled officer transition was cancelled',
                ip_address=request.META.get('REMOTE_ADDR')
            )

            messages.info(request, 'Scheduled transition cancelled.')
            return redirect('slating_transition', period_id=period.id)

    # GET: Build the transition form data
    transition_data = []
    pending_data = period.officer_transition_data or {}

    for position in positions:
        auto_assignment = auto_assignments.get(position.id)

        # Check for pending scheduled assignment
        pending_member_id = pending_data.get(str(position.id))
        pending_member = None
        if pending_member_id:
            pending_member = ParliamentUser.objects.filter(pk=pending_member_id).first()

        # Find current role holder
        current_holder = None
        if position.role:
            current_holder = ParliamentUser.objects.filter(roles=position.role).first()

        # Determine which member should be pre-selected
        selected_member = pending_member or auto_assignment

        transition_data.append({
            'position': position,
            'role': position.role,
            'auto_assigned': auto_assignment,
            'selected_member': selected_member,
            'current_holder': current_holder,
            'enabled': selected_member is not None,
        })

    context = {
        'period': period,
        'transition_data': transition_data,
        'all_members': all_members,
        'has_auto_assignments': any(d['auto_assigned'] for d in transition_data),
        'has_scheduled': period.officer_transition_at is not None,
        'scheduled_date': period.officer_transition_at,
    }

    return render(request, 'slating/transition_confirm.html', context)


def build_auto_assignments(period):
    """
    Build a dict of position_id -> applicant based on slate results.
    """
    assignments = {}

    # Get winning candidates from passed slate
    passed_slate = period.slates.filter(passed=True).first()
    if passed_slate:
        for candidate in passed_slate.candidates.select_related('position', 'application__applicant'):
            assignments[candidate.position_id] = candidate.application.applicant
    else:
        # Check for individual voting results
        individual_winners = SlateCandidate.objects.filter(
            slate__period=period,
            individual_passed=True
        ).select_related('position', 'application__applicant')

        for candidate in individual_winners:
            assignments[candidate.position_id] = candidate.application.applicant

    return assignments


def execute_transition(period, transition_data, performed_by=None):
    """
    Execute the officer transition with the given data.
    transition_data is a dict of position_id (str) -> member_id (int)
    Returns dict with 'added', 'removed', 'errors' lists.
    """
    results = {
        'added': [],
        'removed': [],
        'errors': [],
    }

    positions = {str(p.id): p for p in period.positions.filter(is_active=True).select_related('role')}

    for position_id_str, member_id in transition_data.items():
        position = positions.get(position_id_str)
        if not position:
            results['errors'].append(f'Position ID {position_id_str} not found')
            continue

        try:
            new_officer = ParliamentUser.objects.get(pk=member_id)
        except ParliamentUser.DoesNotExist:
            results['errors'].append(f'Member ID {member_id} not found for {position.title}')
            continue

        role = position.role
        if not role:
            results['errors'].append(f'Position "{position.title}" has no linked role')
            continue

        # Remove role from current holders (except new officer)
        current_holders = ParliamentUser.objects.filter(roles=role).exclude(pk=new_officer.pk)
        for holder in current_holders:
            holder.roles.remove(role)
            results['removed'].append({
                'user': holder.name,
                'user_id': holder.user_id,
                'role': role.name,
                'position': position.title,
            })

        # Add role to new officer (if they don't already have it)
        if not new_officer.roles.filter(pk=role.pk).exists():
            new_officer.roles.add(role)
            results['added'].append({
                'user': new_officer.name,
                'user_id': new_officer.user_id,
                'role': role.name,
                'position': position.title,
            })

    # Mark transition as completed
    period.officer_transition_completed = True
    period.officer_transition_completed_at = timezone.now()
    period.officer_transition_data = transition_data
    period.save(update_fields=['officer_transition_completed', 'officer_transition_completed_at', 'officer_transition_data'])

    # Log the transition
    SlatingActivity.objects.create(
        period=period,
        user=performed_by,
        action='officers_transitioned',
        details=f'Added {len(results["added"])} roles, removed {len(results["removed"])} roles',
        metadata=results,
        ip_address=None
    )

    return results


def check_and_execute_scheduled_transitions():
    """
    Check for any scheduled transitions that are due and execute them.
    This should be called by a cron job or management command.
    """
    now = timezone.now()
    pending_periods = SlatingPeriod.objects.filter(
        officer_transition_at__lte=now,
        officer_transition_completed=False,
        officer_transition_data__isnull=False
    ).exclude(officer_transition_data={})

    for period in pending_periods:
        try:
            results = execute_transition(period, period.officer_transition_data)
            print(f"Executed scheduled transition for {period.name}: {len(results['added'])} added, {len(results['removed'])} removed")
        except Exception as e:
            print(f"Error executing scheduled transition for {period.name}: {e}")
