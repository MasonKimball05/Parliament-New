"""
v3.51.1 — shared vote-result rules.

One place for "did this plurality vote produce a result", because the answer
had drifted: the committee paths passed any tie (`max > 0`), and chapter
`end_vote` passed a vote nobody had voted in (its tie check needed `max > 0`
too, so 0-0-0 was "no tie" and the first option "won").
"""


def plurality_passed(counts):
    """
    True only when one option has strictly more votes than every other, and at
    least one vote. `counts` maps option → votes; a 'total' key is ignored, so
    `src.view.committee.vote.get_vote_tally()`'s dict can be passed as-is.
    """
    values = [v for k, v in counts.items() if k != 'total']
    if not values:
        return False
    top = max(values)
    return top > 0 and values.count(top) == 1
