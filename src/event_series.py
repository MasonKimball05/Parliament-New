"""
Bulk changes to a recurring event series (v3.36.0, 09-29-26).

A recurring event is stored as one ROOT row (`is_recurring=True`, no
`parent_event`) plus one row per generated occurrence (`parent_event=root`).
Until v3.36.0 every edit and delete touched exactly one row, so moving a
weekly meeting from 7 to 8 PM meant editing up to 52 events by hand. Worse,
deleting the root row silently CASCADE-deleted every occurrence, including
past ones with attendance.

Scopes (the same three Google Calendar uses):

  * ``this``      — only the event being edited (the old behaviour);
  * ``following`` — this event and every later one in the series;
  * ``all``       — every upcoming event in the series, plus this one.

Rules shared by edit and delete:

  * Events whose attendance is FINALIZED are never touched by a bulk change.
    Their attendance record is history. ``all`` also leaves past events
    alone, so "all" never rewrites what already happened.
  * Edit propagates only the fields the officer actually CHANGED on the form,
    so a one-off tweak on another occurrence (a different room one week)
    survives a later series-wide title change.
  * A change to the date/time moves every affected event by the same amount
    (e.g. +1 hour), and excuse deadlines move with them.
  * The recurrence pattern itself (weekly → biweekly) is not edited in bulk;
    delete the following events and create a new series for that.
"""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

SCOPE_THIS, SCOPE_FOLLOWING, SCOPE_ALL = 'this', 'following', 'all'
SCOPES = (SCOPE_THIS, SCOPE_FOLLOWING, SCOPE_ALL)

# Fields a bulk edit may copy from the edited event to the rest of the series.
# Deliberately NOT here: date_time / excuse_deadline (shifted, not copied, see
# apply_series_edit), the recurrence_* fields and is_recurring (the pattern
# lives on the root only), rsvp_email_enabled (one chapter-wide email per event
# would become one per occurrence), and every *_sent_at / attendance_* field
# (per-row state, never configuration).
SERIES_FIELDS = (
    'title', 'description', 'location', 'visible_to', 'is_active', 'committee',
    'requires_attendance', 'allow_excuses',
    'requires_signup', 'max_signups', 'signups_open', 'allow_waitlist',
    'reminder_1_enabled', 'reminder_1_hours_before', 'reminder_1_email_enabled',
    'reminder_2_enabled', 'reminder_2_hours_before', 'reminder_2_email_enabled',
)

# Copied onto a promoted root when the old root is deleted but occurrences remain.
RECURRENCE_FIELDS = ('recurrence_type', 'recurrence_interval', 'recurrence_unit',
                     'recurrence_days', 'recurrence_end_date')


def clean_scope(value):
    return value if value in SCOPES else SCOPE_THIS


def series_root(event):
    """The root row of `event`'s series, or None when it isn't in one."""
    if event.parent_event_id:
        return event.parent_event
    return event if event.is_recurring else None


def series_events(root):
    from src.models import Event
    return Event.objects.filter(Q(pk=root.pk) | Q(parent_event_id=root.pk))


def affected_events(event, scope, now=None, pivot=None):
    """Queryset of the events a change with `scope` applies to (always includes `event`).

    `pivot` is where "following" starts; default `event.date_time`. An edit
    passes the time the event had BEFORE the form moved it: after a +8 day
    move, `event.date_time` would skip the next weekly occurrence, and after a
    -7 day move it would pull in the previous one.
    """
    from src.models import Event
    root = series_root(event)
    if scope == SCOPE_THIS or root is None:
        return Event.objects.filter(pk=event.pk)
    qs = series_events(root).filter(attendance_finalized=False)
    if scope == SCOPE_FOLLOWING:
        qs = qs.filter(date_time__gte=event.date_time if pivot is None else pivot)
    else:  # SCOPE_ALL
        qs = qs.filter(Q(date_time__gte=now or timezone.now()) | Q(pk=event.pk))
    return qs | Event.objects.filter(pk=event.pk)


