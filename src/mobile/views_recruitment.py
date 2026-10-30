"""
The app's Recruitment dashboard (mobile plan, Phase 3f), for a committee with
``is_recruitment_committee``. Same access rules as ``src/view/committee/
recruitment.py`` (its ``_user_access`` / ``_can_manage`` / ``_can_view_private``
/ ``_can_take_attendance``), under the ``committee_home`` page toggle.

* ``GET  committees/<code>/recruitment/`` — upcoming and past events (public
  ones only without private access), and the candidate pipeline for members
  who may see it.
* ``POST committees/<code>/recruitment/events/`` — create an event (managers).
* ``GET  .../recruitment/events/<id>/`` — one event: sign-ups or RSVPs, your
  RSVP, notes as their visibility allows.
* ``POST .../recruitment/events/<id>/`` — edit (managers). Unlike the web form,
  which has no reminder inputs and so switches an RSVP reminder off on every
  save, the app leaves the reminder settings alone.
* ``POST .../recruitment/events/<id>/delete/`` — delete (managers).
* ``POST .../recruitment/events/<id>/rsvp/`` ``{status}`` — your RSVP.
* ``POST .../recruitment/events/<id>/check-in/`` ``{user_id, checked_in}`` —
  attendance takers.
* ``POST .../recruitment/candidates/`` — add a candidate; ``GET/POST
  .../candidates/<id>/`` read or edit one; ``.../delete/``, ``.../status/``,
  ``.../notes/`` and ``.../notes/<note id>/delete/`` as the web's JSON actions.
"""
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_committees import _person, _require_committees
from src.models import (
    ActivityLog, Committee, Event, RecruitmentCandidate, RecruitmentCandidateNote, RecruitmentEvent,
    RecruitmentEventRSVP,
)
from src.models.users import member_defer
from src.view.committee.recruitment import _can_manage, _can_take_attendance, _can_view_private, _user_access

PAST_LIMIT = 20
TEXT_MAX = 5000


class BadRequest(exceptions.APIException):
    status_code = 400


def _choices(field_choices):
    return [{'value': v, 'label': str(label)} for v, label in field_choices]


def _event_summary(re, signup_count=None):
    e = re.event
    return {
        'id': re.id,
        'event_id': e.id,
        'title': e.title,
        'date_time': e.date_time.isoformat(),
        'location': e.location or '',
        'event_type': re.event_type,
        'event_type_label': re.get_event_type_display(),
        'visibility': re.visibility,
        'status': re.status,
        'status_label': re.get_status_display(),
        'requires_signup': e.requires_signup,
        'requires_attendance': e.requires_attendance,
        'max_signups': e.max_signups,
        'signup_count': signup_count,
    }


def _candidate_json(c, notes=None):
    out = {
        'id': c.id,
        'name': c.name,
        'email': c.email or '',
        'phone': c.phone or '',
        'status': c.status,
        'status_label': c.get_status_display(),
        'assigned_to': _person(c.assigned_to) if c.assigned_to else None,
        'last_contacted': c.last_contacted.isoformat() if c.last_contacted else None,
        'source_event': ({'id': c.source_event.id, 'title': c.source_event.event.title}
                         if c.source_event_id and c.source_event else None),
        'notes': c.notes or '',
    }
    if notes is not None:
        out['note_entries'] = notes
    return out


def _note_json(n, user, is_chair):
    return {
        'id': n.id,
        'author': n.author.get_display_name() if n.author else None,
        'body': n.body,
        'created_at': n.created_at.isoformat(),
        'can_delete': n.author_id == user.pk or is_chair,
    }


class _RecruitmentView(MobileAPIView):
    web_page = 'committee_home'

    def setup_access(self, request, code):
        _require_committees()
        committee = Committee.objects.select_related('role').filter(code=code, is_recruitment_committee=True).first()
        if committee is None:
            raise exceptions.NotFound()
        user = request.user
        has_access, is_chair, perm = _user_access(committee, user)
        if not has_access:
            raise exceptions.PermissionDenied('You don’t have access to the recruitment dashboard.', code='no_access')
        self.committee, self.is_chair = committee, is_chair
        self.can_manage = _can_manage(committee, user, perm=perm)
        self.can_view_private = _can_view_private(committee, user, perm=perm)
        self.can_take_attendance = _can_take_attendance(committee, user, perm=perm)
        return committee

    def require(self, allowed, message='You can’t do that here.'):
        if not allowed:
            raise exceptions.PermissionDenied(message, code='forbidden')

    def recruitment_event(self, re_id):
        re = (RecruitmentEvent.objects.select_related('event', 'created_by')
              .filter(id=re_id, committee=self.committee).first())
        if re is None or (re.visibility == 'committee_only' and not self.can_view_private):
            raise exceptions.NotFound()
        return re

    def candidate(self, candidate_id):
        self.require(self.can_view_private)
        c = (RecruitmentCandidate.objects.select_related('assigned_to', 'source_event__event')
             .filter(id=candidate_id, committee=self.committee).first())
        if c is None:
            raise exceptions.NotFound()
        return c

    def assignees(self):
        return (self.committee.chairs.all() | self.committee.members.all()).distinct().order_by('name')


