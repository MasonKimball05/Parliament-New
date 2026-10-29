"""
The app's My Excuses and Polls screens (mobile plan, Phase 3d).

* ``GET my/excuses/`` — My Excuses: the member's requests (newest first) with
  the web page's status counts, and the upcoming events they can still ask to
  be excused from. Same filters as ``my_excuses``.
* ``POST my/excuses/`` ``{event_id, reason}`` — submit one, under every rule
  ``submit_excuse`` applies. A supporting document is attached on the website.
* ``POST my/excuses/<id>/cancel/`` — withdraw a pending request.
* ``GET polls/`` — every poll on an announcement the member can see, split
  into open and closed as ``polls_view`` splits them.
* ``GET polls/<announcement_id>/`` — one poll's questions, with the member's
  current answers when they have responded.
* ``POST polls/<announcement_id>/`` ``{answers: {"<question id>": [option ids]
  | "text"}}`` — answer, or change an answer while the poll is open
  (``take_poll``: the one response row is updated in place).
"""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView
from src.models import (
    ActivityLog, Announcement, AnnouncementPollAnswer, AnnouncementPollResponse, AttendanceExcuse, Event,
)
from src.models.users import member_defer
from src.utils.visibility import visible_to_q

EXCUSE_LIMIT = 100
POLL_LIMIT = 100
REASON_MIN = 10
REASON_MAX = 5000
TEXT_ANSWER_MAX = 5000


class BadRequest(exceptions.APIException):
    """A 400 that keeps its message and code (``ValidationError`` wraps both in a list)."""
    status_code = 400


# ── Excuses ──────────────────────────────────────────────────────────────────

def _require_excuses():
    if not (check_feature_enabled('attendance_tracking') and check_feature_enabled('excuse_system')):
        raise exceptions.PermissionDenied('Excuses are off.', code='feature_disabled')


def _available_events(user):
    """``my_excuses``' list of events the member can still be excused from."""
    events = (Event.objects
              .filter(allow_excuses=True, date_time__gte=timezone.now(), is_active=True)
              .select_related('committee')
              .exclude(excuse_requests__user=user)
              .order_by('date_time'))
    return [e for e in events
            if e.is_visible_to_user(user) and e.can_submit_excuse() and e.user_is_required(user)]


def _event_json(event):
    return {'id': event.id, 'title': event.title, 'date_time': event.date_time.isoformat()}


def _excuse_json(excuse):
    reviewer = excuse.reviewed_by
    return {
        'id': excuse.id,
        'event': _event_json(excuse.event),
        'reason': excuse.reason,
        'status': excuse.status,
        'submitted_at': excuse.submitted_at.isoformat(),
        'reviewed_at': excuse.reviewed_at.isoformat() if excuse.reviewed_at else None,
        'reviewed_by': reviewer.get_display_name() if reviewer else None,
        'review_notes': excuse.review_notes or '',
        'has_document': bool(excuse.supporting_document),
    }


class MyExcusesView(MobileAPIView):
    web_page = 'my_excuses'
    web_page_toggle = False   # the web page has only the feature flags

    def get(self, request):
        _require_excuses()
        mine = (AttendanceExcuse.objects
                .filter(user=request.user)
                .select_related('event', 'reviewed_by')
                .defer(*member_defer('reviewed_by'))
                .order_by('-submitted_at'))
        rows = list(mine[:EXCUSE_LIMIT])
        statuses = list(mine.values_list('status', flat=True))
        return Response({
            'counts': {
                'pending': statuses.count('pending'),
                'approved': statuses.count('approved'),
                'denied': statuses.count('denied'),
                'total': len(statuses),
            },
            'excuses': [_excuse_json(e) for e in rows],
            'available': [
                {**_event_json(e),
                 'location': e.location or '',
                 'deadline': e.excuse_deadline.isoformat() if e.excuse_deadline else None,
                 'requires_attendance': e.requires_attendance}
                for e in _available_events(request.user)
            ],
        })

    def post(self, request):
        _require_excuses()
        user = request.user
        try:
            event_id = int(request.data.get('event_id'))
        except (TypeError, ValueError):
            raise BadRequest('Choose an event.', code='invalid_event')
        reason = str(request.data.get('reason') or '').strip()

        event = Event.objects.filter(id=event_id, is_active=True).first()
        if event is None or not event.is_visible_to_user(user):
            raise exceptions.NotFound()
        if not event.user_is_required(user):
            raise exceptions.PermissionDenied('This event is not one you are required to attend.',
                                              code='not_required')
        if not event.allow_excuses:
            raise exceptions.PermissionDenied('Excuses are not allowed for this event.', code='excuses_not_allowed')
        if not event.can_submit_excuse():
            raise exceptions.PermissionDenied(
                'Attendance for this event has been finalized. Excuses can no longer be submitted.'
                if event.attendance_finalized else 'The deadline for submitting excuses has passed.',
                code='deadline_passed')
        if len(reason) < REASON_MIN:
            raise BadRequest('Please give a detailed reason (at least 10 characters).',
                                             code='reason_too_short')
        if len(reason) > REASON_MAX:
            raise BadRequest('That reason is too long.', code='reason_too_long')

        with transaction.atomic():
            excuse, created = AttendanceExcuse.objects.get_or_create(
                event=event, user=user, defaults={'reason': reason})
        if not created:
            raise exceptions.PermissionDenied('You have already submitted an excuse for this event.',
                                              code='already_submitted')
        ActivityLog.log_activity(
            action_type='other', user=user, description=f'Submitted excuse request for {event.title}',
            request=request._request, object_type='AttendanceExcuse', object_id=excuse.id,
            object_repr=str(excuse))
        return Response(_excuse_json(excuse), status=201)


