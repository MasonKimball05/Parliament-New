"""
A pledge's My Tasks in the app (10-30-26): the website's ``my_pledge_tasks``,
quiz, quiz breakdown and absence request pages, through the same functions
(src/view/pledge_tasks.py). Pledges only, as on the website.

* ``GET pledge/tasks/`` — ``build_pledge_tasks_context``: tasks by phase with
  each one's status, points and overdue flag, progress, upcoming meetings
  (with homework and this pledge's absence request), meeting attendance, the
  revealed big and this week's study hours. A song task carries its lyrics, as
  the web page shows them.
* ``GET pledge/tasks/<id>/quiz/`` — the questions and this pledge's answers;
  ``POST`` ``{answers: {"<question id>": text}}`` submits them for review.
* ``GET pledge/tasks/<id>/analysis/`` — the class's question-by-question
  breakdown, when the chair has shared it (``quiz_analysis_context`` with
  ``viewer_is_pledge``: no names, small classes withheld).
* ``POST pledge/meetings/<id>/absence/`` ``{reason}`` — ask to be excused
  from a meeting that hasn't happened.

Every task a pledge is shown or may submit goes through
``pledge_may_see_task`` (live, unlocked for their phase, assigned to them).
"""
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.mobile.views_education import PHASE_LABELS, _completion_json, _meeting_json, _study_json, _task_json
from src.mobile.views_excuses_polls import BadRequest
from src.models import EducationMeeting, PledgeTask
from src.view import pledge_tasks as pt


class _PledgeView(MobileAPIView):
    web_page_toggle = False   # the My Tasks pages have no page toggle

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.is_pledge:
            raise exceptions.PermissionDenied('Pledges only.', code='forbidden')

    @staticmethod
    def quiz(request, task_id):
        task = PledgeTask.objects.filter(pk=task_id, is_active=True, task_type='quiz').first()
        if task is None or not pt.pledge_may_see_task(request.user, task):
            raise exceptions.NotFound()
        return task


def _song_json(song):
    if song is None:
        return None
    return {
        'id': song.id,
        'title': song.title,
        'lyrics': song.lyrics,
        'category': {'name': song.category.name, 'color': song.category.color} if song.category else None,
        'has_audio': bool(song.audio_file),
    }


def _absence_json(req):
    if req is None:
        return None
    return {'status': req.status, 'status_label': req.get_status_display(), 'reason': req.reason,
            'review_note': req.review_note or ''}


class MyTasksView(_PledgeView):
    web_page = 'my_pledge_tasks'

    def get(self, request):
        # As my_pledge_tasks: a reveal that's due is applied before the page is built.
        from src.big_reveal import reveal_due_bigs
        reveal_due_bigs()
        ctx = pt.build_pledge_tasks_context(request.user)
        big = ctx['my_big']
        return Response({
            'phase_label': ctx['pledge_phase_label'],
            'progress': {
                'required_total': ctx['all_required'],
                'required_done': ctx['required_done'],
                'percent': ctx['overall_percent'],
                'overdue_count': ctx['overdue_count'],
            },
            'points': {
                'tasks': ctx['task_points'],
                'attendance': ctx['attendance_points'],
                'total': ctx['total_points'],
            },
            'phases': [{
                'phase': g['phase'],
                'label': g['label'],
                'completed': g['completed'],
                'total': g['total'],
                'required_done': g['required_done'],
                'required_total': g['required_total'],
                'tasks': [{
                    **_task_json(i['task']),
                    'completion': _completion_json(i['completion']),
                    'overdue': i['overdue'],
                    'points_earned': i['points_earned'],
                    'points_scaled': i['points_scaled'],
                    'show_analysis': i['task'].task_type == 'quiz' and i['task'].show_analysis_to_pledges,
                    'song': _song_json(i['task'].song) if i['task'].task_type == 'song' else None,
                } for i in g['items']],
            } for g in ctx['phase_groups']],
            'upcoming_meetings': [{
                **_meeting_json(m),
                'description': m.event.description or '',
                'homework': [{'id': t.id, 'title': t.title} for t in m.homework.all()],
                'absence_request': _absence_json(m.my_absence_request),
            } for m in ctx['upcoming_meetings']],
            'attendance': [{
                'meeting': {'id': r.meeting_id, 'title': r.meeting.event.title,
                            'date_time': r.meeting.event.date_time.isoformat()},
                'status': r.status,
                'status_label': r.get_status_display(),
                'points': r.points_earned,
                'max_points': r.meeting.points,
            } for r in ctx['attendance_history']],
            'meetings_attended': ctx['meetings_attended'],
            'missed_count': ctx['missed_count'],
            'big': {
                'name': big.get_display_name(),
                'house': big.get_house_display(),
                'pledge_class': big.pledge_class_greek or big.pledge_class or '',
                'role_number': big.role_number or '',
                'revealed_at': ctx['my_big_revealed_at'].isoformat(),
            } if big else None,
            'study': _study_json(request.user) if ctx['study'] else None,
            'phase_names': PHASE_LABELS,
        })


