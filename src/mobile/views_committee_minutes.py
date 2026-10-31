"""
Committee minutes in the app (v4.3.0): the committee minutes editor, through
the same functions as the website (src/view/committee/committee_minutes_editor.py),
in the chapter minutes API's shape (views_minutes.py) so the app's editor
serves both.

* ``POST committees/<code>/minutes/`` ``{title, date, start_time}`` — starts a
  session (any committee member or above, as on the website). The list
  itself is ``views_committees.CommitteeMinutesView``.
* ``GET committees/<code>/minutes/<id>/`` — sections, roll call (the
  committee's members) and choices. ``DELETE`` deletes it (editors; only
  admins once published).
* ``PUT …/sections/`` ``{sections, end_time, edit_reason}`` — the write-up.
* ``PUT …/roll-call/`` ``{attendance: [{user_id, status}], edit_reason}``.
* ``POST …/publish/`` ``{visibility, publish_to_chapter}`` — publish as a PDF
  in the committee's documents (chairs, admins and minute-takers).
* ``GET …/pdf/`` — the PDF, for the app's viewer.

Editing a session: chairs, admins and minute-takers any; its creator their
own draft (``can_edit_specific_minutes``).
"""
from datetime import datetime

from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.views_committees import _CommitteeView
from src.mobile.views_excuses_polls import BadRequest
from src.mobile.views_minutes import ROLL_STATUSES, _choices, _clean_sections, _summary
from src.models import ChapterMinutes, CommitteeDocument, MinutesMotion
from src.models.users import member_defer
from src.view.committee import committee_minutes_editor as ce
from src.view.officer.chapter_minutes import generate_minutes_pdf_buffer

PUBLISH_VISIBILITIES = tuple(v for v, _ in CommitteeDocument.VISIBILITY_CHOICES if v != 'custom')


class _CommitteeMinutes(_CommitteeView):
    web_page = 'edit_committee_minutes'
    web_page_toggle = False   # the web pages have no @require_page_enabled

    def minutes(self, request, code, minutes_id, edit=True):
        committee, access = self.committee(request, code)
        minutes = get_object_or_404(ChapterMinutes.objects.select_related('created_by', 'event')
                                    .defer(*member_defer('created_by')), pk=minutes_id, committee=committee)
        if edit and not ce.can_edit_specific_minutes(request.user, committee, minutes):
            raise exceptions.PermissionDenied('You can’t edit these minutes.', code='forbidden')
        return committee, minutes

    def editors(self, request, committee):
        if not ce.can_edit_committee_minutes(request.user, committee):
            raise exceptions.PermissionDenied('Only the committee’s chairs and minute-takers can do that.',
                                              code='forbidden')


def create(view, request, code):
    """POST on the committee's minutes list: a new draft session."""
    committee, access = view.committee(request, code)
    if not access.can_view_minutes:   # is_committee_member_or_above, as on the website
        raise exceptions.PermissionDenied('You can’t take minutes for this committee.', code='forbidden')
    body = request.data if isinstance(request.data, dict) else {}
    title = str(body.get('title') or '').strip()[:200]
    try:
        day = datetime.strptime(str(body.get('date') or ''), '%Y-%m-%d').date()
        start = datetime.strptime(str(body.get('start_time') or ''), '%H:%M').time()
    except ValueError:
        raise BadRequest('A date (YYYY-MM-DD) and start time (HH:MM) are needed.', code='invalid')
    if not title:
        raise BadRequest('Title, date, and start time are required.', code='invalid')
    minutes = ce.create_committee_minutes_record(request, committee, title, day, start)
    return Response({'minutes': _summary(minutes)}, status=201)


