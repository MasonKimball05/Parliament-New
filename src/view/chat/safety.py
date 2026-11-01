"""
Chat safety on the website (v4.4.0, App Store guideline 1.2): report a
message and mute a member from the chat page, the member's Muted members list,
and the moderators' Chat reports page. The rules are ``src/chat_safety.py``,
shared with the app (``src/mobile/views_chat_safety.py``).
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from src import chat_safety, chat_service
from src.feature_flag_decorators import require_feature_flag
from src.models import ChatMessage, ChatMessageReport, ParliamentUser

REVIEW_PAGE = 100


@login_required
@require_feature_flag('chats')
@require_POST
def chat_report_message(request, message_id):
    """JSON: report a message (``reason``, optional ``details``)."""
    message = (ChatMessage.objects.select_related('channel')
               .filter(pk=message_id, is_deleted=False, channel__isnull=False,
                       channel__is_active=True).first())
    if message is None or not message.channel.has_access(request.user):
        return JsonResponse({'error': 'Message not found'}, status=404)
    try:
        chat_safety.report_message(message, request.user, request.POST.get('reason'),
                                   request.POST.get('details', ''))
    except chat_service.ChatError as exc:
        return JsonResponse({'error': str(exc), 'code': exc.code}, status=exc.status)
    return JsonResponse({'success': True})


@login_required
@require_feature_flag('chats')
@require_POST
def chat_mute_member(request, user_id):
    """JSON: mute a member from the chat page."""
    target = ParliamentUser.objects.filter(pk=user_id).first()
    try:
        chat_safety.mute(request.user, target)
    except chat_service.ChatError as exc:
        return JsonResponse({'error': str(exc), 'code': exc.code}, status=exc.status)
    return JsonResponse({'success': True, 'user_id': target.user_id})


@login_required
@require_feature_flag('chats')
def chat_muted_members(request):
    return render(request, 'chat/muted_members.html', {
        'mutes': chat_safety.mutes_for(request.user),
    })


@login_required
@require_feature_flag('chats')
@require_POST
def chat_unmute_member(request, user_id):
    chat_safety.unmute(request.user, user_id)
    messages.success(request, 'Member unmuted. You will see their messages again.')
    return redirect('chat_muted_members')


@login_required
@require_feature_flag('chats')
def chat_reports(request):
    """The review list: reports in channels this member reviews and can read (chat_safety)."""
    if not chat_safety.can_moderate_chat(request.user):
        return HttpResponseForbidden('Only chat moderators can review reports.')
    status = request.GET.get('status', 'open')
    if status not in {'open', 'dismissed', 'actioned', 'all'}:
        status = 'open'
    reports = list(chat_safety.reports_for_review(request.user, None if status == 'all' else status)
                   .annotate(report_count=Count('message__reports'))[:REVIEW_PAGE])
    return render(request, 'chat/reports.html', {
        'reports': reports,
        'status': status,
        'statuses': [('open', 'Open'), ('dismissed', 'Dismissed'), ('actioned', 'Removed'), ('all', 'All')],
        'auto_hide_threshold': chat_safety.AUTO_HIDE_THRESHOLD,
    })


@login_required
@require_feature_flag('chats')
@require_POST
def chat_review_report(request, report_id):
    if not chat_safety.can_moderate_chat(request.user):
        return HttpResponseForbidden('Only chat moderators can review reports.')
    get_object_or_404(ChatMessageReport, pk=report_id)
    try:
        # Read access to the report's channel is required (chat_safety.can_review_channel).
        report = chat_safety.get_report_for_review(request.user, report_id)
        chat_safety.review_report(report, request.user, request.POST.get('action'),
                                  request.POST.get('note', ''))
    except chat_service.ChatError as exc:
        if exc.status == 403:
            return HttpResponseForbidden(str(exc))
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Message removed.' if request.POST.get('action') == 'delete_message'
                         else 'Report dismissed.')
    return redirect('chat_reports')
