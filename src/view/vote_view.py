from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.utils import timezone
from datetime import timedelta
from ..models import Legislation, Vote, ParliamentUser
from src.utils.file_validation import validate_uploaded_file
from src.feature_flag_decorators import require_page_enabled, require_feature_flag
from src.view.webauthn import check_vote_reauth
from src import ballots, chapter_votes


def _attendance_can_vote(user):
    """Present/late within the last 3 hours AND a voting-eligible member type
    (excludes pledges). The rule lives in src/ballots.py (v4.0.0), shared
    with the app's Votes endpoints.
    """
    return ballots.can_cast_now(user)


@login_required
@require_page_enabled('vote')
@require_feature_flag('legislation_voting')
@require_POST
def upload_chapter_legislation(request):
    """v3.14.1 split — chapter-legislation upload form.

    Was multiplexed into vote_view behind `'title' in request.POST`.
    Appointment votes have their own path (upload_legislation.py); the
    vote.html upload form posts here.
    """
    user = request.user
    if not chapter_votes.can_upload(user):
        # The old multiplexed view silently fell through to a page render
        # for non-officers — say it instead (v3.13.3 no-silent-drops rule).
        messages.error(request, "Only chairs and officers can upload legislation.")
        return redirect('vote')
    document = request.FILES.get('document')

    # Validate uploaded file for security
    if document:
        try:
            validate_uploaded_file(document)
        except ValidationError as e:
            messages.error(request, f'File upload error: {str(e)}')
            return redirect('home')

    # v4.4.2: parsing, the rules, the log and the broadcast are
    # src/chapter_votes.py, shared with the app.
    try:
        _leg, message = chapter_votes.create_chapter_legislation(user, request.POST, document, request)
    except chapter_votes.ChapterVoteError as exc:
        messages.error(request, exc.message)
        return redirect('vote')
    messages.success(request, message)
    return redirect('vote')


@login_required
@require_page_enabled('vote')
@require_POST
def mark_attendance_quick(request):
    """v3.14.1 split — officer quick-attendance panel (JSON endpoint).

    Was multiplexed into vote_view behind action=mark_attendance_quick;
    the attendance panel JS on vote.html posts here.
    """
    user = request.user
    # v3.13.3: respond with JSON so the panel JS can detect failures and
    # revert its optimistic UI. Previously this redirected to a full page
    # render per click (and errors were invisible to the panel).
    if not chapter_votes.can_upload(user):
        return JsonResponse({'ok': False, 'error': 'Not allowed.'}, status=403)
    target_user_id = request.POST.get('target_user_id')
    new_status = request.POST.get('attendance_status')
    if not target_user_id or new_status not in ['present', 'late', 'absent']:
        return JsonResponse({'ok': False, 'error': 'Invalid request.'}, status=400)
    try:
        target = ParliamentUser.objects.get(user_id=target_user_id)
    except ParliamentUser.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Unknown member.'}, status=404)

    chapter_votes.mark_attendance_quick(user, target, new_status)
    return JsonResponse({'ok': True, 'status': new_status})


@login_required
@require_page_enabled('vote')
@require_POST
def cast_vote(request):
    """v3.14.1 split — ballot casting (re-auth, validation, receipt mint).

    Was multiplexed into vote_view behind action=cast_vote; the vote forms
    on vote.html post here. v4.0.0: eligibility, validation, the locked
    insert, the activity log and the receipt are src/ballots.py, shared with
    the app; this view keeps the web's re-auth and its messages.
    """
    user = request.user
    try:
        ballots.check_eligible(user)
    except ballots.BallotError as exc:
        messages.error(request, str(exc))
        return redirect('vote')

    # Identity confirmation: password, or passkey (v3.13.3)
    reauth_ok, reauth_error = check_vote_reauth(request)
    if not reauth_ok:
        messages.error(request, reauth_error)
        return redirect('vote')

    legislation = get_object_or_404(Legislation, id=request.POST.get('legislation_id'))
    if ballots.is_multi_select(legislation):
        choices = request.POST.getlist('vote_choices')
    else:
        choice = request.POST.get('vote_choice')
        choices = [choice] if choice else []
    try:
        result = ballots.cast(user, legislation, choices, request)
    except ballots.BallotError as exc:
        messages.error(request, str(exc))
        return redirect('vote')

    request.session['fresh_vote_receipt'] = {
        'token': result['receipt'],
        'legislation_title': legislation.title,
    }
    if ballots.is_multi_select(legislation):
        messages.success(
            request,
            f"Your {len(choices)} vote(s) have been submitted. "
            "Copy your receipt below — it can also be re-issued under "
            "My Work → My Ballots."
        )
    else:
        messages.success(
            request,
            "Your vote has been submitted. Copy your receipt below — "
            "it can also be re-issued under My Work → My Ballots."
        )
    return redirect('vote')


