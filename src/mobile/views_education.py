"""
The app's Education dashboard (mobile plan, Phase 3f), for a committee with
``is_education_committee``. Access is ``_get_education_access``: chairs and
admins have everything; others need an ``EducationMemberPermission`` row
(view submissions, grade, manage tasks). The numbers come from the same
functions the web dashboard uses (``education_overview``, ``pledge_progress``).

* ``GET  committees/<code>/education/`` — pledge progress and points, the
  task list with how many pledges have done each, meetings, and absence
  requests waiting for a decision.
* ``GET  .../education/pledges/<pk>/`` — one pledge: tasks, marks, attendance.
* ``GET  .../education/tasks/<id>/`` — one task: every pledge's status, and
  for a quiz each pledge's answers.
* ``POST .../education/tasks/<id>/pledges/<pk>/`` ``{status, score?}`` — mark
  a pledge's task (grade), as ``education_toggle_completion``'s explicit mode.
* ``POST .../education/answers/<id>/`` ``{verdict}`` — mark an answer (grade).
* ``GET/POST .../education/meetings/<id>/attendance/`` — take attendance
  (manage); POST ``{statuses: {"<pledge pk>": status}}``.
* ``POST .../education/absences/<id>/`` ``{decision, review_note}`` (manage).
* ``POST .../education/pledges/<pk>/phase/`` ``{phase}`` and
  ``.../education/phases/advance/`` (manage).

Creating and editing tasks, quiz questions, meetings, bigs, point adjustments
and pledge page access stay on the website; the dashboard links there.
"""
from django.db import transaction
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_committees import _person
from src.models import (
    EducationAbsenceRequest, EducationMeeting, EducationMeetingAttendance, ParliamentUser, PledgeQuizAnswer, PledgeTask,
    PledgeTaskCompletion,
)
from src.models.users import member_defer
from src.pledge_phases import PHASES, next_phase
from src.pledge_points import grading_preview, is_scaled, points_for, quiz_marks
from src.view.committee.education import (
    _education_committee_or_404, _pledge_roster, education_overview, pledge_progress,
)

PHASE_LABELS = {'all': 'All phases', '1': 'Phase 1', '2': 'Phase 2', '3': 'Phase 3'}


class BadRequest(exceptions.APIException):
    status_code = 400


def _task_json(task):
    return {
        'id': task.id,
        'title': task.title,
        'description': task.description or '',
        'type': task.task_type,
        'type_label': task.get_task_type_display(),
        'phase': task.phase,
        'phase_label': PHASE_LABELS.get(task.phase, task.phase),
        'due_date': task.due_date.isoformat() if task.due_date else None,
        'is_required': task.is_required,
        'points': task.points,
        'max_score': task.max_score,
        'live': task.is_live,
        'status_label': task.activation_status_label,
    }


def _completion_json(c):
    if c is None:
        return None
    return {'status': c.status, 'status_label': c.get_status_display(), 'score': c.score,
            'score_display': c.score_display, 'notes': c.notes or ''}


def _meeting_json(m):
    e = m.event
    return {
        'id': m.id,
        'title': e.title,
        'date_time': e.date_time.isoformat(),
        'location': e.location or '',
        'type_label': m.get_meeting_type_display(),
        'attendance_required': m.attendance_required,
        'points': m.points,
        'marked_count': getattr(m, 'marked_count', None),
        'is_past': m.is_past,
    }


class _EducationView(MobileAPIView):
    web_page = 'committee_home'

    def setup_access(self, request, code):
        try:
            self.committee, self.access = _education_committee_or_404(code, request.user)
        except Http404:
            raise exceptions.NotFound()
        return self.committee

    def require(self, key):
        if not self.access[key]:
            raise exceptions.PermissionDenied('You don’t have permission to do that.', code='forbidden')

    @staticmethod
    def pledge(pledge_pk, active=False):
        qs = ParliamentUser.objects.filter(pk=pledge_pk, member_type='Pledge')
        if active:
            qs = qs.filter(is_active=True)
        pledge = qs.first()
        if pledge is None:
            raise exceptions.NotFound()
        return pledge


