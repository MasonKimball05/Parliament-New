"""
Casting a chapter ballot (v3.55.0).

The one implementation behind the website's vote form (``src/view/vote_view.py``
``cast_vote``) and the app's ``votes/<id>/cast/`` (``src/mobile/views_votes.py``).
Moved here from ``cast_vote`` so the eligibility rule, the choice validation,
the locked insert and the receipt can't drift between the two. Identity
re-authentication is NOT here: each caller confirms it its own way (web: form
password or session passkey grant; app: body password or a device-bound grant
ticket) BEFORE calling ``cast``.

Callers:

    ballots.check_eligible(user)                    # before re-auth, as the web did
    result = ballots.cast(user, legislation, choices, request)

Both raise ``BallotError`` (``.code`` for the app, ``str()`` for the web's
message) instead of returning an error.
"""
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from src.models import ActivityLog, Attendance, Legislation, Vote
from src.utils.vote_events import broadcast_vote_event
from src.utils.vote_receipts import make_receipt

#: How recent a present/late attendance mark must be to vote.
ATTENDANCE_WINDOW = timedelta(hours=3)

ALREADY_VOTED_MSG = "You have already voted on this legislation."


class BallotError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def attendance_ok(user):
    """Present/late within the last 3 hours."""
    return Attendance.objects.filter(
        user=user,
        created_at__gte=timezone.now() - ATTENDANCE_WINDOW,
        status__in=['present', 'late'],
    ).exists()


def can_cast_now(user):
    """Voting-eligible member type AND marked present recently."""
    return user.can_vote and attendance_ok(user)


def check_eligible(user):
    if not user.can_vote:
        raise BallotError('not_eligible', "Your membership type is not eligible to vote.")
    if not attendance_ok(user):
        raise BallotError(
            'not_present',
            "Your vote was NOT counted: you aren't marked present within "
            "the last 3 hours. Ask an officer to mark your attendance, "
            "then vote again.")


def is_multi_select(legislation):
    return legislation.vote_mode == 'plurality' and (legislation.plurality_votes_allowed or 1) > 1


def _validate(legislation, choices):
    """Raise BallotError unless ``choices`` (a list of str) is a valid ballot."""
    if is_multi_select(legislation):
        if len(choices) < 1:
            raise BallotError('invalid_choice', "Please select at least one option.")
        if len(choices) > legislation.plurality_votes_allowed:
            raise BallotError('invalid_choice',
                              f"You can only select up to {legislation.plurality_votes_allowed} options.")
        if len(set(choices)) != len(choices) or any(
                c not in (legislation.plurality_options or []) for c in choices):
            raise BallotError('invalid_choice', "Invalid vote option.")
        return
    if len(choices) != 1 or not choices[0]:
        raise BallotError('invalid_choice', "Please select a vote option.")
    choice = choices[0]
    if legislation.vote_mode == 'plurality':
        if choice not in (legislation.plurality_options or []):
            raise BallotError('invalid_choice', "Invalid vote option.")
    elif choice not in ('yes', 'no', 'abstain'):
        raise BallotError('invalid_choice', "Invalid vote option.")
    if choice == 'abstain' and not legislation.allow_abstain:
        raise BallotError('invalid_choice', "Abstaining is not allowed on this vote.")


def _record_ballot(user, legislation, choices):
    """P0: create one Vote row per choice, all-or-nothing, serialized per
    legislation. The row lock makes the already-voted check and the inserts
    one step, so two concurrent submissions can't both record a ballot; the
    (user, legislation, vote_choice) unique constraint is the backstop.
    Returns the created rows, or None if the user had already voted."""
    try:
        with transaction.atomic():
            Legislation.objects.select_for_update().only('pk').get(pk=legislation.pk)
            if Vote.objects.filter(user=user, legislation=legislation).exists():
                return None
            return [
                Vote.objects.create(user=user, legislation=legislation, vote_choice=choice)
                for choice in choices
            ]
    except IntegrityError:
        return None


def cast(user, legislation, choices, request=None):
    """
    Record ``user``'s ballot. ``choices`` is a list of option strings (one
    for every mode but multi-select plurality). Returns
    ``{'votes': [...], 'receipt': <token>}``. ``request`` (an HttpRequest) is
    only for the activity log's IP / user agent.
    """
    if Vote.objects.filter(user=user, legislation=legislation).exists():
        raise BallotError('already_voted', ALREADY_VOTED_MSG)
    if legislation.voting_closed:
        raise BallotError('voting_closed', "Voting on this legislation has ended.")
    if not legislation.voting_has_started():
        raise BallotError('not_started', "Voting has not started yet on this legislation.")
    # v3.55.0: the web form only checked the two flags above, so a hand-built
    # POST could vote on a tabled, removed or pending bill whose voting was
    # never marked closed. Same rule as the Home page's "pending votes".
    if not Legislation.objects.open_for_voting().filter(pk=legislation.pk).exists():
        raise BallotError('not_open', "This legislation isn't open for voting.")

    choices = [str(c) for c in choices]
    _validate(legislation, choices)

    votes = _record_ballot(user, legislation, choices)
    if votes is None:
        raise BallotError('already_voted', ALREADY_VOTED_MSG)

    # Never the choice on an anonymous bill: this logger feeds django_actions.log,
    # which /officers/system-logs/ shows to every officer (10-28-26).
    _choice = ('(anonymous)' if legislation.anonymous_vote
               else choices if is_multi_select(legislation) else repr(choices[0]))
    logging.getLogger('function_calls').info(
        f"{user.username} voted {_choice} "
        f"on '{legislation.title}' (ID: {legislation.id}) at {timezone.now()}")
    meta = {'legislation_id': legislation.id, 'vote_mode': legislation.vote_mode,
            'anonymous': bool(legislation.anonymous_vote)}
    if legislation.anonymous_vote:
        if is_multi_select(legislation):
            description = f'{user.name} cast {len(choices)} vote(s) on "{legislation.title}" (anonymous)'
            meta['choices_count'] = len(choices)
        else:
            description = f'{user.name} cast a vote on "{legislation.title}" (anonymous)'
    elif is_multi_select(legislation):
        description = f'{user.name} voted for {choices} on "{legislation.title}"'
        meta['vote_choices'] = choices
    else:
        description = f'{user.name} voted "{choices[0]}" on "{legislation.title}"'
        meta['vote_choice'] = choices[0]
    ActivityLog.log_activity(
        action_type='vote_cast',
        user=user,
        description=description,
        request=request,
        object_type='Legislation',
        object_id=legislation.id,
        object_repr=legislation.title,
        metadata=meta,
    )
    # v3.14.0: mint the receipt AT CAST TIME from the rows just created — the
    # only moment the choices are known-good.
    receipt = make_receipt(user, legislation, votes, cast_at=votes[0].cast_at)
    broadcast_vote_event('tally', legislation.id)
    return {'votes': votes, 'receipt': receipt}


