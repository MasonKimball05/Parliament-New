"""
Live editing on the resolution edit page (v3.42.0, 10-02-26).

Mason, during a bylaws committee meeting: "the edit resolution page should
have a websocket so work isn't written and overwritten by multiple people
working on it at the same time."

Three layers, each of which works without the one above it:

1. **Field-level saves + a conflict check** (`edit_resolution`). The page
   says which fields this person changed and what each held when they loaded
   it. Only those fields are written, and a field someone else changed in the
   meantime is NOT overwritten: the editor gets their own text back next to
   the other version. This is the part that protects data, and it does not
   need the socket.
2. **Field locks** (`ResolutionEditConsumer`). Focusing a field takes a lock;
   everyone else sees it read-only with the holder's name. Locks live in the
   cache (shared across Daphne processes via Redis in prod) as one key per
   field, taken with `cache.add`, which is atomic. They expire unless the
   holder's page keeps refreshing them, so a closed laptop cannot hold a
   field forever.
3. **Presence + live updates.** Who else is on the page; and when someone
   saves, everyone else's untouched fields update in place.

Everything here is keyed by resolution id. The field list is the single
source of truth for the view, the consumer and the page.
"""
import json

from django.core.cache import cache

#: The main form's fields, in page order. `vote_date` is a date; the rest text.
FIELDS = (
    'title', 'resolution_type', 'authors', 'sponsors', 'vote_date',
    'whereas_clauses', 'resolved_text', 'resolution_body', 'additional_notes',
)
FIELD_LABELS = {
    'title': 'Title', 'resolution_type': 'Type', 'authors': 'Author(s)',
    'sponsors': 'Sponsor(s)', 'vote_date': 'Vote date',
    'whereas_clauses': 'Whereas clauses', 'resolved_text': 'Resolved text',
    'resolution_body': 'Resolution body', 'additional_notes': 'Additional notes',
}

#: A lock not refreshed for this long is gone. The page refreshes every 25s.
LOCK_TTL_SECONDS = 75


import re

#: v3.44.0 — the amendment editor is lockable too: one lock per section being
#: amended, named `amend:<section id>`. Same store, same rules as a field.
AMEND_KEY = re.compile(r'^amend:\d{1,12}$')


def lockable(name):
    return name in FIELDS or (isinstance(name, str) and bool(AMEND_KEY.match(name)))


def _amend_index_key(resolution_id):
    return f'cnbres:{resolution_id}:amendlocks'


def group_name(resolution_id):
    return f'cnb_res_{resolution_id}'


def _lock_key(resolution_id, field):
    return f'cnbres:{resolution_id}:lock:{field}'


def norm(value):
    """Text as compared for "did this change": LF line endings, trimmed."""
    return (value or '').replace('\r\n', '\n').replace('\r', '\n').strip()


def field_value(resolution, field):
    """A field's saved value, as the string the form would show."""
    value = getattr(resolution, field)
    if field == 'vote_date':
        return value.isoformat() if value else ''
    return norm(value)


def baseline(resolution):
    return {f: field_value(resolution, f) for f in FIELDS}


# ── Locks ────────────────────────────────────────────────────────────────────

def acquire_lock(resolution_id, field, owner):
    """
    Take `field` for `owner` ({'cid', 'uid', 'name'}). Returns the holder:
    `owner` itself on success, or whoever already has it.
    """
    key = _lock_key(resolution_id, field)
    payload = json.dumps(owner)
    if field not in FIELDS:
        # Amendment locks have open-ended names, so `current_locks` needs a
        # list of the ones in play. A stale entry is harmless (its lock key
        # has expired and is skipped); a lost update only delays a newcomer
        # seeing a lock until the holder's next message.
        index = set(cache.get(_amend_index_key(resolution_id)) or [])
        if field not in index:
            index.add(field)
            cache.set(_amend_index_key(resolution_id), sorted(index), 6 * 3600)
    if cache.add(key, payload, LOCK_TTL_SECONDS):
        return owner
    held = cache.get(key)
    if held is None:                      # expired between add() and get()
        cache.set(key, payload, LOCK_TTL_SECONDS)
        return owner
    holder = json.loads(held)
    if holder.get('cid') == owner['cid']:
        cache.set(key, payload, LOCK_TTL_SECONDS)
        return owner
    return holder


def release_lock(resolution_id, field, cid):
    """Release `field` if `cid` holds it. Returns True if it was released."""
    key = _lock_key(resolution_id, field)
    held = cache.get(key)
    if held and json.loads(held).get('cid') == cid:
        cache.delete(key)
        return True
    return False


def refresh_locks(resolution_id, fields, cid):
    for field in fields:
        key = _lock_key(resolution_id, field)
        held = cache.get(key)
        if held and json.loads(held).get('cid') == cid:
            cache.set(key, held, LOCK_TTL_SECONDS)


def current_locks(resolution_id):
    names = list(FIELDS) + [n for n in (cache.get(_amend_index_key(resolution_id)) or []) if lockable(n)]
    found = cache.get_many([_lock_key(resolution_id, f) for f in names])
    return {
        f: json.loads(found[_lock_key(resolution_id, f)])
        for f in names if _lock_key(resolution_id, f) in found
    }


def force_release(resolution_id, field):
    """The chair takes a lock away (v3.44.0). Returns whoever held it, or None."""
    key = _lock_key(resolution_id, field)
    held = cache.get(key)
    if not held:
        return None
    cache.delete(key)
    return json.loads(held)


# ── Broadcasts from the HTTP views ───────────────────────────────────────────

def _send(resolution_id, event):
    """Best effort: a save must never fail because the channel layer is down."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is not None:
            async_to_sync(layer.group_send)(group_name(resolution_id), event)
    except Exception:  # noqa: BLE001 - see docstring
        import logging
        logging.getLogger('src').warning('cnb_live: broadcast failed', exc_info=True)


def broadcast_saved(resolution, changed_fields, user):
    if not changed_fields:
        return
    _send(resolution.pk, {
        'type': 'res.saved',
        'by': user.get_display_name(),
        'uid': str(user.pk),
        'fields': {f: field_value(resolution, f) for f in changed_fields},
    })


def broadcast_amendments_changed(resolution_id, user, what=''):
    """`what` is a plain phrase for the page: "added an amendment to Art. III § 3"."""
    _send(resolution_id, {
        'type': 'res.amendments', 'by': user.get_display_name(), 'uid': str(user.pk), 'what': what,
    })


#: A draft relayed to the other editors is display-only; cap it anyway.
MAX_DRAFT_CHARS = 60000
