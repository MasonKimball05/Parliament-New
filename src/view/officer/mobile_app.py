"""
Mobile App (10-30-26) — the officer portal's page for the app. Officers and
chairs (``officer_required``), three tabs:

* **Notifications** — which members get the app's push notifications. Each
  active member (and advisor) is in one of four states, from their
  ``MobileDevice`` rows:

  - ``on``: a signed-in device with a push token. "Signed in" is
    ``MobileDevice.still_allowed``, the check ``src.mobile.push.live_push_devices``
    makes before sending, so the page and the sender agree. (The page only
    reads; the sender is what clears a signed-out device's token.)
  - ``off``: signed in, but no push token (declined the iOS prompt, turned
    notifications off in Settings, or the app hasn't registered).
  - ``signed_out``: had the app, but every device is revoked or signed out.
  - ``none``: never signed in to the app.

  For ``on`` members, the kinds they've turned off in Preferences are listed.

* **Location check-in** — whether it's on (``src.location_checkin.FLAGS``),
  the upcoming attendance events with and without a check-in area (an area is
  set on the event form), and the saved places, which can be renamed or
  deleted here. A place is copied onto an event, never linked, so deleting
  one moves no event (``CheckinPlace``).

* **App versions** — the app versions on members' signed-in phones, and the
  update-notice builds an admin sets (``admin_v2.APP_UPDATE_DEFAULTS``).
"""
from collections import Counter, defaultdict
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from src import location_checkin as lc
from src.constants import MemberStatus, MemberType
from src.decorators import officer_required
from src.models import ActivityLog, CheckinPlace, Event, ParliamentUser
from src.models.mobile import MobileDevice

TABS = [('notifications', 'Notifications'), ('location', 'Location check-in'), ('versions', 'App versions')]

#: The push kinds in Preferences, as the page names them.
PUSH_LABELS = {
    'announcements': 'Announcements',
    'legislation': 'Legislation',
    'events': 'Events',
    'slating': 'Slating',
    'chat': 'Chat',
    'excuses': 'Excuses',
    'security': 'Security',
    'reminders': 'Reminders',
    'mentions': 'Mentions',
}

STATES = [
    ('on', 'Notifications on'),
    ('off', 'App, notifications off'),
    ('signed_out', 'Signed out of the app'),
    ('none', 'No app'),
]

#: How far ahead the location tab looks for attendance events.
UPCOMING_DAYS = 30


def _muted(user):
    prefs = getattr(user, 'preferences', None)
    if prefs is None:
        return []
    return [label for key, label in PUSH_LABELS.items() if not prefs._pref('push', key, True)]


def member_push_states(members):
    """[(member, state, live_devices, muted)] for ``members``, in their order."""
    devices = defaultdict(list)
    for d in MobileDevice.objects.filter(user__in=members).order_by('-last_seen_at', '-created_at'):
        devices[d.user_id].append(d)
    rows = []
    for m in members:
        mine = devices.get(m.pk, [])
        live = [d for d in mine if d.revoked_at is None and d.still_allowed(m)[0]]
        if any(d.push_token for d in live):
            state = 'on'
        elif live:
            state = 'off'
        elif mine:
            state = 'signed_out'
        else:
            state = 'none'
        rows.append((m, state, live, _muted(m) if state == 'on' else []))
    return rows


def _members():
    # Active members and advisors, as the Member List's default. Full rows (not
    # member_defer): still_allowed compares the password hash.
    return list(
        (ParliamentUser.objects.filter(member_status=MemberStatus.ACTIVE)
         | ParliamentUser.objects.filter(member_type=MemberType.ADVISOR))
        .exclude(member_status=MemberStatus.REMOVED)
        .select_related('preferences')
        .order_by('name')
    )


