"""
Views for announcement polls/surveys.

Officer views:
  - create_poll / edit_poll     — create or edit a poll on an announcement
  - poll_results                — view results, respondents, non-respondents, export CSV

Member views:
  - take_poll                   — submit (or, since v3.31.3, edit) a response
  - poll_confirmation           — shown after submission
  - polls_view                  — v3.31.3: browse open/closed polls
"""
from src.utils.export_utils import safe_csv_writer
import random
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from src.middleware.geo_restriction import geo_export_blocked

from src.models import (
    Announcement, AnnouncementPoll,
    AnnouncementPollOption, AnnouncementPollResponse, AnnouncementPollAnswer,
    AnnouncementPollEmbed,
)
from src.decorators import officer_required
from src.poll_editing import poll_results_data, save_poll
from src.utils.visibility import visible_to_q


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

        # --- Save: the form's fields as plain data, saved by src/poll_editing.py ---
        closes_at_raw = request.POST.get('closes_at', '').strip()
        closes_at = None
        if closes_at_raw:
            from django.utils.dateparse import parse_datetime
            closes_at = parse_datetime(closes_at_raw)

        def _indices(prefix):
            return sorted(set(k[len(prefix):] for k in request.POST if k.startswith(prefix)),
                          key=lambda x: int(x) if x.isdigit() else 0)

        questions = []
        for idx in _indices('question_text_'):
            questions.append({
                'id': request.POST.get(f'question_id_{idx}', ''),
                'text': request.POST.get(f'question_text_{idx}', ''),
                'type': request.POST.get(f'question_type_{idx}', 'single'),
                'required': request.POST.get(f'question_required_{idx}') == 'on',
                'options': [{
                    'id': request.POST.get(f'option_id_{idx}_{opt_idx}', ''),
                    'text': request.POST.get(f'option_text_{idx}_{opt_idx}', ''),
                } for opt_idx in _indices(f'option_text_{idx}_')],
            })
        # A question row whose text field was missing but whose id was sent is kept, as before.
        sent_ids = {v for k, v in request.POST.items() if k.startswith('question_id_') and v}
        listed = {str(q['id']) for q in questions if q['id']}
        questions += [{'id': qid, 'text': ''} for qid in sent_ids - listed]

        try:
            save_poll(announcement, request.user, {
                'title': request.POST.get('poll_title', ''),
                'description': request.POST.get('poll_description', ''),
                'is_anonymous': request.POST.get('is_anonymous') == 'on',
                'is_open': request.POST.get('is_open') == 'on',
                'closes_at': closes_at,
                'questions': questions,
            })
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('create_or_edit_poll', announcement_id=announcement_id)

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

    data = poll_results_data(poll)
    questions, responses = data['questions'], data['all_responses']

    # CSV export
    if request.GET.get('export') == 'csv':
        # v3.17.7: this export is a query-parameter MODE of `poll_results`, not
        # a route of its own, so `RESTRICTED_EXPORT_VIEWS` cannot reach it —
        # listing the URL name would geo-block the results page as well. It
        # dumps respondent names and every answer, which is the same class of
        # bulk member data as the directory and user-list exports, so it is
        # guarded here instead. See `geo_export_blocked` for the reasoning.
        blocked = geo_export_blocked(request)
        if blocked:
            return blocked
        return _export_poll_csv(poll, questions, responses)

    return render(request, 'officer/announcement_poll_results.html', {
        'announcement': announcement,
        'poll': poll,
        **{k: v for k, v in data.items() if k not in ('questions', 'all_responses')},
    })


def _export_poll_csv(poll, questions, responses):
    response = HttpResponse(content_type='text/csv')
    filename = f"poll_{poll.id}_{timezone.now().strftime('%Y%m%d')}.csv"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'

    writer = safe_csv_writer(response)

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
                timezone.localtime(resp.submitted_at).strftime('%Y-%m-%d %H:%M'),
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
# Officer: QR code for the poll (v3.28.7)
#
# Unlike the event check-in QR (EventCheckinWindow), a poll has no rotating,
# time-boxed token — `take_poll`'s URL is just the announcement's permanent
# id, and the poll's own `is_open`/`closes_at` already govern whether it
# accepts responses. So there is no "window" to open here; the QR always
# encodes the same URL, and `AnnouncementPollEmbed` (src/models/
# announcements.py) exists only to give that same image a stable,
# unauthenticated fetch address for pasting into a slide deck — see its
# docstring for why that's safe.
# ---------------------------------------------------------------------------

