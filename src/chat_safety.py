"""
Chat safety: reporting messages, muting members, moderator review (v4.4.0).

Apple's App Store guideline 1.2 (user-generated content) asks for a way to
report objectionable content, a way to block abusive users, and someone who
acts on reports. The chapter's word for blocking is **mute** (owner's call,
10-31-26): the mechanics are a block, named for what the member sees. This module is the one implementation behind the website
(``src/view/chat/safety.py``) and the app (``src/mobile/views_chat_safety.py``).

**Muting** hides the muted member's content *for the muter only*: their
messages in channel lists, in unread counts and badges, in chat pushes and
@mention notifications, and on the live socket. It does NOT remove anyone from
a channel — chapter and committee channels are official, so both members keep
their access; the muter just stops seeing the other's messages. The muted
member is never told.

**Reporting** records a ``ChatMessageReport`` and notifies the chat moderators
who can READ its channel (``channel_reviewers``: officers and admins, or the
committee's chairs where no officer can read it, e.g. Kai) with an in-app
Notification. Once
``AUTO_HIDE_THRESHOLD`` distinct members have open reports on one message it
is hidden from everyone (``hidden_pending_review``) until a moderator reviews
it. Who reported is shown to moderators only; a reported member never sees
reports about their own messages, even a moderator (their own messages are
left out of their review list, and they cannot review them).

**Review**: a moderator dismisses (the message is restored if it was
auto-hidden) or deletes the message through ``chat_service.delete_message`` —
the same soft delete and websocket broadcast as every other delete. Either
decision closes every open report on that message.
"""
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from src.chat_service import ChatError

#: A message reported by this many DISTINCT members (open reports) is hidden
#: from every channel view pending moderator review, so a pile-on of reports
#: takes something down before an officer gets to it, while a single report
#: (which could be a grudge) does not.
AUTO_HIDE_THRESHOLD = 3
DETAILS_MAX = 1000
NOTE_MAX = 500
REVIEW_ACTIONS = ('dismiss', 'delete_message')
MUTES_CACHE_SECONDS = 300


def _models():
    from src.models import ChatMessageReport, ChatUserMute
    return ChatMessageReport, ChatUserMute


# ── Who moderates ────────────────────────────────────────────────────────────

# CONFIDENTIALITY: a report carries the message text and the reporter's
# identity, so a reviewer must be able to READ the channel (ChatChannel's one
# access rule, `has_access`, no admin "View All" override). Per channel:
#
# * officers and admins who can read it review its reports;
# * if none can (e.g. Kai's committee channel, membership-gated), the channel's
#   own moderators do: the chairs of its committee (`Committee.chairs`) who can
#   read it. (Admins who can read it are already in the first group.)
#
# A channel with neither has nobody to review; its reports still count toward
# the auto-hide. See docs/CONFIDENTIALITY_MATRIX.md, "Chat".

def moderators():
    """Active officers and admins — the chapter-wide chat moderators."""
    from django.db.models import Q
    from src.constants import MemberType
    from src.models import ParliamentUser
    return ParliamentUser.objects.filter(
        Q(member_type=MemberType.OFFICER) | Q(is_admin=True), member_status='Active')


def _officers_can_read(channel, memo=None):
    if memo is not None and channel.pk in memo:
        return memo[channel.pk]
    answer = channel.access_type == 'open' or any(channel.has_access(u) for u in moderators())
    if memo is not None:
        memo[channel.pk] = answer
    return answer


def channel_reviewers(channel):
    """Who reviews reports in ``channel`` (and is notified of them)."""
    if not channel.is_active:
        return []
    officers = [u for u in moderators() if channel.has_access(u)]
    if officers:
        return officers
    if channel.committee_id:
        return [u for u in channel.committee.chairs.filter(member_status='Active')
                if channel.has_access(u)]
    return []


def can_review_channel(user, channel, memo=None):
    """May ``user`` see and act on reports in ``channel``? See the rule above."""
    if not getattr(user, 'is_authenticated', False) or channel is None:
        return False
    if not channel.has_access(user):
        return False
    if user.is_officer:
        return True
    return bool(channel.committee_id
                and channel.committee.chairs.filter(pk=user.pk).exists()
                and not _officers_can_read(channel, memo))


def reviewable_channel_ids(user):
    """The channels whose reports ``user`` may review."""
    from src.models import ChatChannel
    if not getattr(user, 'is_authenticated', False):
        return set()
    if user.is_officer:
        channels = list(ChatChannel.objects.filter(is_active=True))
    else:
        channels = list(ChatChannel.objects.filter(
            is_active=True, committee__chairs=user).select_related('committee'))
        if not channels:
            return set()
    readable = ChatChannel.access_map(channels, user)
    if user.is_officer:
        return {c.pk for c in channels if readable[c.pk]}
    memo = {}
    return {c.pk for c in channels if readable[c.pk] and not _officers_can_read(c, memo)}


