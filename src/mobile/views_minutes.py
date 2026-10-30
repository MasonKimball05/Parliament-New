"""
Chapter minutes in the app (10-30-26): the officer portal's minutes editor,
through the same functions as the website (src/view/officer/chapter_minutes.py).

* ``GET officer/minutes/`` — every chapter minutes session, and the events
  one can be linked to; ``POST`` ``{title, date, start_time, event_id}``
  starts one.
* ``GET officer/minutes/<id>/`` — the session: its sections (text, headers,
  motions), the roll call (roll call order, each member's status) and the
  choices for motions. ``DELETE`` deletes it (officers: drafts only).
* ``PUT officer/minutes/<id>/sections/`` ``{sections, end_time, edit_reason}``
  — saves the write-up (the editor's JSON shape).
* ``PUT officer/minutes/<id>/roll-call/`` ``{attendance: [{user_id, status}],
  edit_reason}`` — saves the roll call and syncs attendance.
* ``POST officer/minutes/<id>/event/`` ``{event_id}`` — link or unlink.
* ``POST officer/minutes/<id>/publish/`` ``{visibility}`` — publish as a PDF.
* ``GET officer/minutes/<id>/pdf/`` — the PDF, for the app's viewer.

Officers and chairs (``officer_required``), like the web pages.
"""
from datetime import datetime

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_officer_tools import _Officers
from src.models import ChapterMinutes, Event, MinutesMotion, MinutesSection
from src.models.users import member_defer
from src.view.officer import chapter_minutes as cm

ROLL_STATUSES = ('present', 'late', 'absent', 'excused', 'pending')
SECTION_TYPES = tuple(k for k, _ in MinutesSection.SECTION_TYPES)


def _choices(pairs):
    return [{'value': v, 'label': str(label)} for v, label in pairs]


def _event_json(e):
    return {'id': e.id, 'title': e.title, 'date_time': e.date_time.isoformat()} if e else None


def _summary(m):
    return {
        'id': m.id,
        'title': m.title,
        'date': m.date.isoformat(),
        'start_time': m.start_time.strftime('%H:%M'),
        'end_time': m.end_time.strftime('%H:%M') if m.end_time else None,
        'status': m.status,
        'status_label': m.get_status_display(),
        'attendance_taken': m.attendance_taken,
        'event': _event_json(m.event),
        'created_by': m.created_by.get_display_name() if m.created_by_id else '',
        'edited_after_publish': m.edited_after_publish,
    }


def _minutes(minutes_id):
    return get_object_or_404(ChapterMinutes.objects.select_related('event', 'created_by')
                             .defer(*member_defer('created_by')), pk=minutes_id, committee__isnull=True)


class MinutesListView(_Officers):
    web_page = 'chapter_minutes_list'

    def get(self, request):
        rows = (ChapterMinutes.objects.filter(committee__isnull=True)
                .select_related('created_by', 'event').defer(*member_defer('created_by'))
                .order_by('-date', '-start_time'))
        return Response({'minutes': [_summary(m) for m in rows],
                         'events': [_event_json(e) for e in cm._linkable_events()]})

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        title = str(body.get('title') or '').strip()[:200]
        try:
            day = datetime.strptime(str(body.get('date') or ''), '%Y-%m-%d').date()
            start = datetime.strptime(str(body.get('start_time') or ''), '%H:%M').time()
        except ValueError:
            raise BadRequest('A date (YYYY-MM-DD) and start time (HH:MM) are needed.', code='invalid')
        if not title:
            raise BadRequest('Title, date, and start time are required.', code='invalid')
        event = Event.objects.filter(pk=body.get('event_id')).first() if body.get('event_id') else None
        minutes = cm.create_minutes(request, title, day, start, event)
        return Response({'minutes': _summary(minutes)}, status=201)


class MinutesView(_Officers):
    web_page = 'edit_chapter_minutes'

    def get(self, request, minutes_id):
        minutes = _minutes(minutes_id)
        sections, roll, choices = cm.minutes_editor_state(minutes)
        return Response({
            'minutes': _summary(minutes),
            'sections': sections,
            'roll_call': roll,
            'members': choices,
            'events': [_event_json(e) for e in cm._linkable_events()],
            'choices': {
                'motion_type': _choices(MinutesMotion.MOTION_TYPE_CHOICES),
                'vote_method': _choices(MinutesMotion.VOTE_METHOD_CHOICES),
                'result': _choices(MinutesMotion.RESULT_CHOICES),
                'caucus_type': _choices(MinutesMotion.CAUCUS_TYPE_CHOICES),
                'visibility': _choices(ChapterMinutes.VISIBILITY_CHOICES),
            },
            'can_delete': minutes.status != 'published' or request.user.is_admin,
        })

    def delete(self, request, minutes_id):
        error = cm.delete_minutes(request, _minutes(minutes_id))
        if error:
            raise exceptions.PermissionDenied(error, code='forbidden')
        return Response({'ok': True})