@login_required
@require_page_enabled('vote')
def vote_view(request):
    user = request.user

    # v3.14.1 split POSTs to dedicated endpoints (cast_vote,
    # mark_attendance_quick, upload_chapter_legislation); v3.14.2 removed the
    # forwarding dispatcher that lived here (Mason-directed, 07-19). This
    # explicit-error guard stays: a tab opened before the deploy still
    # submits to /vote/, and silently dropping that ballot is exactly the
    # failure class v3.13.3 stamped out — the member gets told to reload
    # instead. Do NOT reduce this to a bare GET-only view.
    if request.method == 'POST':
        messages.error(
            request,
            "That action couldn't be processed — the page may be out of "
            "date. Please reload and try again."
        )
        return redirect('vote')

    # Attendance window: can_vote for the template + the officer panel below
    three_hours_ago = timezone.now() - timedelta(hours=3)
    can_vote = _attendance_can_vote(user)

    # Gather available legislation
    # Show legislation that is available OR pending legislation created by the current user
    # Exclude tabled, passed, failed, and removed legislation
    available_legislation = list(Legislation.objects.filter(
        Q(available_at__lte=timezone.now()) | Q(posted_by=user),
        voting_closed=False
    ).exclude(
        status__in=['pending', 'tabled', 'passed', 'failed', 'removed']
    ).order_by('-available_at'))

    # Build vote data for uploader — one GROUP BY per owned bill instead of
    # one COUNT query per choice (07-18: same aggregate pattern as auto-close).
    vote_data = {}
    for leg in available_legislation:
        if leg.posted_by == user:
            vote_data[leg.id] = chapter_votes.owner_tally(leg)

    from src.models import Role
    appointment_roles = Role.objects.all().order_by('name')
    appointment_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')

    # Build attendance panel for officers
    members_attendance = None
    attendance_present_count = 0
    if user.member_type in ['Chair', 'Officer']:
        members_attendance = chapter_votes.attendance_panel(three_hours_ago)
        attendance_present_count = sum(
            1 for m in members_attendance if m['status'] in ('present', 'late')
        )

        # v3.14.0: live turnout per open vote — ballots cast vs voting-eligible
        # members currently present, plus who hasn't voted yet (names only;
        # reveals participation, never choices — anonymous-vote safe).
        # One query for all open votes instead of one per bill (07-16 N+1 nit).
        _open_legs = [
            leg for leg in available_legislation
            if not leg.voting_closed and leg.voting_has_started()
        ]
        _turnout = chapter_votes.turnout(members_attendance, _open_legs)
        for leg in _open_legs:
            leg.turnout_info = _turnout[leg.id]

    # v3.14.0: legislation the user already voted on — the template shows a
    # "vote recorded" state instead of re-offering the ballot form
    user_voted = set(
        Vote.objects.filter(user=user, legislation__in=available_legislation)
        .values_list('legislation_id', flat=True))

    # One-shot: the receipt minted at cast time (set in cast_vote, shown
    # exactly once after the redirect, then gone from the session).
    fresh_receipt = request.session.pop('fresh_vote_receipt', None)

    return render(request, 'vote.html', {
        'profile': user,
        'can_vote': can_vote,
        'fresh_receipt': fresh_receipt,
        'user_voted': user_voted,
        'has_passkeys': user.webauthn_credentials.exists(),
        'legislation': available_legislation,
        'vote_data': vote_data,
        'default_vote_mode': 'percentage',
        'appointment_roles': appointment_roles,
        'appointment_members': appointment_members,
        'members_attendance': members_attendance,
        'attendance_present_count': attendance_present_count,
    })


