"""
Views for announcement polls/surveys.

Officer views:
  - create_poll / edit_poll     — create or edit a poll on an announcement
  - poll_results                — view results, respondents, non-respondents, export CSV

Member views:
  - take_poll                   — submit a response
  - poll_confirmation           — shown after submission
"""
import csv
import random
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST

from src.models import (
    Announcement, AnnouncementPoll, AnnouncementPollQuestion,
    AnnouncementPollOption, AnnouncementPollResponse, AnnouncementPollAnswer,
)
from src.decorators import officer_required


# ---------------------------------------------------------------------------
# Officer: Create / Edit Poll
# ---------------------------------------------------------------------------

@login_required
@officer_required
def create_or_edit_poll(request, announcement_id):
    """Create or edit the poll attached to an announcement."""
    announcement = get_object_or_404(Announcement, id=announcement_id)
    poll = getattr(announcement, 'poll', None)
    is_edit = poll is not None

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'delete' and is_edit:
            poll.delete()
            messages.success(request, 'Poll deleted.')
            return redirect('manage_announcements')

        # --- Save poll metadata ---
        title = request.POST.get('poll_title', '').strip()
        description = request.POST.get('poll_description', '').strip()
        # Once anonymous, it cannot be reversed — guard against tampered POSTs
        if is_edit and poll.is_anonymous:
            is_anonymous = True
        else:
            is_anonymous = request.POST.get('is_anonymous') == 'on'
        is_open = request.POST.get('is_open') == 'on'
        closes_at_raw = request.POST.get('closes_at', '').strip()
        closes_at = None
        if closes_at_raw:
            from django.utils.dateparse import parse_datetime
            closes_at = parse_datetime(closes_at_raw)

        if not title:
            messages.error(request, 'Poll title is required.')
            return redirect('create_or_edit_poll', announcement_id=announcement_id)

        if not is_edit:
            poll = AnnouncementPoll(announcement=announcement, created_by=request.user)
        poll.title = title
        poll.description = description
        poll.is_anonymous = is_anonymous
        poll.is_open = is_open
        poll.closes_at = closes_at
        poll.save()

        # --- Save questions ---
        # Delete removed questions (those whose IDs aren't in the submitted list)
        submitted_q_ids = [
            v for k, v in request.POST.items()
            if k.startswith('question_id_') and v
        ]
        poll.questions.exclude(id__in=submitted_q_ids).delete()

        question_indices = sorted(set(
            k.split('_')[-1] for k in request.POST
            if k.startswith('question_text_')
        ), key=lambda x: int(x) if x.isdigit() else 0)

        # v3.16.3 perf: the loops below used to do one .get() per submitted
        # question and one per submitted option — a 6-question / 4-option poll
        # cost ~30 point lookups on every save. Fetch both sets once and index
        # in Python. Scoping the fetch to this poll preserves the ownership
        # check the per-row .get(id=..., poll=poll) was doing.
        existing_questions = {q.id: q for q in poll.questions.all()}
        existing_options = {}
        for option in AnnouncementPollOption.objects.filter(question__poll=poll):
            existing_options.setdefault(option.question_id, {})[option.id] = option

        def _as_pk(raw):
            """POST ids arrive as strings; anything unparseable means 'new row'."""
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None

        for order, idx in enumerate(question_indices):
            text = request.POST.get(f'question_text_{idx}', '').strip()
            q_type = request.POST.get(f'question_type_{idx}', 'single')
            is_required = request.POST.get(f'question_required_{idx}') == 'on'
            q_id = request.POST.get(f'question_id_{idx}', '')

            if not text:
                continue

            question = existing_questions.get(_as_pk(q_id)) if q_id else None
            if question is None:
                question = AnnouncementPollQuestion(poll=poll)

            question.text = text
            question.question_type = q_type
            question.is_required = is_required
            question.order = order
            question.save()

            # Save options for choice questions
            if q_type in ('single', 'multiple'):
                submitted_opt_ids = [
                    v for k, v in request.POST.items()
                    if k.startswith(f'option_id_{idx}_') and v
                ]
                question.options.exclude(id__in=submitted_opt_ids).delete()

                opt_indices = sorted(set(
                    k.split('_')[-1] for k in request.POST
                    if k.startswith(f'option_text_{idx}_')
                ), key=lambda x: int(x) if x.isdigit() else 0)

                for opt_order, opt_idx in enumerate(opt_indices):
                    opt_text = request.POST.get(f'option_text_{idx}_{opt_idx}', '').strip()
                    opt_id = request.POST.get(f'option_id_{idx}_{opt_idx}', '')
                    if not opt_text:
                        continue
                    option = (
                        existing_options.get(question.id, {}).get(_as_pk(opt_id))
                        if opt_id else None
                    )
                    if option is None:
                        option = AnnouncementPollOption(question=question)
                    option.text = opt_text
                    option.order = opt_order
                    option.save()
            else:
                # Text question — remove any stale options
                question.options.all().delete()

        messages.success(request, 'Poll saved successfully.')
        return redirect('poll_results', announcement_id=announcement_id)

    questions = poll.questions.prefetch_related('options').all() if is_edit else []
    return render(request, 'officer/announcement_poll_edit.html', {
        'announcement': announcement,
        'poll': poll,
        'questions': questions,
        'is_edit': is_edit,
    })


