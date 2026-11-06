"""
Admin v2 console, Messaging and Content: the Actions ▾ endpoints.

The console lists (admin_console_ui; templates/admin_v2/console/
manage_announcements.html, manage_events.html, manage_legislation.html,
push_subscriptions.html) let an admin tick rows and pick "Delete selected"
from the list header's Actions menu. console.js posts one ``ids`` value per
ticked row to one of these views. Each one:

* is POST only and behind the same admin-v2 gate as the single-row delete;
* re-reads every id from the database (the posted list is never trusted:
  unknown, malformed or duplicate ids are ignored);
* deletes and logs each row exactly the way the single-row view does
  (same ActivityLog action type and wording), so the audit trail can't tell
  a bulk delete from several single ones;
* caps one request at BULK_MAX rows.

The single-row delete views in src/view/admin_v2.py are unchanged.
"""
from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from src.models import ActivityLog, Announcement, Event, Legislation, PushSubscription
from src.view.admin_v2 import delete_push_subscription_row, require_admin_v2_auth

BULK_MAX = 200


def delete_selected_action(url_name, plural):
    """The list header's Actions ▾ entry for one of the views below
    (admin_v2/console/_list_header.html `actions`)."""
    from django.urls import reverse
    return [{'label': 'Delete selected', 'url': reverse(url_name), 'danger': True,
             'confirm': f'Delete the selected {plural}? This cannot be undone.'}]


def posted_ids(request):
    """The ticked row ids as ints: malformed ones dropped, duplicates folded,
    at most BULK_MAX."""
    ids = []
    for raw in request.POST.getlist('ids'):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in ids:
            ids.append(value)
    return ids[:BULK_MAX]


def _report(request, deleted, noun, plural=None):
    plural = plural or f'{noun}s'
    if deleted:
        messages.success(request, f'Deleted {deleted} {noun if deleted == 1 else plural}.')
    else:
        messages.error(request, f'Nothing was deleted. Tick the {plural} to delete first.')


def _delete_titled(request, model, action_type, label):
    """Delete each selected row of `model` (all have a ``title``) and log it
    like the single-row view: '<admin> deleted <label>: <title>'."""
    actor = request.user
    deleted = 0
    for obj in model.objects.filter(id__in=posted_ids(request)).order_by('id'):
        title = obj.title
        obj.delete()
        ActivityLog.log_activity(
            action_type=action_type,
            user=actor,
            description=f'{actor.get_display_name()} deleted {label}: {title}',
            request=request,
        )
        deleted += 1
    return deleted


@require_admin_v2_auth
@require_POST
def bulk_delete_announcements(request):
    deleted = _delete_titled(request, Announcement, 'announcement_deleted', 'announcement')
    _report(request, deleted, 'announcement')
    return redirect('admin_v2_manage_announcements')


@require_admin_v2_auth
@require_POST
def bulk_delete_events(request):
    deleted = _delete_titled(request, Event, 'event_deleted', 'event')
    _report(request, deleted, 'event')
    return redirect('admin_v2_manage_events')


@require_admin_v2_auth
@require_POST
def bulk_delete_legislation(request):
    deleted = _delete_titled(request, Legislation, 'legislation_deleted', 'legislation')
    _report(request, deleted, 'piece of legislation', 'pieces of legislation')
    return redirect('admin_v2_manage_legislation')


@require_admin_v2_auth
@require_POST
def bulk_delete_push_subscriptions(request):
    deleted = 0
    subs = PushSubscription.objects.filter(id__in=posted_ids(request)).select_related('user').order_by('id')
    for sub in subs:
        delete_push_subscription_row(request, request.user, sub)
        deleted += 1
    _report(request, deleted, 'push subscription')
    return redirect('admin_v2_push_subscriptions')
