"""
How the chapter decides the slate, round by round (10-29-26). One copy, used by
the web's slating pages and the app (src/mobile/views_slating.py).

Bylaws Art. II §3, and the pending resolution "On the Adjustment of the Slating
Process to Better Enable Chapter Feedback" (run-off positions):

* The first ballot is always the FULL SLATE, pass/fail, at the period's
  approval percentage (60%). Passing it elects every nominee on it that isn't a
  run-off or an open-nominations position, and affirms both run-off candidates.
  A failed slate can be changed by the committee and voted again.
* After that the chair chooses each round: the full slate again (for whatever
  is still undecided), or a POSITION round with the positions the chair ticks
  (one at a time, as the bylaws order it, or several):
    - an ordinary position is pass/fail on its nominee;
    - a RUN-OFF (primary + run-off candidate) is pick-one between the two;
    - an OPEN-NOMINATIONS position (left without a nominee) is pick-one among
      the accepted floor nominees, which the chair can narrow (e.g. to the top
      two after a "true division of the house").
  The chair sets the round's threshold (default the period's, e.g. 51% for a
  final ballot) and whether abstaining is allowed.
* A pick-one ballot elects the leader only with at least the threshold share
  of the votes for candidates (abstentions don't count) and no tie. A tie is
  for the chapter to settle; the chair records what it decided
  (``decide_manually``), or leaves the position unfilled.

"Decided" is one thing everywhere: a candidate with ``individual_passed=True``
or a nominee with ``elected=True``, or the position marked ``left_unfilled``.
Results, the officer transition and the app all read ``position_states``.
"""
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Q

from src.models import (
    SlateCandidate, SlatingActivity, SlatingNominee, SlatingPaperBallots, SlatingPosition, SlatingRound,
    SlatingVote, Slate,
)
from src.models.users import member_defer

KIND_APPROVE, KIND_RUNOFF, KIND_NOMINATIONS, KIND_VACANT = 'approve', 'runoff', 'nominations', 'vacant'
KIND_LABELS = {
    KIND_APPROVE: 'Pass/fail', KIND_RUNOFF: 'Run-off', KIND_NOMINATIONS: 'Floor nominations',
    KIND_VACANT: 'No nominee',
}


class RoundError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@dataclass
class Option:
    key: str                  # 'c<candidate id>' or 'n<nominee id>'
    name: str
    candidate: SlateCandidate = None
    nominee: SlatingNominee = None

    @property
    def member(self):
        if self.nominee is not None:
            return self.nominee.member
        c = self.candidate
        return c.application.applicant if c.application_id else c.write_in_member


@dataclass
class PositionState:
    position: SlatingPosition
    kind: str
    primary: SlateCandidate = None
    runoff: SlateCandidate = None
    nominees: list = field(default_factory=list)       # accepted floor nominees
    all_nominees: list = field(default_factory=list)
    winner: Option = None
    unfilled: bool = False

    @property
    def decided(self):
        return self.winner is not None or self.unfilled

    @property
    def failed_last(self):
        """An ordinary position whose nominee failed a pass/fail ballot (the committee renominates)."""
        return self.kind == KIND_APPROVE and self.primary is not None and self.primary.individual_passed is False

    def options(self):
        if self.kind == KIND_RUNOFF:
            return [Option(f'c{c.id}', c.candidate_name, candidate=c) for c in (self.primary, self.runoff)]
        if self.kind == KIND_NOMINATIONS:
            return [Option(f'n{n.id}', n.name, nominee=n) for n in self.nominees]
        if self.primary is not None:
            return [Option(f'c{self.primary.id}', self.primary.candidate_name, candidate=self.primary)]
        return []


def primary_slate(period):
    return Slate.objects.filter(period=period, is_approved=True, slate_type='primary').first()


