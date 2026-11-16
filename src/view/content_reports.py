"""
Reported profiles and ideas (11-16-26, App Store guideline 1.2): the officers'
review page. Rules in src/content_reports.py; the app's screen is
src/mobile/views_content_reports.py.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from src import content_reports
from src.chat_service import ChatError
from src.models import ContentReport

STATUSES = [('open', 'Open'), ('dismissed', 'Dismissed'), ('actioned', 'Removed'), ('all', 'All')]


def _can_review(user):
    return bool(getattr(user, 'is_officer', False) or getattr(user, 'is_admin', False))


@login_required
def content_report_list(request):
    if not _can_review(request.user):
        return HttpResponseForbidden('Only officers can review reported content.')
    status = request.GET.get('status', 'open')
    if status not in dict(STATUSES):
        status = 'open'
    return render(request, 'officer/content_reports.html', {
        'reports': list(content_reports.officer_queue(status)[:100]),
        'status': status,
        'statuses': STATUSES,
        'auto_hide_threshold': content_reports.AUTO_HIDE_THRESHOLD,
    })


@login_required
@require_POST
def content_report_review(request, report_id):
    if not _can_review(request.user):
        return HttpResponseForbidden('Only officers can review reported content.')
    report = get_object_or_404(ContentReport.objects.select_related('member', 'idea'), pk=report_id)
    action = request.POST.get('action')
    try:
        content_reports.review(report, request.user, action, request.POST.get('note', ''))
    except ChatError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, 'Removed.' if action == 'remove' else 'Report dismissed.')
    return redirect('content_reports')
