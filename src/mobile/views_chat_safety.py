"""
Chat safety in the app (v4.4.0, App Store guideline 1.2): report a message,
mute a member, and the moderators' review list. The rules are
``src/chat_safety.py``, shared with the website.

* ``POST   chat/messages/<id>/report/`` ``{reason, details?}`` — reason is one
  of ``chat_safety.reasons()``. 409 ``already_reported`` on a second report.
* ``GET    chat/mutes/`` — the members I muted.
* ``POST   chat/mutes/`` ``{user_id}`` — mute a member. Their messages,
  unread counts and chat notifications disappear for me; nobody leaves a
  channel.
* ``DELETE chat/mutes/<user_id>/`` — unmute.
* ``GET    chat/reports/?status=open|dismissed|actioned|all`` — moderators
  (``can_moderate_chat``) only: reports with the reporter's name, only from
  channels the moderator can READ (``chat_safety.reviewable_channel_ids``). A
  moderator's own messages never appear in their list.
* ``POST   chat/reports/<id>/`` ``{action: dismiss|delete_message, note?}`` —
  reviewers of that channel only (403 ``forbidden`` otherwise); closes every open report on that message.

Behind the ``chats`` flag and the ``chat_index`` page toggle, like the rest of
the Chat tab (``views_chat._ChatBase``). Validation errors are
``invalid_input`` — never a code the app signs out on.
"""
from django.db.models import Count
from django.http import Http404
from rest_framework import exceptions
from rest_framework.response import Response

from src import chat_safety, chat_service
from src.mobile.views_chat import ChatRefused, _ChatBase, _int
from src.mobile.views_excuses_polls import BadRequest
from src.models import ChatMessage, ChatMessageReport, ParliamentUser

REVIEW_PAGE = 100
STATUSES = {'open', 'dismissed', 'actioned', 'all'}


def _refuse(exc):
    if exc.code == 'invalid_input':
        return BadRequest(str(exc), code='invalid_input')
    if exc.status == 404:
        return Http404(str(exc))
    return ChatRefused(exc.code, str(exc), exc.status)


def _body(request):
    return request.data if isinstance(request.data, dict) else {}


def _photo(user):
    return user.profile_picture.url if user.profile_picture else None


class ReportMessageView(_ChatBase):

    def post(self, request, message_id):
        message = (ChatMessage.objects.select_related('channel')
                   .filter(pk=message_id, is_deleted=False, channel__isnull=False,
                           channel__is_active=True).first())
        # Normal read access only (no admin override), as for every chat endpoint.
        if message is None or not message.channel.has_access(request.user):
            raise Http404
        body = _body(request)
        try:
            report = chat_safety.report_message(message, request.user, body.get('reason'),
                                                body.get('details') or '')
        except chat_service.ChatError as exc:
            raise _refuse(exc)
        return Response({'ok': True, 'report_id': report.id}, status=201)


class MutesView(_ChatBase):

    def get(self, request):
        return Response({'mutes': [{
            'user_id': b.muted.user_id,
            'name': b.muted.name,
            'profile_picture': _photo(b.muted),
            'muted_at': b.created_at.isoformat(),
        } for b in chat_safety.mutes_for(request.user)]})

    def post(self, request):
        user_id = _body(request).get('user_id')
        if not isinstance(user_id, (str, int)) or str(user_id).strip() == '':
            raise BadRequest('user_id is required.', code='invalid_input')
        target = ParliamentUser.objects.filter(pk=str(user_id)).only('user_id', 'name').first()
        try:
            _, created = chat_safety.mute(request.user, target)
        except chat_service.ChatError as exc:
            raise _refuse(exc)
        return Response({'ok': True, 'user_id': target.user_id, 'name': target.name},
                        status=201 if created else 200)


class UnmuteView(_ChatBase):

    def delete(self, request, user_id):
        chat_safety.unmute(request.user, user_id)
        return Response({'ok': True})


class _ModeratorBase(_ChatBase):

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not chat_safety.can_moderate_chat(request.user):
            raise exceptions.PermissionDenied('Chat moderators only.', code='forbidden')


class ReportListView(_ModeratorBase):

    def get(self, request):
        status = request.query_params.get('status') or 'open'
        if status not in STATUSES:
            raise BadRequest(f'status must be one of: {", ".join(sorted(STATUSES))}', code='invalid_input')
        qs = (chat_safety.reports_for_review(request.user, None if status == 'all' else status)
              .annotate(report_count=Count('message__reports')))
        rows = list(qs[:REVIEW_PAGE])
        return Response({
            'reports': [chat_safety.report_json(r) for r in rows],
            'open_count': chat_safety.open_report_count(request.user),
            'auto_hide_threshold': chat_safety.AUTO_HIDE_THRESHOLD,
        })


class ReportReviewView(_ModeratorBase):

    def post(self, request, report_id):
        body = _body(request)
        try:
            report = chat_safety.get_report_for_review(request.user, _int(report_id))
            closed = chat_safety.review_report(report, request.user, body.get('action'),
                                               body.get('note') or '')
        except chat_service.ChatError as exc:
            raise _refuse(exc)
        report = ChatMessageReport.objects.select_related(
            'message__channel', 'message__sender', 'reporter', 'reviewed_by').get(pk=report.pk)
        return Response({'report': chat_safety.report_json(report), 'closed': closed})
