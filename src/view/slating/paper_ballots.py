"""
Paper ballots (v3.58.0).

Mason (10-26-26): "We've had to use paper ballots sometimes for people who had
tech issues, so not all votes may be reflected on the site." The committee
enters this round's paper totals here, and every result adds them to the
online votes (src/view/slating/tallies.py), shown separately.

* Only while a round is open or paused — closing the vote is what decides the
  result, so paper ballots go in before it. Saving again replaces the round's
  paper counts (corrections).
* Full-slate vote: approve / reject / abstain; the number of paper ballots is
  their sum (one choice per ballot).
* Individual votes: the number of paper ballots, then each position still on
  this round's ballot; no position can have more votes than there were ballots.
* A paper count holds no voter identity, like an online SlatingVote. Each save
  is logged (SlatingActivity) with the counts, not who voted.

Access: slating_chair_required (the period's managers).
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from src.models import Slate, SlatingActivity, SlatingPaperBallots, SlatingPeriod
from .permissions import slating_chair_required

CHOICES = ('approve', 'reject', 'abstain')


def round_accepts_paper(period):
    """A round is in progress (open, or paused and not yet closed/voided)."""
    if not period.current_voting_attempt:
        return False
    if period.status == 'voting_open':
        return True
    return period.status == 'deliberation' and not (period.extra_settings or {}).get('round_ended')


def _num(post, key):
    raw = (post.get(key) or '').strip()
    if raw == '':
        return 0
    if not raw.isdigit():
        raise ValueError
    return int(raw)


@login_required
@slating_chair_required
def record_paper_ballots(request, period_id):
    period = get_object_or_404(SlatingPeriod, id=period_id)
    if not round_accepts_paper(period):
        messages.error(request, 'Paper ballots can only be entered while a voting round is open or paused.')
        return redirect('slating_period_setup', period_id=period.id)
    attempt = period.current_voting_attempt
    slate = Slate.objects.filter(period=period, is_approved=True, slate_type='primary').first()
    individual = period.vote_type == 'individual'
    candidates = (list(slate.candidates.filter(is_runoff=False, individual_passed__isnull=True)
                       .select_related('position', 'application__applicant', 'write_in_member')
                       .order_by('display_order')) if (slate and individual) else [])
    existing = {p.slate_candidate_id: p for p in
                SlatingPaperBallots.objects.filter(period=period, voting_attempt=attempt)}

    if request.method == 'POST':
        try:
            if individual:
                ballots = _num(request.POST, 'ballots')
                rows = [(c, {k: _num(request.POST, f'{k}_{c.id}') for k in CHOICES}) for c in candidates]
                over = [c.position.title for c, n in rows if sum(n.values()) > ballots]
                if over:
                    messages.error(request, f'More votes than paper ballots ({ballots}) for: ' + ', '.join(over) + '.')
                    return redirect('slating_paper_ballots', period_id=period.id)
            else:
                counts = {k: _num(request.POST, k) for k in CHOICES}
                ballots = sum(counts.values())
                rows = [(None, counts)]
        except ValueError:
            messages.error(request, 'Counts must be whole numbers (0 or more).')
            return redirect('slating_paper_ballots', period_id=period.id)
        note = (request.POST.get('note') or '').strip()[:300]
        with transaction.atomic():
            SlatingPeriod.objects.select_for_update().only('pk').get(pk=period.pk)
            for cand, n in rows:
                SlatingPaperBallots.objects.update_or_create(
                    period=period, voting_attempt=attempt, slate_candidate=cand,
                    defaults={**n, 'ballots': ballots, 'note': note, 'recorded_by': request.user,
                              'position_title': cand.position.title if cand else '',
                              'candidate_name': cand.candidate_name if cand else ''})
            SlatingActivity.objects.create(
                period=period, user=request.user, action='vote_cast',
                details=f'Paper ballots recorded for round {attempt}: {ballots} ballot(s)',
                metadata={'paper_ballots': ballots, 'attempt': attempt,
                          'counts': [{'position': c.position.title if c else 'slate', **n} for c, n in rows]},
            )
        messages.success(request, f'Saved {ballots} paper ballot(s) for round {attempt}. '
                                  'They\'ll be counted when you close the vote.')
        return redirect('slating_period_setup', period_id=period.id)

    if individual:
        any_row = next(iter(existing.values()), None)
        rows = [{'candidate': c, 'counts': {k: getattr(existing.get(c.id), k, 0) for k in CHOICES}}
                for c in candidates]
        ballots = any_row.ballots if any_row else 0
    else:
        slate_row = existing.get(None)
        rows = [{'candidate': None, 'counts': {k: getattr(slate_row, k, 0) for k in CHOICES}}]
        ballots = slate_row.ballots if slate_row else 0
    note = next((p.note for p in existing.values() if p.note), '')
    return render(request, 'slating/paper_ballots.html', {
        'period': period, 'attempt': attempt, 'individual': individual, 'rows': rows,
        'ballots': ballots, 'note': note, 'allow_abstain': period.allow_abstain,
    })