def can_moderate_chat(user):
    """Does ``user`` review chat reports anywhere? (Officers/admins; chairs for their channel.)"""
    if not getattr(user, 'is_authenticated', False):
        return False
    return bool(user.is_officer or reviewable_channel_ids(user))


# ── Muting ───────────────────────────────────────────────────────────────────

def _mutes_key(user_pk):
    return f'chat_mutes_{user_pk}'


def muted_ids(user):
    """The pks (``user_id``) of members ``user`` has muted. Cached; cleared on change."""
    pk = getattr(user, 'pk', None)
    if pk is None:
        return frozenset()
    key = _mutes_key(pk)
    ids = cache.get(key)
    if ids is None:
        _, ChatUserMute = _models()
        ids = frozenset(ChatUserMute.objects.filter(muter_id=pk).values_list('muted_id', flat=True))
        cache.set(key, ids, MUTES_CACHE_SECONDS)
    return ids


def muters_of(sender_pk):
    """The pks of members who muted ``sender_pk`` (they get no chat pushes from them)."""
    _, ChatUserMute = _models()
    return set(ChatUserMute.objects.filter(muted_id=sender_pk).values_list('muter_id', flat=True))


def _changed(user):
    cache.delete(_mutes_key(user.pk))
    cache.delete(f'chat_unread_{user.pk}')


def mute(muter, muted):
    """``muter`` mutes ``muted``. Idempotent. Returns (mute, created)."""
    _, ChatUserMute = _models()
    if muted is None:
        raise ChatError('not_found', 'Member not found', 404)
    if muted.pk == muter.pk:
        raise ChatError('self_mute', "You can't mute yourself.")
    row, created = ChatUserMute.objects.get_or_create(muter=muter, muted=muted)
    _changed(muter)
    return row, created


def unmute(muter, muted_pk):
    _, ChatUserMute = _models()
    deleted, _ = ChatUserMute.objects.filter(muter=muter, muted_id=muted_pk).delete()
    _changed(muter)
    return bool(deleted)


def mutes_for(user):
    _, ChatUserMute = _models()
    return (ChatUserMute.objects.filter(muter=user).select_related('muted')
            .only('id', 'created_at', 'muted__user_id', 'muted__name', 'muted__profile_picture'))


def visible_to(qs, viewer):
    """
    Narrow a ChatMessage queryset to what ``viewer`` should see: no messages
    hidden pending review, nothing from members they muted. Works on
    subqueries (``OuterRef``) too.
    """
    qs = qs.filter(hidden_pending_review=False)
    if viewer is not None and getattr(viewer, 'pk', None) is not None:
        # A subquery, not muted_ids(): no extra query on the pages that count
        # unread chat (every page, via the context processor).
        _, ChatUserMute = _models()
        qs = qs.exclude(sender_id__in=ChatUserMute.objects.filter(muter_id=viewer.pk).values('muted_id'))
    return qs


# ── Reporting ────────────────────────────────────────────────────────────────

def reasons():
    ChatMessageReport, _ = _models()
    return [{'value': v, 'label': label} for v, label in ChatMessageReport.REASON_CHOICES]


def _clean(text, limit):
    return (text.strip() if isinstance(text, str) else '')[:limit]


def report_message(message, reporter, reason, details=''):
    """
    ``reporter`` reports ``message``. The caller has checked they can read its
    channel. Refuses their own message, a deleted one, a bad reason and a
    second report from the same member. Returns the report.
    """
    ChatMessageReport, _ = _models()
    if message.is_deleted or message.channel_id is None:
        raise ChatError('not_found', 'Message not found', 404)
    if message.sender_id == reporter.pk:
        raise ChatError('own_message', "You can't report your own message.")
    if reason not in {c[0] for c in ChatMessageReport.REASON_CHOICES}:
        raise ChatError('invalid_input', 'Choose a reason for the report.')
    try:
        with transaction.atomic():
            report = ChatMessageReport.objects.create(
                message=message, reporter=reporter, reason=reason,
                details=_clean(details, DETAILS_MAX))
    except IntegrityError:
        raise ChatError('already_reported', 'You already reported this message.', 409)

    open_reports = ChatMessageReport.objects.filter(message=message, status=ChatMessageReport.STATUS_OPEN)
    reporters = open_reports.values('reporter').distinct().count()
    hidden_now = False
    if reporters >= AUTO_HIDE_THRESHOLD and not message.hidden_pending_review:
        # See AUTO_HIDE_THRESHOLD: hidden from everyone until reviewed.
        message.hidden_pending_review = True
        message.save(update_fields=['hidden_pending_review'])
        hidden_now = True
        from src.chat_service import ws_broadcast
        ws_broadcast(f'chat_{message.channel_id}', {'type': 'chat.delete', 'message_id': message.id})
    if reporters == 1 or hidden_now:
        _notify_moderators(message, reporter, reason, hidden_now)
    return report