@login_required
@officer_required
def manage_poll_qr(request, announcement_id):
    """Show the poll's QR code, with a control to generate/revoke the
    stable embed link for pasting into slides."""
    announcement = get_object_or_404(Announcement, id=announcement_id)
    poll = get_object_or_404(AnnouncementPoll, announcement=announcement)
    embed = AnnouncementPollEmbed.objects.filter(poll=poll, revoked_at__isnull=True).first()

    embed_url = None
    if embed:
        embed_url = request.build_absolute_uri(
            reverse('poll_qr_embed_image', args=[announcement.id, embed.token])
        )

    return render(request, 'officer/manage_poll_qr.html', {
        'announcement': announcement,
        'poll': poll,
        'embed_url': embed_url,
    })


@login_required
@officer_required
def poll_qr_image(request, announcement_id):
    """
    The QR code itself, as SVG — same technique as the event check-in QR
    (`qr_checkin_image`) and the TOTP enrolment QR. Gated behind login for
    the same reason as that one: not because the image is more sensitive
    than the take_poll link everyone eligible can already navigate to
    directly, but so a bookmarked image URL doesn't outlive an officer's
    own access. The stable, no-login version for embedding is
    `poll_qr_embed_image`, below.
    """
    announcement = get_object_or_404(Announcement, id=announcement_id)
    get_object_or_404(AnnouncementPoll, announcement=announcement)  # 404 before rendering anything

    from src.utils.qr_svg import render_qr_svg

    poll_url = request.build_absolute_uri(reverse('take_poll', args=[announcement.id]))
    response = HttpResponse(render_qr_svg(poll_url), content_type='image/svg+xml')
    response['Cache-Control'] = 'no-store'
    return response


@login_required
@officer_required
@require_POST
def generate_poll_qr_embed_link(request, announcement_id):
    """Create (or reuse) the stable, unauthenticated embed link for this
    poll, then send the officer back to the management page where it's
    displayed. Mirrors generate_qr_embed_link (event_attendance.py)."""
    announcement = get_object_or_404(Announcement, id=announcement_id)
    poll = get_object_or_404(AnnouncementPoll, announcement=announcement)
    AnnouncementPollEmbed.get_or_create_for(poll, created_by=request.user)
    return redirect('manage_poll_qr', announcement_id=announcement.id)


@login_required
@officer_required
@require_POST
def revoke_poll_qr_embed_link(request, announcement_id):
    """Revoke the current embed link (e.g. it leaked somewhere unintended).
    A later "Get embed link" click issues a fresh token rather than
    reviving this one."""
    announcement = get_object_or_404(Announcement, id=announcement_id)
    poll = get_object_or_404(AnnouncementPoll, announcement=announcement)
    embed = AnnouncementPollEmbed.objects.filter(poll=poll, revoked_at__isnull=True).first()
    if embed:
        embed.revoke()
        messages.success(request, 'Embed link revoked. Generate a new one to keep using it elsewhere.')
    return redirect('manage_poll_qr', announcement_id=announcement.id)


def poll_qr_embed_image(request, announcement_id, embed_token):
    """
    The public, no-login embed image — for pasting into a slide deck. See
    `AnnouncementPollEmbed`'s docstring for the full reasoning on why this
    is safe to be unauthenticated.

    Deliberately NOT @login_required — a slideshow fetching this has no
    session, so a redirect-to-login would just be a broken image in the
    deck. Answered with a plain 404 (unknown/revoked token, or the poll no
    longer exists) or a placeholder (announcement not published yet, or the
    poll isn't currently accepting responses) instead.
    """
    announcement = get_object_or_404(Announcement, id=announcement_id)
    poll = get_object_or_404(AnnouncementPoll, announcement=announcement)
    embed = AnnouncementPollEmbed.objects.filter(
        poll=poll, token=embed_token, revoked_at__isnull=True,
    ).first()
    if not embed:
        return HttpResponse(status=404)

    from src.utils.qr_svg import POLL_WAITING_PLACEHOLDER_SVG, render_qr_svg

    if not announcement.is_published() or not poll.is_accepting_responses():
        response = HttpResponse(POLL_WAITING_PLACEHOLDER_SVG, content_type='image/svg+xml')
    else:
        poll_url = request.build_absolute_uri(reverse('take_poll', args=[announcement.id]))
        response = HttpResponse(render_qr_svg(poll_url), content_type='image/svg+xml')

    # No caching — the same url should stop showing a QR the moment the
    # poll closes, and start again if it's reopened, without anyone having
    # to re-paste the link.
    response['Cache-Control'] = 'no-store'
    return response


# ---------------------------------------------------------------------------
# Member: Take Poll
# ---------------------------------------------------------------------------

