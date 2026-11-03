"""
The education committee's officer tools in the app (11-02-26): everything the
website's education pages do that the dashboard (src/mobile/views_education.py)
didn't. Each endpoint calls the same helper as its web view
(src/view/committee/education.py, manage_education_permissions.py), with the
same permission: ``can_manage_tasks`` for building tasks, quizzes, meetings,
points, bigs and page access; any education access for the quiz breakdown and
"view as pledge"; a real chair or an admin for member permissions.

All under ``committees/<code>/``:

* ``GET/POST education/tasks/new/`` — the task form's choices; create a task.
* ``GET/POST education/tasks/<id>/edit/`` — a task's fields; save them.
* ``POST education/tasks/<id>/publish/`` | ``delete/`` | ``duplicate/``.
* ``GET/POST education/tasks/<id>/questions/`` — a quiz's questions; add one.
* ``POST education/tasks/<id>/questions/<qid>/`` (edit) and ``.../delete/``.
* ``GET education/tasks/<id>/analysis/`` — which questions the class missed.
* ``GET education/pledges/<pk>/preview/`` — his My Tasks, as he sees it.
* ``POST education/pledges/<pk>/points/`` ``{current_delta, max_delta, reason}``
  and ``POST education/points/<id>/delete/``.
* ``GET/POST education/meetings/new/``, ``education/meetings/<id>/edit/``,
  ``POST education/meetings/<id>/delete/``.
* ``GET education/bigs/``; ``POST education/bigs/<pk>/`` ``{big, reveal_mode,
  reveals_at}``, ``.../reveal/``, ``.../unreveal/``, ``.../delete/``,
  ``education/bigs/reveal-all/``.
* ``GET/POST education/page-access/`` ``{url_name, display_name, phases}``,
  ``POST education/page-access/<id>/delete/``.
* ``GET education-permissions/``; ``POST education-permissions/members/<user_id>/``
  ``{can_view_submissions, ...}``; ``POST education-permissions/reset/``.

Forms take the app's JSON through ``FormData``, which reads it the way the web
form posts it, so the shared parsers see the same thing from both.
"""
from django.db.models import Count
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_committees import _person
from src.mobile.views_education import (
    BadRequest, _EducationView, _adjustment_json, _meeting_json, _task_json,
)
from src.mobile.views_pledge import my_tasks_json
from src.models import (
    Committee, EducationMeeting, ParliamentUser, PledgeBigAssignment, PledgePageRestriction, PledgePointAdjustment,
    PledgeTask, PledgeTaskQuestion,
)
from src.models.users import member_defer
from src.view.committee import education as edu
from src.view.committee import manage_education_permissions as perms


class FormData:
    """
    The app's JSON body, read like the web form's ``request.POST``: a ticked
    box (``true``) is ``'on'`` and an unticked one ``''``, numbers are
    strings, ``null`` is absent, and ``getlist`` gives a list of strings.
    """

    def __init__(self, data):
        self._data = data if isinstance(data, dict) else {}

    def get(self, key, default=None):
        value = self._data.get(key)
        if value is None or isinstance(value, (list, dict)):
            return default
        if isinstance(value, bool):
            return 'on' if value else ''
        return str(value)

    def getlist(self, key):
        value = self._data.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            value = [value]
        return [str(v) for v in value if isinstance(v, (str, int)) and not isinstance(v, bool)]


def _choices(pairs):
    return [{'value': v, 'label': str(label)} for v, label in pairs]


def _invalid(error):
    return BadRequest(error, code='invalid_input')


class _ManageView(_EducationView):
    """``can_manage_tasks``, the permission every web view this mirrors checks."""

    def setup_access(self, request, code):
        committee = super().setup_access(request, code)
        self.require('can_manage_tasks')
        return committee

    @staticmethod
    def task(task_id, **extra):
        task = PledgeTask.objects.filter(pk=task_id, is_active=True, **extra).first()
        if task is None:
            raise exceptions.NotFound()
        return task


# ── Tasks ────────────────────────────────────────────────────────────────────

def _task_options(task=None):
    return {
        'task_types': _choices(PledgeTask.TASK_TYPES),
        'phases': _choices(PledgeTask.PHASE_CHOICES),
        'activation_modes': _choices(PledgeTask.ACTIVATION_MODES),
        'points_modes': _choices(PledgeTask.POINTS_MODE_CHOICES),
        'pledges': [_person(p) for p in edu._pledge_roster().defer(*member_defer())],
        'songs': [{'id': s.id, 'title': s.title, 'category': s.category.name if s.category else None,
                   'is_active': s.is_active} for s in edu.task_song_choices(task)],
    }


