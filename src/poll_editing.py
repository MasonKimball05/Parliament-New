"""
Saving an announcement's poll and reading its results (10-30-26): the rules
``create_or_edit_poll`` and ``poll_results`` (src/view/officer/
announcement_polls.py) used, moved here unchanged so the app's officer
endpoints (src/mobile/views_officer_tools.py) share them.

``save_poll`` takes the poll as plain data:

    {'title', 'description', 'is_anonymous', 'is_open', 'closes_at',
     'questions': [{'id', 'text', 'type', 'required',
                    'options': [{'id', 'text'}]}]}

Questions and options are matched by id; ones not sent are deleted; a blank
question or option is skipped (but a blank one that has an id is kept, as the
web form did); a text question loses its options. A poll that is anonymous
stays anonymous.
"""
from django.db.models import Count

from src.models import (
    AnnouncementPoll, AnnouncementPollAnswer, AnnouncementPollOption, AnnouncementPollQuestion,
)
from src.models.users import member_defer

QUESTION_TYPES = ('single', 'multiple', 'text')


def _pk(raw):
    """Ids arrive as strings or numbers; anything else means a new row."""
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def save_poll(announcement, user, data):
    """Create or update ``announcement``'s poll from ``data``. Raises ValueError for a missing title."""
    poll = getattr(announcement, 'poll', None)
    title = (data.get('title') or '').strip()
    if not title:
        raise ValueError('Poll title is required.')
    if poll is None:
        poll = AnnouncementPoll(announcement=announcement, created_by=user)
        is_anonymous = bool(data.get('is_anonymous'))
    else:
        # Once anonymous, it cannot be reversed.
        is_anonymous = poll.is_anonymous or bool(data.get('is_anonymous'))
    poll.title = title[:200]
    poll.description = (data.get('description') or '').strip()
    poll.is_anonymous = is_anonymous
    poll.is_open = bool(data.get('is_open'))
    poll.closes_at = data.get('closes_at') or None
    poll.save()

    questions = list(data.get('questions') or [])
    keep_ids = [_pk(q.get('id')) for q in questions if _pk(q.get('id'))]
    poll.questions.exclude(id__in=keep_ids).delete()

    existing_questions = {q.id: q for q in poll.questions.all()}
    existing_options = {}
    for option in AnnouncementPollOption.objects.filter(question__poll=poll):
        existing_options.setdefault(option.question_id, {})[option.id] = option

    order = 0
    for q in questions:
        text = (q.get('text') or '').strip()
        if not text:
            continue
        q_type = q.get('type') if q.get('type') in QUESTION_TYPES else 'single'
        question = existing_questions.get(_pk(q.get('id'))) or AnnouncementPollQuestion(poll=poll)
        question.text = text[:500]
        question.question_type = q_type
        question.is_required = bool(q.get('required'))
        question.order = order
        question.save()
        order += 1

        if q_type in ('single', 'multiple'):
            options = list(q.get('options') or [])
            question.options.exclude(id__in=[_pk(o.get('id')) for o in options if _pk(o.get('id'))]).delete()
            opt_order = 0
            for o in options:
                opt_text = (o.get('text') or '').strip()
                if not opt_text:
                    continue
                option = existing_options.get(question.id, {}).get(_pk(o.get('id'))) \
                    or AnnouncementPollOption(question=question)
                option.text = opt_text[:200]
                option.order = opt_order
                option.save()
                opt_order += 1
        else:
            question.options.all().delete()
    return poll


def poll_results_data(poll):
    """Everything the results page shows, with the anonymity rules (more than two responses to reveal who)."""
    from src.models import ParliamentUser
    questions = poll.questions.prefetch_related('options').all()
    responses = poll.responses.select_related('respondent').defer(*member_defer('respondent')).prefetch_related(
        'answers__selected_options', 'answers__question',
    ).all()

    option_count_map = {
        (row['question_id'], row['selected_options']): row['n']
        for row in (
            AnnouncementPollAnswer.objects
            .filter(question__poll=poll, selected_options__isnull=False)
            .values('question_id', 'selected_options')
            .annotate(n=Count('id'))
        )
    }
    text_answer_map = {}
    for q_id, text in (
        AnnouncementPollAnswer.objects
        .filter(question__poll=poll)
        .exclude(text_answer='')
        .values_list('question_id', 'text_answer')
    ):
        text_answer_map.setdefault(q_id, []).append(text)

    question_stats = []
    for question in questions:
        if question.question_type in ('single', 'multiple'):
            option_counts = {
                option: option_count_map.get((question.id, option.id), 0)
                for option in question.options.all()
            }
            question_stats.append({
                'question': question,
                'option_counts': option_counts,
                'total_answers': sum(option_counts.values()),
            })
        else:
            question_stats.append({
                'question': question,
                'text_answers': text_answer_map.get(question.id, []),
            })

    non_respondents = poll.get_non_respondents()
    respondent_count = responses.count()

    # For anonymous polls, only reveal who has/hasn't responded once more than 2
    # people have voted — prevents identifying early respondents by elimination.
    anon_threshold_met = respondent_count > 2
    poll_is_closed = not poll.is_accepting_responses()

    if poll.is_anonymous:
        anon_respondents = (
            responses.values_list('respondent', flat=True)
            if anon_threshold_met else None
        )
        anon_respondent_users = (
            ParliamentUser.objects.filter(pk__in=anon_respondents)
            if anon_threshold_met else None
        )
        if anon_threshold_met:
            non_respondents_display = non_respondents
            non_respondents_partial = False
        elif poll_is_closed and respondent_count > 0:
            # Closed without enough votes — show roughly half the non-respondent
            # list so the voter(s) can't be identified by elimination.
            nr_count = non_respondents.count()
            non_respondents_display = list(non_respondents[:nr_count // 2])
            non_respondents_partial = True
        else:
            non_respondents_display = None
            non_respondents_partial = False
    else:
        anon_respondent_users = None
        non_respondents_display = non_respondents
        non_respondents_partial = False

    return {
        'questions': questions,
        'all_responses': responses,
        'question_stats': question_stats,
        'responses': responses if not poll.is_anonymous else None,
        'respondent_count': respondent_count,
        'non_respondents': non_respondents_display,
        'non_respondents_partial': non_respondents_partial,
        'anon_respondent_users': anon_respondent_users,
        'anon_threshold_met': anon_threshold_met,
        'poll_is_closed': poll_is_closed,
    }