@login_required
def take_poll(request, announcement_id):
    """
    Display and process a poll for a regular member.

    v3.31.3 — a respondent may now EDIT their answer for as long as the poll
    keeps accepting responses (`poll.is_accepting_responses()`), instead of
    the second submission being refused outright. The one-response-per-
    (poll, respondent) DB constraint (`AnnouncementPollResponse.Meta.
    unique_together`) is unchanged — this updates the same row's answers in
    place rather than creating a second one, so `poll_results`' respondent
    counting, the anonymous-poll reveal threshold, and the CSV export all
    keep working exactly as before with no changes there. Editing stops the
    moment the poll closes, same as first-time submission — there's no path
    to changing an answer after the poll is no longer accepting responses.
    """
    announcement = get_object_or_404(Announcement, id=announcement_id)

    if not announcement.is_visible_to_user(request.user):
        messages.error(request, 'You do not have access to this announcement.')
        return redirect('announcements')

    poll = get_object_or_404(AnnouncementPoll, announcement=announcement)

    existing_response = poll.responses.filter(respondent=request.user).first()
    already_responded = existing_response is not None
    accepting = poll.is_accepting_responses()
    is_edit = already_responded

    if request.method == 'POST':
        if not accepting:
            messages.error(
                request,
                'This poll is no longer accepting responses, so your answer '
                'can no longer be changed.' if already_responded else
                'This poll is no longer accepting responses.',
            )
            return redirect('announcements')

        if is_edit:
            resp = existing_response
            # Clear the previous answer rows and rebuild them below — simpler
            # and less error-prone than diffing per-question, and an
            # AnnouncementPollAnswer's only purpose is to hold this response's
            # current answer to one question, so there's nothing to preserve
            # in the old rows once a new submission has arrived.
            resp.answers.all().delete()
            resp.updated_at = timezone.now()
            resp.save(update_fields=['updated_at'])
        else:
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

        messages.success(
            request,
            'Your response has been updated.' if is_edit else 'Thanks for responding!',
        )
        return redirect('poll_confirmation', announcement_id=announcement_id)

    questions = list(poll.questions.prefetch_related('options').all())

    # Pre-fill the form when editing, so a respondent sees what they picked
    # last time rather than a blank poll. Set directly as attributes on the
    # already-prefetched question/option objects (`option.is_selected`,
    # `question.existing_text_answer`) rather than passing separate lookup
    # dicts into the template — Django templates have no clean way to do a
    # two-level dict lookup keyed on a loop variable without a custom filter,
    # and mutating the prefetched objects in place is free (no extra
    # queries: `question.options.all()` below reads the same prefetch cache
    # these attributes were just set on).
    if is_edit:
        answers_by_question_id = {
            a.question_id: a
            for a in existing_response.answers.prefetch_related('selected_options')
        }
        for question in questions:
            answer = answers_by_question_id.get(question.id)
            if answer is None:
                continue
            if question.question_type == 'text':
                question.existing_text_answer = answer.text_answer
            else:
                selected_ids = {o.id for o in answer.selected_options.all()}
                for option in question.options.all():
                    option.is_selected = option.id in selected_ids

    return render(request, 'announcement_poll.html', {
        'announcement': announcement,
        'poll': poll,
        'questions': questions,
        'already_responded': already_responded,
        'accepting': accepting,
        'is_edit': is_edit,
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


# ---------------------------------------------------------------------------
# Member: Browse open/closed polls (v3.31.3)
# ---------------------------------------------------------------------------

@login_required
def polls_view(request):
    """
    Every poll on an announcement the member can see, split into Open (still
    accepting responses) and Closed — a poll "inbox" separate from scrolling
    the whole announcements feed. Mirrors `announcements_view`'s visibility
    and eligibility scoping exactly (`is_active`, `publish_at`, `visible_to_q`)
    so this page can never surface a poll the member couldn't already reach
    by opening its announcement.

    One query for the announcements+polls, one batched query for "have I
    responded" across all of them — same N+1-avoidance shape as
    `announcements_view`'s `responded_poll_ids`, not a query per poll.
    """
    from django.db.models import Q

    now = timezone.now()
    announcements_with_polls = list(
        Announcement.objects
        .filter(is_active=True)
        .filter(Q(publish_at__isnull=True) | Q(publish_at__lte=now))
        .filter(visible_to_q(request.user.member_type))
        .exclude(poll__isnull=True)
        .select_related('poll')
        .order_by('-posted_at')
    )

    responded_poll_ids = set(
        AnnouncementPollResponse.objects.filter(
            respondent=request.user,
            poll__announcement__in=announcements_with_polls,
        ).values_list('poll_id', flat=True)
    )

    open_polls = []
    closed_polls = []
    for announcement in announcements_with_polls:
        poll = announcement.poll
        entry = {
            'announcement': announcement,
            'poll': poll,
            'responded': poll.id in responded_poll_ids,
        }
        if poll.is_accepting_responses():
            open_polls.append(entry)
        else:
            closed_polls.append(entry)

    return render(request, 'polls.html', {
        'open_polls': open_polls,
        'closed_polls': closed_polls,
    })