# ---------------------------------------------------------------------------
# Officer: Results
# ---------------------------------------------------------------------------

@login_required
@officer_required
def poll_results(request, announcement_id):
    """Display poll results, respondents, and non-respondents."""
    announcement = get_object_or_404(Announcement, id=announcement_id)
    poll = get_object_or_404(AnnouncementPoll, announcement=announcement)

    questions = poll.questions.prefetch_related('options').all()
    responses = poll.responses.select_related('respondent').prefetch_related(
        'answers__selected_options', 'answers__question',
    ).all()

    # v3.16.3 perf: these counts used to be one COUNT query per option per
    # question (a 6-question poll with 4 options each = 24 queries), plus one
    # more query per free-text question. Both are now single aggregates for
    # the whole poll, looked up in Python below.
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

    # Build per-question aggregate counts
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

    from src.models import ParliamentUser

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
            # Threshold met — show full non-respondent list
            non_respondents_display = non_respondents
            non_respondents_partial = False
        elif poll_is_closed and respondent_count > 0:
            # Closed without enough votes — show roughly half the non-respondent
            # list so the voter(s) can't be identified by elimination.
            nr_count = non_respondents.count()
            show_count = nr_count // 2
            non_respondents_display = list(non_respondents[:show_count])
            non_respondents_partial = True
        else:
            # Still open, threshold not met (or no votes yet while open)
            non_respondents_display = None
            non_respondents_partial = False
    else:
        anon_respondent_users = None
        non_respondents_display = non_respondents
        non_respondents_partial = False

    # CSV export
    if request.GET.get('export') == 'csv':
        return _export_poll_csv(poll, questions, responses)

    return render(request, 'officer/announcement_poll_results.html', {
        'announcement': announcement,
        'poll': poll,
        'question_stats': question_stats,
        'responses': responses if not poll.is_anonymous else None,
        'respondent_count': respondent_count,
        'non_respondents': non_respondents_display,
        'non_respondents_partial': non_respondents_partial,
        'anon_respondent_users': anon_respondent_users,
        'anon_threshold_met': anon_threshold_met,
        'poll_is_closed': poll_is_closed,
    })


