"""
Slating vote counts: one place, per round, online + paper (v3.57.0, v3.58.0).

Every result in slating is computed here: closing the vote, the setup page's
live stats, the results page, the results summary and the export saved to
Chapter Documents. Before v3.58.0 those each counted for themselves, and two of
them (the results page and the export) added every round together, so a
re-vote's "official" results included the round that failed.

* Online votes are SlatingVote rows for one ``voting_attempt`` (a round).
* Paper ballots are SlatingPaperBallots rows the committee entered for that
  round (members who voted on paper because of tech problems). They are
  added to the online counts and always reported next to them.
* ``round_history`` is every round, oldest first: the snapshot written when
  the round ended (SlatingRound.results) or, for the round in progress, the
  live counts.
"""
from django.db.models import Count

from src.models import SlatingBallot, SlatingPaperBallots, SlatingRound, SlatingVote

CHOICES = ('approve', 'reject', 'abstain')


def _blank():
    return dict.fromkeys(CHOICES, 0)


def _finish(online, paper, required):
    """Combine online + paper counts and decide pass/fail (abstentions don't count)."""
    combined = {c: online[c] + paper[c] for c in CHOICES}
    counted = combined['approve'] + combined['reject']
    pct = (combined['approve'] / counted * 100) if counted else None
    return {
        **combined,
        'total': sum(combined.values()),
        'online': online,
        'paper': paper,
        'percentage': pct,
        'passed': (pct >= required) if pct is not None else None,
    }


def paper_counts(period, attempt):
    """{None: slate paper counts, candidate id: paper counts} for one round."""
    out = {}
    for row in SlatingPaperBallots.objects.filter(period=period, voting_attempt=attempt):
        out[row.slate_candidate_id] = {
            'approve': row.approve, 'reject': row.reject, 'abstain': row.abstain,
            'ballots': row.ballots,
        }
    return out


def slate_tally(period, slate, attempt=None):
    """A full-slate vote's counts for one round (default: the current one)."""
    attempt = attempt or period.current_voting_attempt
    online = _blank()
    if slate is not None:
        for row in (SlatingVote.objects.filter(slate=slate, voting_attempt=attempt)
                    .values('vote_choice').annotate(n=Count('id'))):
            if row['vote_choice'] in online:
                online[row['vote_choice']] = row['n']
    p = paper_counts(period, attempt).get(None, {})
    paper = {c: p.get(c, 0) for c in CHOICES}
    result = _finish(online, paper, period.required_approval_percentage)
    result['ballots_online'] = SlatingBallot.objects.filter(
        period=period, voting_attempt=attempt, vote_type='slate').count()
    result['ballots_paper'] = p.get('ballots', 0)
    return result


def individual_tallies(candidates, attempt=None, period=None):
    """
    {candidate id: {'approve', 'reject', 'abstain', 'online', 'paper', ...}} for
    one round. ``attempt`` None counts every round's online votes and no paper
    (the pre-v3.58.0 behaviour, kept for callers that don't know the round).
    """
    candidates = list(candidates)
    ids = [c.id for c in candidates]
    online = {cid: _blank() for cid in ids}
    if ids:
        qs = SlatingVote.objects.filter(slate_candidate_id__in=ids)
        if attempt is not None:
            qs = qs.filter(voting_attempt=attempt)
        for row in qs.values('slate_candidate_id', 'vote_choice').annotate(n=Count('id')):
            if row['vote_choice'] in CHOICES:
                online[row['slate_candidate_id']][row['vote_choice']] = row['n']
    paper_all = paper_counts(period, attempt) if (period is not None and attempt) else {}
    required = period.required_approval_percentage if period is not None else 0
    out = {}
    for cid in ids:
        p = paper_all.get(cid, {})
        out[cid] = _finish(online[cid], {c: p.get(c, 0) for c in CHOICES}, required)
        out[cid]['ballots_paper'] = p.get('ballots', 0)
    return out