class RecruitmentDashboardView(_RecruitmentView):

    def get(self, request, code):
        committee = self.setup_access(request, code)
        now = timezone.now()
        base = (RecruitmentEvent.objects.filter(committee=committee).select_related('event')
                .annotate(signup_count=Count('event__signups', filter=Q(event__signups__is_cancelled=False))))
        if not self.can_view_private:
            base = base.filter(visibility='public')
        upcoming = base.filter(event__date_time__gte=now).order_by('event__date_time')
        past = base.filter(event__date_time__lt=now).order_by('-event__date_time')[:PAST_LIMIT]
        candidates = None
        if self.can_view_private:
            candidates = [_candidate_json(c) for c in RecruitmentCandidate.objects.filter(committee=committee)
                          .select_related('assigned_to', 'source_event__event')
                          .defer(*member_defer('assigned_to')).order_by('name')]
        return Response({
            'committee': {'code': committee.code, 'name': committee.name},
            'can_manage': self.can_manage,
            'can_view_private': self.can_view_private,
            'can_take_attendance': self.can_take_attendance,
            'is_chair': self.is_chair,
            'upcoming': [_event_summary(re, re.signup_count) for re in upcoming],
            'past': [_event_summary(re, re.signup_count) for re in past],
            'candidates': candidates,
            'choices': {
                'candidate_status': _choices(RecruitmentCandidate._meta.get_field('status').choices),
                'event_type': _choices(RecruitmentEvent._meta.get_field('event_type').choices),
                'event_status': _choices(RecruitmentEvent._meta.get_field('status').choices),
                'visibility': _choices(RecruitmentEvent._meta.get_field('visibility').choices),
                'member_types': _choices(Event.MEMBER_TYPES),
            },
            'assignees': [_person(u) for u in self.assignees().defer(*member_defer())] if self.can_view_private else [],
        })


def _event_fields(data):
    """The event form's fields, checked: (event fields, recruitment fields). Raises BadRequest."""
    def pick(name, field, default):
        value = data.get(name, default)
        valid = {v for v, _ in RecruitmentEvent._meta.get_field(field).choices}
        if value not in valid:
            raise BadRequest(f'Choose a valid {name.replace("_", " ")}.', code='invalid_choice')
        return value

    title = str(data.get('title') or '').strip()
    if not title:
        raise BadRequest('Title is required.', code='title_required')
    raw = str(data.get('date_time') or '')
    if not raw:
        raise BadRequest('Date and time are required.', code='date_required')
    when = parse_datetime(raw)
    if when is None:
        raise BadRequest('Invalid date/time format.', code='invalid_date')
    if timezone.is_naive(when):
        when = timezone.make_aware(when, timezone.get_current_timezone())
    attendance = data.get('attendance_type', 'none')
    if attendance not in ('none', 'signup', 'attendance'):
        raise BadRequest('Choose how attendance works.', code='invalid_choice')
    max_signups = None
    if attendance == 'signup':
        try:
            max_signups = int(data.get('max_signups')) if data.get('max_signups') not in (None, '') else None
        except (TypeError, ValueError):
            max_signups = None
        if max_signups is not None and max_signups <= 0:
            max_signups = None
    member_types = {v for v, _ in Event.MEMBER_TYPES}
    expected = [t for t in (data.get('expected_member_types') or []) if t in member_types]
    event = {
        'title': title[:200],
        'description': str(data.get('description') or '')[:TEXT_MAX],
        'date_time': when,
        'location': str(data.get('location') or '')[:300],
        'requires_attendance': attendance == 'attendance',
        'requires_signup': attendance == 'signup',
        'max_signups': max_signups,
        'visible_to': expected if attendance == 'attendance' and expected else None,
    }
    recruitment = {
        'event_type': pick('event_type', 'event_type', 'other'),
        'visibility': pick('visibility', 'visibility', 'public'),
        'status': pick('status', 'status', 'planned'),
        'notes': str(data.get('notes') or '')[:TEXT_MAX],
        'notes_visibility': pick('notes_visibility', 'notes_visibility', 'committee_only'),
    }
    return event, recruitment


