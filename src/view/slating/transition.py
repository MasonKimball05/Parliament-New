"""
Officer Transition View

Allows admin to trigger the officer transition after election results are published.
This transfers roles from outgoing officers to newly elected officers.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponseForbidden

from src.models import SlatingPeriod, Slate, SlateCandidate, ParliamentUser, Role
from src.decorators import admin_required


@login_required
@admin_required
def transition_officers(request, period_id):
    """
    Transition officer roles based on election results.
    This view shows a preview and requires confirmation.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Verify period is in correct state
    if period.status != 'results_published':
        messages.error(request, 'Officer transition can only be performed after results are published.')
        return redirect('slating_results', period_id=period.id)

    # Build preview of what will happen
    preview = build_transition_preview(period)

    if request.method == 'POST':
        confirm = request.POST.get('confirm') == 'yes'

        if not confirm:
            messages.warning(request, 'You must confirm the transition.')
            return redirect('slating_transition', period_id=period.id)

        try:
            results = period.transition_officers(performed_by=request.user)

            # Build summary message
            added_count = len(results.get('added', []))
            removed_count = len(results.get('removed', []))
            error_count = len(results.get('errors', []))

            if error_count > 0:
                messages.warning(
                    request,
                    f'Transition completed with errors: {added_count} roles added, {removed_count} removed, {error_count} errors.'
                )
            else:
                messages.success(
                    request,
                    f'Officer transition complete: {added_count} roles added, {removed_count} removed.'
                )

            return redirect('slating_results', period_id=period.id)

        except ValueError as e:
            messages.error(request, str(e))
            return redirect('slating_transition', period_id=period.id)
        except Exception as e:
            messages.error(request, f'An error occurred: {str(e)}')
            return redirect('slating_transition', period_id=period.id)

    # GET: show preview
    context = {
        'period': period,
        'preview': preview,
        'has_changes': bool(preview.get('incoming') or preview.get('outgoing')),
    }

    return render(request, 'slating/transition_confirm.html', context)


def build_transition_preview(period):
    """
    Build a preview of what will happen during the transition.
    Returns dict with 'incoming' and 'outgoing' lists.
    """
    preview = {
        'incoming': [],  # New officers getting roles
        'outgoing': [],  # Old officers losing roles
        'no_role': [],   # Positions without linked roles
    }

    # Get winning candidates
    passed_slate = period.slates.filter(passed=True).first()
    if passed_slate:
        winners = passed_slate.candidates.all()
    else:
        # Individual voting - get candidates that passed individually
        winners = SlateCandidate.objects.filter(
            slate__period=period,
            individual_passed=True
        )

    for candidate in winners:
        position = candidate.position
        new_officer = candidate.application.applicant
        role = position.role

        if not role:
            preview['no_role'].append({
                'position': position.title,
                'new_officer': new_officer.name,
            })
            continue

        # Add to incoming
        preview['incoming'].append({
            'position': position.title,
            'role': role.name,
            'new_officer': new_officer.name,
            'new_officer_id': new_officer.user_id,
        })

        # Find current role holders
        current_holders = ParliamentUser.objects.filter(roles=role).exclude(pk=new_officer.pk)
        for holder in current_holders:
            preview['outgoing'].append({
                'position': position.title,
                'role': role.name,
                'old_officer': holder.name,
                'old_officer_id': holder.user_id,
            })

    return preview
