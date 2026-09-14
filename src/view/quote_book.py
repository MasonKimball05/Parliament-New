"""
Quote Book views. See `src/models/quote_book.py` for the full design
writeup and the decisions confirmed with Mason on 09-14-26.

`quote_book` renders the whole book in one request — every visible quote,
grouped into "chapters" by member — and hands it to the template as JSON
for the page-flip reader (`src/view/quote_book.py` doesn't know anything
about the flip animation; that's all client-side, see
`templates/quote_book/book.html`). One query, not one per chapter: at
chapter scale (dozens to low hundreds of quotes) there's no reason to
paginate or lazy-load this — see `src/tests/guards/test_query_budgets.py`
for the measured ceiling.

`flag_quote` is an AJAX-only endpoint (JSON in, JSON out) rather than a
redirect: flagging a quote resets a normal page's scroll position, but on
a flip-book a full page reload also throws the reader back to page 1,
which is a bad experience for "reading, notice something, remove it,
resume reading."

`review_flagged_quotes` / `restore_quote` — added 09-14-26, after Mason
asked where officers could review what had been flagged. The confirmed
design is still that flagging itself needs no approval step (it hides the
quote immediately, on the flagger's say-so alone) — this doesn't add a
review step *before* a flag takes effect, it adds a place to look
*afterward*.

Not officer-only, per a follow-up from Mason the same day: "I do like
that the person can see quotes they flagged and remove the flag if they
change their mind." `Quote.can_be_restored_by` (see
`src/models/quote_book.py`) is the single source of truth for who that
is — the flagger reversing their own call, the quoted member (final say
over their own chapter regardless of who flagged it), or any officer
(general audit/moderation). `review_flagged_quotes` scopes its queryset
to that same permission rather than showing everything to everyone: an
officer sees every flagged quote, anyone else sees only the ones they
have standing over. Restore is the only action offered — no
permanent-delete — because this codebase's standing convention is to
avoid hard-deleting user content by default (see the Kai retention
policy); an admin can still go around this and hard-delete a row directly
if one is ever genuinely garbage, but that's a deliberate escape hatch,
not the normal path.
"""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from src.feature_flag_decorators import require_page_enabled
from src.models import ActivityLog, ParliamentUser, Quote
from src.models.users import member_defer


def _script_safe_json(data):
    """
    json.dumps that is safe to render inside a <script> block with |safe.

    Same fix as `src/view/officer/transitions.py::_script_safe_json` (the
    07-09-26 roles_json XSS finding) and needed here for the same reason:
    quote text and context are free-text member input, embedded straight
    into `book.html`'s script block for the page-flip reader to render.
    Python's json.dumps does not escape '<', so a quote containing
    '</script>' would otherwise terminate the script tag early — stored
    XSS via the quote text itself. Escaping '<' as \\u003c is valid JSON
    and neutralizes both '</script>' and '<!--' breakouts.
    """
    return json.dumps(data).replace('<', '\\u003c')


@login_required
@require_page_enabled('quote_book')
def quote_book(request):
    quotes = (
        Quote.objects.visible()
        .select_related('quoted_member', 'submitted_by')
        .defer(*member_defer('quoted_member', 'submitted_by'))
        .order_by('quoted_member__name', 'created_at')
    )

    # Grouped in Python, not with a second query per member — `quotes` above
    # is already ordered by `quoted_member__name`, so a simple "new member,
    # new chapter" walk over the single result set is enough.
    chapters = []
    chapters_by_member_id = {}
    for q in quotes:
        member = q.quoted_member
        chapter = chapters_by_member_id.get(member.pk)
        if chapter is None:
            chapter = {
                'member_id': member.pk,
                'member_name': member.get_display_name(),
                'quotes': [],
            }
            chapters_by_member_id[member.pk] = chapter
            chapters.append(chapter)
        chapter['quotes'].append({
            'id': q.pk,
            'text': q.text,
            'context': q.context,
            'submitted_by': q.submitted_by.get_display_name() if q.submitted_by else 'Unknown',
            'created_at': timezone.localtime(q.created_at).strftime('%b %d, %Y'),
            'can_flag': q.can_be_flagged_by(request.user),
            # Distinguishes "flag" from "delete outright" in the reader's
            # button label/confirm text — both hit the same flag_quote
            # endpoint; the view decides which one actually happens.
            'delete_not_flag': q.can_be_deleted_outright_by(request.user),
        })

    # Shown to everyone, but scoped to what they can actually act on —
    # an officer sees every flagged quote; anyone else sees only the ones
    # `can_be_restored_by` gives them standing over (a quote they flagged,
    # or a flagged quote on their own chapter). `None` (not 0) for a
    # member with no standing at all, so the template can tell "nothing to
    # show you" apart from "link not relevant to you" — matches how
    # `flag_quote`'s own can_flag flows through per-quote.
    if request.user.is_officer:
        flagged_count = Quote.objects.filter(flagged_at__isnull=False).count()
    else:
        flagged_count = Quote.objects.filter(
            Q(flagged_by=request.user) | Q(quoted_member=request.user),
            flagged_at__isnull=False,
        ).count()
        if flagged_count == 0:
            flagged_count = None

    return render(request, 'quote_book/book.html', {
        'chapters_json': _script_safe_json(chapters),
        'has_quotes': bool(chapters),
        'flagged_count': flagged_count,
    })