def _notify_moderators(message, reporter, reason, hidden_now):
    """In-app notification to the channel's reviewers (``channel_reviewers``), except the
    reported member and the reporter."""
    from src.notification_service import create_notification
    ChatMessageReport, _ = _models()
    label = dict(ChatMessageReport.REASON_CHOICES).get(reason, reason)
    title = ('A reported chat message was hidden pending review' if hidden_now
             else 'A chat message was reported')
    link = reverse('chat_reports')
    for mod in channel_reviewers(message.channel):
        if mod.pk in (message.sender_id, reporter.pk):
            continue
        create_notification(
            recipient=mod,
            notification_type='chat_report',
            title=title,
            message=f'Reason: {label}. Review it in Chat reports.',
            link=link,
            source_type='chatmessage',
            source_id=message.id,
            push_title='Parliament',
            push_body='A chat message needs review',
        )


# ── Review ───────────────────────────────────────────────────────────────────

def reports_for_review(moderator, status='open'):
    """
    Reports a moderator may review: only in channels they review
    (``reviewable_channel_ids``, which requires read access), never ones
    about their own messages.
    """
    ChatMessageReport, _ = _models()
    qs = (ChatMessageReport.objects
          .select_related('message__channel', 'message__sender', 'reporter', 'reviewed_by')
          .filter(message__channel_id__in=reviewable_channel_ids(moderator))
          .exclude(message__sender=moderator))
    if status:
        qs = qs.filter(status=status)
    return qs


def open_report_count(moderator):
    return reports_for_review(moderator).count()


def get_report_for_review(moderator, report_id):
    """The report, or 403 ``forbidden`` if it isn't one ``moderator`` may review."""
    ChatMessageReport, _ = _models()
    report = (ChatMessageReport.objects.select_related('message__channel__committee', 'message__sender')
              .filter(pk=report_id).first())
    if report is None:
        raise ChatError('not_found', 'Report not found', 404)
    if not can_review_channel(moderator, report.message.channel):
        raise ChatError('forbidden', 'You can’t review reports in this channel.', 403)
    return report


def review_report(report, moderator, action, note=''):
    """
    Dismiss ``report`` or delete its message. Closes every open report on the
    same message. Returns the number of reports closed.
    """
    from src import chat_service
    ChatMessageReport, _ = _models()
    if not can_review_channel(moderator, report.message.channel):
        raise ChatError('forbidden', 'You can’t review reports in this channel.', 403)
    if report.message.sender_id == moderator.pk:
        raise ChatError('forbidden', "You can't review a report about your own message.", 403)
    if action not in REVIEW_ACTIONS:
        raise ChatError('invalid_input', f'action must be one of: {", ".join(REVIEW_ACTIONS)}')
    if report.status != ChatMessageReport.STATUS_OPEN:
        raise ChatError('already_reviewed', 'This report was already reviewed.', 409)

    message = report.message
    if action == 'delete_message':
        if not message.is_deleted:
            # The one delete path (soft delete + websocket broadcast).
            chat_service.delete_message(message.channel, message, moderator, as_moderator=True)
        status = ChatMessageReport.STATUS_ACTIONED
    else:
        if message.hidden_pending_review:
            message.hidden_pending_review = False
            message.save(update_fields=['hidden_pending_review'])
        status = ChatMessageReport.STATUS_DISMISSED
    return ChatMessageReport.objects.filter(message=message, status=ChatMessageReport.STATUS_OPEN).update(
        status=status, reviewed_by=moderator, reviewed_at=timezone.now(), action_note=_clean(note, NOTE_MAX))


def report_json(report):
    """A report as a moderator sees it (reporter included — moderators only)."""
    m = report.message
    return {
        'id': report.id,
        'reason': report.reason,
        'reason_label': report.get_reason_display(),
        'details': report.details,
        'status': report.status,
        'created_at': report.created_at.isoformat(),
        'reporter': {'id': report.reporter.user_id, 'name': report.reporter.name},
        'reviewed_by': report.reviewed_by.name if report.reviewed_by else None,
        'reviewed_at': report.reviewed_at.isoformat() if report.reviewed_at else None,
        'action_note': report.action_note,
        'message': {
            'id': m.id,
            'text': m.message,
            'created_at': m.created_at.isoformat(),
            'sender': {'id': m.sender.user_id, 'name': m.sender.name},
            'channel': {'id': m.channel_id, 'name': m.channel.name if m.channel else ''},
            'is_deleted': m.is_deleted,
            'hidden_pending_review': m.hidden_pending_review,
            'report_count': getattr(report, 'report_count', None),
        },
    }