class MyExcuseCancelView(MobileAPIView):
    web_page = 'my_excuses'
    web_page_toggle = False

    def post(self, request, excuse_id):
        _require_excuses()
        excuse = AttendanceExcuse.objects.select_related('event').filter(id=excuse_id, user=request.user).first()
        if excuse is None:
            raise exceptions.NotFound()
        if excuse.status != 'pending':
            raise exceptions.PermissionDenied('You can only cancel pending excuse requests.', code='not_pending')
        title = excuse.event.title
        excuse.delete()
        ActivityLog.log_activity(action_type='other', user=request.user,
                                 description=f'Cancelled excuse request for {title}', request=request._request)
        return Response({'ok': True})


# ── Polls ────────────────────────────────────────────────────────────────────

def _poll_announcements(user):
    """``polls_view``'s announcements: active, published, for this member, with a poll."""
    now = timezone.now()
    return (Announcement.objects
            .filter(is_active=True)
            .filter(Q(publish_at__isnull=True) | Q(publish_at__lte=now))
            .filter(visible_to_q(user.member_type))
            .exclude(poll__isnull=True)
            .select_related('poll'))


def _poll_summary(announcement, responded):
    poll = announcement.poll
    return {
        'id': announcement.id,
        'title': poll.title,
        'announcement_title': announcement.title,
        'posted_at': announcement.posted_at.isoformat(),
        'closes_at': poll.closes_at.isoformat() if poll.closes_at else None,
        'open': poll.is_accepting_responses(),
        'anonymous': poll.is_anonymous,
        'responded': responded,
    }


class PollListView(MobileAPIView):
    web_page = 'polls_view'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request):
        rows = list(_poll_announcements(request.user).order_by('-posted_at')[:POLL_LIMIT])
        responded = set(AnnouncementPollResponse.objects
                        .filter(respondent=request.user, poll__announcement__in=rows)
                        .values_list('poll_id', flat=True))
        polls = [_poll_summary(a, a.poll.id in responded) for a in rows]
        return Response({
            'open': [p for p in polls if p['open']],
            'closed': [p for p in polls if not p['open']],
        })


class PollView(MobileAPIView):
    web_page = 'polls_view'
    web_page_toggle = False

    def _get(self, user, announcement_id):
        announcement = _poll_announcements(user).filter(id=announcement_id).first()
        if announcement is None:
            raise exceptions.NotFound()
        return announcement

    def _body(self, user, announcement):
        poll = announcement.poll
        response = poll.responses.filter(respondent=user).first()
        answers = {}
        if response is not None:
            for a in response.answers.prefetch_related('selected_options'):
                answers[a.question_id] = {
                    'options': [o.id for o in a.selected_options.all()],
                    'text': a.text_answer,
                }
        return {
            **_poll_summary(announcement, response is not None),
            'description': poll.description,
            'edited': bool(response and response.updated_at),
            'questions': [{
                'id': q.id,
                'text': q.text,
                'type': q.question_type,
                'required': q.is_required,
                'options': [{'id': o.id, 'text': o.text} for o in q.options.all()],
                'answer': answers.get(q.id),
            } for q in poll.questions.prefetch_related('options')],
        }

    def get(self, request, announcement_id):
        return Response(self._body(request.user, self._get(request.user, announcement_id)))

    def post(self, request, announcement_id):
        user = request.user
        announcement = self._get(user, announcement_id)
        poll = announcement.poll
        if not poll.is_accepting_responses():
            raise exceptions.PermissionDenied('This poll is no longer accepting responses.', code='poll_closed')
        raw = request.data.get('answers')
        if not isinstance(raw, dict):
            raise BadRequest('Answer the poll.', code='invalid_answers')

        # Check every answer before writing any, so a bad one leaves the old response intact.
        planned = []
        for q in poll.questions.prefetch_related('options'):
            value = raw.get(str(q.id))
            if q.question_type == 'text':
                text = value.strip() if isinstance(value, str) else ''
                if len(text) > TEXT_ANSWER_MAX:
                    raise BadRequest('That answer is too long.', code='answer_too_long')
                if q.is_required and not text:
                    raise BadRequest(f'Answer "{q.text}".', code='answer_required')
                planned.append((q, [], text))
                continue
            ids = value if isinstance(value, list) else []
            valid = {o.id: o for o in q.options.all()}
            chosen = [valid[i] for i in dict.fromkeys(ids) if isinstance(i, int) and i in valid]
            if q.question_type == 'single':
                chosen = chosen[:1]
            if q.is_required and not chosen:
                raise BadRequest(f'Answer "{q.text}".', code='answer_required')
            planned.append((q, chosen, ''))

        with transaction.atomic():
            response, created = AnnouncementPollResponse.objects.get_or_create(poll=poll, respondent=user)
            if not created:
                response.answers.all().delete()
                response.updated_at = timezone.now()
                response.save(update_fields=['updated_at'])
            for q, chosen, text in planned:
                answer = AnnouncementPollAnswer.objects.create(response=response, question=q, text_answer=text)
                if chosen:
                    answer.selected_options.set(chosen)
        return Response(self._body(user, announcement), status=201 if created else 200)
