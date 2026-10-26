"""
The app's Calendar tab (mobile plan, Phase 3).

* ``GET    events/?from=YYYY-MM-DD&to=YYYY-MM-DD`` — visible events in a date
  range (chapter time zone, ``to`` inclusive). Defaults to today + 31 days;
  at most 93 days per request, within a year either side of today (the web
  calendar's own window).
* ``GET    events/<id>/`` — one event. Hidden, inactive or archived is a 404.
* ``POST   events/<id>/signup/`` — sign up (or join the waitlist).
* ``DELETE events/<id>/signup/`` — cancel.
* ``GET    calendar/subscription/`` — the member's ``webcal://`` feed URL,
  for "Subscribe" (behind the ``calendar_subscriptions`` flag, like the web).

Same rules as the website: the ``visible_to_q`` audience rule (the SQL twin of
``Event.is_visible_to_user``, kept equal by its own test), sign-ups through
``src/event_signups.py`` (the web buttons' code), excuses through
``Event.can_submit_excuse``. Excuses are submitted in the web view.

Unlike ``/api/calendar-data/``, sign-up counts and the member's own sign-ups
and excuses are fetched for the whole page in one query each, not per event.
``updated_at`` lets the app refresh a cached event when it changes.
"""
from datetime import date, datetime, timedelta

import pytz
from django.conf import settings
from django.db.models import Count, Q
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.response import Response

from src import event_signups
from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView, check_web_page
from src.models import AttendanceExcuse, Event, EventSignup
from src.models.users import member_defer
from src.models_calendar_subscription import CalendarSubscription
from src.utils.visibility import visible_to_q

DEFAULT_DAYS = 31
MAX_DAYS = 93
WINDOW = timedelta(days=365)


class SignupRefused(exceptions.APIException):
    status_code = 400
    default_code = 'signup_refused'


def _parse_day(raw, name):
    try:
        return date.fromisoformat(raw)
    except (TypeError, ValueError):
        raise exceptions.ValidationError(f'{name} must be YYYY-MM-DD.', code='invalid_request')


def _events_qs():
    return (Event.objects
            .filter(is_active=True, archived=False)
            .select_related('service_event', 'recruitment_event', 'created_by')
            .defer(*member_defer('created_by')))


def _has(event, relation):
    """True if the reverse one-to-one exists (service/recruitment event)."""
    try:
        return getattr(event, relation) is not None
    except Exception:
        return False


def _serialize(events, user):
    """Events → dicts, with sign-ups and excuses fetched once for all of them."""
    ids = [e.id for e in events]
    signup_ids = [e.id for e in events if e.requires_signup]
    tallies = {
        row['event_id']: row for row in
        EventSignup.objects.filter(event_id__in=signup_ids, is_cancelled=False)
        .values('event_id')
        .annotate(confirmed=Count('id', filter=Q(waitlist_position__isnull=True)),
                  waitlisted=Count('id', filter=Q(waitlist_position__isnull=False)))
    } if signup_ids else {}
    mine = {
        s.event_id: s for s in
        EventSignup.objects.filter(event_id__in=signup_ids, user=user, is_cancelled=False)
        .only('event_id', 'waitlist_position')
    } if signup_ids else {}
    excuses = {
        x.event_id: x.status for x in
        AttendanceExcuse.objects.filter(event_id__in=ids, user=user).only('event_id', 'status')
    } if ids else {}

    out = []
    for e in events:
        local = timezone.localtime(e.date_time)
        signup = None
        if e.requires_signup:
            t = tallies.get(e.id, {})
            confirmed = t.get('confirmed', 0)
            own = mine.get(e.id)
            signup = {
                'open': e.signups_open,
                'max': e.max_signups,
                'allow_waitlist': e.allow_waitlist,
                'count': confirmed,
                'waitlist_count': t.get('waitlisted', 0),
                'full': e.max_signups is not None and confirmed >= e.max_signups,
                'status': (None if own is None else
                           'waitlist' if own.waitlist_position is not None else 'confirmed'),
                'waitlist_position': own.waitlist_position if own else None,
            }
        out.append({
            'id': e.id,
            'title': e.title,
            'description': e.description,
            'start': e.date_time.isoformat(),
            'location': e.location or '',
            'created_by': e.created_by.get_display_name(),
            'requires_attendance': e.requires_attendance,
            'is_service_event': _has(e, 'service_event'),
            'is_recruitment_event': _has(e, 'recruitment_event'),
            'excuse': {
                'allowed': e.allow_excuses,
                'can_submit': e.can_submit_excuse() if e.allow_excuses else False,
                'status': excuses.get(e.id),
            },
            'signup': signup,
            'updated_at': (e.updated_at or e.created_at).isoformat(),
            'links': {
                'ics': reverse('export_event_ical', args=[e.id]),
                'google': e.google_calendar_url,
                'web': f"{reverse('calendar')}?year={local.year}&month={local.month}",
            },
        })
    return out