class RecruitmentEventCreateView(_RecruitmentView):

    def post(self, request, code):
        committee = self.setup_access(request, code)
        self.require(self.can_manage, 'You do not have permission to create recruitment events.')
        event_fields, re_fields = _event_fields(request.data)
        with transaction.atomic():
            event = Event.objects.create(**event_fields, allow_excuses=False, is_active=True, archived=False,
                                         created_by=request.user)
            re = RecruitmentEvent.objects.create(event=event, committee=committee, created_by=request.user, **re_fields)
        ActivityLog.log_activity(
            action_type='other', user=request.user, request=request._request,
            description=f'Created recruitment event "{event.title}" ({re.get_event_type_display()})',
            object_type='RecruitmentEvent', object_id=re.id, object_repr=event.title)
        return Response(_event_summary(re, 0), status=201)


class RecruitmentEventView(_RecruitmentView):

    def get(self, request, code, re_id):
        self.setup_access(request, code)
        re = self.recruitment_event(re_id)
        e = re.event
        body = {
            **_event_summary(re),
            'description': e.description or '',
            'is_past': re.is_past,
            'can_manage': self.can_manage,
            'can_take_attendance': self.can_take_attendance,
            'expected_member_types': e.visible_to or [],
            'notes': None,
            'notes_private': False,
            'notes_visibility': re.notes_visibility if self.can_view_private else None,
            'signups': None,
            'rsvps': None,
            'my_rsvp': None,
        }
        if re.notes and self.can_view_private:
            body['notes'], body['notes_private'] = re.notes, re.notes_visibility == 'committee_only'
        elif re.notes and re.notes_visibility == 'public':
            body['notes'] = re.notes
        if e.requires_signup:
            signups = (e.signups.filter(is_cancelled=False).select_related('user')
                       .defer(*member_defer('user')).order_by('signed_up_at'))
            body['signups'] = [{**_person(s.user), 'signed_up_at': s.signed_up_at.isoformat()} for s in signups]
            body['signup_count'] = len(body['signups'])
        else:
            rsvps = re.rsvps.select_related('user').defer(*member_defer('user')).order_by('user__name')
            body['rsvps'] = [{**_person(r.user), 'status': r.status, 'checked_in': r.checked_in} for r in rsvps]
            mine = next((r for r in body['rsvps'] if r['user_id'] == request.user.user_id), None)
            body['my_rsvp'] = mine['status'] if mine else None
        return Response(body)

    def post(self, request, code, re_id):
        self.setup_access(request, code)
        self.require(self.can_manage, 'You do not have permission to edit recruitment events.')
        re = self.recruitment_event(re_id)
        event_fields, re_fields = _event_fields(request.data)
        if not self.can_view_private and re.notes_visibility == 'committee_only':
            # A manager who can't read the private notes can't have edited them.
            re_fields.pop('notes')
            re_fields.pop('notes_visibility')
        with transaction.atomic():
            for k, v in event_fields.items():
                setattr(re.event, k, v)
            re.event.save(update_fields=list(event_fields))
            for k, v in re_fields.items():
                setattr(re, k, v)
            re.save()
        return self.get(request, code, re_id)


class RecruitmentEventDeleteView(_RecruitmentView):

    def post(self, request, code, re_id):
        self.setup_access(request, code)
        self.require(self.can_manage)
        re = self.recruitment_event(re_id)
        re.event.delete()   # cascades to the RecruitmentEvent and its RSVPs
        return Response({'ok': True})


class RecruitmentRSVPView(_RecruitmentView):

    def post(self, request, code, re_id):
        self.setup_access(request, code)
        re = self.recruitment_event(re_id)
        if re.event.requires_signup:
            raise BadRequest('This event uses sign-ups on the calendar.', code='uses_signup')
        status = request.data.get('status')
        if status not in {v for v, _ in RecruitmentEventRSVP._meta.get_field('status').choices}:
            raise BadRequest('Choose Going, Maybe or Not going.', code='invalid_choice')
        RecruitmentEventRSVP.objects.update_or_create(recruitment_event=re, user=request.user,
                                                      defaults={'status': status})
        return Response({'status': status})