@login_required
@require_page_enabled('quote_book')
def submit_quote(request):
    if request.method == 'POST':
        quoted_member_id = request.POST.get('quoted_member', '').strip()
        text = request.POST.get('text', '').strip()
        context = request.POST.get('context', '').strip()

        if not quoted_member_id or not text:
            messages.error(request, 'Please choose who said it and enter the quote.')
            return redirect('submit_quote')

        quoted_member = get_object_or_404(ParliamentUser, pk=quoted_member_id)

        quote = Quote.objects.create(
            quoted_member=quoted_member,
            text=text,
            context=context,
            submitted_by=request.user,
        )

        # Structural details only (who/who/id) — never the quote text
        # itself. Same discipline v3.29.10 established for FeedbackRequest:
        # ActivityLog.description is readable by every officer and chair,
        # so free text a member wrote doesn't belong in it even here, where
        # the quote itself is otherwise public on the book page.
        ActivityLog.log_activity(
            action_type='quote_submitted',
            user=request.user,
            description=(
                f'{request.user.get_display_name()} added a quote '
                f'(#{quote.pk}) to {quoted_member.get_display_name()}\'s chapter'
            ),
            request=request,
            object_type='Quote',
            object_id=quote.pk,
        )

        messages.success(request, 'Added to the book.')
        return redirect('quote_book')

    # Alumni keep their chapter (the book is a record, not a roster) —
    # only excluded status is filtered out, matching how the rest of the
    # app treats a fully removed member.
    members = ParliamentUser.objects.exclude(member_status='Removed').order_by('name')
    return render(request, 'quote_book/submit.html', {'members': members})


@login_required
@require_page_enabled('quote_book')
@require_POST
def flag_quote(request, quote_id):
    quote = get_object_or_404(Quote, pk=quote_id)

    # Self-authored, self-quoted — delete outright rather than flag. See
    # Quote.can_be_deleted_outright_by's docstring: nobody else has a
    # record at stake here, so there's nothing a restore would ever need
    # to bring back. Checked BEFORE can_be_flagged_by because this quote
    # never enters the flagged/soft-hidden state at all in this case.
    if quote.can_be_deleted_outright_by(request.user):
        quote_pk = quote.pk
        quoted_member_name = quote.quoted_member.get_display_name()
        quote.delete()

        ActivityLog.log_activity(
            action_type='quote_deleted',
            user=request.user,
            description=(
                f'{request.user.get_display_name()} deleted their own quote '
                f'(#{quote_pk}) from {quoted_member_name}\'s chapter'
            ),
            request=request,
            object_type='Quote',
            object_id=quote_pk,
        )
        return JsonResponse({'success': True, 'deleted': True})

    if not quote.can_be_flagged_by(request.user):
        return JsonResponse({'success': False, 'error': 'not_permitted'}, status=403)

    if quote.flagged_at is None:
        quote.flagged_at = timezone.now()
        quote.flagged_by = request.user
        quote.save(update_fields=['flagged_at', 'flagged_by'])

        ActivityLog.log_activity(
            action_type='quote_flagged',
            user=request.user,
            description=(
                f'{request.user.get_display_name()} flagged a quote '
                f'(#{quote.pk}) on {quote.quoted_member.get_display_name()}\'s chapter'
            ),
            request=request,
            object_type='Quote',
            object_id=quote.pk,
        )

    return JsonResponse({'success': True, 'deleted': False})


@login_required
@require_page_enabled('quote_book')
def review_flagged_quotes(request):
    flagged = Quote.objects.filter(flagged_at__isnull=False)

    # Officers see everything (general audit/moderation); anyone else sees
    # only what Quote.can_be_restored_by would let them act on. Scoping
    # the QUERY rather than filtering the rendered list in Python — a
    # member with no standing over a flagged quote shouldn't be able to
    # tell from this page that the quote even exists, the same "don't
    # leak via what the list contains" reasoning the Kai audit surfaces
    # in this codebase are built on.
    if not request.user.is_officer:
        flagged = flagged.filter(
            Q(flagged_by=request.user) | Q(quoted_member=request.user)
        )

    flagged = (
        flagged
        .select_related('quoted_member', 'submitted_by', 'flagged_by')
        .defer(*member_defer('quoted_member', 'submitted_by', 'flagged_by'))
        .order_by('-flagged_at')
    )
    return render(request, 'quote_book/flagged.html', {
        'flagged_quotes': flagged,
        'viewing_as_officer': request.user.is_officer,
    })


@login_required
@require_page_enabled('quote_book')
@require_POST
def restore_quote(request, quote_id):
    quote = get_object_or_404(Quote, pk=quote_id)

    if not quote.can_be_restored_by(request.user):
        return HttpResponseForbidden("You don't have permission to restore this quote.")

    if quote.flagged_at is not None:
        quote.flagged_at = None
        quote.flagged_by = None
        quote.save(update_fields=['flagged_at', 'flagged_by'])

        # Structural-only, same discipline as quote_submitted/quote_flagged.
        ActivityLog.log_activity(
            action_type='quote_restored',
            user=request.user,
            description=(
                f'{request.user.get_display_name()} restored a flagged quote '
                f'(#{quote.pk}) on {quote.quoted_member.get_display_name()}\'s chapter'
            ),
            request=request,
            object_type='Quote',
            object_id=quote.pk,
        )
        messages.success(request, 'Quote restored to the book.')

    return redirect('review_flagged_quotes')
