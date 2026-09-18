"""
Notification center views: full page, dropdown API, mark-read, mark-all-read, delete.
"""
from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_GET
from django.core.paginator import Paginator
from django.utils import timezone
from src.models import Notification, Announcement, UserAnnouncementView
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)


def _invalidate_notification_cache(user):
    """Clear the cached notification count for a user."""
    cache.delete(f'notif_count_{user.pk}')


@login_required
def notifications_page(request):
    """Full notifications page with filtering and pagination."""
    filter_type = request.GET.get('filter', 'all')
    notifications = Notification.objects.filter(recipient=request.user)

    if filter_type == 'unread':
        notifications = notifications.filter(is_read=False)
    elif filter_type in dict(Notification.NOTIFICATION_TYPES):
        notifications = notifications.filter(notification_type=filter_type)

    paginator = Paginator(notifications, 25)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    unread_count = Notification.objects.filter(
        recipient=request.user, is_read=False
    ).count()

    context = {
        'page_obj': page_obj,
        'filter_type': filter_type,
        'unread_count': unread_count,
        'notification_types': Notification.NOTIFICATION_TYPES,
    }
    return render(request, 'notifications.html', context)


@login_required
@require_GET
def notifications_dropdown_api(request):
    """JSON API for the navbar dropdown. Returns latest 10 notifications + unread count."""
    notifications = Notification.objects.filter(
        recipient=request.user
    ).order_by('-created_at')[:10]

    unread_count = Notification.objects.filter(
        recipient=request.user, is_read=False
    ).count()

    items = []
    for n in notifications:
        items.append({
            'id': n.id,
            'type': n.notification_type,
            'title': n.title,
            'message': n.message[:100] if n.message else '',
            'link': n.link,
            'is_read': n.is_read,
            'created_at': n.created_at.isoformat(),
            'time_ago': _time_ago(n.created_at),
        })

    return JsonResponse({
        'notifications': items,
        'unread_count': unread_count,
    })


@login_required
@require_POST
def mark_notification_read(request, notification_id):
    """Mark a single notification as read."""
    try:
        notification = Notification.objects.get(id=notification_id, recipient=request.user)
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=['is_read', 'read_at'])
            _record_announcement_view(notification, request.user)
            _invalidate_notification_cache(request.user)
        return JsonResponse({'success': True})
    except Notification.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Notification not found'}, status=404)


@login_required
@require_POST
def mark_all_notifications_read(request):
    """Mark all of the user's unread notifications as read."""
    unread = Notification.objects.filter(
        recipient=request.user, is_read=False
    )
    # Record announcement views before bulk-updating. Batch-fetch every
    # referenced Announcement in one query (Announcement.objects.in_bulk)
    # rather than one `Announcement.objects.get(id=...)` per notification —
    # a member with a backlog of unread announcement notifications (e.g.
    # after being away a while) fired that single-row SELECT once per
    # notification on one "mark all read" click (caught by the dev-mode
    # query monitor, 62x on a single request). ensure_target_audience_
    # snapshots() is the same batched-vs-per-object shape this model
    # already uses elsewhere (see its own docstring).
    announcement_notifs = list(unread.filter(notification_type='announcement'))
    if announcement_notifs:
        source_ids = {n.source_id for n in announcement_notifs if n.source_id}
        announcements_by_id = Announcement.objects.in_bulk(source_ids)
        Announcement.ensure_target_audience_snapshots(announcements_by_id.values())
        for notification in announcement_notifs:
            announcement = announcements_by_id.get(notification.source_id)
            if announcement is not None:
                _record_announcement_view(notification, request.user, announcement=announcement)
    count = unread.update(is_read=True, read_at=timezone.now())
    if count > 0:
        _invalidate_notification_cache(request.user)
    return JsonResponse({'success': True, 'count': count})


@login_required
@require_POST
def delete_notification(request, notification_id):
    """Delete a single notification."""
    try:
        notification = Notification.objects.get(id=notification_id, recipient=request.user)
        was_unread = not notification.is_read
        notification.delete()
        if was_unread:
            _invalidate_notification_cache(request.user)
        return JsonResponse({'success': True})
    except Notification.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Notification not found'}, status=404)


def _record_announcement_view(notification, user, announcement=None):
    """
    If the notification is for an announcement, record a UserAnnouncementView
    for stats.

    `announcement` may be passed in by a caller that's already fetched it
    (mark_all_notifications_read batches every referenced Announcement in
    one query rather than calling this once per notification) — avoids
    re-fetching a row the caller already has. mark_notification_read (a
    single click, not a loop) still leaves this to fetch its own.
    """
    if notification.notification_type != 'announcement' or not notification.source_id:
        return
    if announcement is None:
        try:
            announcement = Announcement.objects.get(id=notification.source_id)
        except Announcement.DoesNotExist:
            return
    announcement.ensure_target_audience_snapshot()
    UserAnnouncementView.objects.get_or_create(
        user=user,
        announcement=announcement,
        defaults={
            'view_source': 'site',
            'dismissed': True,
            'counted_in_target': announcement.is_in_target_audience(user),
        },
    )


def _time_ago(dt):
    """Return a human-readable time-ago string."""
    now = timezone.now()
    diff = now - dt
    seconds = int(diff.total_seconds())

    if seconds < 60:
        return 'just now'
    elif seconds < 3600:
        mins = seconds // 60
        return f'{mins}m ago'
    elif seconds < 86400:
        hours = seconds // 3600
        return f'{hours}h ago'
    elif seconds < 604800:
        days = seconds // 86400
        return f'{days}d ago'
    else:
        return dt.strftime('%b %d')
