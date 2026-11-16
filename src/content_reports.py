"""
Reporting a member's profile or an idea (11-16-26, App Store guideline 1.2:
every place one member's content reaches the others can be reported). Chat
messages and quotes have their own reports (src/chat_safety.py,
src/quote_reports.py); this follows the same rules.

* **Profile:** anyone can report another member's profile (photo, About me).
  It notifies the officers. An officer who acts on it removes the photo and
  clears About me (the photo the way Admin v2's "Remove photo" does, so the
  member is told an admin removed it).
* **Idea:** anyone can report an idea on the ideas board except whoever
  submitted it. Once ``AUTO_HIDE_THRESHOLD`` distinct members (the chat one)
  have open reports, the idea is hidden from the board pending review. An
  officer acting on it hides it; dismissing the reports brings an
  auto-hidden one back.

Officers review both in the app (Officer tools → Reported content) through
``officer_queue``/``review``. Who reported is shown to officers only.
"""
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from src.chat_safety import AUTO_HIDE_THRESHOLD, DETAILS_MAX, NOTE_MAX, moderators
from src.chat_service import ChatError

REVIEW_ACTIONS = ('dismiss', 'remove')


def _models():
    from src.models import ChatMessageReport, ContentReport, FeedbackRequest
    return ChatMessageReport, ContentReport, FeedbackRequest


def _clean(text, limit):
    return (text.strip() if isinstance(text, str) else '')[:limit]


def _check_reason(reason):
    ChatMessageReport, _, _ = _models()
    if reason not in {c[0] for c in ChatMessageReport.REASON_CHOICES}:
        raise ChatError('invalid_input', 'Choose a reason for the report.')


def _create(**fields):
    _, ContentReport, _ = _models()
    try:
        with transaction.atomic():
            return ContentReport.objects.create(**fields)
    except IntegrityError:
        what = 'profile' if fields['kind'] == ContentReport.KIND_PROFILE else 'idea'
        raise ChatError('already_reported', f'You already reported this {what}.', 409)


def report_profile(member, reporter, reason, details=''):
    _, ContentReport, _ = _models()
    if member is None or not member.is_active:
        raise ChatError('not_found', 'Member not found', 404)
    if member.pk == reporter.pk:
        raise ChatError('own_profile', 'You can’t report your own profile.')
    _check_reason(reason)
    report = _create(kind=ContentReport.KIND_PROFILE, member=member, reporter=reporter, reason=reason,
                     details=_clean(details, DETAILS_MAX))
    if ContentReport.objects.filter(kind=ContentReport.KIND_PROFILE, member=member,
                                    status=ContentReport.STATUS_OPEN).count() == 1:
        _notify(report, f'{member.get_display_name()}’s profile was reported', skip={member.pk})
    return report


def idea_visible(idea):
    return idea.hidden_at is None


def report_idea(idea, reporter, reason, details=''):
    _, ContentReport, _ = _models()
    if idea is None or idea.request_type != 'feature_idea' or idea.hidden_at is not None:
        raise ChatError('not_found', 'Idea not found', 404)
    if reporter.pk == idea.submitted_by_id:
        raise ChatError('own_idea', 'You can’t report your own idea.')
    _check_reason(reason)
    report = _create(kind=ContentReport.KIND_IDEA, idea=idea, member_id=idea.submitted_by_id,
                     reporter=reporter, reason=reason, details=_clean(details, DETAILS_MAX))
    reporters = (ContentReport.objects.filter(kind=ContentReport.KIND_IDEA, idea=idea,
                                              status=ContentReport.STATUS_OPEN)
                 .values('reporter').distinct().count())
    hidden_now = reporters >= AUTO_HIDE_THRESHOLD
    if hidden_now:
        idea.hidden_at = timezone.now()
        idea.save(update_fields=['hidden_at'])
    if reporters == 1 or hidden_now:
        title = 'A reported idea was hidden pending review' if hidden_now else 'An idea was reported'
        _notify(report, title, skip={idea.submitted_by_id})
    return report


