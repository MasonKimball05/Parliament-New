"""
Slating API Endpoints

AJAX endpoints for the slating system.
"""

from django.http import JsonResponse
from django.views.decorators.http import require_POST, require_GET
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404
from src.models import (
    SlatingPeriod, SlatingPosition, SlatingFormField,
    SlatingApplication, SlatingBallot, SlatingVote, Slate
)
from .permissions import slating_chair_required, slating_committee_required


@login_required
@slating_chair_required
@require_POST
def reorder_fields(request, period_id):
    """
    Reorder form fields via drag-and-drop.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    try:
        import json
        data = json.loads(request.body)
        field_order = data.get('field_order', [])

        for index, field_id in enumerate(field_order):
            SlatingFormField.objects.filter(
                id=field_id,
                period=period
            ).update(display_order=index)

        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


@login_required
@slating_chair_required
@require_POST
def reorder_positions(request, period_id):
    """
    Reorder positions via drag-and-drop.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    try:
        import json
        data = json.loads(request.body)
        position_order = data.get('position_order', [])

        for index, position_id in enumerate(position_order):
            SlatingPosition.objects.filter(
                id=position_id,
                period=period
            ).update(display_order=index)

        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


@login_required
@require_GET
def period_status(request, period_id):
    """
    Get current period status and stats.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    data = {
        'id': period.id,
        'name': period.name,
        'status': period.status,
        'status_display': period.get_status_display(),
    }

    # Add stats for committee members
    if period.slating_committee and (
        period.slating_committee.is_member(request.user) or
        request.user.is_admin
    ):
        data['stats'] = {
            'applications': period.applications.exclude(status='draft').count(),
            'pending_review': period.applications.filter(status='submitted').count(),
            'interviewed': period.applications.filter(status='interviewed').count(),
            'slated': period.applications.filter(status='slated').count(),
        }

    # Add voting stats if voting is open/closed
    if period.status in ['voting_open', 'voting_closed', 'results_published']:
        total_ballots = SlatingBallot.objects.filter(
            period=period,
            voting_attempt=period.current_voting_attempt,
            vote_type='slate'
        ).count()

        data['voting'] = {
            'attempt': period.current_voting_attempt,
            'max_attempts': period.max_slate_voting_attempts,
            'total_ballots': total_ballots,
        }

        # Only show vote tallies if results published or user is admin/chair
        if period.status == 'results_published' or request.user.is_admin or (
            period.slating_committee and period.slating_committee.is_chair(request.user)
        ):
            votes = SlatingVote.objects.filter(
                period=period,
                voting_attempt=period.current_voting_attempt,
                slate__isnull=False
            )
            data['voting']['approve'] = votes.filter(vote_choice='approve').count()
            data['voting']['reject'] = votes.filter(vote_choice='reject').count()
            data['voting']['abstain'] = votes.filter(vote_choice='abstain').count()

    return JsonResponse(data)


@login_required
@require_GET
def check_eligibility(request, period_id):
    """
    Check if current user is eligible to apply.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    user = request.user

    eligibility = {
        'eligible': True,
        'reasons': [],
    }

    # Check member status
    if user.member_status != 'Active':
        eligibility['eligible'] = False
        eligibility['reasons'].append('Must be an active member.')

    # Check member type
    if user.member_type == 'Pledge':
        eligibility['eligible'] = False
        eligibility['reasons'].append('Pledges cannot apply for officer positions.')

    # Check if already applied
    existing_app = SlatingApplication.objects.filter(
        period=period,
        applicant=user
    ).exclude(status='withdrawn').first()

    if existing_app:
        eligibility['has_application'] = True
        eligibility['application_id'] = existing_app.id
        eligibility['application_status'] = existing_app.status

    return JsonResponse(eligibility)


@login_required
@slating_committee_required
@require_GET
def application_summary(request, period_id, app_id):
    """
    Get application summary for quick view.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    application = get_object_or_404(
        SlatingApplication, id=app_id, period=period
    )

    # Get position preferences (handles both legacy list and tiered dict)
    positions = period.positions.filter(is_active=True)
    position_dict = {p.id: p.title for p in positions}

    prefs = application.position_preferences or {}
    if isinstance(prefs, list):
        # Legacy format - convert to tiered
        prefs = {'first_choice': prefs, 'second_choice': [], 'third_choice': [], 'do_not_want': []}

    tiered_position_prefs = {}
    for tier in ['first_choice', 'second_choice', 'third_choice', 'do_not_want']:
        tiered_position_prefs[tier] = [
            position_dict.get(pid, 'Unknown')
            for pid in prefs.get(tier, [])
            if pid in position_dict
        ]

    # Flat list for backward compatibility
    position_prefs = (
        tiered_position_prefs['first_choice'] +
        tiered_position_prefs['second_choice'] +
        tiered_position_prefs['third_choice']
    )

    data = {
        'id': application.id,
        'applicant': {
            'name': application.applicant.name,
            'user_id': application.applicant.user_id,
        },
        'status': application.status,
        'status_display': application.get_status_display(),
        'position_preferences': position_prefs,
        'tiered_position_preferences': tiered_position_prefs,
        'gpa_level': application.gpa_level,
        'gpa_verified': application.gpa_verified,
        'submitted_at': application.submitted_at.isoformat() if application.submitted_at else None,
    }

    # Include interview info if any
    interviews = application.interviews.all()
    if interviews.exists():
        interview = interviews.first()
        data['interview'] = {
            'scheduled_at': interview.scheduled_at.isoformat() if interview.scheduled_at else None,
            'completed': interview.completed_at is not None,
            'recommendation': interview.recommendation,
        }

    return JsonResponse(data)


@login_required
@slating_chair_required
@require_GET
def slate_candidates(request, period_id, slate_id):
    """
    Get slate candidates for slate builder.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    slate = get_object_or_404(Slate, id=slate_id, period=period)

    candidates = []
    for sc in slate.candidates.select_related('position', 'application__applicant'):
        candidates.append({
            'id': sc.id,
            'position': {
                'id': sc.position.id,
                'title': sc.position.title,
            },
            'applicant': {
                'name': sc.application.applicant.name,
                'user_id': sc.application.applicant.user_id,
            },
            'display_order': sc.display_order,
        })

    return JsonResponse({'candidates': candidates})


@login_required
@require_GET
def voting_status(request, period_id):
    """
    Get current voting status for a user.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    user = request.user

    data = {
        'can_vote': period.can_vote(),
        'status': period.status,
        'voting_attempt': period.current_voting_attempt,
        'max_attempts': period.max_slate_voting_attempts,
    }

    # Check if user has voted
    if period.status == 'voting_open':
        has_voted = SlatingBallot.objects.filter(
            period=period,
            voter=user,
            voting_attempt=period.current_voting_attempt,
            vote_type='slate'
        ).exists()
        data['has_voted'] = has_voted

    return JsonResponse(data)


@login_required
@slating_chair_required
@require_POST
def toggle_field_active(request, period_id, field_id):
    """
    Toggle a form field's active status.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    field = get_object_or_404(SlatingFormField, id=field_id, period=period)

    field.is_active = not field.is_active
    field.save()

    return JsonResponse({
        'status': 'success',
        'is_active': field.is_active
    })


@login_required
@slating_chair_required
@require_POST
def toggle_position_active(request, period_id, position_id):
    """
    Toggle a position's active status.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    position = get_object_or_404(SlatingPosition, id=position_id, period=period)

    position.is_active = not position.is_active
    position.save()

    return JsonResponse({
        'status': 'success',
        'is_active': position.is_active
    })