class RecruitmentCheckInView(_RecruitmentView):

    def post(self, request, code, re_id):
        self.setup_access(request, code)
        self.require(self.can_take_attendance)
        re = self.recruitment_event(re_id)
        rsvp = re.rsvps.filter(user_id=str(request.data.get('user_id') or '')).first()
        if rsvp is None:
            raise exceptions.NotFound()
        rsvp.checked_in = request.data.get('checked_in') is True
        rsvp.save(update_fields=['checked_in', 'updated_at'])
        return Response({'checked_in': rsvp.checked_in})


def _candidate_fields(data, committee, assignees, current=None):
    name = str(data.get('name') or '').strip()
    if not name:
        raise BadRequest('Name is required.', code='name_required')
    statuses = {v for v, _ in RecruitmentCandidate._meta.get_field('status').choices}
    status = data.get('status')
    if status not in statuses:
        status = current.status if current else 'prospect'
    assignee = None
    if data.get('assigned_to'):
        assignee = assignees.filter(user_id=str(data['assigned_to'])).first()
    source = None
    if data.get('source_event'):
        source = RecruitmentEvent.objects.filter(committee=committee, id=data['source_event']).first()
    last = parse_date(str(data.get('last_contacted') or '')) if data.get('last_contacted') else None
    return {
        'name': name[:200], 'email': str(data.get('email') or '').strip()[:254],
        'phone': str(data.get('phone') or '').strip()[:30], 'status': status, 'assigned_to': assignee,
        'source_event': source, 'last_contacted': last, 'notes': str(data.get('notes') or '')[:TEXT_MAX],
    }


class RecruitmentCandidateCreateView(_RecruitmentView):

    def post(self, request, code):
        committee = self.setup_access(request, code)
        self.require(self.can_view_private)
        fields = _candidate_fields(request.data, committee, self.assignees())
        c = RecruitmentCandidate.objects.create(committee=committee, added_by=request.user, **fields)
        ActivityLog.log_activity(
            action_type='other', user=request.user, request=request._request,
            description=f'Added recruitment candidate "{c.name}" ({c.get_status_display()}) to {committee.code}',
            object_type='RecruitmentCandidate', object_id=c.id, object_repr=c.name)
        return Response(_candidate_json(c, notes=[]), status=201)


class RecruitmentCandidateView(_RecruitmentView):

    def body(self, request, c):
        notes = c.note_entries.select_related('author').defer(*member_defer('author')).order_by('created_at')
        return {**_candidate_json(c, [_note_json(n, request.user, self.is_chair) for n in notes]),
                'can_manage': self.can_manage}

    def get(self, request, code, candidate_id):
        self.setup_access(request, code)
        return Response(self.body(request, self.candidate(candidate_id)))

    def post(self, request, code, candidate_id):
        committee = self.setup_access(request, code)
        c = self.candidate(candidate_id)
        for k, v in _candidate_fields(request.data, committee, self.assignees(), current=c).items():
            setattr(c, k, v)
        c.save()
        return Response(self.body(request, c))


class RecruitmentCandidateDeleteView(_RecruitmentView):

    def post(self, request, code, candidate_id):
        self.setup_access(request, code)
        c = self.candidate(candidate_id)
        self.require(self.can_manage)
        c.delete()
        return Response({'ok': True})


class RecruitmentCandidateStatusView(_RecruitmentView):

    def post(self, request, code, candidate_id):
        self.setup_access(request, code)
        c = self.candidate(candidate_id)
        self.require(self.can_manage)
        status = request.data.get('status')
        if status not in {v for v, _ in RecruitmentCandidate._meta.get_field('status').choices}:
            raise BadRequest('Invalid status.', code='invalid_choice')
        c.status = status
        c.save(update_fields=['status', 'updated_at'])
        return Response({'status': c.status, 'status_label': c.get_status_display()})


class RecruitmentCandidateNoteView(_RecruitmentView):

    def post(self, request, code, candidate_id):
        self.setup_access(request, code)
        c = self.candidate(candidate_id)
        body = str(request.data.get('body') or '').strip()
        if not body:
            raise BadRequest('Note cannot be empty.', code='note_empty')
        note = RecruitmentCandidateNote.objects.create(candidate=c, author=request.user, body=body[:TEXT_MAX])
        return Response(_note_json(note, request.user, self.is_chair), status=201)


class RecruitmentCandidateNoteDeleteView(_RecruitmentView):

    def post(self, request, code, candidate_id, note_id):
        self.setup_access(request, code)
        c = self.candidate(candidate_id)
        note = c.note_entries.filter(id=note_id).first()
        if note is None:
            raise exceptions.NotFound()
        self.require(note.author_id == request.user.pk or self.is_chair, 'You can only delete your own notes.')
        note.delete()
        return Response({'ok': True})

