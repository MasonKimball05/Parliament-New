"""
Dev mode — showing the rows a query actually returned, with redaction.

WHAT THIS IS, AND THE RULE IT BENDS
-----------------------------------
`src/dev_mode.py` states, and the Shapes tab enforces, that the panel shows
metadata and never record content: *"Re-running captured SELECTs to show rows
would make the developer allowlist a read-anything key to Kai reports, ballots
and slating notes, bypassing every app-level gate."* That reasoning is still
correct, and this module is the deliberate, bounded exception to it — requested
explicitly, and built so the sentence above stays true for the data it was
written about.

The boundary CLAUDE.md sets is the one that governs here: **being an operator is
not a grant of judicial, deliberative or ballot-level access**, and the
developer allowlist is an operational role that will be handed to a successor.
So the design rule for this module is the opposite of the usual convenience
default:

    ⚠️ REDACTION FAILS CLOSED. A table nobody has classified is withheld, not
    shown. If you add a model and want to inspect its rows here, you have to
    come back and say so — which is the moment to think about whether its
    contents are anyone's business.

WHAT IS WITHHELD, AND WHY
-------------------------
Three layers, in order:

1. **Whole tables, by domain.** Judicial (every Kai model), ballots (Vote,
   CommitteeVote, SlatingVote, SlatingBallot), anonymous poll responses and
   answers, slating interviews and application responses, and the credential
   stores. These are withheld outright rather than column-redacted, because
   CLAUDE.md's own lesson from v3.16.2 is that **a timestamp, a sequence or an
   ordering is a join key** — showing "harmless" columns of a ballot table still
   leaks who voted when.

   The Kai set is *derived from the module* rather than listed, so a new Kai
   model is covered the day it is written.

2. **Columns, everywhere.** Password hashes, tokens, keys, session material,
   WebAuthn credentials, push-subscription secrets, and every field built on
   `EncryptedFieldMixin` — the last of these also derived from the models, since
   the whole point of an encrypted field is that its plaintext is not for
   casual reading.

3. **Statement type.** SELECT only. Never a write, and never a statement with
   more than one statement in it.

HOW THE ROWS ARE OBTAINED
-------------------------
By re-running the captured SQL with its captured parameters, inside a
transaction that is always rolled back, with a row cap. Re-running is what
django-debug-toolbar does and is the only option available after the fact: the
cursor from the original execution is consumed by Django itself.

The re-run means row values are a *second* read, so a query whose result
depended on state changed later in the request may render differently from what
the view saw. That is noted in the panel rather than hidden.
"""

import re

# Statements we will re-run. Anything else — writes, DDL, multi-statement — is
# refused outright rather than parsed cleverly.
_SELECT_RE = re.compile(r'^\s*SELECT\b', re.IGNORECASE)

#: Never re-run more than this many rows, however many the query returned.
MAX_ROWS = 25

#: Truncate each cell to keep one wide column from swamping the panel.
MAX_CELL = 120

#: Tables whose *contents* are withheld entirely, by domain rather than by
#: column. Kai is added at runtime from the models module; these are the rest.
#:
#: Ballots and anonymity: showing any column of a ballot table — even a
#: timestamp or a row order — is a join key back to a voter. Slating interview
#: notes and application responses are marked CONFIDENTIAL on the models
#: themselves and are subject to notes-destruction. Credential stores hold
#: bearer material.
_EXPLICIT_SENSITIVE_TABLES = {
    # Ballots / deliberation
    'src_vote',
    'src_committeevote',
    'src_slatingvote',
    'src_slatingballot',
    'src_slatinginterview',
    'src_slatingapplicationresponse',
    'src_announcementpollresponse',
    'src_announcementpollanswer',
    # Credentials / bearer material
    'src_apitoken',
    'src_webauthncredential',
    'src_pushsubscription',
    'src_emailverificationtoken',
    'src_calendarsubscription',
    'django_session',
    # Security detail that is not a developer's business by default
    'src_loginhistory',
    'src_honeypotaccess',
}

