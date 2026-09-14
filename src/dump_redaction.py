"""
v3.29.35 — shared redaction for `manage.py dump_db`.

Raised by Mason: `dump_db` walks `apps.get_models()` and prints every field
of every row to stdout, unfiltered. That's a genuine gap in the same
"admin confidentiality boundary" documented at length elsewhere (Kai case
content, Slating interview notes, anonymous-vote/poll identities, stored
secrets) — all of that boundary was built at the admin/API/CSV/search layer,
and this command sits underneath all of it, reading straight off the ORM.
None of the existing redaction helpers (`kai_audit.py`, the admin's
`exclude`-respecting `export_as_csv`) run anywhere near this code path.

Two categories, both redacted to a keyed digest rather than dropped
outright — the point is a dump you can still diff/inspect structurally
(row exists, value changed between two dumps, field is populated vs blank)
without the plaintext ever reaching stdout, a log file, or a piped-to-file
export. This is "encrypted/hashed at the minimum," not full omission,
per Mason's own phrasing when raising this.

1. WHOLE-MODEL: every model defined in a confidential module (Kai, Kai
   commendations, Slating) has every field redacted except pks, FK ids, and
   timestamps — mirrors `_model_is_confidential` in `src/models/activity.py`,
   which uses the same module-derivation technique for a narrower job (only
   `models.kai`, only the `title`/`name` shortcut fields). Deliberately NOT
   reusing that function directly: this needs a wider module set (Slating,
   Kai commendations) and a wider field set (every field, not just
   title/name), and importing an activity-log-specific helper into a dump
   tool for an unrelated job is the kind of coupling that breaks quietly
   when one side changes for its own reasons.

2. NAMED FIELDS: known secrets/credentials that live on otherwise-ordinary
   models — the same list `export_as_csv`'s `exclude`-respecting fix
   (v3.16.2) closed for CSV exports: `APIToken.key`,
   `EmailVerificationToken.token`, `PushSubscription.p256dh`/`.auth`,
   `WebAuthnCredential.credential_id`/`.public_key` — plus `password` on
   every model (it's already hashed, but a dump tool printing a crackable
   hash regardless of strength is unnecessary exposure) and any field using
   `EncryptedFieldMixin` (currently only IP-address fields — see
   `src/encrypted_fields.py` — but `from_db_value` decrypts transparently
   for any Python-level read, so `dump_db` would otherwise print the
   plaintext straight through the encryption and defeat it entirely).

⚠️ NOT COVERED, deliberately left for a separate pass if Mason wants it:
per-row anonymous-vote/poll redaction (`Vote`/`CommitteeVote` where
`legislation.anonymous_vote`, `AnnouncementPollAnswer` where
`response.poll.is_anonymous`) — that needs a relation traversal per row,
which this dump tool's flat "one field at a time" loop isn't structured
for, and guessing at that structure risks getting it wrong more than the
value of covering it in this pass. Flag it, don't fake it.
"""
import hashlib

from src.encrypted_fields import EncryptedFieldMixin

#: Module suffixes whose models are confidential in full. Checked with
#: `.endswith(...)` against `model.__module__`, same technique as
#: `_model_is_confidential` in `src/models/activity.py` — derived from the
#: module, not a hand-typed model list, so a new Kai/Slating model is
#: covered by whoever adds the file.
CONFIDENTIAL_MODULES = (
    'models.kai',
    'models.kai_commendations',
    'models.slating',
)

#: Field names on a confidential model that are still safe to show — they
#: identify a *row*, not a *person* or its content, and keeping them
#: readable is what makes a redacted dump still useful for structural
#: debugging (row counts, which case a related row belongs to, when it
#: happened).
_SAFE_ON_CONFIDENTIAL_MODELS = frozenset({
    'id', 'pk', 'created_at', 'updated_at', 'submitted_at', 'occurred_at',
})

#: (model class name, field name) pairs that are secrets regardless of
#: which module they live in. Same list `export_as_csv`'s `exclude` fix
#: (v3.16.2) closed for CSV exports — kept in sync by hand since the two
#: live in different files for different reasons; if you add a secret field
#: to a ModelAdmin's `exclude`, add it here too.
_NAMED_SECRET_FIELDS = frozenset({
    ('APIToken', 'key'),
    ('EmailVerificationToken', 'token'),
    ('PushSubscription', 'p256dh'),
    ('PushSubscription', 'auth'),
    ('WebAuthnCredential', 'credential_id'),
    ('WebAuthnCredential', 'public_key'),
})


def _is_confidential_model(model):
    module = getattr(model, '__module__', '') or ''
    return any(module.endswith(suffix) for suffix in CONFIDENTIAL_MODULES)


def _digest(value):
    """
    A short, keyless digest — NOT for security (no secret, not meant to
    resist a dictionary attack on short/guessable values), only to let two
    dumps be compared for "did this change" without ever printing the
    plaintext. `str(value)` first so this never raises on a non-string
    field (dates, decimals, FKs that slipped through).
    """
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:16]


def redacted_field_value(obj, field):
    """
    Returns the value `dump_db` should print for `field` on `obj` — the
    real value if nothing about it is confidential, or a
    `[REDACTED sha256:...]` marker otherwise. `None`/empty values are
    passed through unredacted (nothing to protect, and "was this ever
    filled in" is exactly the kind of structural fact a redacted dump
    should still answer).
    """
    value = getattr(obj, field.name, None)
    if value in (None, ''):
        return value

    model = obj.__class__

    if _is_confidential_model(model) and field.name not in _SAFE_ON_CONFIDENTIAL_MODELS:
        return f'[REDACTED sha256:{_digest(value)}]'

    if (model.__name__, field.name) in _NAMED_SECRET_FIELDS:
        return f'[REDACTED sha256:{_digest(value)}]'

    if field.name == 'password':
        return f'[REDACTED sha256:{_digest(value)}]'

    if isinstance(field, EncryptedFieldMixin):
        return f'[REDACTED sha256:{_digest(value)}]'

    return value
