"""
Recording a slating vote (10-29-26) — the one copy, used by the web's
``slating_vote`` / ``individual_vote`` and the app's slating screen
(``src/mobile/views_slating.py``). Moved out of ``src/view/slating/vote.py``
unchanged in behaviour:

* a slate ballot is approve / reject (with at least one objected position on
  this slate) / abstain when the period allows it;
* individual ballots need a valid choice for every position on this round's
  ballot (src/slating_rounds.py: pass/fail, or pick-one for a run-off or floor
  nominations) not yet voted on this round;
* both are written under a lock on the period row, re-checking "already voted"
  inside it (v4.0.0: the ballots' unique constraint can't catch a double slate
  ballot, and a double individual submit used to 500), as a SlatingBallot (who
  voted) and a separate SlatingVote (what was chosen) that share no key, so the
  choice can't be traced back to the voter.

Eligibility (``user.can_vote``, voting open, marked present, a slate to vote
on) and re-authentication stay with the caller, which knows how it asks.
"""
import hashlib
import secrets

from django.db import transaction
from django.utils import timezone

from src.models import SlatingActivity, SlatingAttendance, SlatingBallot, SlatingPeriod, SlatingVote, Slate
from src.models.users import member_defer


class SlatingBallotError(Exception):
    def __init__(self, code, message, messages=None):
        super().__init__(message)
        self.code = code
        self.messages = messages or [message]


def primary_slate(period):
    return Slate.objects.filter(period=period, is_approved=True, slate_type='primary').first()


def slate_candidates(slate):
    return (slate.candidates.select_related('position', 'application__applicant', 'write_in_member')
            .defer(*member_defer('application__applicant', 'write_in_member'))
            .order_by('display_order'))


def is_present(period, user):
    return SlatingAttendance.objects.filter(period=period, member=user).exists()


def has_slate_ballot(period, user, attempt=None):
    attempt = period.current_voting_attempt if attempt is None else attempt
    return SlatingBallot.objects.filter(period=period, voter=user, voting_attempt=attempt,
                                        vote_type='slate').exists()


def cast_slate(user, period, slate, candidates, vote_choice, rejected_raw):
    """Record one slate vote. `rejected_raw` is a list of position ids (strings or ints)."""
    valid = ['approve', 'reject'] + (['abstain'] if period.allow_abstain else [])
    if vote_choice not in valid:
        raise SlatingBallotError('invalid_choice', 'Invalid vote choice.')
    rejected = []
    if vote_choice == 'reject':
        # v4.0.0: only this slate's positions; junk is ignored, not a 500.
        on_slate = {c.position_id for c in candidates}
        raw = [str(p) for p in (rejected_raw or [])]
        rejected = sorted({int(p) for p in raw if p.isdigit() and int(p) in on_slate})
        if not rejected:
            raise SlatingBallotError('no_positions', 'Please select at least one position you are objecting to.')

    attempt = period.current_voting_attempt
    with transaction.atomic():
        SlatingPeriod.objects.select_for_update().only('pk').get(pk=period.pk)
        if has_slate_ballot(period, user, attempt):
            raise SlatingBallotError('already_voted', 'You have already voted in this round.')
        SlatingBallot.objects.create(
            period=period, voter=user, voting_attempt=attempt, vote_type='slate',
            ballot_hash=hashlib.sha256(
                f'{user.user_id}:{period.id}:{attempt}:{secrets.token_hex(16)}'.encode()).hexdigest())
        SlatingVote.objects.create(
            period=period, slate=slate, voting_attempt=attempt, vote_choice=vote_choice,
            rejected_positions=rejected,
            vote_hash=hashlib.sha256(
                f'{secrets.token_hex(32)}:{timezone.now().isoformat()}'.encode()).hexdigest())
    SlatingActivity.objects.create(period=period, user=user, action='vote_cast', details='Vote recorded')


def individual_ballot(period, slate, user):
    """
    (decided, ballot, voted_position_ids, config) for a position round: the
    positions already decided (shown without results), the undecided ones the
    chair put on this round's ballot (src/slating_rounds.py), and which of
    those this member has voted on this round.
    """
    from src.slating_rounds import ballot_config, ballot_states, position_states
    states = position_states(period, slate)
    config = ballot_config(period)
    ballot = ballot_states(period, states, config)
    decided = [s for s in states if s.decided]
    voted = set(SlatingBallot.objects.filter(
        period=period, voter=user, vote_type='individual', voting_attempt=period.current_voting_attempt,
        position_id__in=[s.position.id for s in ballot]).values_list('position_id', flat=True))
    return decided, ballot, voted, config


def cast_individual(user, period, ballot, voted_position_ids, config, choice_for):
    """
    Record a vote on every position on this round's ballot not yet voted.
    `choice_for(state)` → 'approve' / 'reject' / 'abstain' for a pass/fail
    position, or an option key ('c<id>' / 'n<id>') or 'abstain' for a pick-one.
    """
    from src.slating_rounds import KIND_APPROVE, ballot_options
    unvoted = [s for s in ballot if s.position.id not in voted_position_ids]
    chosen, errors = {}, []
    for state in unvoted:
        choice = choice_for(state)
        options, abstain = ballot_options(state, config)
        if choice not in options + (['abstain'] if abstain else []):
            errors.append(f'Please select a valid vote for {state.position.title}.')
        else:
            chosen[state.position.id] = choice
    if errors:
        raise SlatingBallotError('invalid_choice', errors[0], errors)

    attempt = period.current_voting_attempt
    with transaction.atomic():
        SlatingPeriod.objects.select_for_update().only('pk').get(pk=period.pk)
        already = set(SlatingBallot.objects.filter(
            period=period, voter=user, vote_type='individual', voting_attempt=attempt,
            position_id__in=[s.position.id for s in unvoted]).values_list('position_id', flat=True))
        for state in unvoted:
            if state.position.id in already:
                continue
            choice = chosen[state.position.id]
            SlatingBallot.objects.create(
                period=period, voter=user, vote_type='individual', voting_attempt=attempt,
                position=state.position,
                ballot_hash=hashlib.sha256(
                    f'{user.user_id}:{period.id}:{state.position.id}:{secrets.token_hex(16)}'.encode()).hexdigest())
            vote = {'period': period, 'voting_attempt': attempt, 'position': state.position,
                    'vote_hash': hashlib.sha256(
                        f'{secrets.token_hex(32)}:{timezone.now().isoformat()}'.encode()).hexdigest()}
            if state.kind == KIND_APPROVE:
                SlatingVote.objects.create(**vote, slate_candidate=state.primary, vote_choice=choice)
            elif choice == 'abstain':
                SlatingVote.objects.create(**vote, vote_choice='abstain')
            elif choice.startswith('n'):
                SlatingVote.objects.create(**vote, nominee_id=int(choice[1:]), vote_choice='approve')
            else:
                SlatingVote.objects.create(**vote, slate_candidate_id=int(choice[1:]), vote_choice='approve')
    SlatingActivity.objects.create(period=period, user=user, action='vote_cast',
                                   details='Individual position votes recorded')