class EducationDashboardView(_EducationView):

    def get(self, request, code):
        committee = self.setup_access(request, code)
        from src.big_reveal import reveal_due_bigs
        reveal_due_bigs()   # as education_home: a due reveal is never shown pending
        overview = education_overview(committee)

        tasks = []
        for row in overview['task_rows']:
            applies = [pc for pc in row['pledge_completions'] if pc['applies']]
            tasks.append({
                **_task_json(row['task']),
                'pledge_count': len(applies),
                'done_count': sum(1 for pc in applies if pc['completion'] and pc['completion'].status == 'completed'),
                'waiting_count': sum(1 for pc in applies if pc['completion'] and pc['completion'].status == 'pending'),
            })
        pledges = [{
            **_person(ps['pledge']),
            'phase': ps['pledge'].pledge_phase,
            'completed': ps['completed'],
            'total': ps['total'],
            'percent': ps['percent'],
            'points': ps['points'],
            'max_points': ps['max_points'],
            'points_percent': ps['points_percent'],
            'has_adjustments': ps.get('has_adjustments', False),
            'last_seen_days': ps['last_seen_days'],
        } for ps in overview['pledge_summaries']]
        absences = (EducationAbsenceRequest.objects
                    .filter(meeting__committee=committee, status='pending')
                    .select_related('pledge', 'meeting__event')
                    .defer(*member_defer('pledge'))
                    .order_by('meeting__event__date_time'))
        web = reverse('education_home', args=[code])
        return Response({
            'committee': {'code': committee.code, 'name': committee.name},
            'access': {k: bool(v) for k, v in self.access.items()},
            'pledges': pledges,
            'tasks': tasks,
            'upcoming_meetings': [_meeting_json(m) for m in overview['upcoming_meetings']],
            'past_meetings': [_meeting_json(m) for m in overview['past_meetings']],
            'pending_absences': [{
                'id': a.id,
                'pledge': _person(a.pledge),
                'meeting': {'id': a.meeting_id, 'title': a.meeting.event.title,
                            'date_time': a.meeting.event.date_time.isoformat()},
                'reason': a.reason,
                'created_at': a.created_at.isoformat(),
            } for a in absences],
            'web_links': [
                {'title': title, 'path': f'{web}{anchor}'}
                for title, anchor in (('Tasks, meetings and page access', ''), ('Bigs', '#bigs'),
                                      ('Point adjustments', '#pledge-progress'))
            ] if self.access['can_manage_tasks'] else [],
        })


class EducationPledgeView(_EducationView):

    def get(self, request, code, pledge_pk):
        committee = self.setup_access(request, code)
        data = pledge_progress(committee, self.pledge(pledge_pk))
        pledge = data['pledge']
        return Response({
            **_person(pledge),
            'phase': pledge.pledge_phase,
            'required_total': data['required_total'],
            'required_done': data['required_done'],
            'required_percent': data['required_percent'],
            'overdue_count': len(data['overdue_rows']),
            'task_points': data['task_points'],
            'attendance_points': data['attendance_points'],
            'total_points': data['total_points'],
            'attended_count': data['attended_count'],
            'missed_count': data['missed_count'],
            'tasks': [{
                **_task_json(row['task']),
                'completion': _completion_json(row['completion']),
                'overdue': row['overdue'],
                'points_earned': row['points_earned'],
            } for row in data['task_rows']],
            'attendance': [{
                'meeting': {'id': r.meeting_id, 'title': r.meeting.event.title,
                            'date_time': r.meeting.event.date_time.isoformat()},
                'status': r.status,
                'status_label': r.get_status_display(),
                'points': r.points_earned,
            } for r in data['attendance']],
            'phases': list(PHASES),
            'study': _study_json(pledge),
        })


def _study_json(member):
    """This week's study hours (src/location_time.py) against the weekly requirement, if any."""
    from src.location_time import study_week
    week = study_week(member)
    return {'week_start': week['week_start'].isoformat(), 'minutes': week['minutes'],
            'required_minutes': week['required_minutes']}