def _notify(report, title, skip):
    from src.notification_service import create_notification
    ChatMessageReport, _, _ = _models()
    label = dict(ChatMessageReport.REASON_CHOICES).get(report.reason, report.reason)
    skip = set(skip) | {report.reporter_id}
    for officer in moderators():
        if officer.pk in skip:
            continue
        create_notification(
            recipient=officer, notification_type='content_report', title=title,
            message=f'Reason: {label}. Review it under Reported content.',
            link=reverse('content_reports'), source_type='content_report', source_id=report.pk,
            push_title='Parliament', push_body='Reported content needs review')


def officer_queue(status='open'):
    _, ContentReport, _ = _models()
    qs = ContentReport.objects.select_related('member', 'idea', 'reporter', 'reviewed_by')
    return qs if status == 'all' else qs.filter(status=status)


def open_count():
    _, ContentReport, _ = _models()
    return ContentReport.objects.filter(status=ContentReport.STATUS_OPEN).count()


def _close_matching(report, officer, status, note):
    """Close this report and every other open one on the same profile or idea."""
    _, ContentReport, _ = _models()
    same = ContentReport.objects.filter(kind=report.kind, status=ContentReport.STATUS_OPEN)
    same = same.filter(idea=report.idea) if report.kind == ContentReport.KIND_IDEA else same.filter(
        member=report.member)
    return same.update(status=status, reviewed_by=officer, reviewed_at=timezone.now(),
                       action_note=_clean(note, NOTE_MAX))


def review(report, officer, action, note=''):
    """An officer dismisses the reports or removes the content. Returns reports closed."""
    _, ContentReport, _ = _models()
    if not getattr(officer, 'is_officer', False) and not getattr(officer, 'is_admin', False):
        raise ChatError('forbidden', 'Only officers can review reported content.', 403)
    if action not in REVIEW_ACTIONS:
        raise ChatError('invalid_input', f'action must be one of: {", ".join(REVIEW_ACTIONS)}')
    if report.status != ContentReport.STATUS_OPEN:
        raise ChatError('already_reviewed', 'This report was already reviewed.', 409)
    if action == 'remove':
        if report.kind == ContentReport.KIND_PROFILE:
            _clear_profile(report.member, officer)
        elif report.idea.hidden_at is None:
            report.idea.hidden_at = timezone.now()
            report.idea.save(update_fields=['hidden_at'])
        return _close_matching(report, officer, ContentReport.STATUS_ACTIONED, note)
    if report.kind == ContentReport.KIND_IDEA and report.idea.hidden_at is not None:
        # Hidden by the report count: dismissing the reports brings it back.
        report.idea.hidden_at = None
        report.idea.save(update_fields=['hidden_at'])
    return _close_matching(report, officer, ContentReport.STATUS_DISMISSED, note)


def _clear_profile(member, officer):
    from src.models import ActivityLog
    fields = ['about_me']
    member.about_me = ''
    if member.profile_picture:
        member.profile_picture.delete(save=False)
        member.profile_picture_removed_by_admin = True
        fields += ['profile_picture', 'profile_picture_removed_by_admin']
    member.save(update_fields=fields)
    ActivityLog.log_activity(
        action_type='profile_picture_removed', user=officer,
        description=f'{officer.get_display_name()} removed {member.get_display_name()}’s profile photo and '
                    'About me after a member report')


def report_json(report):
    member = report.member
    return {
        'id': report.id,
        'kind': report.kind,
        'reason': report.reason,
        'reason_label': report.get_reason_display(),
        'details': report.details,
        'status': report.status,
        'status_label': report.get_status_display(),
        'created_at': report.created_at.isoformat(),
        'reporter': report.reporter.get_display_name(),
        'member': ({'user_id': member.user_id, 'name': member.get_display_name()} if member else None),
        'about_me': (member.about_me if member and report.kind == 'profile' else ''),
        'idea': ({'id': report.idea.id, 'title': report.idea.title, 'description': report.idea.description,
                  'hidden': report.idea.hidden_at is not None} if report.idea_id else None),
        'reviewed_by': report.reviewed_by.get_display_name() if report.reviewed_by_id else None,
        'reviewed_at': report.reviewed_at.isoformat() if report.reviewed_at else None,
        'action_note': report.action_note,
    }
