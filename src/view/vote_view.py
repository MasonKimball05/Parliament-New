from django.db.models import Count, Q
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.utils.timezone import make_aware
from django.utils.dateparse import parse_datetime
from datetime import timedelta
from django.db import transaction
from ..models import Legislation, Vote, Attendance, ParliamentUser, ActivityLog
from src.utils.file_validation import validate_uploaded_file
from src.feature_flag_decorators import require_page_enabled, require_feature_flag
from src.view.webauthn import check_vote_reauth
from src.utils.vote_events import broadcast_vote_event
from src.utils.vote_receipts import make_receipt
import logging


def _attendance_can_vote(user):
    """Present/late within the last 3 hours AND a voting-eligible member type
    (excludes pledges). Shared by cast_vote and the vote page's can_vote state.
    """
    three_hours_ago = timezone.now() - timedelta(hours=3)
    attendance = Attendance.objects.filter(
        user=user,
        created_at__gte=three_hours_ago,
        status__in=['present', 'late']
    ).order_by('-created_at').first()
    return bool(attendance) and user.can_vote


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
    if user.member_type not in ['Chair', 'Officer']:
        # The old multiplexed view silently fell through to a page render
        # for non-officers — say it instead (v3.13.3 no-silent-drops rule).
        messages.error(request, "Only chairs and officers can upload legislation.")
        return redirect('vote')
    title = request.POST.get('title')
    description = request.POST.get('description')
    document = request.FILES.get('document')

    # Validate uploaded file for security
    if document:
        try:
            validate_uploaded_file(document)
        except ValidationError as e:
            messages.error(request, f'File upload error: {str(e)}')
            return redirect('home')

    anonymous = request.POST.get('anonymous') == 'on'
    allow_abstain = not (request.POST.get('remove_abstain') == 'on')
    required_percentage = int(request.POST.get('required_percentage', 51))

    raw_available_at = request.POST.get('available_at')
    parsed_available_at = parse_datetime(raw_available_at)
    available_at = make_aware(parsed_available_at) if parsed_available_at else None

    # Parse voting_starts_at (optional - defaults to available_at if not set)
    raw_voting_starts_at = request.POST.get('voting_starts_at')
    voting_starts_at = None
    if raw_voting_starts_at:
        parsed_voting_starts_at = parse_datetime(raw_voting_starts_at)
        voting_starts_at = make_aware(parsed_voting_starts_at) if parsed_voting_starts_at else None

    # v3.14.0: the "Now" buttons send an explicit is_now flag that the
    # SERVER resolves — the browser-filled text is cosmetic. This makes
    # "open it now" immune to any skew between the user's device clock /
    # timezone and the server's (reported live: Now-filled votes landing
    # minutes-to-hours in the future and never opening).
    if request.POST.get('available_at_is_now') == '1':
        available_at = timezone.now()
    if request.POST.get('voting_starts_at_is_now') == '1' and voting_starts_at:
        voting_starts_at = timezone.now()

    # v3.13.3: voting-mode toggle. 'separate' + no start time means voting
    # stays closed until the author hits "Open Voting Now". Anything else
    # (including forms that don't send the field — committee push, older
    # clients) keeps the historical unified behavior: blank start = voting
    # opens at available_at.
    voting_manual_open = (
        request.POST.get('voting_mode_choice') == 'separate'
        and not voting_starts_at
    )

    # Parse voting_ends_at (optional)
    raw_voting_ends_at = request.POST.get('voting_ends_at')
    voting_ends_at = None
    if raw_voting_ends_at:
        parsed_voting_ends_at = parse_datetime(raw_voting_ends_at)
        voting_ends_at = make_aware(parsed_voting_ends_at) if parsed_voting_ends_at else None

    vote_mode = request.POST.get('vote_mode', 'percentage')
    plurality_options = []
    required_number = None
    plurality_votes_allowed = 1
    plurality_runoff_enabled = False
    plurality_runoff_count = 2

    if vote_mode == 'plurality':
        # Support up to 10 plurality options
        for i in range(1, 11):
            val = request.POST.get(f'plurality_option_{i}')
            if val and val.strip():
                plurality_options.append(val.strip())

        if len(plurality_options) < 2:
            messages.error(request, "Plurality voting requires at least two options.")
            return redirect('vote')

        # Parse multi-select settings
        votes_allowed_raw = request.POST.get('plurality_votes_allowed', '1')
        try:
            plurality_votes_allowed = max(1, min(10, int(votes_allowed_raw)))
        except (ValueError, TypeError):
            plurality_votes_allowed = 1

        # Ensure votes_allowed doesn't exceed number of options
        plurality_votes_allowed = min(plurality_votes_allowed, len(plurality_options))

        # Parse runoff settings
        plurality_runoff_enabled = request.POST.get('plurality_runoff_enabled') == 'on'
        if plurality_runoff_enabled:
            runoff_count_raw = request.POST.get('plurality_runoff_count', '2')
            try:
                plurality_runoff_count = max(2, min(len(plurality_options), int(runoff_count_raw)))
            except (ValueError, TypeError):
                plurality_runoff_count = 2

    elif vote_mode == 'piecewise':
        required_number = request.POST.get('required_number')
        if not required_number or int(required_number) < 1:
            messages.error(request, "Piecewise voting requires a valid number of required votes (at least 1).")
            return redirect('vote')
        required_number = int(required_number)

    # v3.13.3: document is optional when the description is detailed
    # (20+ characters — same rule as the legislation tracker's add form)
    # or for plurality votes.
    detailed_description = description and len(description.strip()) >= 20
    if title and description and available_at and (
            document or vote_mode == 'plurality' or detailed_description):
        new_legislation = Legislation.objects.create(
            title=title,
            description=description,
            document=document if vote_mode != 'plurality' else None,
            posted_by=user,
            available_at=available_at,
            voting_starts_at=voting_starts_at,
            voting_manual_open=voting_manual_open,
            voting_ends_at=voting_ends_at,
            anonymous_vote=anonymous,
            allow_abstain=allow_abstain,
            required_percentage=required_percentage,
            vote_mode=vote_mode,
            plurality_options=plurality_options if vote_mode == 'plurality' else None,
            plurality_votes_allowed=plurality_votes_allowed if vote_mode == 'plurality' else 1,
            plurality_runoff_enabled=plurality_runoff_enabled if vote_mode == 'plurality' else False,
            plurality_runoff_count=plurality_runoff_count if vote_mode == 'plurality' else 2,
            required_number=required_number if vote_mode == 'piecewise' else None
        )

        logger = logging.getLogger('function_calls')
        logger.info(f"{user.username} uploaded legislation titled '{title}' (mode: {vote_mode}, required %: {required_percentage})")
        ActivityLog.log_activity(
            action_type='legislation_created',
            user=user,
            description=f'{user.name} created legislation "{title}" ({vote_mode} vote, {"anonymous" if anonymous else "non-anonymous"})',
            request=request,
            object_type='Legislation',
            object_id=new_legislation.id,
            object_repr=title,
            metadata={
                'vote_mode': vote_mode,
                'anonymous': anonymous,
                'required_percentage': required_percentage if vote_mode == 'percentage' else None,
                'required_number': required_number if vote_mode == 'piecewise' else None,
                'plurality_options': plurality_options if vote_mode == 'plurality' else None,
            },
        )

        # v3.13.3: echo back the parsed schedule so any clock/timezone
        # mismatch is visible immediately instead of surfacing later as
        # "it opened at the wrong time".
        _now = timezone.now()
        _visible = timezone.localtime(available_at)
        if voting_manual_open:
            _opens_text = 'when you open it (use "Open Voting Now" on the card)'
        else:
            _opens = timezone.localtime(voting_starts_at or available_at)
            _opens_text = ('now' if (voting_starts_at or available_at) <= _now
                           else _opens.strftime('%b %d at %I:%M %p'))
        _msg = "Legislation uploaded — visible {} · voting opens {}.".format(
            'now' if available_at <= _now else _visible.strftime('%b %d at %I:%M %p'),
            _opens_text,
        )
        # If anything is scheduled for later, show the server's own clock —
        # makes device-clock/timezone skew instantly visible (v3.14.0)
        if available_at > _now or (
                not voting_manual_open and (voting_starts_at or available_at) > _now):
            _msg += " Server time is now {}.".format(
                timezone.localtime(_now).strftime('%I:%M %p'))
        messages.success(request, _msg)
        if available_at <= _now:
            broadcast_vote_event('opened', new_legislation.id)
        return redirect('vote')
    else:
        # v3.13.3: explain instead of silently ignoring the POST
        messages.error(
            request,
            "Legislation was NOT saved — title, description, and a go-live "
            "time are required. A document is also required unless the "
            "description is detailed (at least 20 characters) or it's a "
            "plurality vote."
        )
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
    if user.member_type not in ['Chair', 'Officer']:
        return JsonResponse({'ok': False, 'error': 'Not allowed.'}, status=403)
    target_user_id = request.POST.get('target_user_id')
    new_status = request.POST.get('attendance_status')
    if not target_user_id or new_status not in ['present', 'late', 'absent']:
        return JsonResponse({'ok': False, 'error': 'Invalid request.'}, status=400)
    try:
        target = ParliamentUser.objects.get(user_id=target_user_id)
    except ParliamentUser.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'Unknown member.'}, status=404)

    now = timezone.now()
    # v3.17.4: localdate(), not now().date(). `Attendance.date` is written on the
    # Central calendar; a UTC date here missed the row every evening and this
    # endpoint inserted a duplicate instead of updating — which is what the
    # MultipleObjectsReturned branch below was really healing.
    lookup = {
        'user': target,
        'date': timezone.localdate(),
        'attendance_type': 'committee',
        'committee': None,
    }
    defaults = {
        'status': new_status,
        'created_at': now,
        'marked_by': user,
        'marked_at': now,
    }
    try:
        Attendance.objects.update_or_create(**lookup, defaults=defaults)
    except Attendance.MultipleObjectsReturned:
        # No unique constraint on the lookup keys, so two rapid clicks
        # could race update_or_create into creating duplicate rows —
        # after which every subsequent update_or_create for that member
        # 500s and the panel silently stops saving (v3.13.3). Heal by
        # keeping the newest row and updating it.
        dupes = Attendance.objects.filter(**lookup).order_by('-created_at')
        keep = dupes.first()
        dupes.exclude(pk=keep.pk).delete()
        for field, value in defaults.items():
            setattr(keep, field, value)
        keep.save()
    return JsonResponse({'ok': True, 'status': new_status})