def _export_poll_csv(poll, questions, responses):
    response = HttpResponse(content_type='text/csv')
    filename = f"poll_{poll.id}_{timezone.now().strftime('%Y%m%d')}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)

    # v3.16.2: for anonymous polls, omit the submission timestamp AND shuffle
    # row order. The respondent name was already omitted, but submitted_at
    # (and, failing that, the -submitted_at row ordering) is a join key: pair
    # it with any per-respondent timestamp and the answers are re-identified.
    # Non-anonymous polls are unchanged.
    headers = [] if poll.is_anonymous else ['Respondent', 'Submitted At']
    for q in questions:
        headers.append(q.text[:80])
    writer.writerow(headers)

    rows_source = list(responses)
    if poll.is_anonymous:
        # v3.16.3: os.urandom-backed rather than the module-level Mersenne
        # Twister. This shuffle is an unlinkability control, not a cosmetic
        # one — the global `random` state is shared process-wide and its
        # output is reconstructable from enough observed values.
        random.SystemRandom().shuffle(rows_source)

    for resp in rows_source:
        if poll.is_anonymous:
            row = []
        else:
            row = [
                resp.respondent.get_display_name() if resp.respondent else '',
                resp.submitted_at.strftime('%Y-%m-%d %H:%M'),
            ]
        # v3.16.2 perf: the caller prefetches `answers__selected_options`, but
        # `resp.answers.get(question=q)` bypasses the prefetch cache and fired
        # one query per response × question. Index the prefetched rows instead.
        answers_by_question = {a.question_id: a for a in resp.answers.all()}
        for q in questions:
            answer = answers_by_question.get(q.id)
            if answer is None:
                row.append('')
            elif q.question_type in ('single', 'multiple'):
                row.append(', '.join(o.text for o in answer.selected_options.all()))
            else:
                row.append(answer.text_answer)
        writer.writerow(row)

    return response


# ---------------------------------------------------------------------------
# Member: Take Poll
# ---------------------------------------------------------------------------

@login_required
def take_poll(request, announcement_id):
    """Display and process a poll for a regular member."""
    announcement = get_object_or_404(Announcement, id=announcement_id)

    if not announcement.is_visible_to_user(request.user):
        messages.error(request, 'You do not have access to this announcement.')
        return redirect('announcements')

    poll = get_object_or_404(AnnouncementPoll, announcement=announcement)

    already_responded = poll.has_user_responded(request.user)
    accepting = poll.is_accepting_responses()

    if request.method == 'POST':
        if already_responded:
            messages.warning(request, 'You have already submitted a response.')
            return redirect('poll_confirmation', announcement_id=announcement_id)
        if not accepting:
            messages.error(request, 'This poll is no longer accepting responses.')
            return redirect('announcements')

        # Create the response
        resp = AnnouncementPollResponse.objects.create(
            poll=poll,
            respondent=request.user,
        )

        questions = poll.questions.prefetch_related('options').all()
        for question in questions:
            answer = AnnouncementPollAnswer.objects.create(
                response=resp,
                question=question,
            )
            if question.question_type == 'text':
                answer.text_answer = request.POST.get(f'q_{question.id}', '').strip()
                answer.save()
            elif question.question_type == 'single':
                option_id = request.POST.get(f'q_{question.id}')
                if option_id:
                    try:
                        option = question.options.get(id=option_id)
                        answer.selected_options.add(option)
                    except AnnouncementPollOption.DoesNotExist:
                        pass
            elif question.question_type == 'multiple':
                option_ids = request.POST.getlist(f'q_{question.id}')
                options = question.options.filter(id__in=option_ids)
                answer.selected_options.set(options)

        return redirect('poll_confirmation', announcement_id=announcement_id)

    questions = poll.questions.prefetch_related('options').all()
    return render(request, 'announcement_poll.html', {
        'announcement': announcement,
        'poll': poll,
        'questions': questions,
        'already_responded': already_responded,
        'accepting': accepting,
    })


@login_required
def poll_confirmation(request, announcement_id):
    """Shown after a user submits a poll response."""
    announcement = get_object_or_404(Announcement, id=announcement_id)
    poll = get_object_or_404(AnnouncementPoll, announcement=announcement)
    return render(request, 'announcement_poll_confirmation.html', {
        'announcement': announcement,
        'poll': poll,
    })