def _task_fields(task):
    """Everything the task form edits, as it is now."""
    return {
        'id': task.id,
        'title': task.title,
        'description': task.description or '',
        'task_type': task.task_type,
        'phase': task.phase,
        'due_date': task.due_date.isoformat() if task.due_date else None,
        'is_required': task.is_required,
        'points': task.points,
        'max_score': task.max_score,
        'points_mode': task.points_mode,
        'show_analysis_to_pledges': task.show_analysis_to_pledges,
        'display_order': task.display_order,
        'activation_mode': task.activation_mode,
        'activates_at': task.activates_at.isoformat() if task.activates_at else None,
        'is_published': task.is_published,
        'song': task.song_id,
        'assigned_to': list(task.assigned_to.values_list('pk', flat=True)),
    }


class TaskCreateView(_ManageView):
    """``education_add_task``: a manual or timed task starts as a draft."""

    def get(self, request, code):
        self.setup_access(request, code)
        return Response({'task': None, 'options': _task_options()})

    def post(self, request, code):
        self.setup_access(request, code)
        form = FormData(request.data)
        task = PledgeTask(created_by=request.user)
        error = edu._apply_task_fields(form, task)
        if error:
            raise _invalid(error)
        edu.save_new_task(task, form.getlist('assigned_to'))
        # The web sends a new quiz straight to its questions; so does the app.
        return Response({'task': _task_json(task), 'add_questions': task.task_type == 'quiz'}, status=201)


class TaskEditView(_ManageView):
    """``education_edit_task``: completions are left alone."""

    def get(self, request, code, task_id):
        self.setup_access(request, code)
        task = self.task(task_id)
        return Response({'task': _task_fields(task), 'options': _task_options(task)})

    def post(self, request, code, task_id):
        self.setup_access(request, code)
        task = self.task(task_id)
        form = FormData(request.data)
        error = edu._apply_task_fields(form, task)
        if error:
            raise _invalid(error)
        edu.save_task(task, form.getlist('assigned_to'))
        return Response({'task': _task_json(task)})


class TaskPublishView(_ManageView):
    """``education_toggle_task_published``."""

    def post(self, request, code, task_id):
        self.setup_access(request, code)
        task = self.task(task_id)
        task.is_published = not task.is_published
        task.save(update_fields=['is_published', 'updated_at'])
        return Response({'is_published': task.is_published, 'task': _task_json(task)})


class TaskDeleteView(_ManageView):
    """``education_delete_task``: a soft delete, so marks are kept."""

    def post(self, request, code, task_id):
        self.setup_access(request, code)
        # As the web: found whether or not it is already deleted.
        task = PledgeTask.objects.filter(pk=task_id).first()
        if task is None:
            raise exceptions.NotFound()
        task.is_active = False
        task.save(update_fields=['is_active', 'updated_at'])
        return Response({'deleted': True})


class TaskDuplicateView(_ManageView):
    """``education_duplicate_task``: the copy is always a draft."""

    def post(self, request, code, task_id):
        self.setup_access(request, code)
        clone = edu.duplicate_task(self.task(task_id), request.user)
        return Response({'task': _task_json(clone)}, status=201)


# ── Quiz questions ───────────────────────────────────────────────────────────

def _question_json(q):
    return {'id': q.id, 'text': q.question_text, 'hint': q.answer_hint or '', 'display_order': q.display_order,
            'answer_count': getattr(q, 'answer_count', None)}


class QuestionsView(_ManageView):
    """``education_manage_quiz_questions`` and ``education_add_quiz_question``."""

    def body(self, task):
        # An annotation drops the model's default ordering, so it is restated.
        questions = (task.questions.annotate(answer_count=Count('answers'))
                     .order_by(*PledgeTaskQuestion._meta.ordering))
        return {'task': _task_json(task), 'questions': [_question_json(q) for q in questions]}

    def get(self, request, code, task_id):
        self.setup_access(request, code)
        return Response(self.body(self.task(task_id, task_type='quiz')))

    def post(self, request, code, task_id):
        self.setup_access(request, code)
        task = self.task(task_id, task_type='quiz')
        question = PledgeTaskQuestion(task=task, display_order=0)
        error = edu.apply_question_fields(FormData(request.data), question)
        if error:
            raise _invalid(error)
        question.save()
        return Response({'question': _question_json(question), **self.body(task)}, status=201)


