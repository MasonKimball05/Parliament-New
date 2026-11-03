"""
Chapter legislation tools for authors and officers, shared by the website's
views (``src/view/vote_view.py``, ``end_vote.py``, ``reopen_legislation.py``,
``upload_legislation.py``, ``edit_legislation.py``, ``submit_new_version.py``,
``passed_legislation.py``, ``assign_appointment.py``) and the app's
(``src/mobile/views_legislation_manage.py``), so the two can't drift on the
vote's outcome or its side effects: the status and ``passed`` written, the
live-page broadcast, the activity log, the voters' notifications.

Each function does the work and the logging; the caller decides who is asked
(its own permission check — the web view's, restated next to the matching
helper here) and how to answer. Refusals a caller should show the member are
``ChapterVoteError`` with the website's wording; ``code='forbidden'`` marks
the ones the website answers with a 403.

Form fields arrive as the web form's own keys (``plurality_option_1`` …,
``appt_vote_mode`` …) in any mapping with ``.get``, so the app's JSON goes
through the same parsing as the website's POST.

There is no quorum rule for chapter legislation: ending a vote counts the
ballots cast, as it always has (quorum belongs to slating).
"""
import logging

from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.timezone import make_aware

logger = logging.getLogger(__name__)
function_logger = logging.getLogger('function_calls')

#: Who may upload chapter legislation (``upload_chapter_legislation``).
UPLOADER_TYPES = ('Chair', 'Officer')
#: The upload forms' option slots.
MAX_PLURALITY_OPTIONS = 10


class ChapterVoteError(Exception):
    """A refusal to show the member. ``code`` lets a caller tell kinds apart."""

    def __init__(self, message, code='invalid_input'):
        super().__init__(message)
        self.message = message
        self.code = code


# ── Who may ─────────────────────────────────────────────────────────────────

def can_upload(user):
    """``upload_chapter_legislation``: chairs and officers (by member type)."""
    return user.member_type in UPLOADER_TYPES


def can_run_vote(user, legislation):
    """End vote, runoff, open now: the uploader or an admin."""
    return user == legislation.posted_by or user.is_admin


def can_manage_tracker(user):
    """The Legislation Tracker's officer tools: add, notes (not chairs)."""
    return bool(user.is_admin or user.member_type == 'Officer')


# ── Tallies ─────────────────────────────────────────────────────────────────

def owner_tally(legislation):
    """The vote page's live tally for the uploader (``vote_data`` / ``vote_tally``)."""
    from src.models import Vote
    choice_counts = {
        row['vote_choice']: row['n']
        for row in Vote.objects.filter(legislation=legislation)
        .values('vote_choice').annotate(n=Count('id'))
    }
    if legislation.vote_mode == 'plurality':
        tally = {opt: choice_counts.get(opt, 0) for opt in (legislation.plurality_options or [])}
        tally['total'] = sum(choice_counts.values())
        return tally
    return {
        'yes': choice_counts.get('yes', 0),
        'no': choice_counts.get('no', 0),
        'abstain': choice_counts.get('abstain', 0),
        'total': sum(choice_counts.values()),
    }


def attendance_panel(since):
    """The vote page's officer attendance panel: every active member's status since ``since``."""
    from src.models import Attendance, ParliamentUser
    all_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')
    recent_atts = {}
    for att in Attendance.objects.filter(
        user__in=all_members,
        created_at__gte=since
    ).order_by('created_at'):  # ascending so later records win
        recent_atts[att.user_id] = att.status
    return [
        {'member': m, 'status': recent_atts.get(m.user_id, 'absent')}
        for m in all_members
    ]


def turnout(members_attendance, open_legislation):
    """
    {legislation_id: {voted, present, not_voted}} — ballots cast vs voting-
    eligible members present, and who hasn't voted (names only; never choices).
    """
    from src.models import Vote
    present_voters = [
        m['member'] for m in members_attendance
        if m['status'] in ('present', 'late') and m['member'].can_vote
    ]
    voters_by_leg = {}
    for leg_id, user_pk in Vote.objects.filter(
            legislation__in=open_legislation).values_list('legislation_id', 'user'):
        voters_by_leg.setdefault(leg_id, set()).add(user_pk)
    info = {}
    for leg in open_legislation:
        voter_pks = voters_by_leg.get(leg.id, set())
        info[leg.id] = {
            'voted': len(voter_pks),
            'present': len(present_voters),
            'not_voted': [m for m in present_voters if m.pk not in voter_pks],
        }
    return info


def mark_attendance_quick(marker, target, new_status):
    """The panel's one-tap attendance: today's chapter row for ``target``."""
    from src.models import Attendance
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
        'marked_by': marker,
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


# ── End vote / runoff / reopen / open now ───────────────────────────────────

