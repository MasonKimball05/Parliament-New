"""
Reporting a quote (v4.4.0, App Store guideline 1.2). The one implementation
behind the website (``src/view/quote_book.py``) and the app
(``src/mobile/views_songbook.py``).

The Quote Book already lets the *involved* parties — the quoted member, the
submitter, an officer — flag a quote, which hides it at once
(``Quote.can_be_flagged_by``). That is unchanged. The Terms also promise that
*any* member can report any quote, so this adds a second, gentler path:

* Any member who can see a quote (the book is chapter-wide) may report it,
  except whoever submitted it (they can take it back with the flag). A report
  does NOT hide the quote. It notifies the officers (in-app Notification) and
  puts the quote in the existing flagged/review queue, where officers see who
  reported and why.
* Once ``AUTO_HIDE_THRESHOLD`` distinct members (the chat one) have open
  reports on a quote it is hidden (``flagged_at`` set, ``flagged_by`` left
  empty: no single person hid it) pending review.
* Officers ``dismiss`` (an auto-hidden quote comes back) or ``hide`` (flags it
  as themselves). An officer restoring a flagged quote from the queue also
  dismisses its open reports.

Who reported is shown to officers only — never to the quoted member or the
submitter, including on the flagged page they can see for their own chapter.
"""
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.urls import reverse
from django.utils import timezone

from src.chat_safety import AUTO_HIDE_THRESHOLD, DETAILS_MAX, NOTE_MAX, moderators
from src.chat_service import ChatError

REVIEW_ACTIONS = ('dismiss', 'hide')


def _models():
    from src.models import ChatMessageReport, Quote, QuoteReport
    return ChatMessageReport, Quote, QuoteReport


def _clean(text, limit):
    return (text.strip() if isinstance(text, str) else '')[:limit]


def can_report(quote, user):
    """Anyone who can see the quote, except its submitter (who can flag it instead)."""
    pk = getattr(user, 'pk', None)
    return pk is not None and quote.flagged_at is None and pk != quote.submitted_by_id


def report_quote(quote, reporter, reason, details=''):
    ChatMessageReport, _, QuoteReport = _models()
    if quote.flagged_at is not None:
        raise ChatError('not_found', 'Quote not found', 404)
    if reporter.pk == quote.submitted_by_id:
        raise ChatError('own_quote', 'You added this quote — remove it with "Flag for removal" instead.')
    if reason not in {c[0] for c in ChatMessageReport.REASON_CHOICES}:
        raise ChatError('invalid_input', 'Choose a reason for the report.')
    try:
        with transaction.atomic():
            report = QuoteReport.objects.create(quote=quote, reporter=reporter, reason=reason,
                                                details=_clean(details, DETAILS_MAX))
    except IntegrityError:
        raise ChatError('already_reported', 'You already reported this quote.', 409)

    reporters = (QuoteReport.objects.filter(quote=quote, status=QuoteReport.STATUS_OPEN)
                 .values('reporter').distinct().count())
    hidden_now = False
    if reporters >= AUTO_HIDE_THRESHOLD:
        # Same rule as chat: enough distinct reporters hide it pending review.
        quote.flagged_at, quote.flagged_by = timezone.now(), None
        quote.save(update_fields=['flagged_at', 'flagged_by'])
        hidden_now = True

    if hidden_now:
        from src.models import ActivityLog
        # Structural only (the quote book's rule), and with NO user: the
        # activity feed is readable by every officer and chair, and the
        # actor of this row would be a reporter.
        ActivityLog.log_activity(
            action_type='quote_flagged',
            description=f'Quote #{quote.pk} was hidden after {reporters} member reports',
            object_type='Quote', object_id=quote.pk)
    if reporters == 1 or hidden_now:
        _notify_officers(quote, reporter, reason, hidden_now)
    return report


def _notify_officers(quote, reporter, reason, hidden_now):
    from src.notification_service import create_notification
    ChatMessageReport, _, _ = _models()
    label = dict(ChatMessageReport.REASON_CHOICES).get(reason, reason)
    title = 'A reported quote was hidden pending review' if hidden_now else 'A quote was reported'
    skip = {reporter.pk, quote.quoted_member_id, quote.submitted_by_id}
    for officer in moderators():
        if officer.pk in skip:
            continue
        create_notification(
            recipient=officer, notification_type='quote_report', title=title,
            message=f'Reason: {label}. Review it in Flagged quotes.',
            link=reverse('review_flagged_quotes'), source_type='quote', source_id=quote.pk,
            push_title='Parliament', push_body='A quote needs review')


def officer_queue():
    """Officers' review queue: flagged quotes, plus visible ones with open reports."""
    _, Quote, QuoteReport = _models()
    return (Quote.objects
            .annotate(open_reports=Count('reports', filter=Q(reports__status=QuoteReport.STATUS_OPEN)))
            .filter(Q(flagged_at__isnull=False) | Q(open_reports__gt=0)))


def open_reports_by_quote(quote_ids):
    """``{quote_id: [report, …]}`` of open reports, reporter loaded — officers only."""
    _, _, QuoteReport = _models()
    out = {}
    for r in (QuoteReport.objects.filter(quote_id__in=list(quote_ids), status=QuoteReport.STATUS_OPEN)
              .select_related('reporter').order_by('created_at')):
        out.setdefault(r.quote_id, []).append(r)
    return out


def _close(quote, user, status, note=''):
    _, _, QuoteReport = _models()
    return QuoteReport.objects.filter(quote=quote, status=QuoteReport.STATUS_OPEN).update(
        status=status, reviewed_by=user, reviewed_at=timezone.now(), action_note=_clean(note, NOTE_MAX))


def review_reports(quote, officer, action, note=''):
    """An officer dismisses a quote's reports or hides it. Returns reports closed."""
    _, _, QuoteReport = _models()
    if not getattr(officer, 'is_officer', False):
        raise ChatError('forbidden', 'Only officers can review reported quotes.', 403)
    if action not in REVIEW_ACTIONS:
        raise ChatError('invalid_input', f'action must be one of: {", ".join(REVIEW_ACTIONS)}')
    if action == 'hide':
        if quote.flagged_at is None:
            quote.flagged_at, quote.flagged_by = timezone.now(), officer
            quote.save(update_fields=['flagged_at', 'flagged_by'])
        return _close(quote, officer, QuoteReport.STATUS_ACTIONED, note)
    if quote.flagged_at is not None and quote.flagged_by_id is None:
        # Hidden by the report count alone: dismissing the reports restores it.
        quote.flagged_at = None
        quote.save(update_fields=['flagged_at'])
    return _close(quote, officer, QuoteReport.STATUS_DISMISSED, note)


def restored(quote, user):
    """After ``restore_quote``: an officer restoring also dismisses the open reports."""
    _, _, QuoteReport = _models()
    if getattr(user, 'is_officer', False):
        _close(quote, user, QuoteReport.STATUS_DISMISSED, 'Restored')
