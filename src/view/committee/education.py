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
from django.utils import timezone

from src.feature_flag_decorators import require_page_enabled
from src.models import Committee, ParliamentUser, PledgeTask, PledgeTaskCompletion, PledgePageRestriction, PledgeTaskQuestion, PledgeQuizAnswer  # noqa: F401 PledgeQuizAnswer used in quiz submissions view


def _parse_non_negative_int(value, default=0):
    """Parse a POST field as a non-negative integer, returning default on bad input."""
    try:
        return max(0, int(value or default))
    except (ValueError, TypeError):
        return default


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

    tasks = PledgeTask.objects.filter(is_active=True).select_related('created_by').prefetch_related('assigned_to').order_by('display_order', 'due_date', 'title')
    pledges = ParliamentUser.objects.filter(member_type='Pledge', is_active=True).order_by('name')

    # Build completion map: {(task_pk, pledge_pk): PledgeTaskCompletion}
    completions = PledgeTaskCompletion.objects.filter(
        task__in=tasks,
        pledge__in=pledges,
    ).select_related('reviewed_by')
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

    context = {
        'committee': committee,
        'tasks': tasks,
        'task_rows': task_rows,
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
    """Toggle a pledge's completion status for a task (cycle: pending → completed → incomplete)."""
    committee, _ = _education_committee_or_404(code, request.user)

    task = get_object_or_404(PledgeTask, pk=task_pk, is_active=True)
    pledge = get_object_or_404(ParliamentUser, pk=pledge_pk, member_type='Pledge')

    comp, _ = PledgeTaskCompletion.objects.get_or_create(
        task=task,
        pledge=pledge,
        defaults={'status': 'pending'},
    )

    # Cycle: pending → completed → incomplete → pending
    cycle = {'pending': 'completed', 'completed': 'incomplete', 'incomplete': 'pending', 'waived': 'pending'}
    new_status = cycle.get(comp.status, 'completed')
    comp.status = new_status
    comp.reviewed_by = request.user
    comp.completed_at = timezone.now() if new_status == 'completed' else None
    comp.save(update_fields=['status', 'reviewed_by', 'completed_at', 'updated_at'])

    return JsonResponse({'status': new_status, 'task_pk': task_pk, 'pledge_pk': pledge_pk})


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
    ).select_related('pledge')
    answer_map = {(a.pledge_id, a.question_id): a.answer_text for a in answers}

    # Fetch completions
    completions = PledgeTaskCompletion.objects.filter(task=task, pledge__in=pledges)
    completion_map = {c.pledge_id: c for c in completions}

    pledge_rows = []
    for pledge in pledges:
        submitted = any((pledge.pk, q.pk) in answer_map for q in questions)
        # Pre-zip (q, answer) pairs — Django templates can't zip/dict-lookup
        qa_pairs = [(q, answer_map.get((pledge.pk, q.pk), '')) for q in questions]
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