def end_vote(legislation, user, request):
    """
    Close voting and record the outcome (``end_vote``; the uploader or an
    admin). Writes ``voting_closed``, ``status`` and ``passed``, broadcasts the
    close, logs it, and notifies everyone who voted. Returns the counts the
    result page shows.
    """
    from src.models import ActivityLog, ParliamentUser, Vote
    from src.notification_service import notify_users
    from src.utils.vote_events import broadcast_vote_event
    from src.vote_rules import plurality_passed

    # Close voting
    legislation.voting_closed = True
    legislation.save(update_fields=['voting_closed'])

    # Gather votes
    votes = Vote.objects.filter(legislation=legislation)
    vote_summary = votes.values('vote_choice').annotate(count=Count('id'))

    # Count totals
    yes_votes = votes.filter(vote_choice='yes').count()
    no_votes = votes.filter(vote_choice='no').count()
    abstain_votes = votes.filter(vote_choice='abstain').count()
    total_votes = votes.exclude(vote_choice='abstain').count()

    if legislation.vote_mode == 'plurality':
        vote_breakdown_dict = {str(option): votes.filter(vote_choice=option).count() for option in (legislation.plurality_options or [])}
        winner = max(vote_breakdown_dict, key=vote_breakdown_dict.get) if vote_breakdown_dict else None
        vote_breakdown = {'keys': list(vote_breakdown_dict.keys()), 'values': list(vote_breakdown_dict.values())}
    else:
        vote_breakdown = {
            'yes': yes_votes,
            'no': no_votes,
            'abstain': abstain_votes,
        }
        winner = None

    vote_passed = False
    required_pct = None
    yes_percentage = None
    if legislation.vote_mode == 'percentage':
        required_pct = int(legislation.required_percentage or 51)
        yes_percentage = (yes_votes / total_votes) * 100 if total_votes > 0 else 0
        vote_passed = yes_percentage >= required_pct
    elif legislation.vote_mode == 'piecewise':
        required_number = legislation.required_number or 0
        vote_passed = yes_votes >= required_number
    elif legislation.vote_mode == 'plurality':
        plurality_counts = {
            option: votes.filter(vote_choice=option).count()
            for option in (legislation.plurality_options or [])
        }
        most_voted = max(plurality_counts, key=plurality_counts.get, default=None)
        # Only passes if there's a clear winner (no tie). v3.51.1: and at
        # least one vote — 0-0 was "no tie", so the first option "won" a vote
        # nobody voted in (and became assignable as an appointment).
        vote_passed = plurality_passed(plurality_counts)
        winner = most_voted if vote_passed else None

    # Update status based on vote outcome
    # v3.13.3: failed votes are now marked 'failed', not 'removed' — the
    # legislation history page excludes 'removed' entirely, so failed votes
    # were vanishing from history and its Failed tab could never populate.
    # ('removed' is reserved for admin deletion/removal.) The `passed` bool is
    # also set now (the old set_passed() call was commented out), which the
    # history pages' fallback filters rely on.
    if vote_passed:
        legislation.status = 'passed'
    else:
        legislation.status = 'failed'
    legislation.passed = vote_passed
    legislation.save(update_fields=['status', 'passed'])
    broadcast_vote_event('closed', legislation.id)  # v3.14.0: live page update

    end_meta = {
        'result': 'passed' if vote_passed else 'failed',
        'vote_mode': legislation.vote_mode,
        'anonymous': legislation.anonymous_vote,
        'total_votes': total_votes,
    }
    if not legislation.anonymous_vote:
        end_meta['vote_breakdown'] = vote_breakdown
    ActivityLog.log_activity(
        action_type='vote_ended',
        user=user,
        description=f'{user.name} ended voting on "{legislation.title}" — {"Passed" if vote_passed else "Did Not Pass"}',
        request=request,
        object_type='Legislation',
        object_id=legislation.id,
        object_repr=legislation.title,
        metadata=end_meta,
    )

    # Send in-app notification to all users who voted
    try:
        voter_user_ids = votes.values_list('user', flat=True)
        voter_users = ParliamentUser.objects.filter(pk__in=voter_user_ids)
        result_text = 'Passed' if vote_passed else 'Did Not Pass'
        notify_users(
            voter_users,
            'vote_ended',
            f'Vote Ended: {legislation.title} — {result_text}',
            link=f'/legislation/detail/{legislation.pk}/',
            source_type='Legislation',
            source_id=legislation.id,
        )
    except Exception as e:
        logger.error(f"Failed to create vote-ended notifications: {e}", exc_info=True)

    return {
        'votes': votes,
        'vote_summary': vote_summary,
        'passed': vote_passed,
        'total_votes': total_votes,
        'yes_votes': yes_votes,
        'no_votes': no_votes,
        'abstain_votes': abstain_votes,
        'yes_percentage': yes_percentage,
        'required_pct': required_pct,
        'vote_breakdown': vote_breakdown,
        'winner': winner,
    }