# ── Committee ballots ───────────────────────────────────────────────────────
# Same shape as the chapter functions above, moved out of
# src/view/committee/vote.py (v3.55.0). Differences from chapter ballots, all
# as the web had them: eligibility is the committee's voting_members (not
# member type), there is no receipt, and the lock re-checks voting_closed
# because a chair can end the vote mid-request.

def check_committee_eligible(user, committee):
    if not committee.voting_members.filter(pk=user.pk).exists():
        raise BallotError('not_eligible', "You aren't a voting member of this committee.")
    if not attendance_ok(user):
        raise BallotError(
            'not_present',
            "Your vote was NOT counted: you aren't marked present within "
            "the last 3 hours. Ask the chair to mark your attendance, "
            "then vote again.")


class _VotingClosed(Exception):
    pass


def cast_committee(user, committee, legislation, choices, request=None):
    """Record a committee ballot. ``legislation`` must belong to ``committee``."""
    from src.models import CommitteeLegislation, CommitteeVote
    # P0: scope to the committee — a voting member of one committee must not
    # be able to cast on another's legislation.
    if legislation.committee_id != committee.pk:
        raise BallotError('not_open', "This legislation isn't open for voting.")
    if CommitteeVote.objects.filter(user=user, legislation=legislation).exists():
        raise BallotError('already_voted', ALREADY_VOTED_MSG)
    if legislation.voting_closed:
        raise BallotError('voting_closed', "Voting on this legislation has ended.")
    if not legislation.voting_has_started():
        raise BallotError('not_started', "Voting has not started yet on this legislation.")
    # v3.55.0: a removed bill whose voting was never closed took ballots.
    if legislation.status == 'removed':
        raise BallotError('not_open', "This legislation isn't open for voting.")

    choices = [str(c) for c in choices]
    _validate(legislation, choices)

    # P0: serialize check-then-create on the legislation row so two
    # concurrent submissions can't both record a ballot; the
    # (user, legislation, vote_choice) unique constraint backs it up.
    try:
        with transaction.atomic():
            locked = CommitteeLegislation.objects.select_for_update().get(pk=legislation.pk)
            if locked.voting_closed:
                raise _VotingClosed
            if CommitteeVote.objects.filter(user=user, legislation=legislation).exists():
                raise IntegrityError('ballot already recorded')
            votes = [CommitteeVote.objects.create(user=user, legislation=legislation, vote_choice=c)
                     for c in choices]
    except _VotingClosed:
        raise BallotError('voting_closed', "Voting on this legislation has ended.")
    except IntegrityError:
        raise BallotError('already_voted', ALREADY_VOTED_MSG)

    multi = is_multi_select(legislation)
    _choice = ('(anonymous)' if legislation.anonymous_vote
               else choices if multi else repr(choices[0]))
    logging.getLogger('function_calls').info(
        f"{user.username} voted {_choice} on committee "
        f"legislation '{legislation.title}' (ID: {legislation.id})")
    meta = {'legislation_id': legislation.id, 'vote_mode': legislation.vote_mode,
            'anonymous': bool(legislation.anonymous_vote), 'committee': committee.code}
    if legislation.anonymous_vote:
        if multi:
            description = (f'{user.name} cast {len(choices)} vote(s) on committee legislation '
                           f'"{legislation.title}" (anonymous)')
            meta['choices_count'] = len(choices)
        else:
            description = f'{user.name} cast a vote on committee legislation "{legislation.title}" (anonymous)'
    elif multi:
        description = f'{user.name} voted for {choices} on committee legislation "{legislation.title}"'
        meta['vote_choices'] = choices
    else:
        description = f'{user.name} voted "{choices[0]}" on committee legislation "{legislation.title}"'
        meta['vote_choice'] = choices[0]
    ActivityLog.log_activity(
        action_type='vote_cast',
        user=user,
        description=description,
        request=request,
        object_type='CommitteeLegislation',
        object_id=legislation.id,
        object_repr=legislation.title,
        metadata=meta,
    )
    return {'votes': votes}