class EducationTaskView(_EducationView):

    def get(self, request, code, task_id):
        self.setup_access(request, code)
        task = PledgeTask.objects.filter(pk=task_id, is_active=True).first()
        if task is None:
            raise exceptions.NotFound()
        assigned = set(task.assigned_to.values_list('pk', flat=True))
        pledges = [p for p in _pledge_roster().defer(*member_defer()) if not assigned or p.pk in assigned]
        completions = {c.pledge_id: c for c in PledgeTaskCompletion.objects.filter(task=task, pledge__in=pledges)}
        questions = list(task.questions.order_by('display_order', 'created_at')) if task.task_type == 'quiz' else []
        answers = {}
        if questions:
            for a in PledgeQuizAnswer.objects.filter(question__task=task, pledge__in=pledges):
                answers.setdefault(a.pledge_id, {})[a.question_id] = a
        marks = quiz_marks([task], pledges)
        rows = []
        for p in pledges:
            mine = answers.get(p.pk, {})
            rows.append({
                **_person(p),
                'completion': _completion_json(completions.get(p.pk)),
                'points_earned': points_for(task, completions.get(p.pk), p.pk, marks),
                'submitted': bool(mine),
                'answers': [{
                    'question_id': q.id,
                    'answer_id': mine[q.id].id if q.id in mine else None,
                    'text': mine[q.id].answer_text if q.id in mine else None,
                    'is_correct': mine[q.id].is_correct if q.id in mine else None,
                } for q in questions] if mine else [],
            })
        return Response({
            **_task_json(task),
            'points_scaled': is_scaled(task),
            'can_grade': bool(self.access['can_grade_submissions']),
            'questions': [{'id': q.id, 'text': q.question_text,
                           'hint': q.answer_hint or ''} for q in questions],
            'pledges': rows,
            'statuses': [{'value': v, 'label': str(label)} for v, label in PledgeTaskCompletion.STATUS_CHOICES],
        })


class EducationMarkTaskView(_EducationView):

    def post(self, request, code, task_id, pledge_pk):
        self.setup_access(request, code)
        self.require('can_grade_submissions')
        task = PledgeTask.objects.filter(pk=task_id, is_active=True).first()
        if task is None:
            raise exceptions.NotFound()
        pledge = self.pledge(pledge_pk)
        status = request.data.get('status')
        if status not in {v for v, _ in PledgeTaskCompletion.STATUS_CHOICES}:
            raise BadRequest('Unknown status.', code='invalid_choice')
        comp, _ = PledgeTaskCompletion.objects.get_or_create(task=task, pledge=pledge, defaults={'status': 'pending'})
        updated = ['status', 'reviewed_by', 'completed_at', 'updated_at']
        if 'score' in request.data:   # absent = leave the score alone, as on the web
            raw = request.data.get('score')
            if raw in (None, ''):
                comp.score = None
            else:
                try:
                    value = int(raw)
                except (TypeError, ValueError):
                    raise BadRequest('Score must be a whole number.', code='invalid_score')
                if value < 0:
                    raise BadRequest('Score cannot be negative.', code='invalid_score')
                if not task.max_score:
                    raise BadRequest('This task has no maximum score. Set one on the task first.', code='invalid_score')
                if value > task.max_score:
                    raise BadRequest(f'Score cannot exceed the maximum of {task.max_score}.', code='invalid_score')
                comp.score = value
            updated.append('score')
        comp.status = status
        comp.reviewed_by = request.user
        comp.completed_at = timezone.now() if status == 'completed' else None
        comp.save(update_fields=updated)

        warning = ''
        if status == 'completed' and is_scaled(task):
            marked = PledgeQuizAnswer.objects.filter(question__task=task, pledge=pledge)
            qcount = task.questions.count()
            preview = grading_preview(task, comp, marked.filter(is_correct=True).count(), qcount,
                                      marked.exclude(is_correct=None).count())
            if preview and preview['unmarked']:
                warning = (f"{pledge.get_display_name()} is marked passed, but {preview['unmarked']} of {qcount} "
                           f"answers aren't marked right or wrong yet, so he has {preview['points']} of "
                           f"{task.points} pts for now.")
        return Response({'completion': _completion_json(comp), 'warning': warning})


class EducationMarkAnswerView(_EducationView):

    def post(self, request, code, answer_id):
        self.setup_access(request, code)
        self.require('can_grade_submissions')
        answer = PledgeQuizAnswer.objects.filter(
            pk=answer_id, question__task__is_active=True, question__task__task_type='quiz').first()
        if answer is None:
            raise exceptions.NotFound()
        mapping = {'correct': True, 'wrong': False, 'clear': None}
        verdict = request.data.get('verdict')
        if verdict not in mapping:
            raise BadRequest('Unknown verdict.', code='invalid_choice')
        answer.is_correct = mapping[verdict]
        answer.save(update_fields=['is_correct'])
        return Response({'is_correct': answer.is_correct})