def runoff_refusal(original):
    """Why ``create_runoff`` would refuse ``original`` (the website's messages), or ''."""
    if original.vote_mode != 'plurality':
        return "Runoff votes can only be created for plurality votes."
    if not original.plurality_runoff_enabled:
        return "Runoff voting is not enabled for this legislation."
    if not original.voting_closed:
        return "The original vote must be closed before creating a runoff."
    if original.runoff_votes.exists():
        return "A runoff vote has already been created for this legislation."
    if len(original.get_top_options_for_runoff()) < 2:
        return "Not enough options for a runoff vote."
    return ''


def create_runoff(original, user, request):
    """
    A runoff from a closed plurality vote, with its top options
    (``create_runoff``; the uploader or an admin). Raises ChapterVoteError.
    Returns (runoff, top_options).
    """
    from src.models import ActivityLog, Legislation
    refusal = runoff_refusal(original)
    if refusal:
        raise ChapterVoteError(refusal)
    top_options = original.get_top_options_for_runoff()

    runoff = Legislation.objects.create(
        title=f"Runoff: {original.title}",
        description=f"Runoff vote for: {original.description}\n\nTop {len(top_options)} options from original vote.",
        document=None,
        posted_by=user,
        available_at=timezone.now(),
        voting_starts_at=timezone.now(),
        anonymous_vote=original.anonymous_vote,
        allow_abstain=original.allow_abstain,
        vote_mode='plurality',
        plurality_options=top_options,
        plurality_votes_allowed=1,  # Runoff is typically single vote
        plurality_runoff_enabled=False,  # No nested runoffs
        plurality_is_runoff=True,
        plurality_parent=original,
    )

    ActivityLog.log_activity(
        action_type='legislation_created',
        user=user,
        description=f'{user.name} created runoff vote for "{original.title}"',
        request=request,
        object_type='Legislation',
        object_id=runoff.id,
        object_repr=runoff.title,
        metadata={
            'is_runoff': True,
            'parent_legislation_id': original.id,
            'parent_title': original.title,
            'runoff_options': top_options,
        },
    )
    return runoff, top_options


def runoff_offered(legislation):
    """The result page's "Create Runoff Vote" button (a closed tie, not itself a runoff)."""
    return bool(legislation.vote_mode == 'plurality' and legislation.plurality_runoff_enabled
                and legislation.has_plurality_tie() and not legislation.plurality_is_runoff)


def reopen(legislation, user):
    """
    Reopen a closed vote (``reopen_legislation``: officers, and only the
    uploader). A passed bill can't be reopened. Raises ChapterVoteError.
    """
    # Prevent reopening if already passed
    if legislation.status == 'passed':
        raise ChapterVoteError("This legislation has already passed and cannot be reopened.")
    if user != legislation.posted_by:
        raise ChapterVoteError("Only the uploader can reopen this legislation.", code='forbidden')

    # v3.13.3: also reset status — end_vote leaves it 'failed'/'passed', and
    # the vote page excludes those, so a reopened vote never reappeared on the
    # vote page. 'draft' is the open status. voting_ended_at is cleared so the
    # tally poller doesn't treat it as just-closed.
    legislation.voting_closed = False  # Reopen the voting
    legislation.status = 'draft'
    legislation.voting_ended_at = None
    legislation.save(update_fields=['voting_closed', 'status', 'voting_ended_at'])


def open_now(legislation, user, mode, request):
    """
    The "Now" buttons (``open_legislation_now``; the uploader or an admin).
    ``mode='show'`` makes it available now; anything else also starts voting.
    Raises ChapterVoteError when voting has ended. Returns (level, message):
    level is 'success', or 'info' when nothing needed to change.
    """
    from src.models import ActivityLog
    from src.utils.vote_events import broadcast_vote_event
    if legislation.voting_closed:
        raise ChapterVoteError("Voting on this legislation has already ended.")

    mode = 'show' if mode == 'show' else 'open'
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

    if not update_fields:
        return 'info', "This legislation is already open."

    legislation.save(update_fields=update_fields)
    ActivityLog.log_activity(
        action_type='legislation_created',
        user=user,
        description=(
            f'{user.name} opened "{legislation.title}" early '
            f'({"voting started" if mode == "open" else "made visible"} via Now button)'
        ),
        request=request,
        object_type='Legislation',
        object_id=legislation.id,
        object_repr=legislation.title,
        metadata={'action': 'open_now', 'mode': mode,
                  'fields_advanced': update_fields},
    )
    broadcast_vote_event('opened', legislation.id)
    if legislation.voting_has_started():
        return 'success', f'"{legislation.title}" is now open for voting.'
    return 'success', f'"{legislation.title}" is now visible; voting opens at its scheduled time.'


# ── Uploading ───────────────────────────────────────────────────────────────

def _aware(raw):
    """A form date: the website sends local times, the app ISO 8601 with an offset."""
    try:
        parsed = parse_datetime(raw) if isinstance(raw, str) and raw else None
    except ValueError:
        parsed = None
    if parsed is None:
        return None
    return parsed if timezone.is_aware(parsed) else make_aware(parsed)


def _int(raw, message):
    try:
        return int(raw)
    except (ValueError, TypeError):
        raise ChapterVoteError(message)


