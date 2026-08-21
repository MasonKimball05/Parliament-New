"""
Education committee dashboard — VPE + education committee chairs only.

Provides:
  - GET  committee/<code>/education/                                      → pledge task grid + page access settings
  - POST committee/<code>/education/tasks/add/                            → create a new PledgeTask
  - POST committee/<code>/education/tasks/<task_pk>/toggle/<pledge_pk>/   → mark completion (cycles status)
  - POST committee/<code>/education/tasks/<task_pk>/delete/               → soft-delete task (chair only)
  - POST committee/<code>/education/restrictions/update/                  → create/update PledgePageRestriction
  - POST committee/<code>/education/restrictions/<restriction_pk>/delete/ → remove PledgePageRestriction
"""
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, Http404
from django.shortcuts import get_object_or_404, render, redirect
from django.views.decorators.http import require_POST
from django.db import transaction
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.db.models import Count, Q

from src.feature_flag_decorators import require_page_enabled
from src.models import (  # noqa: F401 PledgeQuizAnswer used in the quiz submissions view
    Committee, ParliamentUser, PledgeTask, PledgeTaskCompletion,
    PledgePageRestriction, PledgeTaskQuestion, PledgeQuizAnswer,
    Event, EducationMeeting, EducationMeetingAttendance, EducationAbsenceRequest,
)
from src.models.users import member_defer, member_prefetch


def _parse_non_negative_int(value, default=0):
    """Parse a POST field as a non-negative integer, returning default on bad input."""
    try:
        return max(0, int(value or default))
    except (ValueError, TypeError):
        return default


def _parse_optional_positive_int(value):
    """
    Parse an optional positive integer field, returning None for blank/invalid.

    ⚠️ Distinct from `_parse_non_negative_int` on purpose. That helper folds a
    blank field to 0, which is right for `points` (a task worth nothing) and
    wrong for `max_score`, where 0 would mean "scored out of zero" — a task
    that reports 0/0 on every pledge's page — instead of "not scored".
    """
    raw = (value or '').strip()
    if not raw:
        return None
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _education_committee_or_404(code, user):
    """Return (committee, is_chair). Raises Http404 if the user lacks access."""
    committee = get_object_or_404(Committee, code=code, is_active=True, is_education_committee=True)
    is_chair = committee.chairs.filter(pk=user.pk).exists()
    is_officer = getattr(user, 'is_officer', False)
    if not (is_chair or is_officer):
        raise Http404
    return committee, is_chair


