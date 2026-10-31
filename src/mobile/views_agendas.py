"""
Meeting agendas in the app (10-30-26): the website's agenda pages, through the
same functions (src/view/officer/meeting_agenda.py).

* ``GET agendas/`` — upcoming and past agendas. Members see published ones;
  officers and chairs drafts too, plus the events one can be linked to.
  ``POST`` ``{title, date, start_time, event_id, standard_order}`` creates one
  (officers).
* ``GET agendas/<id>/`` — the agenda and its items. Officers also get the item
  types and the members a presenter can be picked from.
* ``POST agendas/<id>/items/`` ``{item_type, title, notes, presenter,
  presenter_text, duration_minutes}`` adds an item (officers).
* ``PATCH agendas/<id>/items/<item_id>/`` (same fields) saves one; ``DELETE``
  removes it (officers).
* ``POST agendas/<id>/items/<item_id>/move/`` ``{direction: up|down}`` (officers).
* ``POST agendas/<id>/status/`` ``{status: draft|published}`` (officers).
* ``POST agendas/<id>/start-minutes/`` — the minutes draft laid out like the
  agenda, made once; the app opens it at ``officer/minutes/<id>/``.

Behind feature flag ``meeting_agendas``, as the web pages are. Writes need
``officer_required``'s rule (``user_is_officer_or_chair``).
"""
import datetime

from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView
from src.mobile.views_excuses_polls import BadRequest
from src.models import AgendaItem, Event, MeetingAgenda, ParliamentUser
from src.models.users import member_defer
from src.permissions import user_is_officer_or_chair
from src.view.officer import meeting_agenda as ma
from src.view.officer.chapter_minutes import _linkable_events


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _event_json(e):
    return {'id': e.id, 'title': e.title, 'date_time': e.date_time.isoformat()} if e else None


def _summary(a):
    return {
        'id': a.id,
        'title': a.title,
        'date': a.date.isoformat(),
        'start_time': a.start_time.strftime('%H:%M'),
        'status': a.status,
        'status_label': a.get_status_display(),
        'event': _event_json(a.event),
        'minutes_id': a.minutes_id,
    }


def _item_json(i):
    return {
        'id': i.id,
        'order': i.order,
        'item_type': i.item_type,
        'item_type_label': i.get_item_type_display(),
        'title': i.title,
        'notes': i.notes,
        'presenter': i.presenter.user_id if i.presenter_id else None,
        'presenter_text': i.presenter_text,
        'presenter_display': i.presenter_display,
        'duration_minutes': i.duration_minutes,
    }


class _AgendaView(MobileAPIView):
    web_page_toggle = False   # the agenda pages have only the feature flag
    officers_only = False

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not check_feature_enabled(ma.FLAG):
            raise exceptions.PermissionDenied('Meeting agendas are off.', code='feature_disabled')
        if self.officers_only:
            self.require_officer(request)

    @staticmethod
    def require_officer(request):
        if not user_is_officer_or_chair(request.user):
            raise exceptions.PermissionDenied('Officers and chairs only.', code='forbidden')

    @staticmethod
    def agenda(agenda_id):
        agenda = MeetingAgenda.objects.select_related('event', 'minutes').filter(pk=agenda_id).first()
        if agenda is None:
            raise exceptions.NotFound()
        return agenda

    def detail(self, request, agenda):
        officer = user_is_officer_or_chair(request.user)
        data = {'agenda': _summary(agenda), 'items': [_item_json(i) for i in ma._items(agenda)],
                'is_officer': officer}
        if officer:
            data['item_types'] = [{'value': v, 'label': str(label)} for v, label in AgendaItem.ITEM_TYPES]
            data['members'] = [{'user_id': u.user_id, 'name': u.get_display_name()} for u in
                               ParliamentUser.objects.filter(is_active=True).defer(*member_defer()).order_by('name')]
        return data