def create_chapter_legislation(user, data, document, request):
    """
    The vote page's "Upload Legislation" form (``upload_chapter_legislation``;
    chairs and officers). ``data`` has the form's keys; ``document`` is the
    already-validated upload or None. Raises ChapterVoteError. Returns
    (legislation, message).
    """
    from src.models import ActivityLog, Legislation
    from src.utils.vote_events import broadcast_vote_event

    title = data.get('title')
    description = data.get('description')
    anonymous = data.get('anonymous') == 'on'
    allow_abstain = not (data.get('remove_abstain') == 'on')
    required_percentage = _int(data.get('required_percentage', 51), 'Choose a valid percentage.')

    available_at = _aware(data.get('available_at'))
    # Parse voting_starts_at (optional - defaults to available_at if not set)
    voting_starts_at = _aware(data.get('voting_starts_at')) if data.get('voting_starts_at') else None

    # v3.14.0: the "Now" buttons send an explicit is_now flag that the
    # SERVER resolves — the browser-filled text is cosmetic. This makes
    # "open it now" immune to any skew between the user's device clock /
    # timezone and the server's (reported live: Now-filled votes landing
    # minutes-to-hours in the future and never opening).
    if data.get('available_at_is_now') == '1':
        available_at = timezone.now()
    if data.get('voting_starts_at_is_now') == '1' and voting_starts_at:
        voting_starts_at = timezone.now()

    # v3.13.3: voting-mode toggle. 'separate' + no start time means voting
    # stays closed until the author hits "Open Voting Now". Anything else
    # (including forms that don't send the field — committee push, older
    # clients) keeps the historical unified behavior: blank start = voting
    # opens at available_at.
    voting_manual_open = (
        data.get('voting_mode_choice') == 'separate'
        and not voting_starts_at
    )

    # Parse voting_ends_at (optional)
    voting_ends_at = _aware(data.get('voting_ends_at')) if data.get('voting_ends_at') else None

    vote_mode = data.get('vote_mode', 'percentage')
    plurality_options = []
    required_number = None
    plurality_votes_allowed = 1
    plurality_runoff_enabled = False
    plurality_runoff_count = 2

    if vote_mode == 'plurality':
        # Support up to 10 plurality options
        for i in range(1, MAX_PLURALITY_OPTIONS + 1):
            val = data.get(f'plurality_option_{i}')
            if val and val.strip():
                plurality_options.append(val.strip())

        if len(plurality_options) < 2:
            raise ChapterVoteError("Plurality voting requires at least two options.")

        # Parse multi-select settings
        votes_allowed_raw = data.get('plurality_votes_allowed', '1')
        try:
            plurality_votes_allowed = max(1, min(10, int(votes_allowed_raw)))
        except (ValueError, TypeError):
            plurality_votes_allowed = 1

        # Ensure votes_allowed doesn't exceed number of options
        plurality_votes_allowed = min(plurality_votes_allowed, len(plurality_options))

        # Parse runoff settings
        plurality_runoff_enabled = data.get('plurality_runoff_enabled') == 'on'
        if plurality_runoff_enabled:
            runoff_count_raw = data.get('plurality_runoff_count', '2')
            try:
                plurality_runoff_count = max(2, min(len(plurality_options), int(runoff_count_raw)))
            except (ValueError, TypeError):
                plurality_runoff_count = 2

    elif vote_mode == 'piecewise':
        piecewise_error = "Piecewise voting requires a valid number of required votes (at least 1)."
        required_number = data.get('required_number')
        if not required_number or _int(required_number, piecewise_error) < 1:
            raise ChapterVoteError(piecewise_error)
        required_number = int(required_number)

    # v3.13.3: document is optional when the description is detailed
    # (20+ characters — same rule as the legislation tracker's add form)
    # or for plurality votes.
    detailed_description = description and len(description.strip()) >= 20
    if not (title and description and available_at and (
            document or vote_mode == 'plurality' or detailed_description)):
        # v3.13.3: explain instead of silently ignoring the POST
        raise ChapterVoteError(
            "Legislation was NOT saved — title, description, and a go-live "
            "time are required. A document is also required unless the "
            "description is detailed (at least 20 characters) or it's a "
            "plurality vote."
        )

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

    function_logger.info(f"{user.username} uploaded legislation titled '{title}' (mode: {vote_mode}, required %: {required_percentage})")
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
    now = timezone.now()
    visible = timezone.localtime(available_at)
    if voting_manual_open:
        opens_text = 'when you open it (use "Open Voting Now" on the card)'
    else:
        opens = timezone.localtime(voting_starts_at or available_at)
        opens_text = ('now' if (voting_starts_at or available_at) <= now
                      else opens.strftime('%b %d at %I:%M %p'))
    msg = "Legislation uploaded — visible {} · voting opens {}.".format(
        'now' if available_at <= now else visible.strftime('%b %d at %I:%M %p'),
        opens_text,
    )
    # If anything is scheduled for later, show the server's own clock —
    # makes device-clock/timezone skew instantly visible (v3.14.0)
    if available_at > now or (
            not voting_manual_open and (voting_starts_at or available_at) > now):
        msg += " Server time is now {}.".format(
            timezone.localtime(now).strftime('%I:%M %p'))
    if available_at <= now:
        broadcast_vote_event('opened', new_legislation.id)
    return new_legislation, msg


