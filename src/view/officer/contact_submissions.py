import logging
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from src.decorators import officer_or_advisor_required
from src.feature_flag_decorators import require_page_enabled
from src.models import ContactSubmission

logger = logging.getLogger('function_calls')


@login_required
@officer_or_advisor_required
@require_page_enabled('officer_home')
def contact_submissions_view(request):
    submissions = ContactSubmission.objects.all()
    unread_count = submissions.filter(is_read=False).count()
    return render(request, 'officer/contact_submissions.html', {
        'submissions': submissions,
        'unread_count': unread_count,
    })


@login_required
@officer_or_advisor_required
@require_POST
def mark_contact_read(request, pk):
    submission = get_object_or_404(ContactSubmission, pk=pk)
    submission.is_read = True
    submission.save(update_fields=['is_read'])
    logger.info(f"{request.user.username} marked contact submission {pk} as read")
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({'ok': True})
    return redirect('contact_submissions')


@login_required
@officer_or_advisor_required
@require_POST
def mark_all_contact_read(request):
    count = ContactSubmission.objects.filter(is_read=False).update(is_read=True)
    logger.info(f"{request.user.username} marked all {count} contact submissions as read")
    return redirect('contact_submissions')