def position_states(period, slate=None):
    """Every active position, in slate order, with its candidates and whether it's decided."""
    slate = slate if slate is not None else primary_slate(period)
    cands = {}
    if slate is not None:
        for c in (slate.candidates.select_related('application__applicant', 'write_in_member', 'position')
                  .defer(*member_defer('application__applicant', 'write_in_member'))):
            cands.setdefault(c.position_id, {})['runoff' if c.is_runoff else 'primary'] = c
    nominees = {}
    for n in (SlatingNominee.objects.filter(period=period).select_related('member')
              .defer(*member_defer('member'))):
        nominees.setdefault(n.position_id, []).append(n)
    order = {pid: (c.get('primary') or c.get('runoff')).display_order for pid, c in cands.items()}
    positions = sorted(period.positions.filter(is_active=True),
                       key=lambda p: (order.get(p.id, 10_000 + p.display_order), p.display_order, p.title))
    out = []
    for p in positions:
        c = cands.get(p.id, {})
        primary, runoff = c.get('primary'), c.get('runoff')
        if p.open_nominations:
            kind = KIND_NOMINATIONS
        elif primary is not None and runoff is not None:
            kind = KIND_RUNOFF
        elif primary is not None:
            kind = KIND_APPROVE
        else:
            kind = KIND_VACANT
        state = PositionState(position=p, kind=kind, primary=primary, runoff=runoff,
                              all_nominees=nominees.get(p.id, []),
                              nominees=[n for n in nominees.get(p.id, []) if n.accepted],
                              unfilled=p.left_unfilled)
        for n in state.all_nominees:
            if n.elected:
                state.winner = Option(f'n{n.id}', n.name, nominee=n)
        if state.winner is None and kind != KIND_NOMINATIONS:
            for cand in (primary, runoff):
                if cand is not None and cand.individual_passed:
                    state.winner = Option(f'c{cand.id}', cand.candidate_name, candidate=cand)
        if kind == KIND_VACANT and slate is None:
            continue   # nothing slated yet: not part of any vote
        out.append(state)
    return out


def current_round(period):
    if not period.current_voting_attempt:
        return None
    return SlatingRound.objects.filter(period=period, attempt=period.current_voting_attempt).first()


def ballot_config(period, rnd=None):
    rnd = rnd if rnd is not None else current_round(period)
    return dict(rnd.ballot or {}) if rnd is not None else {}


def round_threshold(period, config):
    t = config.get('threshold')
    return int(t) if t else period.required_approval_percentage


def ballot_states(period, states=None, config=None):
    """The undecided positions on the current position round's ballot, in slate order."""
    states = states if states is not None else position_states(period)
    config = config if config is not None else ballot_config(period)
    ids = config.get('positions')
    if ids is None:
        # A round opened before 10-29-26: every undecided pass/fail position.
        return [s for s in states if not s.decided and s.kind == KIND_APPROVE]
    wanted = set(ids)
    return [s for s in states if s.position.id in wanted and not s.decided]


def ballot_options(state, config):
    """(choices, allow_abstain) a member gets for one position this round."""
    abstain = config.get('abstain', state.position.allow_abstain)
    if state.kind == KIND_APPROVE:
        return ['approve', 'reject'], abstain
    opts = state.options()
    narrowed = (config.get('nominees') or {}).get(str(state.position.id))
    if state.kind == KIND_NOMINATIONS and narrowed:
        opts = [o for o in opts if o.nominee.id in set(narrowed)]
    return [o.key for o in opts], abstain


# ── Counting ─────────────────────────────────────────────────────────────────

def position_tally(period, state, attempt, config=None):
    """One position's counts for one round, online + paper, and who it elects."""
    config = config if config is not None else {}
    threshold = round_threshold(period, config)
    cand_ids = [c.id for c in (state.primary, state.runoff) if c is not None]
    nom_ids = [n.id for n in state.all_nominees]
    votes = SlatingVote.objects.filter(period=period, voting_attempt=attempt).filter(
        Q(position=state.position) | Q(position__isnull=True, slate_candidate_id__in=cand_ids))
    paper = list(SlatingPaperBallots.objects.filter(period=period, voting_attempt=attempt).filter(
        Q(slate_candidate_id__in=cand_ids) | Q(nominee_id__in=nom_ids)))

    if state.kind == KIND_APPROVE:
        counts = {'approve': 0, 'reject': 0, 'abstain': 0}
        for v in votes.filter(slate_candidate=state.primary):
            if v.vote_choice in counts:
                counts[v.vote_choice] += 1
        online = dict(counts)
        for p in paper:
            if p.slate_candidate_id == (state.primary.id if state.primary else None):
                for k in counts:
                    counts[k] += getattr(p, k)
        counted = counts['approve'] + counts['reject']
        pct = counts['approve'] / counted * 100 if counted else None
        return {'kind': state.kind, **counts, 'online': online, 'threshold': threshold,
                'percentage': pct, 'passed': (pct >= threshold) if pct is not None else None,
                'winner': state.options()[0].key if (pct is not None and pct >= threshold) else None}

    picks = {o.key: 0 for o in state.options()} if state.kind == KIND_RUNOFF else \
        {f'n{n.id}': 0 for n in state.all_nominees if n.accepted}
    abstain = 0
    for v in votes:
        if v.vote_choice == 'abstain':
            abstain += 1
        elif v.vote_choice == 'approve':
            key = f'n{v.nominee_id}' if v.nominee_id else f'c{v.slate_candidate_id}'
            if key in picks:
                picks[key] += 1
    online = dict(picks)
    for p in paper:
        key = f'n{p.nominee_id}' if p.nominee_id else f'c{p.slate_candidate_id}'
        if key in picks:
            picks[key] += p.approve
        abstain += p.abstain
    total = sum(picks.values())
    ranked = sorted(picks.items(), key=lambda kv: -kv[1])
    leader = ranked[0] if ranked else None
    tie = len(ranked) > 1 and ranked[0][1] == ranked[1][1]
    pct = leader[1] / total * 100 if (leader and total) else None
    winner = leader[0] if (leader and pct is not None and pct >= threshold and not tie) else None
    return {'kind': state.kind, 'picks': picks, 'online': online, 'abstain': abstain, 'total': total,
            'threshold': threshold, 'percentage': pct, 'tie': tie, 'winner': winner,
            'passed': winner is not None if total else None}