def create_appointment(user, data, request):
    """
    The vote page's "Chair Appointment" form (``upload_legislation`` with
    ``action_type=create_appointment``; officers and chairs). ``data`` has the
    form's ``appt_*`` keys. Raises ChapterVoteError. Returns
    (legislation, message, warning or '').
    """
    from src.feature_flag_decorators import check_feature_enabled
    from src.models import Legislation, ParliamentUser, Role

    role_id = (data.get('appointment_role_id') or '').strip()
    member_id = (data.get('appointment_member_id') or '').strip()
    vote_mode = data.get('appt_vote_mode', 'percentage')
    available_at_str = (data.get('appt_available_at') or '').strip()
    voting_starts_at_str = (data.get('appt_voting_starts_at') or '').strip()
    voting_ends_at_str = (data.get('appt_voting_ends_at') or '').strip()
    required_percentage = data.get('appt_required_percentage', '51')
    required_number_str = (data.get('appt_required_number') or '').strip()
    anonymous = 'appt_anonymous' in data and check_feature_enabled('anonymous_voting')
    allow_abstain = ('appt_remove_abstain' not in data) and check_feature_enabled('abstain_voting')
    warning = ''

    # Validate role — may be an existing role ID or the sentinel "__new__"
    if not role_id:
        raise ChapterVoteError('Please select a role for the appointment.')

    if role_id == '__new__':
        new_role_name = (data.get('new_role_name') or '').strip()
        new_role_code = (data.get('new_role_code') or '').strip().upper()
        if not new_role_name or not new_role_code:
            raise ChapterVoteError('Please provide both a name and a code for the new role.')
        role, created = Role.objects.get_or_create(
            code=new_role_code,
            defaults={'name': new_role_name},
        )
        if not created and role.name != new_role_name:
            # Code already exists under a different name — just use the existing role
            warning = f'A role with code "{new_role_code}" already exists ({role.name}). Using the existing role.'
    else:
        role = Role.objects.filter(id=role_id).first() if str(role_id).isdigit() else None
        if role is None:
            raise ChapterVoteError('Selected role not found.')

    # v3.13.3: make_aware() added — these were saved naive, and the DB layer
    # interprets naive datetimes as UTC, so appointment vote open times were
    # skewed by the UTC offset (~5-6 h for America/Chicago).
    available_at = parse_datetime(available_at_str) if available_at_str else None
    if not available_at:
        raise ChapterVoteError('Please set an available date for the appointment vote.')
    if timezone.is_naive(available_at):
        available_at = timezone.make_aware(available_at)
    # v3.14.0: "Now" button — server-resolved, immune to device-clock skew
    if data.get('appt_available_at_is_now') == '1':
        available_at = timezone.now()

    voting_starts_at = parse_datetime(voting_starts_at_str) if voting_starts_at_str else None
    if voting_starts_at and timezone.is_naive(voting_starts_at):
        voting_starts_at = timezone.make_aware(voting_starts_at)
    voting_ends_at = parse_datetime(voting_ends_at_str) if voting_ends_at_str else None
    if voting_ends_at and timezone.is_naive(voting_ends_at):
        voting_ends_at = timezone.make_aware(voting_ends_at)

    # Validate appointment_member for non-plurality votes
    appointment_member = None
    if vote_mode != 'plurality':
        if not member_id:
            raise ChapterVoteError('Please select a nominee for the appointment.')
        appointment_member = ParliamentUser.objects.filter(user_id=member_id, member_status='Active').first()
        if appointment_member is None:
            raise ChapterVoteError('Selected nominee not found or is not active.')

    # Build plurality options for plurality appointments
    plurality_options = None
    plurality_votes_allowed = 1
    plurality_runoff_enabled = False
    plurality_runoff_count = 2
    if vote_mode == 'plurality':
        opts = []
        for i in range(1, MAX_PLURALITY_OPTIONS + 1):
            val = (data.get(f'appt_plurality_option_{i}') or '').strip()
            if val:
                opts.append(val)
        if len(opts) < 2:
            raise ChapterVoteError('Plurality appointment votes require at least 2 candidate options.')
        plurality_options = opts
        try:
            plurality_votes_allowed = int(data.get('appt_plurality_votes_allowed', 1))
        except (ValueError, TypeError):
            plurality_votes_allowed = 1
        plurality_runoff_enabled = 'appt_plurality_runoff_enabled' in data
        try:
            plurality_runoff_count = int(data.get('appt_plurality_runoff_count', 2))
        except (ValueError, TypeError):
            plurality_runoff_count = 2

    required_number = None
    if vote_mode == 'piecewise':
        required_number = _int(required_number_str, 'Please enter the required number of yes votes.')

    # Auto-generate title
    title = (data.get('appt_title') or '').strip()
    if not title:
        if appointment_member:
            title = f'Chair Appointment: {appointment_member.name} as {role.name}'
        else:
            title = f'Chair Appointment: {role.name}'

    description = (data.get('appt_description') or '').strip()
    if not description:
        if appointment_member:
            description = f'Chapter vote on the appointment of {appointment_member.name} as {role.name}.'
        else:
            description = f'Chapter vote on the appointment of a new {role.name}.'

    legislation = Legislation(
        title=title,
        description=description,
        posted_by=user,
        legislation_type='appointment',
        appointment_role=role,
        appointment_member=appointment_member,
        available_at=available_at,
        voting_starts_at=voting_starts_at,
        voting_ends_at=voting_ends_at,
        vote_mode=vote_mode,
        required_percentage=required_percentage,
        required_number=required_number,
        anonymous_vote=anonymous,
        allow_abstain=allow_abstain,
        plurality_options=plurality_options,
        plurality_votes_allowed=plurality_votes_allowed,
        plurality_runoff_enabled=plurality_runoff_enabled,
        plurality_runoff_count=plurality_runoff_count,
    )
    legislation.save()

    announce_if_available(legislation)
    return legislation, f'Appointment vote for {role.name} created successfully.', warning