class QuestionView(_ManageView):
    """``education_edit_quiz_question``: answers already given are kept."""

    def question(self, task_id, question_id):
        task = self.task(task_id, task_type='quiz')
        question = PledgeTaskQuestion.objects.filter(pk=question_id, task=task).first()
        if question is None:
            raise exceptions.NotFound()
        return task, question

    def post(self, request, code, task_id, question_id):
        self.setup_access(request, code)
        task, question = self.question(task_id, question_id)
        error = edu.apply_question_fields(FormData(request.data), question)
        if error:
            raise _invalid(error)
        question.save(update_fields=['question_text', 'answer_hint', 'display_order'])
        return Response({'question': _question_json(question), **QuestionsView().body(task)})


class QuestionDeleteView(QuestionView):
    """``education_delete_quiz_question``: its answers go with it."""

    def post(self, request, code, task_id, question_id):
        self.setup_access(request, code)
        task, question = self.question(task_id, question_id)
        question.delete()
        return Response({'deleted': True, **QuestionsView().body(task)})


class QuizAnalysisView(_EducationView):
    """``education_quiz_analysis``: any education access, as the web."""

    def get(self, request, code, task_id):
        committee = self.setup_access(request, code)
        task = PledgeTask.objects.filter(pk=task_id, is_active=True, task_type='quiz').first()
        if task is None:
            raise exceptions.NotFound()
        ctx = edu.quiz_analysis_context(task, committee=committee, is_chair=self.access['is_chair'])
        return Response({
            'task': _task_json(task),
            'submissions': ctx['submissions'],
            'scores': {'count': ctx['score_count'], 'average': ctx['score_average'],
                       'low': ctx['score_low'], 'high': ctx['score_high']},
            'questions': [{
                'id': r['question'].id,
                'text': r['question'].question_text,
                'hint': r['question'].answer_hint or '',
                'answered': r['answered'],
                'correct': r['correct'],
                'wrong': r['wrong'],
                'unmarked': r['unmarked'],
                'percent': r['percent'],
            } for r in ctx['rows']],
        })


# ── Pledges: preview and points ──────────────────────────────────────────────

class PledgePreviewView(_EducationView):
    """
    ``education_preview_pledge_tasks``: his My Tasks exactly as he sees it,
    for anyone with education access. Read-only; the app hides taking a quiz
    and asking to be excused, as the web preview does.
    """

    def get(self, request, code, pledge_pk):
        self.setup_access(request, code)
        pledge = self.pledge(pledge_pk)
        return Response({**my_tasks_json(pledge), 'is_preview': True, 'pledge': _person(pledge)})


class AdjustPointsView(_ManageView):
    """``education_adjust_points``."""

    def post(self, request, code, pledge_pk):
        committee = self.setup_access(request, code)
        pledge = self.pledge(pledge_pk)
        adjustment, error = edu.adjust_points(committee, pledge, request.user, FormData(request.data))
        if error:
            raise _invalid(error)
        return Response({'adjustment': _adjustment_json(adjustment)}, status=201)


class DeleteAdjustmentView(_ManageView):
    """``education_delete_point_adjustment``: this committee's rows only."""

    def post(self, request, code, adjustment_id):
        committee = self.setup_access(request, code)
        adjustment = PledgePointAdjustment.objects.filter(pk=adjustment_id, committee=committee).first()
        if adjustment is None:
            raise exceptions.NotFound()
        adjustment.delete()
        return Response({'deleted': True})


# ── Meetings ─────────────────────────────────────────────────────────────────

def _meeting_options():
    return {
        'meeting_types': _choices(EducationMeeting.MEETING_TYPES),
        'tasks': [{'id': t.id, 'title': t.title}
                  for t in PledgeTask.objects.filter(is_active=True).order_by('display_order', 'title')],
    }


def _meeting_fields(m):
    e = m.event
    return {
        'id': m.id,
        'title': e.title,
        'description': e.description or '',
        'date_time': e.date_time.isoformat(),
        'location': e.location or '',
        'meeting_type': m.meeting_type,
        'attendance_required': m.attendance_required,
        'points': m.points,
        'notes': m.notes or '',
        'homework': list(m.homework.values_list('pk', flat=True)),
    }