def round_results(period, attempt, slate=None, vote_type=None):
    """The snapshot stored on SlatingRound.results when a round ends."""
    vote_type = vote_type or period.vote_type
    if vote_type == 'individual':
        if slate is None:
            return {'positions': []}
        cands = list(slate.candidates.filter(is_runoff=False)
                     .select_related('position', 'application__applicant', 'write_in_member')
                     .order_by('display_order'))
        voted = set(SlatingVote.objects.filter(slate_candidate__in=cands, voting_attempt=attempt)
                    .values_list('slate_candidate_id', flat=True))
        papered = set(SlatingPaperBallots.objects.filter(period=period, voting_attempt=attempt)
                      .values_list('slate_candidate_id', flat=True))
        tallies = individual_tallies(cands, attempt, period)
        positions = []
        for c in cands:
            if c.id not in voted and c.id not in papered:
                continue   # not on this round's ballot (decided earlier)
            t = tallies[c.id]
            positions.append({
                'position': c.position.title, 'candidate': c.candidate_name,
                'approve': t['approve'], 'reject': t['reject'], 'abstain': t['abstain'],
                'online': t['online'], 'paper': t['paper'], 'ballots_paper': t['ballots_paper'],
                'percentage': t['percentage'], 'passed': t['passed'],
            })
        return {'positions': positions}
    t = slate_tally(period, slate, attempt)
    return {'slate': {k: t[k] for k in ('approve', 'reject', 'abstain', 'total', 'online', 'paper',
                                       'percentage', 'passed', 'ballots_online', 'ballots_paper')}}


def open_round(period):
    """The SlatingRound for the current attempt, created if missing."""
    if not period.current_voting_attempt:
        return None
    rnd, _ = SlatingRound.objects.get_or_create(
        period=period, attempt=period.current_voting_attempt,
        defaults={'vote_type': period.vote_type})
    return rnd


def end_round(period, status, user=None, slate=None):
    """Snapshot the current round's results and mark it ``status``."""
    from django.utils import timezone
    rnd = open_round(period)
    if rnd is None:
        return None
    rnd.vote_type = period.vote_type
    rnd.results = round_results(period, rnd.attempt, slate, period.vote_type)
    rnd.status = status
    rnd.closed_at = timezone.now()
    rnd.closed_by = user
    rnd.save()
    return rnd


def round_history(period, slate=None):
    """Every round, oldest first, with results (snapshot, or live if open)."""
    rounds = {r.attempt: r for r in period.rounds.all()}
    out = []
    for attempt in range(1, (period.current_voting_attempt or 0) + 1):
        rnd = rounds.get(attempt)
        if rnd is not None and rnd.status != 'open' and rnd.results:
            results = rnd.results
        else:
            vote_type = rnd.vote_type if rnd is not None else period.vote_type
            results = round_results(period, attempt, slate, vote_type)
        out.append({
            'attempt': attempt,
            'vote_type': (rnd.vote_type if rnd else period.vote_type),
            'status': rnd.status if rnd else 'closed',
            'opened_at': rnd.opened_at if rnd else None,
            'closed_at': rnd.closed_at if rnd else None,
            'events': rnd.events if rnd else [],
            **results,
        })
    return out


def note_candidate_replaced(period, candidate):
    """Before a candidate with votes is deleted mid-voting, keep their counts."""
    attempt = period.current_voting_attempt
    if not attempt:
        return
    t = individual_tallies([candidate], attempt, period)[candidate.id]
    if not t['total']:
        return
    rnd = open_round(period)
    rnd.events = (rnd.events or []) + [{
        'event': 'candidate_replaced', 'position': candidate.position.title,
        'candidate': candidate.candidate_name,
        'approve': t['approve'], 'reject': t['reject'], 'abstain': t['abstain'],
    }]
    rnd.save(update_fields=['events'])