def announce_if_available(legislation):
    """
    Announce a newly uploaded bill — but only if it is actually available.

    ⚠️ v3.19.0 CHANGED THE TIMING HERE, and it is a deliberate behaviour change.

    This used to fire unconditionally the moment the row was saved. `available_at`
    is a required field that officers routinely set in the future, so a bill dated
    three weeks out pushed "New Legislation: …" to every active member for
    something none of them could open — and nothing announced it again when it
    *did* become available. The notification was either premature or absent.

    Now: available already → announce inline (unchanged from the member's point
    of view). Dated in the future → say nothing, and let
    `tasks.notify_available_legislation` announce it the minute it lands.

    Both paths go through `announce_legislation_availability`, which stamps
    `availability_notified_at` as its claim, so the inline call and the periodic
    task cannot both send. Do not re-add a bare `notify_all_active_members` call
    here: it would bypass the stamp and the task would announce the bill a
    second time when `available_at` passed.
    """
    from src.tasks.votes import announce_legislation_availability

    try:
        if not legislation.is_available():
            logger.info(
                'Legislation id=%s ("%s") is dated %s — deferring the chapter '
                'notification to tasks.notify_available_legislation.',
                legislation.pk, legislation.title, legislation.available_at,
            )
            return
        announce_legislation_availability(legislation)
    except Exception as e:
        logger.error('Failed to send legislation notifications: %s', e, exc_info=True)


# ── Editing ─────────────────────────────────────────────────────────────────

def edit_access(user, legislation):
    """
    ``edit_legislation``'s gate, in its order: (is_author, is_officer,
    is_admin). Raises ChapterVoteError — ``code='forbidden'`` for its 403s,
    ``'passed'`` / ``'failed'`` / ``'voting_started'`` for the refusals the
    website redirects with a message.
    """
    is_author = user == legislation.posted_by or legislation.co_authors.filter(pk=user.pk).exists()
    is_officer = user.member_type in ['Chair', 'Officer']
    is_admin = user.is_admin
    is_scheduled = not legislation.is_available()
    # Tabled and pending legislation can be edited by author, officers, or admins
    is_editable_status = legislation.status in ['tabled', 'pending']

    if not (is_author or is_officer or is_admin):
        raise ChapterVoteError("You don't have permission to edit this legislation.", code='forbidden')
    if legislation.status == 'passed':
        raise ChapterVoteError(
            "This legislation has passed and cannot be edited. Please submit a new version.", code='passed')
    if legislation.status == 'failed':
        raise ChapterVoteError(
            "This legislation has failed and cannot be edited. Please submit a new version.", code='failed')
    # Check if voting has already started (cannot edit after voting begins)
    # UNLESS it's tabled or pending (these can always be edited)
    if not is_editable_status and legislation.voting_has_started() and not is_admin:
        raise ChapterVoteError(
            "This legislation cannot be edited because voting has already started.", code='voting_started')
    # Authors can only edit scheduled legislation, unless it's tabled/pending
    if is_author and not is_officer and not is_admin and not is_scheduled and not is_editable_status:
        raise ChapterVoteError(
            "You can only edit scheduled legislation before it becomes available.", code='forbidden')
    return is_author, is_officer, is_admin