@login_required
@require_page_enabled('vote')
@require_POST
def cast_vote(request):
    """v3.14.1 split — ballot casting (re-auth, validation, receipt mint).

    Was multiplexed into vote_view behind action=cast_vote; the vote forms
    on vote.html post here.
    """
    user = request.user
    can_vote = _attendance_can_vote(user)
    if not can_vote:
        if not user.can_vote:
            messages.error(request, "Your membership type is not eligible to vote.")
        else:
            messages.error(
                request,
                "Your vote was NOT counted: you aren't marked present within "
                "the last 3 hours. Ask an officer to mark your attendance, "
                "then vote again."
            )
        return redirect('vote')

    # Identity confirmation: password, or passkey (v3.13.3)
    reauth_ok, reauth_error = check_vote_reauth(request)

    if reauth_ok:
        legislation_id = request.POST.get('legislation_id')
        legislation = get_object_or_404(Legislation, id=legislation_id)

        if Vote.objects.filter(user=user, legislation=legislation).exists():
            messages.error(request, "You have already voted on this legislation.")
            return redirect('vote')
        if legislation.voting_closed:
            messages.error(request, "Voting on this legislation has ended.")
            return redirect('vote')
        if not legislation.voting_has_started():
            messages.error(request, "Voting has not started yet on this legislation.")
            return redirect('vote')

        logger = logging.getLogger('function_calls')

        # Handle multi-select plurality voting
        if legislation.vote_mode == 'plurality' and legislation.plurality_votes_allowed > 1:
            vote_choices = request.POST.getlist('vote_choices')

            # Validate number of selections
            if len(vote_choices) < 1:
                messages.error(request, "Please select at least one option.")
                return redirect('vote')
            if len(vote_choices) > legislation.plurality_votes_allowed:
                messages.error(request, f"You can only select up to {legislation.plurality_votes_allowed} options.")
                return redirect('vote')

            # Validate each choice
            for choice in vote_choices:
                if choice not in legislation.plurality_options:
                    messages.error(request, "Invalid vote option.")
                    return redirect('vote')

            # Create a vote record for each selection — atomic so a
            # mid-loop failure can't record a partial ballot (v3.13.3)
            with transaction.atomic():
                created_votes = [
                    Vote.objects.create(user=user, legislation=legislation, vote_choice=choice)
                    for choice in vote_choices
                ]

            logger.info(f"{user.username} voted for {vote_choices} on '{legislation.title}' (ID: {legislation.id}) at {timezone.now()}")
            if legislation.anonymous_vote:
                _vote_desc = f'{user.name} cast {len(vote_choices)} vote(s) on "{legislation.title}" (anonymous)'
                _vote_meta = {'legislation_id': legislation.id, 'vote_mode': legislation.vote_mode, 'anonymous': True, 'choices_count': len(vote_choices)}
            else:
                _vote_desc = f'{user.name} voted for {vote_choices} on "{legislation.title}"'
                _vote_meta = {'legislation_id': legislation.id, 'vote_mode': legislation.vote_mode, 'anonymous': False, 'vote_choices': vote_choices}
            ActivityLog.log_activity(
                action_type='vote_cast',
                user=user,
                description=_vote_desc,
                request=request,
                object_type='Legislation',
                object_id=legislation.id,
                object_repr=legislation.title,
                metadata=_vote_meta,
            )
            # v3.14.0 fix: mint the receipt AT CAST TIME from the rows just
            # created — this is the only moment the choices are known-good.
            # My Ballots regenerates from current DB rows and stays as the
            # convenience re-issue.
            request.session['fresh_vote_receipt'] = {
                'token': make_receipt(user, legislation, created_votes,
                                      cast_at=created_votes[0].cast_at),
                'legislation_title': legislation.title,
            }
            messages.success(
                request,
                f"Your {len(vote_choices)} vote(s) have been submitted. "
                "Copy your receipt below — it can also be re-issued under "
                "My Work → My Ballots."
            )
            broadcast_vote_event('tally', legislation.id)
        else:
            # Single-select voting (percentage, piecewise, or plurality with 1 vote)
            vote_choice = request.POST.get('vote_choice')
            if not vote_choice:
                messages.error(request, "Please select a vote option.")
                return redirect('vote')
            if legislation.vote_mode == 'plurality' and vote_choice not in legislation.plurality_options:
                messages.error(request, "Invalid vote option.")
                return redirect('vote')
            valid_standard = ['yes', 'no', 'abstain']
            if legislation.vote_mode != 'plurality' and vote_choice not in valid_standard:
                messages.error(request, "Invalid vote option.")
                return redirect('vote')

            new_vote = Vote.objects.create(user=user, legislation=legislation, vote_choice=vote_choice)
            logger.info(f"{user.username} voted '{vote_choice}' on '{legislation.title}' (ID: {legislation.id}) at {timezone.now()}")
            if legislation.anonymous_vote:
                _vote_desc = f'{user.name} cast a vote on "{legislation.title}" (anonymous)'
                _vote_meta = {'legislation_id': legislation.id, 'vote_mode': legislation.vote_mode, 'anonymous': True}
            else:
                _vote_desc = f'{user.name} voted "{vote_choice}" on "{legislation.title}"'
                _vote_meta = {'legislation_id': legislation.id, 'vote_mode': legislation.vote_mode, 'anonymous': False, 'vote_choice': vote_choice}
            ActivityLog.log_activity(
                action_type='vote_cast',
                user=user,
                description=_vote_desc,
                request=request,
                object_type='Legislation',
                object_id=legislation.id,
                object_repr=legislation.title,
                metadata=_vote_meta,
            )
            # v3.14.0 fix: mint the receipt at cast time (see plurality path)
            request.session['fresh_vote_receipt'] = {
                'token': make_receipt(user, legislation, [new_vote],
                                      cast_at=new_vote.cast_at),
                'legislation_title': legislation.title,
            }
            messages.success(
                request,
                "Your vote has been submitted. Copy your receipt below — "
                "it can also be re-issued under My Work → My Ballots."
            )
            broadcast_vote_event('tally', legislation.id)

        return redirect('vote')
    else:
        messages.error(request, reauth_error)
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
            choice_counts = {
                row['vote_choice']: row['n']
                for row in Vote.objects.filter(legislation=leg)
                .values('vote_choice').annotate(n=Count('id'))
            }
            if leg.vote_mode == 'plurality':
                tally = {opt: choice_counts.get(opt, 0) for opt in leg.plurality_options}
                tally['total'] = sum(choice_counts.values())
                vote_data[leg.id] = tally
            else:
                vote_data[leg.id] = {
                    'yes': choice_counts.get('yes', 0),
                    'no': choice_counts.get('no', 0),
                    'abstain': choice_counts.get('abstain', 0),
                    'total': sum(choice_counts.values())
                }

    from src.models import Role
    appointment_roles = Role.objects.all().order_by('name')
    appointment_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')

    # Build attendance panel for officers
    members_attendance = None
    attendance_present_count = 0
    if user.member_type in ['Chair', 'Officer']:
        all_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')
        recent_atts = {}
        for att in Attendance.objects.filter(
            user__in=all_members,
            created_at__gte=three_hours_ago
        ).order_by('created_at'):  # ascending so later records win
            recent_atts[att.user_id] = att.status
        members_attendance = [
            {'member': m, 'status': recent_atts.get(m.user_id, 'absent')}
            for m in all_members
        ]
        attendance_present_count = sum(
            1 for m in members_attendance if m['status'] in ('present', 'late')
        )

        # v3.14.0: live turnout per open vote — ballots cast vs voting-eligible
        # members currently present, plus who hasn't voted yet (names only;
        # reveals participation, never choices — anonymous-vote safe).
        _present_voters = [
            m['member'] for m in members_attendance
            if m['status'] in ('present', 'late') and m['member'].can_vote
        ]
        # One query for all open votes instead of one per bill (07-16 N+1 nit).
        _open_legs = [
            leg for leg in available_legislation
            if not leg.voting_closed and leg.voting_has_started()
        ]
        _voters_by_leg = {}
        for _leg_id, _user_pk in Vote.objects.filter(
                legislation__in=_open_legs).values_list('legislation_id', 'user'):
            _voters_by_leg.setdefault(_leg_id, set()).add(_user_pk)
        for leg in _open_legs:
            _voter_pks = _voters_by_leg.get(leg.id, set())
            leg.turnout_info = {
                'voted': len(_voter_pks),
                'present': len(_present_voters),
                'not_voted': [m for m in _present_voters if m.pk not in _voter_pks],
            }

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
    if request.user != legislation.posted_by and not request.user.is_admin:
        return HttpResponseForbidden("Only the uploader or an admin can open this legislation.")
    if legislation.voting_closed:
        messages.error(request, "Voting on this legislation has already ended.")
        return redirect('vote')

    mode = 'show' if request.POST.get('mode') == 'show' else 'open'
    now = timezone.now()
    update_fields = []

    if legislation.available_at and legislation.available_at > now:
        legislation.available_at = now
        update_fields.append('available_at')
    if mode == 'open':
        if legislation.voting_starts_at and legislation.voting_starts_at > now:
            legislation.voting_starts_at = now
            update_fields.append('voting_starts_at')
        elif legislation.voting_manual_open and not legislation.voting_starts_at:
            # Manual-open mode: this click IS the act of opening voting
            legislation.voting_starts_at = now
            update_fields.append('voting_starts_at')

    if update_fields:
        legislation.save(update_fields=update_fields)
        ActivityLog.log_activity(
            action_type='legislation_created',
            user=request.user,
            description=(
                f'{request.user.name} opened "{legislation.title}" early '
                f'({"voting started" if mode == "open" else "made visible"} via Now button)'
            ),
            request=request,
            object_type='Legislation',
            object_id=legislation.id,
            object_repr=legislation.title,
            metadata={'action': 'open_now', 'mode': mode,
                      'fields_advanced': update_fields},
        )
        if legislation.voting_has_started():
            messages.success(request, f'"{legislation.title}" is now open for voting.')
        else:
            messages.success(
                request,
                f'"{legislation.title}" is now visible; voting opens at its scheduled time.'
            )
        broadcast_vote_event('opened', legislation.id)
    else:
        messages.info(request, "This legislation is already open.")
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

        votes = Vote.objects.filter(legislation=leg)
        if leg.vote_mode == 'plurality':
            tally = {opt: votes.filter(vote_choice=opt).count() for opt in (leg.plurality_options or [])}
            tally['total'] = votes.count()
        else:
            tally = {
                'yes': votes.filter(vote_choice='yes').count(),
                'no': votes.filter(vote_choice='no').count(),
                'abstain': votes.filter(vote_choice='abstain').count(),
                'total': votes.count(),
            }
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
