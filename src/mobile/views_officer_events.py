"""
The officers' event lists in the app (v4.4.0), each the website page's own
data and rules:

* ``GET officer/events/all/?section=upcoming|past&q=&page=`` —
  ``view_all_events``: every event that isn't archived, upcoming soonest first
  and past newest first (``all_events_querysets``, shared with the page).
  Officers, chairs and advisors (``officer_or_advisor_required``).
* ``GET officer/events/archived/?q=&page=`` — ``view_archived_events``: every
  archived event, newest first (``archived_events_queryset``). Admins only
  (``admin_required``).
* ``POST officer/events/<id>/archive/`` and ``POST officer/events/<id>/unarchive/``
  — ``archive_event`` / ``unarchive_event`` (``set_event_archived``): archiving
  hides the event, unarchiving shows it again. Admins only.

The pages show everything at once; the app pages them (``PAGE_SIZE``) and
adds a title/description/location search. Editing goes through the existing
``officer/events/<id>/`` (``edit_event``: officers and chairs, so ``can_edit``
is false for advisors, who see the list read-only, as the web's Edit button
refuses them).
"""
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.models import Event
from src.permissions import user_is_officer_or_chair
from src.view.officer.archive_event import set_event_archived
from src.view.officer.view_all_events import all_events_querysets
from src.view.officer.view_archived_events import archived_events_queryset

PAGE_SIZE = 25
Q_MAX = 100
SECTIONS = ('upcoming', 'past')


class _OfficersAndAdvisors(MobileAPIView):
    web_page_toggle = False   # the officer pages have no page toggles

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.can_view_officer_pages:
            raise exceptions.PermissionDenied('Officers and advisors only.', code='forbidden')


class _Admins(MobileAPIView):
    web_page_toggle = False

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.is_admin:
            raise exceptions.PermissionDenied('Admins only.', code='forbidden')


def _search(qs, q):
    if not q:
        return qs
    return qs.filter(Q(title__icontains=q) | Q(description__icontains=q) | Q(location__icontains=q))


def _clean_q(request):
    return (request.query_params.get('q') or '').strip()[:Q_MAX]


def _page_number(request):
    try:
        return max(1, int(request.query_params.get('page') or 1))
    except (TypeError, ValueError):
        return 1


def _row(e, user):
    can_edit = user_is_officer_or_chair(user)
    return {
        'id': e.id,
        'title': e.title,
        'description': e.description or '',
        'date_time': e.date_time.isoformat(),
        'location': e.location or '',
        'is_active': e.is_active,
        'archived': e.archived,
        'created_by': e.created_by.get_display_name() if e.created_by_id else None,
        'requires_attendance': e.requires_attendance,
        'is_recurring': bool(e.is_recurring or e.parent_event_id),
        # The member event page shows only active, unarchived events.
        'viewable': e.is_active and not e.archived,
        'web_path': reverse('edit_event', args=[e.id]) if can_edit else None,
    }


def _paged(request, qs, extra):
    paginator = Paginator(qs, PAGE_SIZE)
    page = paginator.get_page(_page_number(request))
    user = request.user
    return Response({
        **extra,
        'page': page.number,
        'pages': paginator.num_pages,
        'total': paginator.count,
        'has_next': page.has_next(),
        'events': [_row(e, user) for e in page.object_list],
        'can_edit': user_is_officer_or_chair(user),
        'can_archive': bool(user.is_admin),
    })


class OfficerAllEventsView(_OfficersAndAdvisors):
    web_page = 'view_all_events'

    def get(self, request):
        section = request.query_params.get('section') or 'upcoming'
        if section not in SECTIONS:
            raise BadRequest('Section must be upcoming or past.', code='invalid_input')
        q = _clean_q(request)
        upcoming, past = all_events_querysets()
        upcoming, past = _search(upcoming, q), _search(past, q)
        counts = {'upcoming': upcoming.count(), 'past': past.count(),
                  # The archive is admins' (view_archived_events); null hides its tab.
                  'archived': _search(archived_events_queryset(), q).count() if request.user.is_admin else None}
        return _paged(request, upcoming if section == 'upcoming' else past,
                      {'section': section, 'q': q, 'counts': counts})


class OfficerArchivedEventsView(_Admins):
    web_page = 'view_archived_events'

    def get(self, request):
        q = _clean_q(request)
        return _paged(request, _search(archived_events_queryset(), q), {'section': 'archived', 'q': q})


class OfficerArchiveEventView(_Admins):
    web_page = 'archive_event'
    archived = True

    def post(self, request, event_id):
        event = get_object_or_404(Event, pk=event_id)
        set_event_archived(event, self.archived)
        event.refresh_from_db()
        return Response({'event': _row(event, request.user)})


class OfficerUnarchiveEventView(OfficerArchiveEventView):
    web_page = 'unarchive_event'
    archived = False