def scope_counts(event, now=None):
    """{scope: number of events it would touch}, or None when not in a series of 2+."""
    root = series_root(event)
    if root is None or series_events(root).count() < 2:
        return None
    return {s: affected_events(event, s, now).distinct().count() for s in SCOPES}


def apply_series_edit(event, before, changed_fields, scope, now=None):
    """Copy an edit already saved on `event` to the rest of its `scope`.

    `before` is {field: value} captured BEFORE the form touched `event`
    (at least date_time and excuse_deadline). Returns how many OTHER events
    were updated.
    """
    if scope == SCOPE_THIS:
        return 0
    copy = [f for f in SERIES_FIELDS if f in changed_fields]
    delta = event.date_time - before['date_time']
    deadline_changed = 'excuse_deadline' in changed_fields
    if not copy and not delta and not deadline_changed:
        return 0

    others = list(affected_events(event, scope, now, pivot=before['date_time']).exclude(pk=event.pk).distinct())
    for other in others:
        for f in copy:
            setattr(other, f, getattr(event, f))
        if delta:
            other.date_time = other.date_time + delta
        if deadline_changed:
            # Keep the same distance before the event that the officer just set.
            other.excuse_deadline = (None if event.excuse_deadline is None
                                     else other.date_time + (event.excuse_deadline - event.date_time))
        elif delta and other.excuse_deadline:
            other.excuse_deadline = other.excuse_deadline + delta
    fields = list(copy)
    if delta:
        fields.append('date_time')
    if delta or deadline_changed:
        fields.append('excuse_deadline')
    if others and fields:
        from src.models import Event
        with transaction.atomic():
            Event.objects.bulk_update(others, fields)
    return len(others)


def delete_series_events(event, scope, now=None):
    """Delete `event` (and its `scope`) without cascading into events that should stay.

    Deleting the root row would CASCADE-delete every occurrence. When the root
    is deleted but other occurrences remain (past ones, finalized ones, or the
    earlier part of a ``following`` split), the earliest survivor becomes the
    new root first. Returns (deleted_count, kept_finalized_count).
    """
    from src.models import Event
    root = series_root(event)
    doomed_ids = set(affected_events(event, scope, now).values_list('pk', flat=True))
    kept_finalized = 0
    with transaction.atomic():
        if root is not None:
            series = series_events(root)
            if scope != SCOPE_THIS:
                later = series.filter(date_time__gte=event.date_time if scope == SCOPE_FOLLOWING
                                      else (now or timezone.now()))
                kept_finalized = later.filter(attendance_finalized=True).exclude(pk=event.pk).count()
            # A list, not a queryset: `series` is keyed on the OLD root's pk,
            # so after the promotion below it would match nothing (v3.38.2 —
            # the end date was never trimmed when the root itself was deleted).
            survivors = list(series.exclude(pk__in=doomed_ids).order_by('date_time', 'pk'))
            if root.pk in doomed_ids and survivors:
                new_root = survivors[0]
                for f in RECURRENCE_FIELDS:
                    setattr(new_root, f, getattr(root, f))
                new_root.is_recurring = True
                new_root.parent_event = None
                new_root.save(update_fields=list(RECURRENCE_FIELDS) + ['is_recurring', 'parent_event'])
                Event.objects.filter(pk__in=[e.pk for e in survivors[1:]]).update(parent_event=new_root)
                root = new_root
            if scope == SCOPE_FOLLOWING and root.pk not in doomed_ids:
                # The series now ends at its last surviving event.
                last = max(survivors, key=lambda e: e.date_time, default=None)
                if last is not None:
                    root.recurrence_end_date = timezone.localtime(last.date_time).date()
                    root.save(update_fields=['recurrence_end_date'])
        deleted = len(doomed_ids)
        Event.objects.filter(pk__in=doomed_ids).delete()
    return deleted, kept_finalized