class _CalendarBase(MobileAPIView):
    web_page = 'calendar'


class EventListView(_CalendarBase):

    def get(self, request):
        tz = pytz.timezone(settings.TIME_ZONE)
        today = timezone.localtime().date()
        start = _parse_day(request.query_params['from'], 'from') if 'from' in request.query_params else today
        end = (_parse_day(request.query_params['to'], 'to') if 'to' in request.query_params
               else start + timedelta(days=DEFAULT_DAYS - 1))
        if end < start:
            raise exceptions.ValidationError('to is before from.', code='invalid_request')
        if (end - start).days + 1 > MAX_DAYS:
            raise exceptions.ValidationError(f'At most {MAX_DAYS} days per request.',
                                             code='invalid_request')
        # The web calendar's window: a year either side of today.
        lo, hi = today - WINDOW, today + WINDOW
        start, end = max(start, lo), min(end, hi)
        events = []
        if start <= end:
            # Day boundaries in the CHAPTER's time zone, as the web calendar
            # groups them (see calendar_data_api).
            begin = tz.localize(datetime(start.year, start.month, start.day))
            stop = tz.localize(datetime(end.year, end.month, end.day)) + timedelta(days=1)
            events = list(_events_qs()
                          .filter(date_time__gte=begin, date_time__lt=stop)
                          .filter(visible_to_q(request.user.member_type))
                          .order_by('date_time'))
        return Response({
            'from': start.isoformat(),
            'to': end.isoformat(),
            'events': _serialize(events, request.user),
        })


class EventDetailView(_CalendarBase):

    def get(self, request, event_id):
        event = _events_qs().filter(pk=event_id).first()
        if event is None or not event.is_visible_to_user(request.user):
            raise Http404
        return Response({'event': _serialize([event], request.user)[0]})


class EventSignupView(_CalendarBase):
    """The web's sign-up buttons are restrictable by their own url names."""

    def _run(self, request, event_id, action, web_name):
        check_web_page(request.user, web_name, toggle=False)
        try:
            result = action(event_id, request.user)
        except event_signups.SignupError as exc:
            raise SignupRefused(str(exc))
        return Response({'ok': True, **result})

    def post(self, request, event_id):
        return self._run(request, event_id, event_signups.sign_up, 'event_signup')

    def delete(self, request, event_id):
        return self._run(request, event_id, event_signups.cancel, 'event_cancel_signup')


class CalendarSubscriptionView(MobileAPIView):
    web_page = 'get_calendar_subscription_url'
    web_page_toggle = False   # the web view has only the feature flag

    def get(self, request):
        if not check_feature_enabled('calendar_subscriptions'):
            raise exceptions.PermissionDenied('Calendar subscriptions are off.',
                                              code='feature_disabled')
        subscription = CalendarSubscription.get_or_create_for_user(request.user)
        path = reverse('calendar_subscription_feed', kwargs={'token': subscription.token})
        host = request.get_host()
        return Response({
            'webcal_url': f'webcal://{host}{path}',
            'https_url': f'https://{host}{path}',
        })