class MeetingCreateView(_ManageView):
    """``education_add_meeting``: a meeting is a calendar event too."""

    def get(self, request, code):
        self.setup_access(request, code)
        return Response({'meeting': None, 'options': _meeting_options()})

    def post(self, request, code):
        committee = self.setup_access(request, code)
        form = FormData(request.data)
        meeting, event = edu.new_meeting(committee, request.user)
        error = edu._apply_meeting_fields(form, meeting, event)
        if error:
            raise _invalid(error)
        edu.save_new_meeting(meeting, event, form.getlist('homework'))
        return Response({'meeting': _meeting_json(meeting)}, status=201)


class MeetingEditView(_ManageView):
    """``education_edit_meeting``: attendance survives an edit."""

    def meeting(self, meeting_id):
        meeting = (EducationMeeting.objects.select_related('event')
                   .filter(pk=meeting_id, committee=self.committee).first())
        if meeting is None:
            raise exceptions.NotFound()
        return meeting

    def get(self, request, code, meeting_id):
        self.setup_access(request, code)
        return Response({'meeting': _meeting_fields(self.meeting(meeting_id)), 'options': _meeting_options()})

    def post(self, request, code, meeting_id):
        self.setup_access(request, code)
        meeting = self.meeting(meeting_id)
        form = FormData(request.data)
        error = edu._apply_meeting_fields(form, meeting, meeting.event)
        if error:
            raise _invalid(error)
        edu.save_meeting(meeting, form.getlist('homework'))
        return Response({'meeting': _meeting_json(meeting)})


class MeetingDeleteView(MeetingEditView):
    """``education_delete_meeting``: the calendar entry and attendance go too."""

    def post(self, request, code, meeting_id):
        self.setup_access(request, code)
        edu.delete_meeting(self.meeting(meeting_id))
        return Response({'deleted': True})


# ── Bigs ─────────────────────────────────────────────────────────────────────

def _assignment_json(a):
    if a is None:
        return None
    return {
        'big': {'user_id': a.big.user_id, 'name': a.big.get_display_name()},
        'reveal_mode': a.reveal_mode,
        'reveal_mode_label': a.get_reveal_mode_display(),
        'reveals_at': a.reveals_at.isoformat() if a.reveals_at else None,
        'revealed_at': a.revealed_at.isoformat() if a.revealed_at else None,
        'is_revealed': a.is_revealed,
    }


class _BigsView(_ManageView):
    """The Bigs section of ``education_home``; drafts are for ``can_manage_tasks`` only."""

    def body(self, message=''):
        rows, candidates, unrevealed = edu.big_board(edu._pledge_roster().defer(*member_defer()))
        return {
            'rows': [{**_person(r['pledge']), 'assignment': _assignment_json(r['assignment'])} for r in rows],
            'candidates': [{'user_id': c.user_id, 'name': c.get_display_name()} for c in candidates],
            'reveal_modes': _choices(PledgeBigAssignment.REVEAL_MODES),
            'unrevealed_count': unrevealed,
            'message': message,
        }

    def assignment(self, pledge_pk):
        pledge = self.pledge(pledge_pk)
        return pledge, (PledgeBigAssignment.objects.select_related('pledge', 'big').filter(pledge=pledge).first())

    def existing(self, pledge_pk):
        pledge, assignment = self.assignment(pledge_pk)
        if assignment is None:
            raise exceptions.NotFound()
        return pledge, assignment


class BigsView(_BigsView):

    def get(self, request, code):
        self.setup_access(request, code)
        return Response(self.body())


class SetBigView(_BigsView):
    """``education_set_big``."""

    def post(self, request, code, pledge_pk):
        committee = self.setup_access(request, code)
        pledge, assignment = self.assignment(pledge_pk)
        message, error = edu.set_big(committee, pledge, assignment, request.user, FormData(request.data))
        if error:
            raise _invalid(error)
        return Response(self.body(message))


class RevealBigView(_BigsView):
    """``education_reveal_big``."""

    def post(self, request, code, pledge_pk):
        from src.big_reveal import reveal
        self.setup_access(request, code)
        pledge, assignment = self.existing(pledge_pk)
        return Response(self.body(f'{pledge.name}\'s big is now live.' if reveal(assignment) else ''))


class UnrevealBigView(_BigsView):
    """``education_unreveal_big``."""

    def post(self, request, code, pledge_pk):
        self.setup_access(request, code)
        pledge, assignment = self.existing(pledge_pk)
        hidden = edu.unreveal_big(assignment)
        return Response(self.body(f'{pledge.name}\'s big is hidden again (back to draft).' if hidden else ''))