def mark_voted(legislation, user, data):
    """
    The edit page's "Mark as voted" (officers and admins): record an outcome
    reached off the app, with optional historical counts. Returns the outcome.
    """
    outcome = data.get('mark_voted_outcome', 'passed')
    if outcome not in ('passed', 'failed'):
        outcome = 'passed'

    voted_at_str = (data.get('mark_voted_date') or '').strip()
    voted_at = None
    if voted_at_str:
        try:
            voted_at = parse_datetime(voted_at_str)
            # v3.13.3: naive datetimes are stored as UTC by the DB
            # layer — interpret the entered time in the local timezone
            if voted_at and timezone.is_naive(voted_at):
                voted_at = timezone.make_aware(voted_at)
        except Exception:  # nosec B110 # an unreadable date falls back to now, as on the website
            pass
    if not voted_at:
        voted_at = timezone.now()

    legislation.status = outcome
    legislation.passed = (outcome == 'passed')
    legislation.voting_closed = True
    legislation.voting_ended_at = voted_at

    try:
        yes_votes = int(data.get('mark_voted_yes') or 0)
        no_votes = int(data.get('mark_voted_no') or 0)
        abstain_votes = int(data.get('mark_voted_abstain') or 0)
        if yes_votes or no_votes or abstain_votes:
            legislation.historical_yes_votes = yes_votes
            legislation.historical_no_votes = no_votes
            legislation.historical_abstain_votes = abstain_votes
    except (ValueError, TypeError):
        pass

    legislation.save()
    logger.info(
        f"{user.username} marked legislation '{legislation.title}' (ID: {legislation.id}) "
        f"as {outcome} via mark_voted action"
    )
    return outcome


def transfer_authorship(legislation, transfer_to_id):
    """
    Hand the bill to another member; the old author becomes a co-author.
    Raises ChapterVoteError. Returns the website's success message.
    """
    from src.models import ParliamentUser
    transfer_to_id = (transfer_to_id or '').strip()
    if not transfer_to_id:
        raise ChapterVoteError("Please select a member to transfer authorship to.")
    new_author = ParliamentUser.objects.filter(user_id=transfer_to_id).first()
    if not new_author:
        raise ChapterVoteError("Member not found.")
    old_author = legislation.posted_by
    legislation.posted_by = new_author
    # Remove new author from co_authors if they were one
    legislation.co_authors.remove(new_author)
    # Add old author as co-author
    legislation.co_authors.add(old_author)
    legislation.save(update_fields=['posted_by'])
    return f"Authorship transferred to {new_author.name}. {old_author.name} has been added as a co-author."


def save_edit(form, legislation, new_status, co_author_ids):
    """
    Save a valid ``LegislationForm`` bound to ``legislation`` (the edit page):
    the voting feature flags, a tabled/pending bill's status change, and the
    co-authors. Returns (saved legislation, the website's success message).
    """
    from src.feature_flag_decorators import check_feature_enabled
    from src.models import ParliamentUser

    # The status before the form touches the instance (ModelForm validation
    # writes the cleaned fields onto it; `status` isn't one of them).
    status_before = legislation.status
    saved = form.save(commit=False)
    if not check_feature_enabled('anonymous_voting'):
        saved.anonymous_vote = False
    if not check_feature_enabled('abstain_voting'):
        saved.allow_abstain = False

    message = "Legislation has been updated."
    # Handle status change for tabled/pending legislation
    if status_before in ['tabled', 'pending'] and new_status in ['pending', 'active', 'tabled']:
        saved.status = new_status
        # If changing to active, reopen voting
        if new_status == 'active':
            saved.voting_closed = False
            saved.voting_ended_at = None
            # Set voting_starts_at to now if not set
            if not saved.voting_starts_at:
                saved.voting_starts_at = timezone.now()
            message = "Legislation has been updated and reintroduced to voting."
        elif new_status == 'tabled':
            saved.voting_closed = True
            saved.voting_ended_at = timezone.now()
            message = "Legislation has been updated and tabled."
        elif new_status == 'pending':
            saved.voting_closed = False
            saved.voting_ended_at = None
            message = "Legislation has been updated and set to pending."

    saved.save()

    # Handle co-authors update
    co_author_users = ParliamentUser.objects.filter(user_id__in=co_author_ids)
    saved.co_authors.set(co_author_users)
    return saved, message


def save_new_version(form, previous, user):
    """
    ``submit_new_version`` (officers, and only the uploader): a valid
    ``LegislationForm`` becomes a new bill by ``user``. Nothing is announced
    or broadcast, as on the website.
    """
    new_legislation = form.save(commit=False)
    new_legislation.posted_by = user
    # Optionally: mark this as a new version of the old legislation
    new_legislation.previous_version = previous
    new_legislation.save()
    return new_legislation


# ── Legislation Tracker ─────────────────────────────────────────────────────

TRACKER_STATUSES = ['pending', 'active', 'passed', 'failed', 'tabled']