def _notifications(request, rows):
    counts = {key: 0 for key, _ in STATES}
    for _, state, _, _ in rows:
        counts[state] += 1
    state_filter = request.GET.get('state', 'all')
    if state_filter not in counts:
        state_filter = 'all'
    total = len(rows)
    return {
        'rows': [{
            'member': m,
            'state': state,
            'state_label': dict(STATES)[state],
            'devices': [{
                'platform': d.get_platform_display(),
                'app_version': d.app_version,
                'push': bool(d.push_token),
                'last_seen': d.last_seen_at or d.created_at,
            } for d in live],
            'muted': muted,
        } for m, state, live, muted in rows if state_filter == 'all' or state == state_filter],
        'states': [{'key': k, 'label': label, 'count': counts[k]} for k, label in STATES],
        'state_filter': state_filter,
        'total': total,
        'on_rate': round(100 * counts['on'] / total) if total else None,
    }


def _location():
    now = timezone.now()
    events = list(Event.objects.filter(
        requires_attendance=True, date_time__gte=now - timedelta(hours=3),
        date_time__lte=now + timedelta(days=UPCOMING_DAYS)).order_by('date_time'))
    return {
        'location_on': lc.enabled(),
        'location_flags': lc.FLAGS,
        'upcoming_days': UPCOMING_DAYS,
        'events': [{'event': e, 'has_area': lc.has_area(e)} for e in events],
        'without_area': sum(1 for e in events if not lc.has_area(e)),
        'places': CheckinPlace.objects.select_related('created_by').all(),
    }


def _versions(rows):
    from src.mobile.views_auth import app_update
    versions = Counter()
    members_on = defaultdict(list)
    for m, _, live, _ in rows:
        for v in sorted({(d.get_platform_display(), d.app_version or 'unknown') for d in live}):
            versions[v] += 1
            members_on[v].append(m.get_display_name())
    return {
        'versions': [{'platform': p, 'version': v, 'count': n, 'members': members_on[(p, v)]}
                     for (p, v), n in sorted(versions.items(), key=lambda kv: (kv[0][0], kv[0][1]), reverse=True)],
        'update_ios': app_update('ios'),
        'update_android': app_update('android'),
    }


@login_required
@officer_required
def mobile_app(request):
    tab = request.GET.get('tab', 'notifications')
    if tab not in dict(TABS):
        tab = 'notifications'
    context = {'tab': tab, 'tabs': TABS}
    if tab in ('notifications', 'versions'):
        rows = member_push_states(_members())
        context.update(_notifications(request, rows) if tab == 'notifications' else _versions(rows))
    else:
        context.update(_location())
    return render(request, 'officer/mobile_app.html', context)


@login_required
@officer_required
def mobile_app_place(request, place_id):
    """POST: rename (``name``) or delete (``delete``) a saved check-in place."""
    back = reverse('mobile_app') + '?tab=location'
    if request.method != 'POST':
        return redirect(back)
    place = get_object_or_404(CheckinPlace, pk=place_id)
    if request.POST.get('delete'):
        name = place.name
        place.delete()
        ActivityLog.log_activity(
            action_type='other', user=request.user, description=f'Deleted check-in place "{name}"',
            request=request, object_type='CheckinPlace', object_id=place_id)
        messages.success(request, f'Deleted “{name}”. Events that used it keep their area.')
        return redirect(back)
    name = (request.POST.get('name') or '').strip()[:80]
    if not name:
        messages.error(request, 'A place needs a name.')
        return redirect(back)
    if name != place.name:
        if CheckinPlace.objects.filter(name=name).exclude(pk=place.pk).exists():
            messages.error(request, f'There’s already a place called “{name}”.')
            return redirect(back)
        old = place.name
        place.name = name
        place.save(update_fields=['name'])
        ActivityLog.log_activity(
            action_type='other', user=request.user, description=f'Renamed check-in place "{old}" to "{name}"',
            request=request, object_type='CheckinPlace', object_id=place_id)
        messages.success(request, f'Renamed to “{name}”.')
    return redirect(back)