# ── Opening a round ──────────────────────────────────────────────────────────

def validate_round(period, vote_type, position_ids=None, threshold=None, abstain=None, nominees=None):
    """The ballot config for a new round, or RoundError. Doesn't save anything."""
    states = position_states(period)
    undecided = [s for s in states if not s.decided]
    has_rounds = period.rounds.exclude(status='voided').exists()
    if vote_type == 'slate':
        if not any(s.kind == KIND_APPROVE for s in undecided) and has_rounds:
            raise RoundError('nothing_on_slate', 'Every slated nominee is decided — vote on the remaining positions.')
        return {}
    if vote_type != 'individual':
        raise RoundError('invalid', 'Choose the full slate or positions.')
    if not has_rounds:
        raise RoundError('slate_first', 'The first ballot is always the full slate (Bylaws Art. II §3 (4)).')
    by_id = {s.position.id: s for s in undecided}
    ids = []
    for raw in position_ids or []:
        pid = int(raw) if str(raw).isdigit() else None
        if pid in by_id and pid not in ids:
            ids.append(pid)
    if not ids:
        raise RoundError('no_positions', 'Choose at least one undecided position for this ballot.')
    narrowed = {}
    for pid in ids:
        s = by_id[pid]
        if s.kind == KIND_VACANT:
            raise RoundError('no_nominee', f'{s.position.title} has no nominee. Renominate it on the slate, '
                                           'or open nominations for it.')
        if s.kind == KIND_NOMINATIONS:
            accepted = {n.id for n in s.nominees}
            chosen = [int(n) for n in (nominees or {}).get(str(pid), []) if str(n).isdigit() and int(n) in accepted]
            if len(chosen or accepted) < 1:
                raise RoundError('no_nominees', f'{s.position.title} needs at least one accepted nominee.')
            if chosen:
                narrowed[str(pid)] = chosen
    config = {'positions': ids}
    if threshold:
        t = int(threshold)
        if not 50 < t <= 100:
            raise RoundError('invalid_threshold', 'The threshold must be over 50%.')
        config['threshold'] = t
    if abstain is not None:
        config['abstain'] = bool(abstain)
    if narrowed:
        config['nominees'] = narrowed
    return config


def start_round(period, config):
    """Called once the period's attempt has moved on (change_period_status): store the ballot."""
    from src.view.slating.tallies import open_round
    rnd = open_round(period)
    if rnd is not None:
        rnd.ballot = config
        rnd.vote_type = period.vote_type
        rnd.save(update_fields=['ballot', 'vote_type'])
    if period.vote_type == 'individual':
        # A nominee who failed a pass/fail ballot goes back on as undecided.
        SlateCandidate.objects.filter(slate__period=period, is_runoff=False, individual_passed=False,
                                      position_id__in=config.get('positions', [])).update(
            individual_passed=None, individual_votes_for=0, individual_votes_against=0)
    return rnd


# ── Closing a round ──────────────────────────────────────────────────────────

