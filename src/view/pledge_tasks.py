"""
Pledge-facing task view — shows the logged-in pledge their own task list
with per-phase grouping and completion status.

Accessible to pledges (not blocked). Non-pledges are redirected to home.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from src.models import (
    PledgeTask, PledgeTaskCompletion, PledgeTaskQuestion, PledgeQuizAnswer,
    EducationMeeting, EducationMeetingAttendance, EducationAbsenceRequest,
)


def pledge_may_see_task(user, task):
    """
    Whether this pledge is entitled to see this task at all.

    ⚠️ v3.21.5 — TWO PREDICATES, AND ONE VIEW WAS APPLYING NEITHER.
    `pledge_take_quiz` has always checked both — the task must be **live**
    (`activation_mode` satisfied) and, if it has an explicit `assigned_to` list,
    this pledge must be on it. `pledge_quiz_analysis`, added in v3.21.0, checked
    `show_analysis_to_pledges` and nothing else, so a pledge could open the
    breakdown for

      * a quiz assigned to somebody else specifically, and
      * an **unpublished draft** — which matters because `education_duplicate_task`
        copies `show_analysis_to_pledges` onto a clone whose entire purpose is to
        be invisible until the chair publishes it, and the analysis page renders
        every question's text.

    ⚠️ THIS WAS A REAL DISCLOSURE ON THE SHIPPED TREE, AND MY FIRST DRAFT OF
    THIS NOTE SAID IT WAS NOT. I wrote that nothing was exposed in practice
    because the minimum-submissions threshold would empty the rows — then ran
    the new tests against `f241f45` and `test_the_question_text_does_not_reach_
    the_page` **failed**, because that tree has no threshold either. The
    threshold is v3.21.5's own work; reasoning about the shipped code using a
    protection added in the same release is exactly the error this repo has
    recorded before, when a uuid filename described four times as "not the
    access control" was the access control for two days.

    **A threshold about anonymity is not an entitlement check.** Even where it
    happens to cover the same case, it is answering a different question and can
    be tuned or removed by someone thinking only about that question.

    Tenth instance of the shape CLAUDE.md tracks: a rule stated correctly in one
    view and left out of a second view added later. So it is a function, and
    `src/test_pledge_task_entitlement.py` fails the build if either view stops
    calling it.
    """
    if not task.is_live:
        return False
    # An empty assignment means "all pledges"; a non-empty one is a list.
    assigned_pks = set(task.assigned_to.values_list('pk', flat=True))
    return not assigned_pks or user.pk in assigned_pks


@login_required
def my_pledge_tasks(request):
    if not request.user.is_pledge:
        return redirect('home')

    from django.db.models import Q
    from django.utils import timezone
    now = timezone.now()

    # Only show tasks that are live (not deleted, and activation conditions met)
    tasks = PledgeTask.objects.filter(
        is_active=True,
    ).filter(
        Q(activation_mode='immediate') |
        Q(activation_mode='manual', is_published=True) |
        Q(activation_mode='timed', activates_at__lte=now)
    ).filter(
        # Assigned to this pledge specifically, or assigned to nobody (= all pledges)
        Q(assigned_to__isnull=True) | Q(assigned_to=request.user)
    ).distinct().order_by('display_order', 'due_date', 'title')

    # ⚠️ `select_related('task')` as of v3.20.0. `my_tasks.html` asks each
    # completion for `has_score` / `score_display` / `score_percent`, and all
    # three read `self.task.max_score` — so without the join this page costs one
    # extra query per scored task. Measured on a 10-task fixture:
    # **11 × src_pledgetask**, an N+1 the scoring feature introduced on the
    # page it was built for.
    completions = PledgeTaskCompletion.objects.filter(
        task__in=tasks, pledge=request.user
    ).select_related('task')
    completion_map = {c.task_id: c for c in completions}

    phase_order = ['all', '1', '2', '3']
    phase_labels = {'all': 'General', '1': 'Phase 1', '2': 'Phase 2', '3': 'Phase 3'}

    # Group tasks by phase
    phases = {}
    for task in tasks:
        p = task.phase
        if p not in phases:
            phases[p] = []
        comp = completion_map.get(task.pk)
        phases[p].append({
            'task': task,
            'completion': comp,
            # v3.20.2 — overdue is per (task, pledge); see `is_overdue_for`.
            'overdue': task.is_overdue_for(comp),
        })

    phase_groups = [
        {
            'phase': p,
            'label': phase_labels.get(p, p),
            'items': phases[p],
            'completed': sum(1 for i in phases[p] if i['completion'] and i['completion'].status == 'completed'),
            'total': len(phases[p]),
            'required_total': sum(1 for i in phases[p] if i['task'].is_required),
            'required_done': sum(
                1 for i in phases[p]
                if i['task'].is_required and i['completion'] and i['completion'].status == 'completed'
            ),
        }
        for p in phase_order if p in phases
    ]

    # Overall progress on required tasks
    all_required = [t for t in tasks if t.is_required]
    required_done = sum(
        1 for t in all_required
        if completion_map.get(t.pk) and completion_map[t.pk].status == 'completed'
    )

    # ── Meetings (v3.20.0) ────────────────────────────────────────────────
    #
    # Upcoming meetings are shown with their date, time, location and homework
    # so a pledge has one page to look at rather than a calendar and a task
    # list. Attendance points are summed from this pledge's own records only.
    upcoming_meetings = list(
        EducationMeeting.objects
        .filter(event__date_time__gte=now)
        .select_related('event')
        .prefetch_related('homework')
        .order_by('event__date_time')[:5]
    )

    # ⚠️ v3.21.0 — `select_related` down to the EVENT, because the history
    # below renders each meeting's title and date. Without it this is one query
    # per past meeting on the pledge's own page.
    my_attendance = list(
        EducationMeetingAttendance.objects
        .filter(pledge=request.user)
        .select_related('meeting', 'meeting__event')
        .order_by('-meeting__event__date_time')
    )
    attendance_points = sum(record.points_earned for record in my_attendance)
    meetings_attended = sum(
        1 for record in my_attendance
        if record.status in EducationMeetingAttendance.EARNS_POINTS
    )

    # Task points a pledge has actually banked — completed tasks only.
    task_points = sum(
        t.points for t in tasks
        if completion_map.get(t.pk) and completion_map[t.pk].status == 'completed'
    )

    # ⚠️ v3.20.2 — `due_date` had been stored since the model was written and
    # surfaced NOWHERE, so a task three weeks late looked exactly like one due
    # tomorrow. Counted here so the page can lead with it rather than making a
    # pledge scan every group for red text.
    overdue_items = [
        item
        for group in phase_groups
        for item in group['items']
        if item['overdue']
    ]

    # ⚠️ v3.21.0 — a pledge could see "attendance points: 10" and had no way to
    # find out why it was not 25. If you score someone, the person being scored
    # has to be able to audit it; otherwise the first he hears of a gap is at an
    # initiation review, from someone holding a number he cannot check.
    missed_meetings = [
        record for record in my_attendance if record.status == 'absent'
    ]

    # Requests he has already filed, so the page can say "asked" rather than
    # offering the form again.
    absence_requests = {
        req.meeting_id: req
        for req in EducationAbsenceRequest.objects.filter(pledge=request.user)
    }

    for meeting in upcoming_meetings:
        meeting.my_absence_request = absence_requests.get(meeting.pk)

    context = {
        'phase_groups': phase_groups,
        'attendance_history': my_attendance,
        'missed_count': len(missed_meetings),
        'absence_requests': absence_requests,
        'overdue_count': len(overdue_items),
        'all_required': len(all_required),
        'required_done': required_done,
        'overall_percent': round(required_done / len(all_required) * 100) if all_required else 0,
        'upcoming_meetings': upcoming_meetings,
        'attendance_points': attendance_points,
        'meetings_attended': meetings_attended,
        'task_points': task_points,
        'total_points': task_points + attendance_points,
    }
    return render(request, 'pledge/my_tasks.html', context)


@login_required
def pledge_take_quiz(request, task_pk):
    """
    Pledge-facing quiz submission view.

    GET  — renders the quiz questions (only if task is live and not yet submitted).
    POST — saves answers and sets PledgeTaskCompletion to 'pending' for chair review.
    """
    if not request.user.is_pledge:
        return redirect('home')

    from django.db.models import Q
    now = timezone.now()

    task = get_object_or_404(
        PledgeTask,
        pk=task_pk,
        is_active=True,
        task_type='quiz',
    )

    # Live, and assigned to this pledge (or to everybody). v3.21.5 moved these
    # two checks into `pledge_may_see_task` so the analysis view cannot drift
    # from them — see that function for what happened when it did.
    if not pledge_may_see_task(request.user, task):
        return render(request, 'pledge/quiz_not_available.html', {'task': task})

    questions = list(task.questions.all())
    if not questions:
        return render(request, 'pledge/quiz_not_available.html', {'task': task, 'no_questions': True})

    # Check if already submitted
    existing_answers = {
        a.question_id: a.answer_text
        for a in PledgeQuizAnswer.objects.filter(question__task=task, pledge=request.user)
    }
    already_submitted = len(existing_answers) == len(questions)

    completion = PledgeTaskCompletion.objects.filter(task=task, pledge=request.user).first()

    if request.method == 'POST' and not already_submitted:
        errors = []
        answers = {}
        for q in questions:
            text = request.POST.get(f'answer_{q.pk}', '').strip()
            if not text:
                errors.append(f'Please answer question {q.display_order + 1}.')
            answers[q.pk] = text

        if not errors:
            # Save answers (update existing if somehow partial)
            for q in questions:
                PledgeQuizAnswer.objects.update_or_create(
                    question=q,
                    pledge=request.user,
                    defaults={'answer_text': answers[q.pk]},
                )

            # Create or update completion → pending (awaiting chair review).
            #
            # ⚠️ v3.20.0 — THE SCORE IS CLEARED WITH IT, AND THAT MATTERS MORE
            # THAN IT LOOKS. `already_submitted` is "has this pledge answered
            # every question", so **a chair adding a question to a quiz reopens
            # it for everyone who had already sat it** — including pledges a
            # chair had already marked and scored.
            #
            # Before scoring existed, that reset `completed` → `pending`, which
            # is arguably right: there are new answers to read. With scoring it
            # would have left the OLD mark attached to the NEW answers, so a
            # pledge's page would read "50/60" beside answers nobody had graded,
            # and the grading page would show a score for a submission the chair
            # had never seen.
            #
            # A stale grade is worse than no grade, so the mark goes when the
            # answers change. `src/test_education_scoring_and_meetings.py` pins
            # both halves.
            PledgeTaskCompletion.objects.update_or_create(
                task=task,
                pledge=request.user,
                defaults={'status': 'pending', 'score': None},
            )
            return redirect('my_pledge_tasks')

        return render(request, 'pledge/take_quiz.html', {
            'task': task,
            'questions': questions,
            'errors': errors,
            'post': request.POST,
        })

    # Pre-zip for template (Django can't do dict[variable_key] lookups)
    question_answer_pairs = [(q, existing_answers.get(q.pk, '')) for q in questions]

    return render(request, 'pledge/take_quiz.html', {
        'task': task,
        'questions': questions,
        'question_answer_pairs': question_answer_pairs,
        'already_submitted': already_submitted,
        'completion': completion,
    })


@login_required
@require_POST
def pledge_request_absence(request, meeting_pk):
    """
    A pledge asking to be excused from a meeting (v3.21.0 — ideas list #7).

    Before this, an excused absence was a text message to a chair that existed
    only in his memory, and the pledge had no record that he had ever asked.

    ⚠️ ONLY FOR MEETINGS THAT HAVE NOT HAPPENED. Asking to be excused from
    something that already took place is not a request, it is a dispute about a
    record — a different conversation, and one that should happen with a person
    rather than through a form that looks like it will be granted.
    """
    from src.models import EducationMeeting, EducationAbsenceRequest

    if not request.user.is_pledge:
        return redirect('home')

    # ⚠️ DELIBERATELY NOT NARROWED TO "his" MEETINGS, and that is not an
    # oversight (v3.21.5 looked and left it). `my_pledge_tasks` lists every
    # future `EducationMeeting` regardless of committee, so every meeting a
    # pledge can request an absence from is one his own page already shows him.
    # Narrowing here without narrowing there would only make the two disagree.
    # If the roster ever becomes per-committee, both queries change together.
    meeting = get_object_or_404(
        EducationMeeting.objects.select_related('event'), pk=meeting_pk
    )
    if meeting.event.date_time < timezone.now():
        messages.error(
            request,
            'That meeting has already happened. Speak to your educator if the '
            'attendance recorded for it is wrong.',
        )
        return redirect('my_pledge_tasks')

    reason = (request.POST.get('reason') or '').strip()
    if not reason:
        messages.error(request, 'Please say why you cannot attend.')
        return redirect('my_pledge_tasks')

    # `update_or_create` so a second submission edits the first rather than
    # hitting the unique constraint with a 500 — and it resets the status,
    # because a re-asked request has not been decided on its new reason.
    EducationAbsenceRequest.objects.update_or_create(
        meeting=meeting,
        pledge=request.user,
        defaults={'reason': reason, 'status': 'pending',
                  'reviewed_by': None, 'reviewed_at': None, 'review_note': ''},
    )
    messages.success(request, 'Absence request sent to your educator.')
    return redirect('my_pledge_tasks')


@login_required
def pledge_quiz_analysis(request, task_pk):
    """
    The question-by-question breakdown, for a pledge (v3.21.0 — ideas list #9).

    ⚠️ OFF BY DEFAULT. Mason's call: the breakdown is an educator's tool, and a
    pledge sees it only for quizzes where the chair has ticked
    `show_analysis_to_pledges`. The gate is a plain model field rather than a
    feature flag, deliberately — CLAUDE.md records that Python flag reads fail
    OPEN, and a defaulting-open gate on "who may see the class's results" is the
    wrong direction to be wrong in. A boolean that defaults to `False` fails
    closed with no seeding step to forget.

    The numbers come from the same builder the educator page uses, so the two
    audiences cannot be told different things — but a pledge never sees who
    answered what.
    """
    from src.view.committee.education import quiz_analysis_context

    if not request.user.is_pledge:
        return redirect('home')

    task = get_object_or_404(PledgeTask, pk=task_pk, is_active=True, task_type='quiz')

    # ⚠️ v3.21.5 — ENTITLEMENT FIRST, THEN THE SHARING FLAG. They answer
    # different questions: `pledge_may_see_task` asks whether this quiz is any
    # of his business, `show_analysis_to_pledges` asks whether the chair has
    # shared the breakdown for a quiz that is. v3.21.0 asked only the second,
    # so a draft or somebody else's quiz was readable.
    if not pledge_may_see_task(request.user, task):
        raise Http404
    if not task.show_analysis_to_pledges:
        raise Http404

    context = quiz_analysis_context(task, viewer_is_pledge=True)
    return render(request, 'pledge/quiz_analysis.html', context)
