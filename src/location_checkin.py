"""
Location check-in (v3.60.0): a member whose phone is inside an event's
check-in area while its window is open is marked present, as if they had
scanned the officer's QR code (src/view/event_checkin.py).

The app registers each upcoming area as an iOS geofence and, when the phone
enters one (or the app is opened inside one), sends its position here. The
server makes every decision again: the window, the distance, the GPS accuracy,
the member's status and the event's state. A phone's location can be faked by
someone determined; that is accepted (the chapter's call, 10-27-26), and the
attendance note records how and how far away, so officers can see and override.

Window: from WINDOW_BEFORE before the event's start until WINDOW_AFTER after it
(events have no end time).
"""
import math
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from src.utils.visibility import visible_to_q

WINDOW_BEFORE = timedelta(minutes=60)
WINDOW_AFTER = timedelta(hours=3)
#: How far ahead the app is told about areas (it registers at most MAX_AREAS geofences; iOS allows 20).
LOOKAHEAD = timedelta(days=7)
MAX_AREAS = 15
MIN_RADIUS_M, MAX_RADIUS_M = 25, 1000
#: A fix worse than this is refused (the app retries with a better one).
MAX_ACCURACY_M = 150
#: Up to this much of the fix's own uncertainty is given the benefit of the doubt.
ACCURACY_ALLOWANCE_M = 50
FLAGS = ('attendance_tracking', 'event_attendance', 'location_checkin')

#: Results that aren't a check-in: (HTTP status, message for the member).
REFUSALS = {
    'not_open': (409, 'Check-in for this event isn’t open right now.'),
    'too_far': (403, 'You don’t seem to be at the event yet.'),
    'inaccurate': (422, 'Your location isn’t precise enough. Try again in a moment.'),
    'no_area': (404, 'This event has no check-in area.'),
    'finalized': (409, 'Attendance for this event is already final.'),
    'not_eligible': (403, 'Only active members check in this way. Ask an officer.'),
    'already_marked': (409, 'An officer has already marked your attendance for this event.'),
}


def enabled():
    from src.models_feature_flags import FeatureFlag
    return all(FeatureFlag.resolve_many(list(FLAGS)).values())


def has_area(event):
    return (event.checkin_latitude is not None and event.checkin_longitude is not None
            and bool(event.checkin_radius_m))


def window(event):
    return event.date_time - WINDOW_BEFORE, event.date_time + WINDOW_AFTER


def distance_m(lat1, lng1, lat2, lng2):
    """Great-circle distance in meters (haversine; plenty for a few hundred meters)."""
    r = 6_371_000
    p1, p2 = math.radians(float(lat1)), math.radians(float(lat2))
    dp = p2 - p1
    dl = math.radians(float(lng2) - float(lng1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def clean_point(lat, lng, radius):
    """``(lat, lng, radius)`` as stored, or raise ValueError with a message for the officer."""
    try:
        lat, lng = Decimal(str(lat)).quantize(Decimal('0.000001')), Decimal(str(lng)).quantize(Decimal('0.000001'))
        radius = int(radius)
    except (ArithmeticError, TypeError, ValueError):
        raise ValueError('Latitude, longitude and radius must be numbers.')
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise ValueError('That isn’t a place on Earth.')
    if not MIN_RADIUS_M <= radius <= MAX_RADIUS_M:
        raise ValueError(f'The radius must be between {MIN_RADIUS_M} and {MAX_RADIUS_M} meters.')
    return lat, lng, radius


def present_ids(user, events):
    from src.models import Attendance
    return set(Attendance.objects.filter(user=user, attendance_type='event', event__in=events,
                                         status__in=('present', 'late'))
               .values_list('event_id', flat=True))


def upcoming_areas(user, now=None):
    """The check-in areas the app should watch for ``user``, soonest first."""
    from src.models import Event
    now = now or timezone.now()
    if user.member_status != 'Active':
        return []
    rows = list(Event.objects
                .filter(is_active=True, archived=False, requires_attendance=True,
                        attendance_finalized=False, checkin_radius_m__isnull=False,
                        checkin_latitude__isnull=False, checkin_longitude__isnull=False,
                        date_time__gt=now - WINDOW_AFTER, date_time__lt=now + LOOKAHEAD)
                .filter(visible_to_q(user.member_type))
                .only('id', 'title', 'date_time', 'checkin_latitude', 'checkin_longitude', 'checkin_radius_m')
                .order_by('date_time')[:MAX_AREAS])
    done = present_ids(user, rows)
    out = []
    for e in rows:
        opens, closes = window(e)
        out.append({
            'id': e.id,
            'title': e.title,
            'start': e.date_time.isoformat(),
            'latitude': float(e.checkin_latitude),
            'longitude': float(e.checkin_longitude),
            'radius_m': e.checkin_radius_m,
            'opens_at': opens.isoformat(),
            'closes_at': closes.isoformat(),
            'checked_in': e.id in done,
        })
    return out


def location_check_in(request, user, event, lat, lng, accuracy, now=None):
    """
    Check ``user`` in to ``event`` from a phone position. Returns
    ``(result, created, distance)``: result is ``'success'`` or a key of
    REFUSALS. Only fills a missing or still-pending row, so a QR scan or an
    officer's marking (present, late, absent or excused) stays as it was: an
    automatic, four-hour check-in must not undo an officer's "absent" (10-28-26).
    """
    from src.models import ActivityLog, Attendance
    now = now or timezone.now()
    if not has_area(event):
        return 'no_area', False, None
    if event.attendance_finalized:
        return 'finalized', False, None
    if user.member_status != 'Active':
        return 'not_eligible', False, None
    opens, closes = window(event)
    if not opens <= now <= closes:
        return 'not_open', False, None
    if accuracy is None or not math.isfinite(accuracy) or accuracy < 0 or accuracy > MAX_ACCURACY_M:
        return 'inaccurate', False, None
    distance = distance_m(lat, lng, event.checkin_latitude, event.checkin_longitude)
    if distance - min(accuracy, ACCURACY_ALLOWANCE_M) > event.checkin_radius_m:
        return 'too_far', False, round(distance)

    existing = Attendance.objects.filter(event=event, user=user, attendance_type='event').first()
    if existing and existing.status in ('present', 'late'):
        return 'success', False, round(distance)
    if existing and existing.status != 'pending':
        # An officer already marked this member absent or excused: leave it.
        return 'already_marked', False, round(distance)
    Attendance.objects.update_or_create(
        event=event, user=user, attendance_type='event',
        defaults={
            'status': 'present',
            'marked_by': user,
            'marked_at': now,
            'notes': f'Checked in by location ({round(distance)} m from the check-in spot, '
                     f'GPS ±{round(accuracy)} m)',
        },
    )
    ActivityLog.log_activity(
        action_type='attendance_taken', user=user,
        description=f'{user.name} checked in to {event.title} by location ({round(distance)} m)',
        request=request, object_type='Event', object_id=event.id, object_repr=str(event),
    )
    return 'success', True, round(distance)


def area_json(event):
    if not has_area(event):
        return None
    return {'latitude': float(event.checkin_latitude), 'longitude': float(event.checkin_longitude),
            'radius_m': event.checkin_radius_m}

