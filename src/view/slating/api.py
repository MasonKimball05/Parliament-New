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
from src.view.form_field_builder import apply_field_order, toggle_field
from .form_builder import SLATING_FIELDS
from .permissions import slating_chair_required, slating_committee_required
from src.models.users import member_defer


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

        apply_field_order(SlatingFormField.objects.filter(period=period), field_order)

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

        from .position_manager import reorder_period_positions
        reorder_period_positions(period, position_order)

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

    # Add stats for committee members.
    # v4.0.0: these two gates said `request.user.is_admin`, bypassing the
    # period lock (see permissions.py) — any site admin got application stats
    # and, below, LIVE vote tallies while voting was still open.
    from .permissions import can_manage_period, can_view_applications
    if can_view_applications(request.user, period):
        # One aggregate (was four COUNTs over the same table).
        from django.db.models import Count, Q
        data['stats'] = period.applications.aggregate(
            applications=Count('pk', filter=~Q(status='draft')),
            pending_review=Count('pk', filter=Q(status='submitted')),
            interviewed=Count('pk', filter=Q(status='interviewed')),
            slated=Count('pk', filter=Q(status='slated')),
        )

    # Add voting stats if voting is open/closed
    if period.status in ['voting_open', 'voting_closed', 'results_published']:
        total_ballots = SlatingBallot.objects.filter(
            period=period,
            voting_attempt=period.current_voting_attempt,
            vote_type='slate'
        ).count()

        data['voting'] = {
            'attempt': period.current_voting_attempt,
            'vote_type': period.vote_type,
        }
        # 11-05-26: the live ballot count is the committee's (and everyone's once published).
        if period.status == 'results_published' or can_view_applications(request.user, period):
            data['voting']['total_ballots'] = total_ballots

        # Only show vote tallies if results published or the user manages the period
        if period.status == 'results_published' or can_manage_period(request.user, period):
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
    from .permissions import applicant_ineligibility
    reason = applicant_ineligibility(user)
    if reason:
        eligibility['eligible'] = False
        eligibility['reasons'].append(reason)
    # 11-05-26: no candidate on the slating committee (as apply_refusal).
    from .permissions import COMMITTEE_CANT_RUN, on_slating_committee
    if on_slating_committee(user, period):
        eligibility['eligible'] = False
        eligibility['reasons'].append(COMMITTEE_CANT_RUN)

    # v4.0.0 (Mason): pledges CAN apply for slating. They must be initiated
    # before they're interviewed (schedule_interview) or serve on the slating
    # committee (permissions._user_can_view).

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
@slating_committee_required   # 11-06-26: the committee helps build the slate
@require_GET
def slate_candidates(request, period_id, slate_id):
    """
    Get slate candidates for slate builder.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    slate = get_object_or_404(Slate, id=slate_id, period=period)
    return JsonResponse({'candidates': slate_candidates_json(slate)})


def slate_candidates_json(slate):
    """
    The slate's candidates as ``slate_candidates`` returns them (the app's
    copy too). 11-04-26: a write-in's ``applicant`` is the member written in;
    this used to fail on a slate with one (``application`` is None).
    """
    candidates = []
    for sc in slate.candidates.select_related('position', 'application__applicant', 'write_in_member').defer(
            *member_defer('application__applicant', 'write_in_member')):
        person = sc.application.applicant if sc.application_id else sc.write_in_member
        candidates.append({
            'id': sc.id,
            'position': {
                'id': sc.position.id,
                'title': sc.position.title,
            },
            'applicant': {
                'name': person.name if person else '',
                'user_id': person.user_id if person else None,
            },
            'display_order': sc.display_order,
        })
    return candidates


def ballot_fingerprint(rows):
    """
    A short hash of the full-slate ballot as members see it: each row's
    position, kind and name(s) (``slate_rows``). The vote page embeds it
    (``{{ candidates|ballot_fingerprint }}``) and ``voting_status`` returns it,
    so "The slate has been updated" shows when what's on the ballot changed:
    not when a vacant position adds a row the count didn't expect, and also
    when one nominee is swapped for another (same count). 11-05-26.
    """
    import hashlib
    parts = [f'{r.position.id}:{getattr(r, "kind", "")}:{r.candidate_name}' for r in rows]
    return hashlib.sha256('|'.join(parts).encode()).hexdigest()[:16]


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
        'vote_type': period.vote_type,
    }

    # Include a lightweight slate fingerprint so clients can detect changes
    from src.models import Slate
    slate = Slate.objects.filter(
        period=period, is_approved=True, slate_type='primary'
    ).first()
    if slate:
        data['slate_candidate_count'] = slate.candidates.count()
        # 11-05-26: what the vote page compares (its rows as members see them),
        # so a vacant position's row or a same-count swap is judged correctly.
        from src.slating_rounds import slate_rows
        data['slate_fingerprint'] = ballot_fingerprint(slate_rows(period, slate))

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

    toggle_field(SLATING_FIELDS, field)

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

    from .position_manager import toggle_position
    return JsonResponse({
        'status': 'success',
        'is_active': toggle_position(position)
    })