@login_required
@require_page_enabled('committee_home')
def education_home(request, code):
    committee, is_chair = _education_committee_or_404(code, request.user)

    phases = ['all', '1', '2', '3']
    phase_labels = {'all': 'All Phases', '1': 'Phase 1', '2': 'Phase 2', '3': 'Phase 3'}

    tasks = (PledgeTask.objects.filter(is_active=True)
             # v3.17.3: created_by joined but never rendered by education.html
             .prefetch_related(member_prefetch('assigned_to'))
             .order_by('display_order', 'due_date', 'title'))
    pledges = ParliamentUser.objects.filter(member_type='Pledge', is_active=True).order_by('name')

    # Build completion map: {(task_pk, pledge_pk): PledgeTaskCompletion}
    completions = PledgeTaskCompletion.objects.filter(
        task__in=tasks,
        pledge__in=pledges,
    ).select_related('reviewed_by').defer(*member_defer('reviewed_by'))
    completion_map = {(c.task_id, c.pledge_id): c for c in completions}

    # Per-task: list of (pledge, completion_or_None, applies)
    # assigned_pks empty → applies to all; otherwise only those pledges
    task_rows = []
    for task in tasks:
        assigned_pks = {u.pk for u in task.assigned_to.all()}
        pledge_completions = []
        for pledge in pledges:
            applies = not assigned_pks or pledge.pk in assigned_pks
            comp = completion_map.get((task.pk, pledge.pk)) if applies else None
            pledge_completions.append({'pledge': pledge, 'completion': comp, 'applies': applies})
        task_rows.append({'task': task, 'pledge_completions': pledge_completions, 'assigned_pks': assigned_pks})

    # Per-pledge summary: required tasks that apply to this pledge.
    # task_rows already computed assigned_pks per task using the prefetch cache;
    # reuse those sets here rather than calling t.assigned_to.all() twice per task.
    task_assigned_pks = {row['task'].pk: row['assigned_pks'] for row in task_rows}
    pledge_summaries = []
    for pledge in pledges:
        applicable_required = [
            t for t in tasks
            if t.is_required and (not task_assigned_pks.get(t.pk) or pledge.pk in task_assigned_pks[t.pk])
        ]
        completed = sum(
            1 for t in applicable_required
            if completion_map.get((t.pk, pledge.pk)) and
               completion_map[(t.pk, pledge.pk)].status == 'completed'
        )
        pledge_summaries.append({
            'pledge': pledge,
            'completed': completed,
            'total': len(applicable_required),
            'percent': round(completed / len(applicable_required) * 100) if applicable_required else 0,
        })

    # Page access restrictions (VPE settings panel)
    page_restrictions = PledgePageRestriction.objects.all().order_by('display_name', 'url_name')

    # Quiz questions keyed by task_pk (only for quiz-type tasks)
    quiz_task_pks = [t.pk for t in tasks if t.task_type == 'quiz']
    quiz_questions_map = {}
    if quiz_task_pks:
        for q in PledgeTaskQuestion.objects.filter(task_id__in=quiz_task_pks).order_by('display_order'):
            quiz_questions_map.setdefault(q.task_id, []).append(q)

    # Meetings (v3.20.0). Upcoming first, then the most recent past ones —
    # a chair opening this page is either planning the next meeting or taking
    # attendance for the one that just happened.
    now = timezone.now()
    meetings_qs = (
        EducationMeeting.objects
        .filter(committee=committee)
        .select_related('event')
        .prefetch_related('homework')
        # One grouped count instead of a query per meeting for the roll-up.
        #
        # ⚠️ `pending` IS EXCLUDED, AND WITHOUT THAT THE NUMBER IS A LIE. The
        # attendance form pre-selects `pending` for every unmarked pledge, so
        # saving it once writes a row for the WHOLE roster — a plain
        # `Count('attendance_records')` would then report "12 pledges marked"
        # for a meeting where a chair opened the form, saved, and marked nobody.
        # A roll-up that cannot distinguish "everyone recorded" from "form
        # touched once" is worse than no roll-up.
        .annotate(marked_count=Count(
            'attendance_records',
            filter=~Q(attendance_records__status='pending'),
        ))
    )
    upcoming_meetings = [m for m in meetings_qs if m.event.date_time >= now]
    upcoming_meetings.sort(key=lambda m: m.event.date_time)
    past_meetings = [m for m in meetings_qs if m.event.date_time < now][:10]

    # Absence requests awaiting a decision (v3.21.0). Pending only: a decided
    # one is history and belongs on the meeting, not in the chair's queue.
    pending_absences = list(
        EducationAbsenceRequest.objects
        .filter(meeting__committee=committee, status='pending')
        .select_related('pledge', 'meeting', 'meeting__event')
        .order_by('meeting__event__date_time')
    )

    context = {
        'committee': committee,
        'tasks': tasks,
        'task_rows': task_rows,
        'pending_absences': pending_absences,
        'upcoming_meetings': upcoming_meetings,
        'past_meetings': past_meetings,
        'MEETING_TYPES': EducationMeeting.MEETING_TYPES,
        'pledges': pledges,
        'pledge_summaries': pledge_summaries,
        'phases': phases,
        'phase_labels': phase_labels,
        'is_chair': is_chair,
        'TASK_TYPES': PledgeTask.TASK_TYPES,
        'PHASE_CHOICES': PledgeTask.PHASE_CHOICES,
        'page_restrictions': page_restrictions,
        'PHASE_CHOICES_SIMPLE': [('all', 'All'), ('1', 'Ph.1'), ('2', 'Ph.2'), ('3', 'Ph.3')],
        'all_pledges': pledges,
        'quiz_questions_map': quiz_questions_map,
    }
    return render(request, 'committee/education.html', context)


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_add_task(request, code):
    committee, _ = _education_committee_or_404(code, request.user)

    title = request.POST.get('title', '').strip()
    if not title:
        return JsonResponse({'error': 'Title is required'}, status=400)

    _valid_activation_modes = {c[0] for c in PledgeTask.ACTIVATION_MODES}
    activation_mode = request.POST.get('activation_mode', 'immediate')
    if activation_mode not in _valid_activation_modes:
        activation_mode = 'immediate'
    activates_at_raw = request.POST.get('activates_at', '').strip()
    activates_at = None
    if activation_mode == 'timed' and activates_at_raw:
        from django.utils.dateparse import parse_datetime
        import pytz
        parsed = parse_datetime(activates_at_raw)
        if parsed and parsed.tzinfo is None:
            from django.utils import timezone as tz
            parsed = tz.make_aware(parsed)
        activates_at = parsed

    # is_published: True only for immediate mode (timed uses activates_at; manual stays False)
    is_published = activation_mode == 'immediate'

    # Validate choice fields against the model's defined choices
    _valid_task_types = {c[0] for c in PledgeTask.TASK_TYPES}
    _valid_phases = {c[0] for c in PledgeTask.PHASE_CHOICES}
    task_type = request.POST.get('task_type', 'task')
    if task_type not in _valid_task_types:
        task_type = 'task'
    phase = request.POST.get('phase', 'all')
    if phase not in _valid_phases:
        phase = 'all'

    task = PledgeTask.objects.create(
        title=title,
        description=request.POST.get('description', '').strip(),
        task_type=task_type,
        phase=phase,
        is_required=request.POST.get('is_required') == 'on',
        points=_parse_non_negative_int(request.POST.get('points'), 0),
        # Blank = not scored. `_parse_non_negative_int` would turn '' into 0,
        # and a task scored out of 0 is not the same thing as an unscored one.
        max_score=_parse_optional_positive_int(request.POST.get('max_score')),
        show_analysis_to_pledges=request.POST.get('show_analysis_to_pledges') == 'on',
        display_order=_parse_non_negative_int(request.POST.get('display_order'), 0),
        activation_mode=activation_mode,
        activates_at=activates_at,
        is_published=is_published,
        created_by=request.user,
    )
    # Specific pledge assignment (empty = all pledges)
    assigned_pks = request.POST.getlist('assigned_to')
    if assigned_pks:
        task.assigned_to.set(
            ParliamentUser.objects.filter(pk__in=assigned_pks, member_type='Pledge')
        )
    return redirect('education_home', code=code)


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_toggle_completion(request, code, task_pk, pledge_pk):
    """
    Record a pledge's completion of a task.

    Two modes, and the distinction matters:

    * **No `set_status` in the POST** — cycle
      `pending → completed → incomplete → pending`. This is the grid on the
      education dashboard, where one click per cell is the whole point.
    * **`set_status=<status>`** — set that status explicitly.

    ⚠️ v3.20.0 — THE SECOND MODE IS NEW AND IT WAS A LIVE BUG.
    `quiz_submissions.html` has posted `<input type="hidden" name="set_status">`
    since it was written, with buttons labelled *Mark completed* and *Mark
    incomplete*. This view never read the field. So on the grading page both
    buttons did the same thing — from `pending`, *Mark incomplete* marked the
    pledge **completed** — and the only way to reach the status you wanted was
    to click until it came round. A grader marking a failed quiz would have
    passed him.

    `score` is optional and independent of status: scoring is informational and
    a chair still decides pass/fail. See `PledgeTaskCompletion.score_display`.
    """
    committee, _ = _education_committee_or_404(code, request.user)

    task = get_object_or_404(PledgeTask, pk=task_pk, is_active=True)
    pledge = get_object_or_404(ParliamentUser, pk=pledge_pk, member_type='Pledge')

    comp, _ = PledgeTaskCompletion.objects.get_or_create(
        task=task,
        pledge=pledge,
        defaults={'status': 'pending'},
    )

    _valid_statuses = {c[0] for c in PledgeTaskCompletion.STATUS_CHOICES}
    requested = (request.POST.get('set_status') or '').strip()
    if requested:
        if requested not in _valid_statuses:
            return JsonResponse({'error': 'Unknown status'}, status=400)
        new_status = requested
    else:
        # Cycle: pending → completed → incomplete → pending
        cycle = {'pending': 'completed', 'completed': 'incomplete', 'incomplete': 'pending', 'waived': 'pending'}
        new_status = cycle.get(comp.status, 'completed')

    updated = ['status', 'reviewed_by', 'completed_at', 'updated_at']

    # ── Score ────────────────────────────────────────────────────────────
    # Absent key = leave the existing score alone (the dashboard grid posts no
    # score at all, and a click there must not silently wipe a mark). Present
    # but empty = clear it, which is how a grader undoes a typo.
    if 'score' in request.POST:
        raw = (request.POST.get('score') or '').strip()
        if raw == '':
            comp.score = None
        else:
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return JsonResponse({'error': 'Score must be a whole number'}, status=400)
            if value < 0:
                return JsonResponse({'error': 'Score cannot be negative'}, status=400)
            if not task.max_score:
                return JsonResponse(
                    {'error': 'This task has no maximum score. Set one on the task first.'},
                    status=400,
                )
            if value > task.max_score:
                # Rejected rather than clamped or allowed as extra credit: with
                # scoring informational, a wrong number is cheap to correct and
                # a silent typo is not. If extra credit is ever wanted, raise
                # the task's max_score — that is the honest way to say it.
                return JsonResponse(
                    {'error': f'Score cannot exceed the maximum of {task.max_score}'},
                    status=400,
                )
            comp.score = value
        updated.append('score')

    comp.status = new_status
    comp.reviewed_by = request.user
    comp.completed_at = timezone.now() if new_status == 'completed' else None
    comp.save(update_fields=updated)

    if request.headers.get('X-Requested-With') != 'XMLHttpRequest' and requested:
        # The grading page posts a normal form, so send it back where it was.
        #
        # ⚠️ The Referer is attacker-influenced, so it is validated against this
        # host before being used as a redirect target — an unchecked
        # `redirect(request.META['HTTP_REFERER'])` is an open redirect.
        referer = request.META.get('HTTP_REFERER') or ''
        if referer and url_has_allowed_host_and_scheme(
            referer, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(referer)
        return redirect('education_quiz_submissions', code=code, task_pk=task.pk)

    return JsonResponse({
        'status': new_status,
        'task_pk': task_pk,
        'pledge_pk': pledge_pk,
        'score': comp.score,
        'score_display': comp.score_display,
    })


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_update_page_restriction(request, code):
    """
    Create or update a PledgePageRestriction entry.
    Body: url_name, display_name, phases[] (checkboxes: 'all', '1', '2', '3')
    Chair or officer only.
    """
    committee, _ = _education_committee_or_404(code, request.user)

    url_name = request.POST.get('url_name', '').strip()
    if not url_name:
        return JsonResponse({'error': 'url_name required'}, status=400)

    _valid_phases = {'all', '1', '2', '3'}
    phases = [p for p in request.POST.getlist('phases') if p in _valid_phases]
    display_name = request.POST.get('display_name', '').strip()

    restriction, _ = PledgePageRestriction.objects.get_or_create(url_name=url_name)
    restriction.display_name = display_name or restriction.display_name
    restriction.allowed_phases = phases
    restriction.updated_by = request.user
    restriction.save()
    PledgePageRestriction.invalidate_cache(url_name)

    return JsonResponse({'url_name': url_name, 'allowed_phases': phases})


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_delete_page_restriction(request, code, restriction_pk):
    """Remove a PledgePageRestriction entry (page returns to fully blocked)."""
    _, _ = _education_committee_or_404(code, request.user)
    obj = get_object_or_404(PledgePageRestriction, pk=restriction_pk)
    url_name = obj.url_name
    obj.delete()
    PledgePageRestriction.invalidate_cache(url_name)
    return JsonResponse({'deleted': True})


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_toggle_task_published(request, code, task_pk):
    """Manually publish or unpublish a task (manual activation mode only). Chair-only."""
    committee, is_chair = _education_committee_or_404(code, request.user)
    if not is_chair and not request.user.is_admin:
        return JsonResponse({'error': 'Only education chairs can publish tasks.'}, status=403)
    task = get_object_or_404(PledgeTask, pk=task_pk, is_active=True)
    task.is_published = not task.is_published
    task.save(update_fields=['is_published', 'updated_at'])
    return JsonResponse({'is_published': task.is_published, 'task_pk': task_pk})


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_delete_task(request, code, task_pk):
    """Soft-delete (deactivate) a pledge task. Chair or officer only."""
    committee, _ = _education_committee_or_404(code, request.user)
    task = get_object_or_404(PledgeTask, pk=task_pk)
    task.is_active = False
    task.save(update_fields=['is_active', 'updated_at'])
    return JsonResponse({'deleted': True, 'task_pk': task_pk})


# ── Quiz question management ──────────────────────────────────────────────────

@login_required
@require_page_enabled('committee_home')
@require_POST
def education_add_quiz_question(request, code, task_pk):
    """
    Add a question to a quiz-type PledgeTask.
    POST body: question_text, answer_hint (optional), display_order (optional)
    """
    committee, _ = _education_committee_or_404(code, request.user)
    task = get_object_or_404(PledgeTask, pk=task_pk, is_active=True, task_type='quiz')

    question_text = request.POST.get('question_text', '').strip()
    if not question_text:
        return JsonResponse({'error': 'Question text is required.'}, status=400)

    answer_hint = request.POST.get('answer_hint', '').strip()
    display_order = _parse_non_negative_int(request.POST.get('display_order'), 0)

    question = PledgeTaskQuestion.objects.create(
        task=task,
        question_text=question_text,
        answer_hint=answer_hint,
        display_order=display_order,
    )
    return JsonResponse({
        'question_id': question.pk,
        'question_text': question.question_text,
        'display_order': question.display_order,
    })


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_delete_quiz_question(request, code, task_pk, question_pk):
    """Delete a quiz question (and all existing pledge answers for it)."""
    committee, _ = _education_committee_or_404(code, request.user)
    task = get_object_or_404(PledgeTask, pk=task_pk, is_active=True, task_type='quiz')
    question = get_object_or_404(PledgeTaskQuestion, pk=question_pk, task=task)
    question.delete()
    return JsonResponse({'deleted': True, 'question_pk': question_pk})


@login_required
@require_page_enabled('committee_home')
def education_quiz_submissions(request, code, task_pk):
    """
    Chair/officer view of all pledge submissions for a quiz task.
    Shows each pledge's answers alongside the model answer hints.
    """
    committee, is_chair = _education_committee_or_404(code, request.user)
    task = get_object_or_404(PledgeTask, pk=task_pk, is_active=True, task_type='quiz')

    questions = list(task.questions.all())
    pledges = ParliamentUser.objects.filter(member_type='Pledge', is_active=True).order_by('name')

    # Fetch all answers in one query, keyed by (pledge_pk, question_pk)
    answers = PledgeQuizAnswer.objects.filter(
        question__task=task,
    ).select_related('pledge').defer(*member_defer('pledge'))
    # v3.21.0 — the whole answer, not just its text: the grading page now marks
    # each one right or wrong, which needs its pk and current verdict.
    answer_map = {(a.pledge_id, a.question_id): a for a in answers}

    # Fetch completions.
    #
    # ⚠️ `select_related('task')` is load-bearing as of v3.20.0, not tidiness.
    # The template renders `row.completion.score_display`, and that property
    # reads `self.task.max_score` — so without the join it is one query per
    # graded pledge. Measured on a 12-pledge fixture: **13 × src_pledgetask**,
    # i.e. an N+1 introduced by the scoring feature itself, on the page scoring
    # exists for.
    completions = (PledgeTaskCompletion.objects
                   .filter(task=task, pledge__in=pledges)
                   .select_related('task'))
    completion_map = {c.pledge_id: c for c in completions}

    pledge_rows = []
    for pledge in pledges:
        submitted = any((pledge.pk, q.pk) in answer_map for q in questions)
        # Pre-zip (question, answer) pairs — Django templates cannot zip or
        # do dict lookups by a computed key.
        # Pre-zip (q, answer) pairs — Django templates can't zip/dict-lookup
        qa_pairs = []
        for q in questions:
            answer = answer_map.get((pledge.pk, q.pk))
            qa_pairs.append({
                'question': q,
                'text': answer.answer_text if answer else '',
                'answer_pk': answer.pk if answer else None,
                'is_correct': answer.is_correct if answer else None,
            })
        pledge_rows.append({
            'pledge': pledge,
            'submitted': submitted,
            'completion': completion_map.get(pledge.pk),
            'qa_pairs': qa_pairs,
        })

    return render(request, 'committee/quiz_submissions.html', {
        'committee': committee,
        'task': task,
        'questions': questions,
        'pledge_rows': pledge_rows,
        'is_chair': is_chair,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Education meetings (v3.20.0)
#
# A meeting is an `Event` (the calendar entry, visible to the whole chapter)
# plus an `EducationMeeting` sidecar holding the pledge-education specifics —
# the same shape `RecruitmentEvent` uses. Attendance lives in
# `EducationMeetingAttendance`, which only ever contains pledges; see the note
# on the model for why it is not the chapter-wide `Attendance` table.
# ─────────────────────────────────────────────────────────────────────────────

def _pledge_roster():
    """The only population that can have education attendance."""
    return ParliamentUser.objects.filter(
        member_type='Pledge', is_active=True
    ).order_by('name')


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_add_meeting(request, code):
    """Create an education meeting and its calendar event."""
    committee, _ = _education_committee_or_404(code, request.user)

    # ⚠️ `visible_to=None` means "everyone", which is what Mason asked for:
    # brothers should see when the pledge class meets. Only ATTENDANCE is
    # pledge-only, and that is enforced by the attendance table, not by
    # hiding the event.
    event = Event(created_by=request.user, visible_to=None)
    meeting = EducationMeeting(committee=committee, created_by=request.user)

    # Shared with the edit view — see `_apply_meeting_fields`.
    error = _apply_meeting_fields(request, meeting, event)
    if error:
        return JsonResponse({'error': error}, status=400)

    # ⚠️ v3.21.5 — ATOMIC, because a meeting is two rows and a half-written one
    # is worse than none. `EducationMeeting.event` is a OneToOne, so the Event
    # has to be saved first; if the second save raised, the Event survived as a
    # **pledge-education entry on the chapter calendar with nothing behind it** —
    # no attendance page, no delete button on the education dashboard, and
    # `education_delete_meeting` deletes through the meeting, so nothing in the
    # UI could remove it. That is the same reasoning v3.19.3 used to make
    # `publish_legislation_draft` atomic.
    with transaction.atomic():
        event.save()
        meeting.event = event
        meeting.save()

        homework_pks = request.POST.getlist('homework')
        if homework_pks:
            meeting.homework.set(PledgeTask.objects.filter(pk__in=homework_pks, is_active=True))

    return redirect('education_home', code=code)


def _apply_meeting_fields(request, meeting, event):
    """
    Read the meeting form into `event` and `meeting`. Neither is saved here.

    ⚠️ SHARED BY CREATE AND EDIT ON PURPOSE (v3.20.1). Two copies of this
    parsing would drift the first time a field was added to one form, and the
    symptom would be a field that silently does nothing on the other — which is
    the failure mode this codebase has recorded nine times under a different
    name. Returns an error string, or None.
    """
    from django.utils.dateparse import parse_datetime

    title = (request.POST.get('title') or '').strip()
    if not title:
        return 'Title is required'

    raw_when = (request.POST.get('date_time') or '').strip()
    when = parse_datetime(raw_when) if raw_when else None
    if when is None:
        return 'A valid date and time is required'
    if timezone.is_naive(when):
        when = timezone.make_aware(when)

    _valid_types = {c[0] for c in EducationMeeting.MEETING_TYPES}
    meeting_type = request.POST.get('meeting_type', 'meeting')
    if meeting_type not in _valid_types:
        meeting_type = 'meeting'

    event.title = title
    event.description = (request.POST.get('description') or '').strip()
    event.date_time = when
    event.location = (request.POST.get('location') or '').strip()

    meeting.meeting_type = meeting_type
    meeting.attendance_required = request.POST.get('attendance_required') == 'on'
    meeting.points = _parse_non_negative_int(request.POST.get('points'), 0)
    meeting.notes = (request.POST.get('notes') or '').strip()
    return None


@login_required
@require_page_enabled('committee_home')
def education_edit_meeting(request, code, meeting_pk):
    """
    Edit a meeting.

    ⚠️ WHY THIS EXISTS. v3.20.0 shipped create and delete and no edit, which
    meant the only way to correct a mistyped time was to delete the meeting and
    make a new one — **and deleting a meeting cascades to its attendance**. A
    typo therefore destroyed the record of who turned up. That is a data-loss
    trap behind the most ordinary mistake a chair can make.

    Editing deliberately does NOT touch attendance: the meeting keeps its pk, so
    every `EducationMeetingAttendance` row survives a change of time, place or
    points. `test_attendance_survives_an_edit` is the point of the whole change.
    """
    committee, is_chair = _education_committee_or_404(code, request.user)
    meeting = get_object_or_404(
        EducationMeeting.objects.select_related('event'),
        pk=meeting_pk, committee=committee,
    )

    if request.method == 'POST':
        error = _apply_meeting_fields(request, meeting, meeting.event)
        if error:
            return JsonResponse({'error': error}, status=400)

        # Atomic for the same reason as the create path, one degree milder: a
        # failure between these two saves leaves the calendar entry showing a
        # new time and the education dashboard showing the old one.
        with transaction.atomic():
            meeting.event.save()
            meeting.save()
            # `set()` handles all three cases — added, removed, cleared — so an
            # unticked box actually unassigns rather than silently persisting.
            meeting.homework.set(
                PledgeTask.objects.filter(pk__in=request.POST.getlist('homework'), is_active=True)
            )
        return redirect('education_home', code=code)

    return render(request, 'committee/education_meeting_form.html', {
        'committee': committee,
        'meeting': meeting,
        'is_chair': is_chair,
        'tasks': PledgeTask.objects.filter(is_active=True).order_by('display_order', 'title'),
        'assigned_homework_pks': set(meeting.homework.values_list('pk', flat=True)),
        'MEETING_TYPES': EducationMeeting.MEETING_TYPES,
    })


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_delete_meeting(request, code, meeting_pk):
    """Delete a meeting and its calendar entry. Chair only."""
    committee, is_chair = _education_committee_or_404(code, request.user)
    if not is_chair:
        return JsonResponse({'error': 'Chair access required'}, status=403)

    meeting = get_object_or_404(EducationMeeting, pk=meeting_pk, committee=committee)
    # The Event owns the calendar entry and the meeting is a OneToOne on it, so
    # deleting the event cascades to the meeting and its attendance. Deleting
    # the meeting alone would strand a pledge-education event on the calendar
    # with nothing behind it.
    meeting.event.delete()
    return redirect('education_home', code=code)


@login_required
@require_page_enabled('committee_home')
def education_meeting_attendance(request, code, meeting_pk):
    """Take attendance for a meeting. Pledges only, by construction."""
    committee, is_chair = _education_committee_or_404(code, request.user)
    meeting = get_object_or_404(
        EducationMeeting.objects.select_related('event'),
        pk=meeting_pk, committee=committee,
    )

    pledges = list(_pledge_roster())

    if request.method == 'POST':
        _valid = {c[0] for c in EducationMeetingAttendance.STATUS_CHOICES}
        # ⚠️ Keyed by STRING pk. `ParliamentUser.user_id` is a CharField primary
        # key (`models/users.py:139`), so coercing the form key with `int()`
        # both throws away non-numeric ids and silently drops every row — the
        # first draft of this view did exactly that and recorded nothing.
        pledge_by_pk = {str(p.pk): p for p in pledges}
        now = timezone.now()
        for key, value in request.POST.items():
            if not key.startswith('status_') or value not in _valid:
                continue
            pledge_pk = key[len('status_'):]
            # ⚠️ Only pks from the pledge roster are accepted. Without this a
            # crafted POST could write an attendance row for a brother, which
            # is exactly the property this table exists to guarantee.
            if pledge_pk not in pledge_by_pk:
                continue
            EducationMeetingAttendance.objects.update_or_create(
                meeting=meeting,
                pledge=pledge_by_pk[pledge_pk],
                defaults={
                    'status': value,
                    'marked_by': request.user,
                    'marked_at': now,
                },
            )
        return redirect('education_meeting_attendance', code=code, meeting_pk=meeting.pk)

    existing = {
        record.pledge_id: record
        for record in EducationMeetingAttendance.objects.filter(meeting=meeting)
    }
    rows = [{'pledge': pledge, 'record': existing.get(pledge.pk)} for pledge in pledges]

    return render(request, 'committee/education_meeting_attendance.html', {
        'committee': committee,
        'meeting': meeting,
        'rows': rows,
        'is_chair': is_chair,
        'STATUS_CHOICES': EducationMeetingAttendance.STATUS_CHOICES,
    })


@login_required
@require_page_enabled('committee_home')
def education_pledge_detail(request, code, pledge_pk):
    """
    Everything about one pledge, on one page (v3.20.2).

    ⚠️ WHY THIS EXISTS. The dashboard is a task × pledge grid: excellent for
    "who has not done task 4", useless for "how is Jack doing". Before a
    progress conversation — or an initiation vote — a VPE wants one page: his
    tasks and marks, what is overdue, which meetings he came to and which he
    missed, and the points that follow from both. Answering that meant reading
    across a grid and doing arithmetic in your head.

    Read-only on purpose. Marking still happens on the grid and the attendance
    page, so there is one place to change each thing and this page cannot
    disagree with them.
    """
    committee, is_chair = _education_committee_or_404(code, request.user)
    pledge = get_object_or_404(ParliamentUser, pk=pledge_pk, member_type='Pledge')

    tasks = list(
        PledgeTask.objects.filter(is_active=True)
        .prefetch_related(member_prefetch('assigned_to'))
        .order_by('display_order', 'due_date', 'title')
    )
    completions = {
        c.task_id: c
        for c in PledgeTaskCompletion.objects
        .filter(task__in=tasks, pledge=pledge)
        # `score_display` reads `completion.task` — see the v3.20.0 N+1.
        .select_related('task')
    }

    task_rows = []
    for task in tasks:
        assigned_pks = {u.pk for u in task.assigned_to.all()}
        # An empty assignment means "all pledges"; otherwise this task simply
        # does not apply to him and showing it would misreport his progress.
        if assigned_pks and pledge.pk not in assigned_pks:
            continue
        completion = completions.get(task.pk)
        task_rows.append({
            'task': task,
            'completion': completion,
            'overdue': task.is_overdue_for(completion),
        })

    required = [row for row in task_rows if row['task'].is_required]
    required_done = [
        row for row in required
        if row['completion'] and row['completion'].status == 'completed'
    ]
    overdue_rows = [row for row in task_rows if row['overdue']]

    # Attendance, most recent first — "which did he miss" is the question, and
    # the answer is usually about the last few weeks.
    attendance = list(
        EducationMeetingAttendance.objects
        .filter(pledge=pledge, meeting__committee=committee)
        .select_related('meeting', 'meeting__event')
        .order_by('-meeting__event__date_time')
    )
    attendance_points = sum(record.points_earned for record in attendance)
    attended = [
        r for r in attendance
        if r.status in EducationMeetingAttendance.EARNS_POINTS
    ]
    missed = [r for r in attendance if r.status == 'absent']

    task_points = sum(
        row['task'].points for row in task_rows
        if row['completion'] and row['completion'].status == 'completed'
    )

    return render(request, 'committee/education_pledge_detail.html', {
        'committee': committee,
        'is_chair': is_chair,
        'pledge': pledge,
        'task_rows': task_rows,
        'required_total': len(required),
        'required_done': len(required_done),
        'required_percent': (
            round(len(required_done) / len(required) * 100) if required else 0
        ),
        'overdue_rows': overdue_rows,
        'attendance': attendance,
        'attended_count': len(attended),
        'missed_count': len(missed),
        'attendance_points': attendance_points,
        'task_points': task_points,
        'total_points': task_points + attendance_points,
    })


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_duplicate_task(request, code, task_pk):
    """
    Clone a task (v3.21.0 — ideas list #8).

    Every pledge class needs roughly the same twenty tasks and they were typed in
    by hand each semester. Cloning is the honest primitive: tasks are not
    semester-scoped, so "copy last term" is really "make me another one of
    these", and building a set out of clones is a few clicks rather than an hour.

    ⚠️ THE CLONE IS ALWAYS A DRAFT, whatever the original was. A duplicate that
    went live the instant it was made would publish a half-edited task —
    probably still called "… (copy)" and still carrying last term's due date —
    to every pledge's page. `manual` + `is_published=False` means it appears on
    the chair's grid marked *Draft* and nowhere else until he says so.

    Deliberately NOT copied: `due_date` (last term's date is wrong by
    definition and a silently stale one is worse than none), completions,
    and quiz questions — see below.
    """
    committee, _ = _education_committee_or_404(code, request.user)
    original = get_object_or_404(PledgeTask, pk=task_pk, is_active=True)

    clone = PledgeTask.objects.create(
        title=f'{original.title} (copy)',
        description=original.description,
        task_type=original.task_type,
        phase=original.phase,
        is_required=original.is_required,
        points=original.points,
        max_score=original.max_score,
        show_analysis_to_pledges=original.show_analysis_to_pledges,
        display_order=original.display_order,
        due_date=None,
        activation_mode='manual',
        is_published=False,
        created_by=request.user,
    )
    clone.assigned_to.set(original.assigned_to.all())

    # Quiz questions come with it — a quiz without its questions is not a copy
    # of that quiz, and `pledge_take_quiz` refuses to render one with none.
    # Answers do not: they belong to the sitting, not to the paper.
    for question in original.questions.all():
        PledgeTaskQuestion.objects.create(
            task=clone,
            question_text=question.question_text,
            answer_hint=question.answer_hint,
            display_order=question.display_order,
        )

    return JsonResponse({
        'duplicated': True,
        'task_pk': clone.pk,
        'title': clone.title,
    })


@login_required
@require_page_enabled('committee_home')
def education_quiz_analysis(request, code, task_pk):
    """
    Which questions did the class get wrong (v3.21.0 — ideas list #9).

    ⚠️ THIS FEATURE DID NOT EXIST AS DESCRIBED AND COULD NOT. When it was
    proposed, `PledgeQuizAnswer` stored free text and nothing else — there was
    no record of whether any answer was right, so "which question did everyone
    miss" was not a question the data could answer. v3.21.0 adds
    `PledgeQuizAnswer.is_correct` and per-question marking on the grading page;
    this view is only as good as the marking a chair has actually done, which is
    why every row reports how many answers are still unmarked.

    Audience: educators. A pledge may see it only when the chair has ticked
    `show_analysis_to_pledges` on that quiz — see `pledge_quiz_analysis`.
    """
    committee, is_chair = _education_committee_or_404(code, request.user)
    task = get_object_or_404(PledgeTask, pk=task_pk, is_active=True, task_type='quiz')
    return render(request, 'committee/education_quiz_analysis.html',
                  quiz_analysis_context(task, committee=committee, is_chair=is_chair))


#: A pledge sees class totals only once this many pledges have submitted.
#:
#: ⚠️ v3.21.5 — WITHOUT THIS, "CLASS TOTALS" WERE ONE PERSON'S RESULT.
#: v3.21.0 shipped the pledge-facing breakdown under a header reading *"These
#: are class totals. Nobody's individual answers are shown here."* With a single
#: submission the page showed Submissions 1, Lowest 4, Highest 4, Average 4 and
#: "1 answer · 0 right · 1 wrong" per question — i.e. that pledge's exact score
#: and his exact right/wrong pattern, to every other pledge, including ones who
#: had submitted nothing. Reproduced 08-20-26.
#:
#: The first person to submit is the one exposed, and early in a quiz's life
#: that is the normal state rather than an edge case.
#:
#: The number matches `announcement_polls`' `respondent_count > 2`, which was
#: added for the identical reason ("prevents identifying early respondents by
#: elimination") — a pledge class is small and everyone in it knows who else is
#: in it, so aggregate-of-one and aggregate-of-two are not aggregates.
#: **Educators are unaffected**: they are entitled to individual results and
#: reach them through the grading page anyway, so suppressing the summary for
#: them would remove information without protecting anybody.
#:
#: ⚠️ RESIDUAL, KNOWN AND ACCEPTED. The gate counts SUBMISSIONS — people who
#: took the quiz — not answers to each question, and a question that only one of
#: three submitters answered still renders "1 answer · 0 right · 1 wrong". That
#: narrows the field to one of three rather than naming anybody, which is the
#: ordinary weakness of a small-N aggregate and not the defect being fixed here;
#: the defect was a page presenting ONE person's complete result as "the class".
#: Per-question suppression would blank most of the page and remove the thing it
#: exists to show. If this is ever revisited, revisit it as a decision about
#: what a class total means, not as a bug.
PLEDGE_ANALYSIS_MIN_SUBMISSIONS = 3


def quiz_analysis_context(task, committee=None, is_chair=False, viewer_is_pledge=False):
    """
    Shared by the educator view and the pledge-facing one.

    ⚠️ ONE BUILDER, TWO AUDIENCES. The numbers must agree — a pledge told "8 of
    12 got this right" and a chair told something else is worse than showing him
    nothing. The audience makes exactly two differences, and both are made here
    rather than in a template: a pledge never sees who answered what, and a
    pledge sees nothing at all until the class is large enough that "the class"
    is not one identifiable person (`PLEDGE_ANALYSIS_MIN_SUBMISSIONS`).

    ⚠️ THE SUPPRESSION IS IN THE CONTEXT, NOT THE TEMPLATE, ON PURPOSE. A
    template `{% if %}` protects the page it is written on; the value has to be
    absent from the context so that a second template, or a future JSON
    endpoint, cannot render what this one hides. CLAUDE.md records that exact
    correction under the admin-confidentiality boundary: redact in the queryset,
    not only in the view.
    """
    from django.db.models import Count, Q as _Q

    questions = list(
        task.questions
        .annotate(
            answered=Count('answers'),
            correct=Count('answers', filter=_Q(answers__is_correct=True)),
            wrong=Count('answers', filter=_Q(answers__is_correct=False)),
        )
        .order_by('display_order')
    )

    rows = []
    for question in questions:
        marked = question.correct + question.wrong
        rows.append({
            'question': question,
            'answered': question.answered,
            'correct': question.correct,
            'wrong': question.wrong,
            'unmarked': question.answered - marked,
            # None, not 0, when nothing is marked — "0% correct" and "nobody has
            # marked this yet" are opposite messages and must not share a
            # rendering.
            'percent': round(question.correct / marked * 100) if marked else None,
            # v3.21.7 — set True below when this question's own answer count is
            # under the anonymity minimum. Defaulted here so no template has to
            # cope with the key being absent, and so an educator's rows say
            # "nothing is hidden" explicitly rather than by omission.
            'suppressed': False,
        })

    # Weakest first: the page exists to answer "what do I re-teach", so the
    # thing to re-teach should not be at the bottom. Unmarked rows sort last —
    # they are not weak, they are unknown.
    rows.sort(key=lambda r: (r['percent'] is None, r['percent'] if r['percent'] is not None else 0))

    completions = PledgeTaskCompletion.objects.filter(task=task).select_related('task')
    scored = [c for c in completions if c.has_score]
    scores = sorted(c.score for c in scored)

    # ⚠️ v3.21.7 — COUNT THE PEOPLE WHO ANSWERED, NOT THE ROWS THAT EXIST.
    #
    # This was `completions.count()`, and a `PledgeTaskCompletion` is not a
    # submission: `education_toggle_completion` does `get_or_create`, so a chair
    # marking somebody waived or incomplete on the grid mints a row with no
    # answers behind it. Measured on the shipped tree — one pledge sits the
    # quiz, the chair marks two others, and the page reports
    #
    #     submissions = 3 | withheld = False
    #     Q1  answered=1  correct=1  wrong=0  100%
    #     Q2  answered=1  correct=0  wrong=1    0%
    #     score_count = 1   low/high/avg = 4 4 4.0
    #
    # which is the exact disclosure v3.21.5 added the threshold to prevent, one
    # day later, reached by an ordinary chair action rather than an attack.
    #
    # > **A threshold protects the population it counts.** The number gating the
    # > page came from `PledgeTaskCompletion`; every number ON the page is drawn
    # > from `PledgeQuizAnswer` or from `score`. Three populations, and the gate
    # > was on the one that is easiest to inflate and hardest to look at.
    #
    # So each statistic is now gated by its own population — see below — and
    # this one is the population the page is actually about.
    submissions = (
        PledgeQuizAnswer.objects
        .filter(question__task=task)
        .values('pledge').distinct().count()
    )

    # See PLEDGE_ANALYSIS_MIN_SUBMISSIONS. `withheld` is True rather than the
    # rows simply being empty, because "nobody has taken this yet" and "not
    # enough people have taken this yet" are different messages and the page has
    # to be able to tell the pledge which one he is looking at.
    withheld = viewer_is_pledge and submissions < PLEDGE_ANALYSIS_MIN_SUBMISSIONS
    if withheld:
        rows = []
        scores = []
    elif viewer_is_pledge:
        # v3.21.7 — per question, and per the score set, for the same reason.
        # Questions are NOT all answered the same number of times: v3.20.0
        # records that a chair may add a question to a quiz people have already
        # sat, and that question then carries only the answers given since. A
        # page-level count of 12 tells you nothing about the question three
        # people have seen.
        for row in rows:
            if row['answered'] < PLEDGE_ANALYSIS_MIN_SUBMISSIONS:
                row['suppressed'] = True
                row['correct'] = row['wrong'] = row['unmarked'] = None
                row['percent'] = None
        # Likewise the score band: `score_low`/`score_high` with one scored
        # completion are one pledge's mark printed twice under two labels.
        if len(scores) < PLEDGE_ANALYSIS_MIN_SUBMISSIONS:
            scores = []

    return {
        'committee': committee,
        'is_chair': is_chair,
        'viewer_is_pledge': viewer_is_pledge,
        'task': task,
        'rows': rows,
        'withheld': withheld,
        'min_submissions': PLEDGE_ANALYSIS_MIN_SUBMISSIONS,
        # The count itself is safe to show and is the thing a pledge needs in
        # order to understand why the page is empty — but it is deliberately
        # the ONLY number that survives, because a total with no breakdown
        # identifies nobody.
        'submissions': submissions,
        'score_count': len(scores),
        'score_average': round(sum(scores) / len(scores), 1) if scores else None,
        'score_low': scores[0] if scores else None,
        'score_high': scores[-1] if scores else None,
    }


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_mark_answer(request, code, task_pk, answer_pk):
    """
    Mark one answer right or wrong (v3.21.0). `verdict` is `correct`,
    `wrong`, or `clear`.
    """
    committee, _ = _education_committee_or_404(code, request.user)
    task = get_object_or_404(PledgeTask, pk=task_pk, is_active=True, task_type='quiz')
    answer = get_object_or_404(PledgeQuizAnswer, pk=answer_pk, question__task=task)

    verdict = (request.POST.get('verdict') or '').strip()
    # `clear` maps to None, which means "not marked" — distinct from wrong.
    mapping = {'correct': True, 'wrong': False, 'clear': None}
    if verdict not in mapping:
        return JsonResponse({'error': 'Unknown verdict'}, status=400)

    answer.is_correct = mapping[verdict]
    answer.save(update_fields=['is_correct'])
    return JsonResponse({'is_correct': answer.is_correct, 'answer_pk': answer.pk})


@login_required
@require_page_enabled('committee_home')
@require_POST
def education_review_absence(request, code, request_pk):
    """
    Approve or deny a pledge's absence request (v3.21.0 — ideas list #7).

    ⚠️ APPROVING WRITES THE ATTENDANCE. An approval that left the roster
    untouched would mean a chair approves an absence and the pledge is still
    marked `absent` (or unmarked) at the next review — which is exactly the
    "I told him and he forgot" failure the request flow exists to remove.
    Denying deliberately writes nothing: the meeting has not happened yet, and
    the pledge may still turn up.
    """
    committee, _ = _education_committee_or_404(code, request.user)
    absence = get_object_or_404(
        EducationAbsenceRequest.objects.select_related('meeting', 'pledge'),
        pk=request_pk, meeting__committee=committee,
    )

    decision = (request.POST.get('decision') or '').strip()
    if decision not in ('approved', 'denied'):
        return JsonResponse({'error': 'Unknown decision'}, status=400)

    absence.status = decision
    absence.reviewed_by = request.user
    absence.reviewed_at = timezone.now()
    absence.review_note = (request.POST.get('review_note') or '').strip()
    absence.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note'])

    if decision == 'approved':
        EducationMeetingAttendance.objects.update_or_create(
            meeting=absence.meeting,
            pledge=absence.pledge,
            defaults={
                'status': 'excused',
                'marked_by': request.user,
                'marked_at': timezone.now(),
            },
        )

    return redirect('education_home', code=code)