@login_required
def verify_vote_receipt(request):
    """
    v3.14.0 — vote receipt verification: checks the signature, that the
    ballots still exist, and that the recorded choices are unchanged. Choices
    themselves are never displayed (receipts are anonymous-vote safe).
    """
    from src.utils.vote_receipts import verify_receipt
    result = None
    # v3.14.0: My Ballots links prefill the token via ?receipt=
    initial_receipt = request.GET.get('receipt', '')
    if request.method == 'POST':
        result = verify_receipt(request.POST.get('receipt', ''))
        if result.get('valid'):
            result['is_yours'] = result.get('voter_pk') == request.user.pk
            if result.get('cast_at'):
                from datetime import datetime, timezone as _dt_tz
                result['cast_at_dt'] = datetime.fromtimestamp(
                    result['cast_at'], tz=_dt_tz.utc)
    return render(request, 'vote_receipt_verify.html', {
        'result': result,
        'initial_receipt': initial_receipt,
    })


@login_required
@require_feature_flag('legislation_voting')
@require_POST
def open_legislation_now(request, legislation_id):
    """
    v3.13.3 "Now" buttons: instantly reveal scheduled legislation and/or open
    voting, without editing timestamps by hand.

    mode=show — make the document available now (voting keeps its scheduled
                start, if one is set)
    mode=open — make it available AND start voting now

    Author or admin only. Other members' pages pick the change up within one
    poll cycle (votable_ids reload).
    """
    legislation = get_object_or_404(Legislation, id=legislation_id)
    if not chapter_votes.can_run_vote(request.user, legislation):
        return HttpResponseForbidden("Only the uploader or an admin can open this legislation.")
    # v4.4.2: src/chapter_votes.py, shared with the app.
    try:
        level, message = chapter_votes.open_now(legislation, request.user, request.POST.get('mode'), request)
    except chapter_votes.ChapterVoteError as exc:
        messages.error(request, exc.message)
        return redirect('vote')
    getattr(messages, level)(request, message)
    return redirect('vote')


@login_required
def vote_tally_json(request):
    """
    JSON endpoint polled by vote.html to refresh live vote tallies.
    Returns tallies only for legislation the current user posted (matching the
    template restriction), plus a closed/open flag for each piece of legislation
    so the page can react if a vote ends while the user is watching.
    """
    user = request.user

    active_legislation = Legislation.objects.filter(
        Q(available_at__lte=timezone.now()) | Q(posted_by=user),
        voting_closed=False,
    ).exclude(status__in=['pending', 'tabled', 'passed', 'failed', 'removed'])

    tallies = {}
    for leg in active_legislation:
        if leg.posted_by != user:
            # Non-authors only get the closed flag (for page reload trigger)
            tallies[leg.id] = {'closed': False}
            continue

        tally = chapter_votes.owner_tally(leg)
        tally['closed'] = False
        tallies[leg.id] = tally

    # Also include any legislation that just closed since last poll so the
    # page knows to reload
    recently_closed = Legislation.objects.filter(
        posted_by=user,
        voting_closed=True,
        voting_ended_at__gte=timezone.now() - timedelta(minutes=2),
    )
    for leg in recently_closed:
        tallies[leg.id] = {'closed': True}

    # v3.13.3: IDs currently open for voting, from this user's perspective.
    # The page JS compares this against its baseline and reloads when a vote
    # opens — previously legislation whose available_at / voting_starts_at
    # passed while members were sitting on the page never appeared without a
    # manual refresh ("it doesn't open on its own").
    votable_ids = sorted(
        leg.id for leg in active_legislation if leg.voting_has_started()
    )

    return JsonResponse({'tallies': tallies, 'votable_ids': votable_ids})