class EducationAttendanceView(_EducationView):

    def meeting(self, meeting_id):
        meeting = (EducationMeeting.objects.select_related('event')
                   .filter(pk=meeting_id, committee=self.committee).first())
        if meeting is None:
            raise exceptions.NotFound()
        return meeting

    def body(self, meeting):
        existing = {r.pledge_id: r for r in EducationMeetingAttendance.objects.filter(meeting=meeting)}
        return {
            'meeting': _meeting_json(meeting),
            'statuses': [{'value': v, 'label': str(label)} for v, label in EducationMeetingAttendance.STATUS_CHOICES],
            'pledges': [{**_person(p), 'status': existing[p.pk].status if p.pk in existing else 'pending'}
                        for p in _pledge_roster().defer(*member_defer())],
        }

    def get(self, request, code, meeting_id):
        self.setup_access(request, code)
        self.require('can_manage_tasks')
        return Response(self.body(self.meeting(meeting_id)))

    def post(self, request, code, meeting_id):
        self.setup_access(request, code)
        self.require('can_manage_tasks')
        meeting = self.meeting(meeting_id)
        statuses = request.data.get('statuses')
        if not isinstance(statuses, dict):
            raise BadRequest('Send each pledge’s status.', code='invalid_request')
        valid = {v for v, _ in EducationMeetingAttendance.STATUS_CHOICES}
        roster = {str(p.pk): p for p in _pledge_roster()}   # pledges only, as on the web
        now = timezone.now()
        with transaction.atomic():
            for pk, status in statuses.items():
                if status in valid and str(pk) in roster:
                    EducationMeetingAttendance.objects.update_or_create(
                        meeting=meeting, pledge=roster[str(pk)],
                        defaults={'status': status, 'marked_by': request.user, 'marked_at': now})
        return Response(self.body(meeting))


class EducationAbsenceView(_EducationView):

    def post(self, request, code, absence_id):
        committee = self.setup_access(request, code)
        self.require('can_manage_tasks')
        absence = (EducationAbsenceRequest.objects.select_related('meeting', 'pledge')
                   .filter(pk=absence_id, meeting__committee=committee).first())
        if absence is None:
            raise exceptions.NotFound()
        decision = request.data.get('decision')
        if decision not in ('approved', 'denied'):
            raise BadRequest('Approve or deny the request.', code='invalid_choice')
        now = timezone.now()
        absence.status = decision
        absence.reviewed_by = request.user
        absence.reviewed_at = now
        absence.review_note = str(request.data.get('review_note') or '').strip()[:2000]
        absence.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note'])
        if decision == 'approved':   # as on the web: an approval excuses him at the meeting
            EducationMeetingAttendance.objects.update_or_create(
                meeting=absence.meeting, pledge=absence.pledge,
                defaults={'status': 'excused', 'marked_by': request.user, 'marked_at': now})
        return Response({'status': decision})


class EducationPhaseView(_EducationView):

    def post(self, request, code, pledge_pk):
        self.setup_access(request, code)
        self.require('can_manage_tasks')
        pledge = self.pledge(pledge_pk, active=True)
        phase = request.data.get('phase')
        if phase not in PHASES:
            raise BadRequest('Choose Phase 1, 2 or 3.', code='invalid_choice')
        if pledge.pledge_phase != phase:
            pledge.pledge_phase = phase
            pledge.save(update_fields=['pledge_phase'])
        return Response({'phase': phase})


class EducationAdvanceAllView(_EducationView):

    def post(self, request, code):
        self.setup_access(request, code)
        self.require('can_manage_tasks')
        moved = 0
        with transaction.atomic():
            for pledge in (ParliamentUser.objects.select_for_update()
                           .filter(member_type='Pledge', is_active=True).only('pk', 'pledge_phase')):
                following = next_phase(pledge.pledge_phase)
                if following:
                    pledge.pledge_phase = following
                    pledge.save(update_fields=['pledge_phase'])
                    moved += 1
        return Response({'moved': moved})