def _clean_sections(raw):
    """The editor's sections, with types and motion choices checked (bad values fall back to defaults)."""
    if not isinstance(raw, list):
        raise BadRequest('sections is a list.', code='invalid')
    valid = {f: {v for v, _ in getattr(MinutesMotion, c)} for f, c in (
        ('motion_type', 'MOTION_TYPE_CHOICES'), ('vote_method', 'VOTE_METHOD_CHOICES'),
        ('result', 'RESULT_CHOICES'), ('caucus_type', 'CAUCUS_TYPE_CHOICES'))}
    defaults = {'motion_type': 'custom', 'vote_method': 'voice', 'result': 'passed', 'caucus_type': ''}
    out = []
    for s in raw:
        if not isinstance(s, dict) or s.get('type') not in SECTION_TYPES:
            continue
        section = {'type': s['type'], 'content': str(s.get('content') or ''), 'title': str(s.get('title') or '')[:200]}
        if s['type'] == 'motion':
            m = s.get('motion') if isinstance(s.get('motion'), dict) else {}
            m = dict(m)
            for field, ok in valid.items():
                if m.get(field) not in ok:
                    m[field] = defaults[field]
            section['motion'] = m
        out.append(section)
    return out


class MinutesSectionsView(_Officers):
    web_page = 'edit_chapter_minutes'

    def put(self, request, minutes_id):
        minutes = _minutes(minutes_id)
        body = request.data if isinstance(request.data, dict) else {}
        end_time = body.get('end_time')
        if end_time:
            try:
                datetime.strptime(str(end_time), '%H:%M')
            except ValueError:
                raise BadRequest('The end time is HH:MM.', code='invalid')
        cm.save_minutes_sections(request, minutes, {
            'sections': _clean_sections(body.get('sections', [])),
            'end_time': '' if end_time is None else str(end_time),
            'edit_reason': str(body.get('edit_reason') or ''),
        })
        minutes.refresh_from_db()
        sections, _, _ = cm.minutes_editor_state(minutes)
        return Response({'minutes': _summary(minutes), 'sections': sections})


class MinutesRollCallView(_Officers):
    web_page = 'edit_chapter_minutes'

    def put(self, request, minutes_id):
        minutes = _minutes(minutes_id)
        body = request.data if isinstance(request.data, dict) else {}
        rows = body.get('attendance')
        if not isinstance(rows, list):
            raise BadRequest('attendance is a list.', code='invalid')
        _, roll, _ = cm.minutes_editor_state(minutes)
        names = {str(r['user_id']): r['name'] for r in roll}
        attendance = [{'user_id': str(r.get('user_id')), 'name': names[str(r.get('user_id'))], 'status': r.get('status')}
                      for r in rows if isinstance(r, dict) and str(r.get('user_id')) in names
                      and r.get('status') in ROLL_STATUSES]
        cm.save_roll_call(request, minutes, {'attendance': attendance,
                                             'edit_reason': str(body.get('edit_reason') or 'Attendance updated')})
        minutes.refresh_from_db()
        _, roll, _ = cm.minutes_editor_state(minutes)
        return Response({'minutes': _summary(minutes), 'roll_call': roll})


class MinutesEventView(_Officers):
    web_page = 'edit_chapter_minutes'

    def post(self, request, minutes_id):
        minutes = _minutes(minutes_id)
        body = request.data if isinstance(request.data, dict) else {}
        event = None
        if body.get('event_id'):
            event = Event.objects.filter(pk=body['event_id']).first()
            if event is None:
                raise BadRequest('That event no longer exists.', code='invalid')
        cm.link_minutes_event(request, minutes, event, str(body.get('edit_reason') or ''))
        return Response({'minutes': _summary(_minutes(minutes_id))})


class MinutesPublishView(_Officers):
    web_page = 'publish_chapter_minutes'

    def post(self, request, minutes_id):
        minutes = _minutes(minutes_id)
        if minutes.status == 'published':
            raise BadRequest('These minutes are already published.', code='already_published')
        body = request.data if isinstance(request.data, dict) else {}
        cm.publish_minutes(request, minutes, str(body.get('visibility') or 'all_members'))
        return Response({'minutes': _summary(_minutes(minutes_id))})


class MinutesPdfView(_Officers):
    web_page = 'download_minutes_pdf'

    def get(self, request, minutes_id):
        minutes = _minutes(minutes_id)
        buf = cm.generate_minutes_pdf_buffer(minutes)
        response = HttpResponse(buf.read(), content_type='application/pdf')
        response['Content-Disposition'] = 'inline; filename="minutes.pdf"'
        response['Cache-Control'] = 'private, no-store'
        return response
