"""
v3.14.0 — tamper-evident vote receipts.

A receipt is a signed token handed to the voter at cast time. It commits the
server to the ballot(s): the token embeds the vote row ids and a keyed digest
of the recorded choices. Verifying later proves the ballots still exist and
the choices are unchanged — WITHOUT revealing the choices themselves, so
receipts are safe for anonymous votes.

Nothing is stored server-side; integrity comes from the signature
(SECRET_KEY via django.core.signing) and the keyed choice digest.
"""
import hashlib
import hmac
import time

from django.conf import settings
from django.core import signing

RECEIPT_SALT = 'vote-receipt'

# v3.14.0: receipts are verifiable for ~3 months after the ballot was cast.
# Enforced on the embedded cast time ('t'), not the signing timestamp, so
# tokens regenerated later (Personal tab) expire on the same schedule as the
# original. A daily task notifies members when their receipts cross the line.
RECEIPT_MAX_AGE_DAYS = 90


def _choice_digest(vote_rows):
    """Stable keyed digest over (id, choice) pairs. HMAC-SHA256 keyed with
    SECRET_KEY so a choice can be verified later without being stored or
    revealed. NOTE: rotating SECRET_KEY invalidates all outstanding receipts
    (signature AND digest) — call it out in the restore runbook."""
    parts = sorted(f'{v.id}:{v.vote_choice}' for v in vote_rows)
    payload = '|'.join(parts).encode()
    return hmac.new(settings.SECRET_KEY.encode(), payload,
                    hashlib.sha256).hexdigest()[:16]


def make_receipt(user, legislation, vote_rows, cast_at=None):
    """cast_at anchors the receipt to the original ballot time so tokens
    regenerated later (My Ballots page) carry the real cast time."""
    if cast_at is None:
        cast_at_ts = int(time.time())
    else:
        cast_at_ts = int(cast_at.timestamp())
    return signing.dumps({
        'u': user.pk,
        'l': legislation.id,
        'v': sorted(v.id for v in vote_rows),
        'c': _choice_digest(vote_rows),
        't': cast_at_ts,
    }, salt=RECEIPT_SALT, compress=True)


def verify_receipt(token):
    """Verify a receipt token. Never raises; returns a result dict:
    valid      — signature checks out (token really came from this server)
    intact     — all ballots still exist with unchanged choices
    missing    — how many ballots from the receipt no longer exist
    """
    from src.models import Vote, Legislation
    try:
        data = signing.loads((token or '').strip(), salt=RECEIPT_SALT)
    except signing.BadSignature:
        return {'valid': False, 'reason': 'Invalid or tampered receipt token.'}
    except Exception:
        return {'valid': False, 'reason': 'Unreadable receipt token.'}

    cast_at_ts = data.get('t') or 0
    if cast_at_ts and (time.time() - cast_at_ts) > RECEIPT_MAX_AGE_DAYS * 86400:
        return {'valid': False, 'reason': (
            'This receipt has expired — receipts are verifiable for '
            f'{RECEIPT_MAX_AGE_DAYS // 30} months after the vote.')}

    vote_ids = data.get('v') or []
    rows = list(Vote.objects.filter(id__in=vote_ids))
    missing = len(vote_ids) - len(rows)
    intact = missing == 0 and _choice_digest(rows) == data.get('c')
    if intact:
        reason = ''
    elif missing:
        reason = f'{missing} ballot(s) from this receipt no longer exist in the database.'
    else:
        reason = 'The recorded choices no longer match this receipt.'
    return {
        'valid': True,
        'intact': intact,
        'missing': missing,
        'ballots': len(vote_ids),
        'legislation': Legislation.objects.filter(id=data.get('l')).first(),
        'voter_pk': data.get('u'),
        'cast_at': data.get('t'),
        'reason': reason,
    }