class CommitteeMinutesDetailView(_CommitteeMinutes):

    def get(self, request, code, minutes_id):
        committee, minutes = self.minutes(request, code, minutes_id)
        sections, roll, members = ce.committee_editor_state(committee, minutes)
        for row in roll:
            row['user_id'] = str(row['user_id'])
            row.setdefault('role_number', '')
        for m in members:
            m['user_id'] = str(m['user_id'])
        can_edit_any = ce.can_edit_committee_minutes(request.user, committee)
        return Response({
            'minutes': _summary(minutes),
            'sections': sections,
            'roll_call': roll,
            'members': members,
            'events': [],   # no event linking for committees
            'choices': {
                'motion_type': _choices(MinutesMotion.MOTION_TYPE_CHOICES),
                'vote_method': _choices(MinutesMotion.VOTE_METHOD_CHOICES),
                'result': _choices(MinutesMotion.RESULT_CHOICES),
                'caucus_type': _choices(MinutesMotion.CAUCUS_TYPE_CHOICES),
                'visibility': _choices((v, label) for v, label in CommitteeDocument.VISIBILITY_CHOICES
                                       if v in PUBLISH_VISIBILITIES),
            },
            'committee': {'code': committee.code, 'name': committee.name},
            'can_publish': can_edit_any and minutes.status != 'published',
            'can_delete': can_edit_any and (minutes.status != 'published' or request.user.is_admin),
        })

    def delete(self, request, code, minutes_id):
        committee, minutes = self.minutes(request, code, minutes_id)
        self.editors(request, committee)
        error = ce.delete_committee_minutes_record(request, committee, minutes)
        if error:
            raise exceptions.PermissionDenied(error, code='forbidden')
        return Response({'ok': True})


class CommitteeMinutesSectionsView(_CommitteeMinutes):

    def put(self, request, code, minutes_id):
        committee, minutes = self.minutes(request, code, minutes_id)
        body = request.data if isinstance(request.data, dict) else {}
        end_time = body.get('end_time')
        if end_time:
            try:
                datetime.strptime(str(end_time), '%H:%M')
            except ValueError:
                raise BadRequest('The end time is HH:MM.', code='invalid')
        ce.save_committee_sections(request, committee, minutes, {
            'sections': _clean_sections(body.get('sections', [])),
            'end_time': '' if end_time is None else str(end_time),
            'edit_reason': str(body.get('edit_reason') or ''),
        })
        minutes.refresh_from_db()
        sections, _, _ = ce.committee_editor_state(committee, minutes)
        return Response({'minutes': _summary(minutes), 'sections': sections})


class CommitteeMinutesRollCallView(_CommitteeMinutes):

    def put(self, request, code, minutes_id):
        committee, minutes = self.minutes(request, code, minutes_id)
        body = request.data if isinstance(request.data, dict) else {}
        rows = body.get('attendance')
        if not isinstance(rows, list):
            raise BadRequest('attendance is a list.', code='invalid')
        _, roll, _ = ce.committee_editor_state(committee, minutes)
        names = {str(r['user_id']): r['name'] for r in roll}
        attendance = [{'user_id': str(r.get('user_id')), 'name': names[str(r.get('user_id'))], 'status': r.get('status')}
                      for r in rows if isinstance(r, dict) and str(r.get('user_id')) in names
                      and r.get('status') in ROLL_STATUSES]
        ce.save_committee_attendance(request, committee, minutes, {
            'attendance': attendance, 'edit_reason': str(body.get('edit_reason') or 'Attendance updated')})
        minutes.refresh_from_db()
        _, roll, _ = ce.committee_editor_state(committee, minutes)
        for row in roll:
            row['user_id'] = str(row['user_id'])
            row.setdefault('role_number', '')
        return Response({'minutes': _summary(minutes), 'roll_call': roll})


class CommitteeMinutesPublishView(_CommitteeMinutes):

    def post(self, request, code, minutes_id):
        committee, minutes = self.minutes(request, code, minutes_id)
        self.editors(request, committee)
        if minutes.status == 'published':
            raise BadRequest('These minutes are already published.', code='already_published')
        body = request.data if isinstance(request.data, dict) else {}
        visibility = str(body.get('visibility') or 'committee_only')
        if visibility not in PUBLISH_VISIBILITIES:
            raise BadRequest('Choose who can see the minutes.', code='invalid')
        ce.publish_committee_minutes_record(request, committee, minutes, visibility,
                                            body.get('publish_to_chapter') is True)
        minutes.refresh_from_db()
        return Response({'minutes': _summary(minutes)})


class CommitteeMinutesPdfView(_CommitteeMinutes):
    web_page = 'download_committee_minutes_pdf'

    def get(self, request, code, minutes_id):
        committee, minutes = self.minutes(request, code, minutes_id, edit=False)
        user = request.user
        # Who may download it on the website.
        if not (committee.is_member(user) or committee.is_chair(user)
                or user.member_type == 'Officer' or user.is_admin):
            raise exceptions.PermissionDenied('You can’t download these minutes.', code='forbidden')
        response = HttpResponse(generate_minutes_pdf_buffer(minutes).read(), content_type='application/pdf')
        response['Content-Disposition'] = 'inline; filename="minutes.pdf"'
        response['Cache-Control'] = 'private, no-store'
        return response