def close_round(period, user):
    """
    Count the open round and decide what it decides. Returns a summary dict:
    {'vote_type', 'decided': [...], 'undecided': [...], 'slate': tally|None, 'finished': bool}.
    The period goes to voting_closed when every position is decided, otherwise
    back to deliberation for the chair's next round.
    """
    from src.view.slating.tallies import end_round, slate_tally
    slate = primary_slate(period)
    if slate is None:
        raise RoundError('no_slate', 'No slate found.')
    attempt = period.current_voting_attempt
    rnd = current_round(period)
    config = ballot_config(period, rnd)
    states = position_states(period, slate)
    decided, undecided, slate_result = [], [], None

    with transaction.atomic():
        if period.vote_type == 'individual':
            for s in ballot_states(period, states, config):
                t = position_tally(period, s, attempt, config)
                if s.kind == KIND_APPROVE:
                    s.primary.individual_passed = bool(t['passed'])
                    s.primary.individual_votes_for = t['approve']
                    s.primary.individual_votes_against = t['reject']
                    s.primary.save(update_fields=['individual_passed', 'individual_votes_for',
                                                  'individual_votes_against'])
                elif t['winner']:
                    _set_winner(s, t['winner'])
                (decided if t['winner'] else undecided).append(
                    {'position': s.position.title, 'winner': _option_name(s, t['winner']), 'tally': t})
        else:
            t = slate_tally(period, slate, attempt)
            slate.total_votes, slate.approval_votes = t['total'], t['approve']
            slate.rejection_votes, slate.abstain_votes = t['reject'], t['abstain']
            slate.approval_percentage, slate.passed = t['percentage'], t['passed']
            slate.save(update_fields=['total_votes', 'approval_votes', 'rejection_votes',
                                      'abstain_votes', 'approval_percentage', 'passed'])
            slate_result = t
            if t['passed']:
                # Passing the slate elects every slated nominee except run-offs and
                # open-nominations positions, which are decided next.
                for s in states:
                    if not s.decided and s.kind == KIND_APPROVE:
                        s.primary.individual_passed = True
                        s.primary.save(update_fields=['individual_passed'])
                        decided.append({'position': s.position.title, 'winner': s.primary.candidate_name})

        remaining = [s for s in position_states(period, slate) if not s.decided]
        ballot_passed = (slate_result['passed'] if slate_result is not None else not undecided)
        end_round(period, 'passed' if ballot_passed else 'failed', user, slate)
        finished = not remaining
        if finished:
            period.status = 'voting_closed'
            period.save(update_fields=['status'])
        else:
            period.status = 'deliberation'
            period.extra_settings = {**(period.extra_settings or {}), 'round_ended': True}
            period.save(update_fields=['status', 'extra_settings'])
        SlatingActivity.objects.create(
            period=period, user=user, action='voting_closed',
            details=(f'Round {attempt} ({"full slate" if slate_result is not None else "positions"}) closed: '
                     f'{len(decided)} decided, {len(remaining)} still open.'),
            metadata={'attempt': attempt, 'decided': [d['position'] for d in decided],
                      'still_open': [s.position.title for s in remaining]})
    return {'vote_type': period.vote_type, 'decided': decided, 'undecided': undecided,
            'slate': slate_result, 'finished': finished, 'remaining': [s.position.title for s in remaining]}


def _option_name(state, key):
    if not key:
        return None
    return next((o.name for o in state.options() if o.key == key), None) or next(
        (n.name for n in state.all_nominees if f'n{n.id}' == key), None)


def _set_winner(state, key):
    """Mark `key` elected for `state`'s position and everyone else on it not."""
    for cand in (state.primary, state.runoff):
        if cand is not None:
            cand.individual_passed = (f'c{cand.id}' == key)
            cand.save(update_fields=['individual_passed'])
    for n in state.all_nominees:
        n.elected = (f'n{n.id}' == key)
        n.save(update_fields=['elected'])


# ── The chair's own decisions ────────────────────────────────────────────────

def decide_manually(period, user, position_id, key, reason):
    """Record what the chapter decided for a position (a tie, a tabled run-off), or leave it unfilled."""
    reason = (reason or '').strip()[:300]
    if not reason:
        raise RoundError('reason_required', 'Say what the chapter decided (it is kept in the activity log).')
    state = next((s for s in position_states(period) if s.position.id == position_id), None)
    if state is None:
        raise RoundError('not_found', 'That position isn’t on this election.')
    with transaction.atomic():
        if key == 'unfilled':
            state.position.left_unfilled = True
            state.position.save(update_fields=['left_unfilled'])
            _set_winner(state, None)
            what = 'left unfilled'
        else:
            keys = {o.key for o in state.options()} | {f'n{n.id}' for n in state.nominees}
            if key not in keys:
                raise RoundError('invalid_choice', 'Choose one of this position’s candidates.')
            if state.position.left_unfilled:
                state.position.left_unfilled = False
                state.position.save(update_fields=['left_unfilled'])
            _set_winner(state, key)
            what = f'{_option_name(state, key)} selected'
        SlatingActivity.objects.create(
            period=period, user=user, action='voting_closed',
            details=f'{state.position.title}: {what} by the chapter’s decision — {reason}',
            metadata={'position': state.position.title, 'manual': True, 'choice': key, 'reason': reason})