class QuizView(_PledgeView):
    web_page = 'pledge_take_quiz'

    def state(self, request, task):
        questions, answers, submitted, completion = pt.quiz_state(request.user, task)
        return {
            'task': _task_json(task),
            'questions': [{'id': q.id, 'number': n, 'text': q.question_text, 'answer': answers.get(q.pk, '')}
                          for n, q in enumerate(questions, start=1)],
            'submitted': submitted,
            'completion': _completion_json(completion),
            'show_analysis': task.show_analysis_to_pledges,
        }

    def get(self, request, task_id):
        task = self.quiz(request, task_id)
        return Response(self.state(request, task))

    def post(self, request, task_id):
        task = self.quiz(request, task_id)
        questions, _, submitted, _ = pt.quiz_state(request.user, task)
        if not questions:
            raise BadRequest('This quiz has no questions yet.', code='no_questions')
        if submitted:
            raise BadRequest('You have already taken this quiz.', code='already_submitted')
        raw = request.data.get('answers') if isinstance(request.data, dict) else None
        if not isinstance(raw, dict):
            raise BadRequest('answers is an object of question id to text.', code='invalid')
        errors = pt.save_quiz_answers(request.user, task, questions,
                                      {q.pk: raw.get(str(q.pk)) for q in questions})
        if errors:
            raise BadRequest(errors[0], code='invalid')
        return Response(self.state(request, task))


class QuizAnalysisView(_PledgeView):
    web_page = 'pledge_quiz_analysis'

    def get(self, request, task_id):
        from src.view.committee.education import quiz_analysis_context
        task = self.quiz(request, task_id)   # pledge_may_see_task first, then the sharing flag
        if not task.show_analysis_to_pledges:
            raise exceptions.NotFound()
        ctx = quiz_analysis_context(task, viewer_is_pledge=True)
        return Response({
            'task': _task_json(task),
            'withheld': ctx['withheld'],
            'min_submissions': ctx['min_submissions'],
            'submissions': ctx['submissions'],
            'scores': {'count': ctx['score_count'], 'average': ctx['score_average'],
                       'low': ctx['score_low'], 'high': ctx['score_high']},
            'questions': [{
                'id': r['question'].id,
                'text': r['question'].question_text,
                'answered': r['answered'],
                'correct': r['correct'],
                'wrong': r['wrong'],
                'unmarked': r['unmarked'],
                'percent': r['percent'],
                'suppressed': r['suppressed'],
            } for r in ctx['rows']],
        })


class AbsenceRequestView(_PledgeView):
    web_page = 'pledge_request_absence'

    def post(self, request, meeting_id):
        meeting = EducationMeeting.objects.select_related('event').filter(pk=meeting_id).first()
        if meeting is None:
            raise exceptions.NotFound()
        reason = request.data.get('reason') if isinstance(request.data, dict) else None
        error = pt.request_absence(request.user, meeting, reason)
        if error:
            raise BadRequest(error, code='invalid')
        req = meeting.absence_requests.get(pledge=request.user)
        return Response({'absence_request': _absence_json(req)}, status=201)
