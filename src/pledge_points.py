"""
v3.48.0 — how many points a pledge has earned on a task.

Mason, 10-16-26: quizzes get two modes — "full points just for doing the quiz",
or "points proportional to the % they get right. If a quiz is 10 points and
they get 7 right it's 7 points."

The three pages that add up a pledge's points (the education dashboard, the
per-pledge page, and the pledge's own My Tasks, which also renders the chair's
"View as pledge" preview) all call `task_points_earned` so they can't disagree.

Rules:
  * Points are only ever earned on a task marked **Completed**, in both modes
    (Mason's call). Pending / Incomplete / Waived earn 0, as before.
  * `full` (the default, and every non-quiz task): all of `task.points`.
  * `by_score` (quizzes only): `task.points` x the fraction right, where
    "fraction right" is, in order (Mason: "a bit of both"):
      1. the score a chair typed, if the quiz has a max score and this pledge
         has one (score / max_score) — the chair's number is the override,
         e.g. for partial credit;
      2. otherwise the right/wrong marks on his answers (marked right /
         number of questions). A question not yet marked counts as not right.
      3. If neither exists (no score, no questions), full points: the chair
         marked it Completed and there is nothing to scale by.
    Rounded half up to a whole point (7.5 -> 8), never above `task.points`.
"""
from django.db.models import Count

#: Values of `PledgeTask.points_mode` (choices live on the model).
POINTS_FULL = 'full'
POINTS_BY_SCORE = 'by_score'


def is_scaled(task):
    """True when this task's points scale with how much the pledge got right."""
    return task.task_type == 'quiz' and task.points_mode == POINTS_BY_SCORE


def earned_fraction(task, completion, correct=0, question_count=0):
    """
    (numerator, denominator) of the fraction right, or None when there's
    nothing to scale by. See the module docstring for the order.
    """
    if task.max_score and completion is not None and completion.score is not None:
        return min(completion.score, task.max_score), task.max_score
    if question_count:
        return min(correct, question_count), question_count
    return None


def task_points_earned(task, completion, correct=0, question_count=0):
    """Points this completion has earned. `correct`/`question_count` only matter when `is_scaled(task)`."""
    if not task.points or completion is None or completion.status != 'completed':
        return 0
    if not is_scaled(task):
        return task.points
    fraction = earned_fraction(task, completion, correct, question_count)
    if fraction is None:
        return task.points
    num, den = fraction
    # Integer round-half-up: floor(points * num / den + 1/2).
    return (2 * task.points * num + den) // (2 * den)


def quiz_marks(tasks, pledges):
    """
    Right-answer counts and question counts for the scaled quizzes among
    `tasks`, in two queries (none when there are no scaled quizzes).

    Returns (correct, questions):
      correct   — {(task_pk, pledge_pk): answers marked right}
      questions — {task_pk: number of questions}
    """
    scaled_pks = [t.pk for t in tasks if is_scaled(t)]
    if not scaled_pks:
        return {}, {}
    from src.models import PledgeQuizAnswer, PledgeTaskQuestion
    questions = dict(
        PledgeTaskQuestion.objects.filter(task_id__in=scaled_pks)
        .values('task_id').annotate(n=Count('pk')).values_list('task_id', 'n')
    )
    correct = {
        (row['question__task_id'], row['pledge_id']): row['n']
        for row in PledgeQuizAnswer.objects
        .filter(question__task_id__in=scaled_pks, pledge__in=pledges, is_correct=True)
        .values('question__task_id', 'pledge_id').annotate(n=Count('pk'))
        # The manager select_related()s question__task and pledge for __str__;
        # a grouped count must not carry those joins into GROUP BY.
        .order_by()
    }
    return correct, questions


def points_for(task, completion, pledge_pk, marks):
    """`task_points_earned` with the counts looked up from a `quiz_marks` result."""
    correct, questions = marks
    return task_points_earned(
        task, completion,
        correct.get((task.pk, pledge_pk), 0),
        questions.get(task.pk, 0),
    )


def grading_preview(task, completion, correct, question_count, answered_marked):
    """
    v3.50.0 — what a by-% quiz would give if marked Completed right now, for
    the grading page and its warning. `answered_marked` = answers with a
    right/wrong mark. Returns None for a quiz that doesn't scale.

      {'points': 7, 'unmarked': 3, 'uses_score': False}

    `unmarked` is questions without a mark (an unanswered question counts too:
    it's "not right" until marked), and is what makes 0 points a surprise.
    """
    if not is_scaled(task):
        return None
    from types import SimpleNamespace
    score = getattr(completion, 'score', None) if completion is not None else None
    as_completed = SimpleNamespace(status='completed', score=score)
    uses_score = bool(task.max_score) and score is not None
    return {
        'points': task_points_earned(task, as_completed, correct, question_count),
        'unmarked': 0 if uses_score else max(question_count - answered_marked, 0),
        'uses_score': uses_score,
    }
