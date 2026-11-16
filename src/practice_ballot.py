"""
The App Review practice ballot (11-16-26, guideline 2.1: the demo account
should be able to use the app's main feature).

The review account can't cast real ballots (src/ballots.py refuses it: its
vote would count toward chapter business). Instead its Votes tab shows one
extra ballot, id ``PRACTICE_ID``, clearly labelled as practice. Casting it
asks for the password or a passkey exactly like a real ballot and shows a
receipt, but nothing is stored: no Vote row, no tally, no activity log. Only
the review account ever sees it. Whether it has "voted" is remembered in the
cache for a day so the screen shows "You voted" afterwards.
"""
import secrets

from django.core.cache import cache
from django.utils import timezone

from src.app_review import is_app_review_account

PRACTICE_ID = 0
TITLE = 'Practice ballot (demo account)'
DESCRIPTION = ('This ballot is here so the demo account can see how voting works. It isn’t chapter business: '
               'casting it asks for your password or passkey like a real ballot and gives you a receipt, but '
               'nothing is recorded or counted. Real ballots appear above it for members.')
OPTIONS = ['yes', 'no', 'abstain']


def applies(user):
    return is_app_review_account(user)


def _key(user):
    return f'practice_ballot_voted_{user.pk}'


def has_voted(user):
    return bool(cache.get(_key(user)))


def summary(user):
    return {
        'id': PRACTICE_ID, 'title': TITLE, 'vote_mode': 'percentage', 'voting_open': True,
        'voting_starts_at': timezone.now().isoformat(), 'voting_ends_at': None, 'anonymous': True,
        'voted': has_voted(user),
    }


def detail(user):
    return {
        **summary(user), 'description': DESCRIPTION, 'options': OPTIONS, 'votes_allowed': 1,
        'allow_abstain': True, 'required_percentage': '60', 'required_number': None, 'document_url': None,
    }


def cast(user, choices):
    """Validate like a real ballot; record nothing. Returns ``(receipt, error)``."""
    if not isinstance(choices, list) or len(choices) != 1 or choices[0] not in OPTIONS:
        return None, 'Choose one option.'
    if has_voted(user):
        return None, 'You already voted on this ballot.'
    cache.set(_key(user), True, 24 * 60 * 60)
    return f'PRACTICE-{secrets.token_hex(4).upper()}', None