class AgendaListView(_AgendaView):
    web_page = 'agenda_list'

    def get(self, request):
        officer = user_is_officer_or_chair(request.user)
        upcoming, past = ma.visible_agendas(request.user)
        return Response({
            'upcoming': [_summary(a) for a in upcoming],
            'past': [_summary(a) for a in past],
            'is_officer': officer,
            'events': [_event_json(e) for e in _linkable_events()] if officer else [],
        })

    def post(self, request):
        self.require_officer(request)
        body = _body(request)
        title = str(body.get('title') or '').strip()[:ma.MAX_TITLE]
        date = ma._parse(datetime.date.fromisoformat, str(body.get('date') or ''))
        start_time = ma._parse(datetime.time.fromisoformat, str(body.get('start_time') or ''))
        if not (title and date and start_time):
            raise BadRequest('Title, a valid date and a valid start time are required.', code='invalid')
        event = None
        event_id = ma._parse(int, str(body.get('event_id') or ''))
        if event_id:
            event = Event.objects.filter(pk=event_id).first()
        agenda = ma.create_agenda_record(request, title, date, start_time, event,
                                         bool(body.get('standard_order')))
        return Response(self.detail(request, self.agenda(agenda.pk)), status=201)


class AgendaView(_AgendaView):
    web_page = 'agenda_detail'

    def get(self, request, agenda_id):
        agenda = self.agenda(agenda_id)
        if agenda.status != 'published' and not user_is_officer_or_chair(request.user):
            raise exceptions.NotFound()
        return Response(self.detail(request, agenda))


class AgendaItemListView(_AgendaView):
    web_page = 'add_agenda_item'
    officers_only = True

    def post(self, request, agenda_id):
        agenda = self.agenda(agenda_id)
        item = ma.add_item(agenda, _body(request))
        return Response({'item': _item_json(item), **self.detail(request, agenda)}, status=201)


def _item(agenda_id, item_id):
    item = AgendaItem.objects.select_related('agenda', 'presenter').filter(pk=item_id, agenda_id=agenda_id).first()
    if item is None:
        raise exceptions.NotFound()
    return item


class AgendaItemView(_AgendaView):
    web_page = 'update_agenda_item'
    officers_only = True

    def patch(self, request, agenda_id, item_id):
        item = _item(agenda_id, item_id)
        # A PATCH keeps what it doesn't name: start from the item's own values.
        data = {'item_type': item.item_type, 'title': item.title, 'notes': item.notes,
                'presenter': item.presenter_id or '', 'presenter_text': item.presenter_text,
                'duration_minutes': item.duration_minutes or ''}
        data.update(_body(request))
        ma.apply_item_fields(data, item)
        item.save()
        return Response(self.detail(request, self.agenda(agenda_id)))

    def delete(self, request, agenda_id, item_id):
        _item(agenda_id, item_id).delete()
        return Response(self.detail(request, self.agenda(agenda_id)))


class AgendaItemMoveView(_AgendaView):
    web_page = 'update_agenda_item'
    officers_only = True

    def post(self, request, agenda_id, item_id):
        direction = _body(request).get('direction')
        if direction not in ('up', 'down'):
            raise BadRequest('direction is up or down.', code='invalid')
        ma.move_item(_item(agenda_id, item_id), direction)
        return Response(self.detail(request, self.agenda(agenda_id)))


class AgendaStatusView(_AgendaView):
    web_page = 'set_agenda_status'
    officers_only = True

    def post(self, request, agenda_id):
        agenda = self.agenda(agenda_id)
        if not ma.change_status(agenda, _body(request).get('status')):
            raise BadRequest('status is draft or published.', code='invalid')
        return Response(self.detail(request, agenda))


class AgendaStartMinutesView(_AgendaView):
    web_page = 'start_minutes_from_agenda'
    officers_only = True

    def post(self, request, agenda_id):
        self.agenda(agenda_id)
        minutes, created = ma.start_minutes(request, agenda_id)
        return Response({'minutes_id': minutes.pk, 'created': created}, status=201 if created else 200)
