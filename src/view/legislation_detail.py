from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.shortcuts import render, get_object_or_404
from ..models import Legislation, Vote


@login_required
def legislation_detail(request, legislation_id):
    legislation = get_object_or_404(Legislation, id=legislation_id)

    # v3.17.5: one GROUP BY instead of a COUNT per choice.
    #
    # The percentage branch ran four (`yes`, `no`, `abstain`, total) and the
    # plurality branch ran one per option plus a total — so a five-option ballot
    # was six round trips over the same rows. Found by the detail-route sweep
    # once its repeat threshold was tightened from 4 to 3 in this release
    # (it was 3x `src_vote`, which the old `>= 4` rule let through).
    #
    # This is the FOURTH site of this exact pattern: v3.17.1 fixed it on the
    # legislation tracker, v3.17.2 on the status tabs, v3.17.3 on
    # `PassedLegislationDetailView`. Same shape, same fix, one file at a time —
    # which is the argument for the sweep rather than for another manual pass.
    tally = {
        row['vote_choice']: row['n']
        for row in Vote.objects.filter(legislation=legislation)
        .values('vote_choice')
        .annotate(n=Count('id'))
    }
    total = sum(tally.values())

    if legislation.vote_mode == 'plurality':
        vote_result = {
            'mode': 'plurality',
            # `.get(option, 0)` preserves the old behaviour: an option nobody
            # voted for is present with a count of zero, not absent.
            'options': {
                option: tally.get(option, 0)
                for option in legislation.plurality_options
            },
            'total': total,
        }
    else:
        yes_votes = tally.get('yes', 0)
        no_votes = tally.get('no', 0)
        abstain_votes = tally.get('abstain', 0)
        yes_pct = (yes_votes / total * 100) if total > 0 else 0
        vote_result = {
            'mode': 'percentage',
            'yes': yes_votes,
            'no': no_votes,
            'abstain': abstain_votes,
            'yes_percentage': "{:.0f}%".format(yes_pct),
            'required_percentage': legislation.required_percentage,
            'total': total,
        }

    return render(request, 'src/legislation_detail.html', {
        'legislation': legislation,
        'vote_result': vote_result
    })