#: Column-name fragments redacted on every table that is otherwise shown.
_SENSITIVE_COLUMN_FRAGMENTS = (
    'password', 'token', 'secret', 'private', 'session_key',
    'p256dh', 'credential_id', 'public_key', 'backup_code',
)


def _kai_tables():
    """Every table backed by a model in `src/models/kai.py`."""
    from django.apps import apps

    return {
        model._meta.db_table
        for model in apps.get_models()
        if model.__module__.endswith('models.kai')
    }


def _encrypted_columns():
    """{table: {column, …}} for every field built on EncryptedFieldMixin."""
    from django.apps import apps

    from src.encrypted_fields import EncryptedFieldMixin

    found = {}
    for model in apps.get_models():
        for field in model._meta.concrete_fields:
            if isinstance(field, EncryptedFieldMixin):
                found.setdefault(model._meta.db_table, set()).add(field.column)
    return found


def sensitive_tables():
    return _EXPLICIT_SENSITIVE_TABLES | _kai_tables()


def _tables_in(sql):
    """Table names appearing after FROM or JOIN. Deliberately generous."""
    return {
        name.lower()
        for name in re.findall(r'(?:FROM|JOIN)\s+"?([A-Za-z_][A-Za-z0-9_]*)"?', sql,
                               re.IGNORECASE)
    }


def _redact_reason(sql):
    """Why this query's rows will not be shown, or None if they may be."""
    if not _SELECT_RE.match(sql or ''):
        return 'not a SELECT — writes are never re-run'
    if ';' in (sql or '').rstrip().rstrip(';'):
        return 'multiple statements'

    touched = _tables_in(sql)
    if not touched:
        return 'could not identify the tables involved (failing closed)'

    blocked = touched & sensitive_tables()
    if blocked:
        return (
            'confidential table: ' + ', '.join(sorted(blocked)) +
            ' — judicial, ballot, or credential data is withheld by policy, '
            'including its timestamps and row order (they are join keys)'
        )
    return None


def fetch_rows(sql, params):
    """
    Re-run `sql` and return ``(columns, rows, note)``.

    On refusal, returns ``(None, None, reason)``. Never raises: a broken
    inspector must not take down the page it is inspecting.
    """
    reason = _redact_reason(sql)
    if reason:
        return None, None, reason

    from django.db import connection, transaction

    encrypted = _encrypted_columns()
    touched = _tables_in(sql)
    redact_columns = set()
    for table in touched:
        redact_columns |= {c.lower() for c in encrypted.get(table, set())}

    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                columns = [c[0] for c in (cursor.description or [])]
                raw = cursor.fetchmany(MAX_ROWS)
            # Always undo — the statement is a SELECT, but a rollback costs
            # nothing and means a mistake here cannot write.
            transaction.set_rollback(True)
    except Exception as exc:                      # noqa: BLE001 — see docstring
        return None, None, f'could not re-run: {type(exc).__name__}'

    hidden = []
    keep = []
    for index, name in enumerate(columns):
        lowered = (name or '').lower()
        if lowered in redact_columns or any(
                fragment in lowered for fragment in _SENSITIVE_COLUMN_FRAGMENTS):
            hidden.append(name)
        else:
            keep.append(index)

    shown_columns = [columns[i] for i in keep]
    rows = []
    for row in raw:
        cells = []
        for i in keep:
            value = row[i]
            text = '∅' if value is None else str(value)
            if len(text) > MAX_CELL:
                text = text[:MAX_CELL] + '…'
            cells.append(text)
        rows.append(cells)

    note = 'values re-read after the request — may differ from what the view saw'
    if hidden:
        note += f'; {len(hidden)} column(s) redacted: ' + ', '.join(sorted(hidden))
    if len(raw) == MAX_ROWS:
        note += f'; capped at {MAX_ROWS} rows'
    return shown_columns, rows, note
