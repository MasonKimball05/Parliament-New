"""
Pledge-facing task view — shows the logged-in pledge their own task list
with per-phase grouping and completion status.

Accessible to pledges (not blocked). Non-pledges are redirected to home.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from src.models import PledgeTask, PledgeTaskCompletion, PledgeTaskQuestion, PledgeQuizAnswer


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

    completions = PledgeTaskCompletion.objects.filter(
        task__in=tasks, pledge=request.user
    )
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
        phases[p].append({'task': task, 'completion': comp})

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

    context = {
        'phase_groups': phase_groups,
        'all_required': len(all_required),
        'required_done': required_done,
        'overall_percent': round(required_done / len(all_required) * 100) if all_required else 0,
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

    # Check task is live
    if not task.is_live:
        return render(request, 'pledge/quiz_not_available.html', {'task': task})

    # Check pledge is assigned (or task applies to all)
    if task.assigned_to.exists() and not task.assigned_to.filter(pk=request.user.pk).exists():
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

            # Create or update completion → pending (awaiting chair review)
            PledgeTaskCompletion.objects.update_or_create(
                task=task,
                pledge=request.user,
                defaults={'status': 'pending'},
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
