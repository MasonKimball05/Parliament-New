"""
Individual-position vote counts (v3.57.0).

The setup page, the results page and close_voting each counted a candidate's
votes with three COUNT queries per candidate (21 for a 7-position slate, on
every load of the setup page during voting). This is one grouped query for
any number of candidates, with the same numbers.
"""
from django.db.models import Count

from src.models import SlatingVote

CHOICES = ('approve', 'reject', 'abstain')


def individual_tallies(candidates):
    """{candidate id: {'approve': n, 'reject': n, 'abstain': n}}."""
    ids = [c.id for c in candidates]
    out = {cid: dict.fromkeys(CHOICES, 0) for cid in ids}
    if ids:
        for row in (SlatingVote.objects.filter(slate_candidate_id__in=ids)
                    .values('slate_candidate_id', 'vote_choice').annotate(n=Count('id'))):
            if row['vote_choice'] in CHOICES:
                out[row['slate_candidate_id']][row['vote_choice']] = row['n']
    return out