def add_tracker_legislation(user, data, document):
    """
    The tracker's "Add Legislation" form (``add_legislation``; officers and
    admins): a bill recorded with its outcome, optionally its vote counts.
    ``document`` is the upload or None. Raises ChapterVoteError.
    """
    from src.models import Legislation

    title = (data.get('title') or '').strip()
    status = data.get('status', 'pending')
    description = (data.get('description') or '').strip()
    include_votes = data.get('include_votes') == 'on'

    # Validation
    if not title:
        raise ChapterVoteError('Title is required.')
    if not document and len(description) < 20:
        raise ChapterVoteError('Please provide either a document or a detailed description (at least 20 characters).')

    # Validate status
    if status not in TRACKER_STATUSES:
        status = 'pending'

    # Determine if voting is closed based on status
    # Tabled legislation also has voting closed (it's on hold, not being voted on)
    voting_closed = status in ['passed', 'failed', 'tabled']
    passed = status == 'passed'

    # Create the legislation
    now = timezone.now()
    legislation = Legislation.objects.create(
        title=title,
        description=description,
        document=document,
        status=status,
        posted_by=user,
        available_at=now,
        voting_starts_at=now,
        voting_closed=voting_closed,
        passed=passed,
        required_percentage=data.get('required_percentage', '51'),
    )

    # If voting is closed (passed, failed, or tabled), set voting_ended_at
    if voting_closed:
        legislation.voting_ended_at = now
        legislation.save(update_fields=['voting_ended_at'])

    # For pending status, set voting_starts_at to future (so it doesn't appear as active)
    if status == 'pending':
        legislation.voting_starts_at = None
        legislation.save(update_fields=['voting_starts_at'])

    # Handle vote results if included
    if include_votes and voting_closed:
        try:
            yes_votes = int(data.get('yes_votes', 0))
            no_votes = int(data.get('no_votes', 0))
            abstain_votes = int(data.get('abstain_votes', 0))

            # Store historical vote counts on the legislation
            legislation.historical_yes_votes = yes_votes
            legislation.historical_no_votes = no_votes
            legislation.historical_abstain_votes = abstain_votes
            legislation.save(update_fields=['historical_yes_votes', 'historical_no_votes', 'historical_abstain_votes'])
        except (ValueError, TypeError):
            pass  # Ignore invalid vote counts

    logger.info(f"{user.username} added legislation: {title} with status {status}")
    return legislation


def update_note(legislation, user, note):
    """The tracker's officer note (``update_legislation_note``). The result is untouched."""
    note = (note or '').strip()
    legislation.admin_note = note
    legislation.save(update_fields=['admin_note'])
    logger.info(f"{user.username} updated note on legislation '{legislation.title}' (ID: {legislation.id})")
    return note


# ── Appointments ────────────────────────────────────────────────────────────

def not_assignable(legislation):
    """Why this appointment's role can't be assigned now, or '' if it can."""
    if legislation.appointment_assigned:
        return f'"{legislation.title}" has already been assigned.'
    if not legislation.voting_closed:
        return f'Voting on "{legislation.title}" hasn\'t closed yet.'
    if not legislation.passed:
        return f'"{legislation.title}" did not pass, so there is no role to assign.'
    return ''


def winning_option(legislation):
    """A plurality appointment's leading option, as the assign page shows it."""
    results = legislation.get_plurality_results()
    return results[0]['option'] if results else None


def assign_appointment(legislation, user, member, request):
    """
    Give ``member`` the appointment's role (``assign_appointment``; officers
    and chairs). Admin roles stay admin-only; checked again under a row lock
    so it happens once. Raises ChapterVoteError. Returns the success message.
    """
    from django.db import transaction
    from src.models import ActivityLog, Legislation
    from src.permissions import role_change_error

    role = legislation.appointment_role
    # v3.50.1 — same rule as the role pages: an admin role is admin-only.
    role_error = role_change_error(user, role, member)
    if role_error:
        raise ChapterVoteError(role_error, code='forbidden')

    # v3.51.0 — re-checked under a row lock, so two officers (or a
    # double-submit) can't both assign it.
    with transaction.atomic():
        locked = Legislation.objects.select_for_update().get(pk=legislation.pk)
        refusal = not_assignable(locked)
        if refusal:
            raise ChapterVoteError(refusal, code='conflict')
        locked.appointment_assigned = True
        locked.save(update_fields=['appointment_assigned'])
        legislation.appointment_assigned = True

    # Assign role
    member.roles.add(role)

    # Update member_type to Chair if currently Member
    if member.member_type == 'Member':
        member.member_type = 'Chair'
        member.save(update_fields=['member_type'])

    ActivityLog.log_activity(
        action_type='other',
        user=user,
        description=(
            f'{user.get_display_name()} assigned {member.name} '
            f'as {role.name} following appointment vote "{legislation.title}"'
        ),
        request=request,
        metadata={
            'action': 'assign_appointment',
            'legislation_id': legislation.id,
            'legislation_title': legislation.title,
            'member_user_id': member.user_id,
            'member_name': member.name,
            'role_id': role.id,
            'role_name': role.name,
        }
    )
    logging.getLogger('src.view.assign_appointment').info(
        'User %s assigned %s to role %s via appointment vote %s',
        user.user_id, member.user_id, role.id, legislation.id
    )
    return f'{member.name} has been assigned as {role.name}.'