class DeleteBigView(_BigsView):
    """``education_delete_big``."""

    def post(self, request, code, pledge_pk):
        self.setup_access(request, code)
        pledge, assignment = self.existing(pledge_pk)
        edu.delete_big(assignment)
        return Response(self.body(f'Removed {pledge.name}\'s big.'))


class RevealAllBigsView(_BigsView):
    """``education_reveal_all_bigs``."""

    def post(self, request, code):
        self.setup_access(request, code)
        count = edu.reveal_all_bigs()
        return Response(self.body(f'Revealed {count} big{"s" if count != 1 else ""}.'))


# ── Pledge page access ───────────────────────────────────────────────────────

class PageAccessView(_ManageView):
    """The Pledge Page Access panel of ``education_home``, and ``education_update_page_restriction``."""

    def body(self):
        from src.middleware.pledge_pages import restrictable_url_names
        return {
            'restrictions': [{
                'id': r.id,
                'url_name': r.url_name,
                'display_name': r.display_name or '',
                'allowed_phases': list(r.allowed_phases or []),
            } for r in PledgePageRestriction.objects.all().order_by('display_name', 'url_name')],
            'phases': _choices(PledgePageRestriction.PHASE_CHOICES),
            'pages': restrictable_url_names(),
        }

    def get(self, request, code):
        self.setup_access(request, code)
        return Response(self.body())

    def post(self, request, code):
        self.setup_access(request, code)
        form = FormData(request.data)
        _, error = edu.update_page_restriction(form.get('url_name', ''), form.get('display_name', ''),
                                               form.getlist('phases'), request.user)
        if error:
            raise _invalid(error)
        return Response(self.body())


class DeletePageAccessView(PageAccessView):
    """``education_delete_page_restriction``."""

    def post(self, request, code, restriction_id):
        self.setup_access(request, code)
        restriction = PledgePageRestriction.objects.filter(pk=restriction_id).first()
        if restriction is None:
            raise exceptions.NotFound()
        edu.delete_page_restriction(restriction)
        return Response(self.body())


# ── Member permissions ───────────────────────────────────────────────────────

PERMISSION_LABELS = {
    'can_view_submissions': 'View submissions',
    'can_grade_submissions': 'Grade submissions',
    'can_manage_tasks': 'Manage tasks, meetings and pledges',
    'can_adjust_time': 'Adjust study hours',
}


class _PermissionsView(MobileAPIView):
    """``manage_education_permissions``: a real chair of the committee, or an admin."""
    web_page = 'manage_education_permissions'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def committee_for(self, request, code):
        # As the web: is_education_committee, active or not.
        committee = Committee.objects.filter(code=code, is_education_committee=True).first()
        if committee is None:
            raise exceptions.NotFound()
        if not perms.may_manage_education_permissions(request.user, committee):
            raise exceptions.PermissionDenied('Only education chairs can manage member permissions.',
                                              code='forbidden')
        return committee

    @staticmethod
    def body(committee):
        return {
            'committee': {'code': committee.code, 'name': committee.name},
            'fields': [{'field': f, 'label': PERMISSION_LABELS[f]} for f in perms.EDUCATION_PERM_FIELDS],
            'rows': [{
                **_person(r['member']),
                'is_chair': r['is_chair'],
                'flags': {f: bool(r[f]) for f in perms.EDUCATION_PERM_FIELDS},
            } for r in perms.education_permission_rows(committee)],
        }


class PermissionsView(_PermissionsView):

    def get(self, request, code):
        return Response(self.body(self.committee_for(request, code)))


class SetPermissionView(_PermissionsView):
    """``update_education_member_permission``: every flag at once; one left out is off."""
    web_page = 'update_education_member_permission'

    def post(self, request, code, user_id):
        committee = self.committee_for(request, code)
        member = ParliamentUser.objects.filter(user_id=user_id).first()
        if member is None:
            raise exceptions.NotFound()
        data = request.data if isinstance(request.data, dict) else {}
        flags = {f: data.get(f) is True for f in perms.EDUCATION_PERM_FIELDS}
        _, error = perms.set_education_permission(committee, member, flags, request.user)
        if error:
            raise _invalid(error)
        return Response(self.body(committee))


class ResetPermissionsView(_PermissionsView):
    """``reset_education_permissions``."""
    web_page = 'reset_education_permissions'

    def post(self, request, code):
        committee = self.committee_for(request, code)
        deleted = perms.reset_education_permission_rows(committee)
        return Response({
            **self.body(committee),
            'deleted': deleted,
            'message': f'Reset {deleted} member permission(s) to default (no access).',
        })