def set_open_nominations(period, user, position_id, on):
    position = period.positions.filter(pk=position_id, is_active=True).first()
    if position is None:
        raise RoundError('not_found', 'That position isn’t on this election.')
    position.open_nominations = bool(on)
    position.save(update_fields=['open_nominations'])
    SlatingActivity.objects.create(
        period=period, user=user, action='period_status_changed',
        details=f'{position.title}: floor nominations {"opened" if on else "closed"}')


def add_nominee(period, user, position_id, member, accepted=False):
    position = period.positions.filter(pk=position_id, is_active=True, open_nominations=True).first()
    if position is None:
        raise RoundError('not_open', 'Open nominations for that position first.')
    nominee, created = SlatingNominee.objects.get_or_create(
        position=position, member=member,
        defaults={'period': period, 'accepted': accepted, 'added_by': user})
    if not created and accepted and not nominee.accepted:
        nominee.accepted = True
        nominee.save(update_fields=['accepted'])
    return nominee


def set_nominee_accepted(period, nominee_id, accepted):
    nominee = SlatingNominee.objects.filter(period=period, pk=nominee_id).first()
    if nominee is None:
        raise RoundError('not_found', 'That nominee isn’t on this election.')
    if SlatingVote.objects.filter(nominee=nominee).exists() and not accepted:
        raise RoundError('has_votes', 'This nominee already has votes; they can’t withdraw now.')
    nominee.accepted = bool(accepted)
    nominee.save(update_fields=['accepted'])
    return nominee


def remove_nominee(period, nominee_id):
    nominee = SlatingNominee.objects.filter(period=period, pk=nominee_id).first()
    if nominee is None:
        raise RoundError('not_found', 'That nominee isn’t on this election.')
    if SlatingVote.objects.filter(nominee=nominee).exists():
        raise RoundError('has_votes', 'This nominee already has votes; they can’t be removed.')
    nominee.delete()


def winners(period):
    """{position id: member} for every decided position (the officer transition)."""
    return {s.position.id: s.winner.member for s in position_states(period) if s.winner is not None}


def election_context(period):
    """What the chair's controls need (web setup page and the app): positions, ballot, defaults."""
    slate = primary_slate(period)
    if slate is None or period.status not in ('deliberation', 'voting_open', 'voting_closed', 'results_published'):
        return {'states': []}
    states = position_states(period, slate)
    config = ballot_config(period)
    undecided = [s for s in states if not s.decided]
    selectable = [s for s in undecided if s.kind != KIND_VACANT]
    live_positions = period.status == 'voting_open' and period.vote_type == 'individual'
    return {
        'states': states,
        'has_rounds': period.rounds.exclude(status='voided').exists(),
        'round_ended': bool((period.extra_settings or {}).get('round_ended')),
        'undecided': undecided,
        'selectable': selectable,
        # Bylaws: one office (or run-off) at a time, in slate order.
        'default_position': selectable[0].position.id if selectable else None,
        'default_abstain': bool(selectable and selectable[0].kind == KIND_APPROVE
                                and selectable[0].position.allow_abstain),
        'slate_possible': any(s.kind == KIND_APPROVE for s in undecided),
        'on_ballot': {s.position.id for s in ballot_states(period, states, config)} if live_positions else set(),
        'threshold': round_threshold(period, config),
        'config': config,
    }


def slate_rows(period, slate=None):
    """
    The full-slate ballot as members see it: one row per undecided position
    (the web template's candidate shape: ``.position``, ``.candidate_name``).
    A run-off shows both names; a passed slate affirms both. A floor
    nominations position is decided after the slate passes.
    """
    from types import SimpleNamespace
    rows = []
    for s in position_states(period, slate):
        if s.decided:
            continue
        if s.kind == KIND_RUNOFF:
            name = f'{s.primary.candidate_name} or {s.runoff.candidate_name} (run-off, decided after the slate)'
        elif s.kind == KIND_NOMINATIONS:
            name = 'Floor nominations after the slate passes'
        elif s.kind == KIND_VACANT:
            name = 'No nominee'
        else:
            name = s.primary.candidate_name
        rows.append(SimpleNamespace(id=s.position.id, position=s.position, candidate_name=name, kind=s.kind,
                                    notes=s.primary.notes if s.primary else ''))
    return rows
