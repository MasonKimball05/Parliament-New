from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.contrib import messages
from django.contrib.auth import authenticate
from datetime import timedelta
from src.models import Committee, CommitteeLegislation, CommitteeVote, Attendance
from src.view.webauthn import check_vote_reauth
from src.feature_flag_decorators import require_feature_flag
from src import ballots
from src import committee_management as cm
from src.committee_management import recalculate_committee_vote
import logging

logger = logging.getLogger('function_calls')

def get_vote_tally(legislation):
    """Helper to get vote tally for a piece of legislation"""
    votes = CommitteeVote.objects.filter(legislation=legislation)
    if legislation.vote_mode == 'plurality':
        tally = {opt: votes.filter(vote_choice=opt).count() for opt in (legislation.plurality_options or [])}
        tally['total'] = votes.count()
    else:
        tally = {
            'yes': votes.filter(vote_choice='yes').count(),
            'no': votes.filter(vote_choice='no').count(),
            'abstain': votes.filter(vote_choice='abstain').count(),
            'total': votes.count()
        }
    return tally


@require_http_methods(["GET", "POST"])
@login_required
@require_feature_flag('committee_voting')
def committee_vote(request, code):
    committee = get_object_or_404(Committee, code=code)
    user = request.user

    # Check if user can vote in this committee
    is_voting_member = committee.voting_members.filter(pk=user.pk).exists()
    is_chair = committee.is_chair(user)

    # Determine if user is present (same logic as chapter voting)
    three_hours_ago = timezone.now() - timedelta(hours=3)
    # v3.13.3: filter on status (like chapter voting) instead of the legacy
    # `present` boolean — that field misses 'late' members and can go stale
    # (Attendance.save() syncs it, but saves with update_fields skip the sync).
    attendance = Attendance.objects.filter(
        user=user,
        created_at__gte=three_hours_ago,
        status__in=['present', 'late']
    ).order_by('-created_at').first()
    can_vote = bool(attendance) and is_voting_member

    # Handle recalculate vote result (chair only)
    if request.method == 'POST' and 'recalculate_result' in request.POST:
        legislation_id = request.POST.get('legislation_id')
        legislation = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

        if is_chair and legislation.voting_closed:
            # src/committee_management (shared with the app).
            result_text = recalculate_committee_vote(legislation, user, request)
            if result_text:
                messages.success(request, f"Vote result recalculated. The vote {result_text}.")
            else:
                messages.warning(request, "No votes to calculate result from.")
        else:
            messages.error(request, "You don't have permission to recalculate this vote.")
        return redirect('vote', code=code)

    # Handle end vote action (chair or poster only)
    # v4.4.3: the close, result and activity log are src/committee_management
    # (shared with the app).
    if request.method == 'POST' and 'end_vote' in request.POST:
        legislation_id = request.POST.get('legislation_id')
        legislation = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

        if cm.can_end_vote(user, committee, legislation):
            try:
                result_text = cm.end_committee_vote(legislation, user, request)
            except cm.CommitteeActionError as exc:
                messages.warning(request, exc.message)
            else:
                messages.success(request, f"Voting on '{legislation.title}' has been ended. The vote {result_text}.")
        else:
            messages.error(request, "You don't have permission to end this vote.")
        return redirect('vote', code=code)

    # Handle voting
    # v3.13.3: action=cast_vote + explicit can_vote errors — see vote_view.py
    # v4.0.0: eligibility, validation, the locked insert and the activity log
    # are src/ballots.py (shared with the app); re-auth and messages stay here.
    is_vote_post = request.method == 'POST' and (
        request.POST.get('action') == 'cast_vote'
        or 'vote_choice' in request.POST
        or 'vote_choices' in request.POST
    )
    if is_vote_post:
        try:
            ballots.check_committee_eligible(user, committee)
        except ballots.BallotError as exc:
            messages.error(request, str(exc))
            return redirect('vote', code=code)

        # Identity confirmation: password, or passkey (v3.13.3)
        reauth_ok, reauth_error = check_vote_reauth(request)
        if not reauth_ok:
            messages.error(request, reauth_error)
            return redirect('vote', code=code)

        # P0: scope to the committee in the URL — a voting member of one
        # committee must not be able to cast on another's legislation.
        legislation = get_object_or_404(CommitteeLegislation, id=request.POST.get('legislation_id'),
                                        committee=committee)
        if ballots.is_multi_select(legislation):
            vote_choices = request.POST.getlist('vote_choices')
        else:
            vote_choice = request.POST.get('vote_choice')
            vote_choices = [vote_choice] if vote_choice else []
        try:
            ballots.cast_committee(user, committee, legislation, vote_choices, request)
        except ballots.BallotError as exc:
            messages.error(request, str(exc))
            return redirect('vote', code=code)
        if ballots.is_multi_select(legislation):
            messages.success(request, f"Your {len(vote_choices)} vote(s) have been submitted.")
        else:
            messages.success(request, "Your vote has been submitted.")
        return redirect('vote', code=code)

    # Get available (active) legislation for this committee
    available_legislation = CommitteeLegislation.objects.filter(
        committee=committee,
        available_at__lte=timezone.now(),
        voting_closed=False
    ).order_by('-available_at')

    # Get closed/ended legislation for voting history (all committee members can see)
    closed_legislation = CommitteeLegislation.objects.filter(
        committee=committee,
        voting_closed=True
    ).order_by('-voting_ended_at', '-available_at')[:20]  # Last 20 closed votes

    # Track which legislation the user has already voted on
    user_votes = CommitteeVote.objects.filter(
        user=user,
        legislation__committee=committee
    ).values_list('legislation_id', flat=True)
    user_voted = set(user_votes)

    # Build vote data for active legislation (for users who voted or are chairs/posters)
    vote_data = {}
    for leg in available_legislation:
        # Show results if user has voted, is chair, or is the poster
        if leg.id in user_voted or is_chair or leg.posted_by == user:
            vote_data[leg.id] = get_vote_tally(leg)

    # Build vote data for ALL closed legislation (visible to all committee members)
    history_vote_data = {}
    for leg in closed_legislation:
        history_vote_data[leg.id] = get_vote_tally(leg)

    # Track which legislation user can manage (end vote)
    can_end_vote = {}
    for leg in available_legislation:
        can_end_vote[leg.id] = is_chair or leg.posted_by == user

    return render(request, 'committee/vote.html', {
        'committee': committee,
        'profile': user,
        'can_vote': can_vote,
        'has_passkeys': user.webauthn_credentials.exists(),
        'is_chair': is_chair,
        'is_voting_member': is_voting_member,
        'legislation': available_legislation,
        'closed_legislation': closed_legislation,
        'vote_data': vote_data,
        'history_vote_data': history_vote_data,
        'user_voted': user_voted,
        'can_end_vote': can_end_vote,
        'now': timezone.now(),
    })


@require_http_methods(["POST"])
@login_required
@require_feature_flag('committee_voting')
def create_committee_runoff(request, code, legislation_id):
    """Create a runoff vote from a completed committee plurality vote."""
    committee = get_object_or_404(Committee, code=code)
    original = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)
    user = request.user

    # Verify permissions - must be chair or the person who posted it
    # v4.4.3: the checks, the new vote and its log are src/committee_management.
    if not cm.can_end_vote(user, committee, original):
        messages.error(request, "Only committee chairs or the vote creator can create a runoff.")
        return redirect('vote', code=code)
    try:
        runoff, top_options = cm.create_committee_runoff(original, user, request)
    except cm.CommitteeActionError as exc:
        messages.error(request, exc.message)
        return redirect('vote', code=code)
    messages.success(request, f"Runoff vote created with top {len(top_options)} options: {', '.join(top_options)}")
    return redirect('vote', code=code)
