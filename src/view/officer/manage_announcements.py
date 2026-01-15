from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from src.models import Announcement, UserAnnouncementView, ParliamentUser
from src.forms import AnnouncementForm
from src.decorators import log_function_call, officer_required
from src.notifications import send_announcement_notification
from django.utils import timezone
import base64

@login_required
@officer_required
@log_function_call
def manage_announcements(request):
    """View to manage all announcements"""
    announcements = Announcement.objects.all().order_by('-posted_at')
    return render(request, 'officer/manage_announcements.html', {
        'announcements': announcements
    })

@login_required
@officer_required
@log_function_call
def create_announcement(request):
    """View to create a new announcement"""
    if request.method == 'POST':
        form = AnnouncementForm(request.POST)
        if form.is_valid():
            announcement = form.save(commit=False)
            announcement.posted_by = request.user
            announcement.save()

            # Send email notifications if announcement is published now
            if announcement.is_published():
                try:
                    sent_count = send_announcement_notification(announcement)
                    messages.success(request, f'Announcement created and {sent_count} email notifications sent!')
                except Exception as e:
                    messages.warning(request, f'Announcement created but email notifications failed: {str(e)}')
            else:
                messages.success(request, 'Announcement created and scheduled for publication!')

            return redirect('manage_announcements')
    else:
        form = AnnouncementForm(initial={'is_active': True})

    return render(request, 'officer/create_announcement.html', {
        'form': form
    })

@login_required
@officer_required
@log_function_call
def edit_announcement(request, announcement_id):
    """View to edit an existing announcement"""
    announcement = get_object_or_404(Announcement, id=announcement_id)

    if request.method == 'POST':
        form = AnnouncementForm(request.POST, instance=announcement)
        if form.is_valid():
            form.save()
            messages.success(request, 'Announcement updated successfully!')
            return redirect('manage_announcements')
    else:
        form = AnnouncementForm(instance=announcement)

    return render(request, 'officer/edit_announcement.html', {
        'form': form,
        'announcement': announcement
    })

@login_required
@officer_required
@log_function_call
def delete_announcement(request, announcement_id):
    """View to delete an announcement"""
    announcement = get_object_or_404(Announcement, id=announcement_id)

    if request.method == 'POST':
        announcement.delete()
        messages.success(request, 'Announcement deleted successfully!')
        return redirect('manage_announcements')

    return render(request, 'officer/delete_announcement.html', {
        'announcement': announcement
    })

@login_required
@officer_required
@log_function_call
def toggle_announcement_status(request, announcement_id):
    """View to toggle announcement active status"""
    announcement = get_object_or_404(Announcement, id=announcement_id)
    announcement.is_active = not announcement.is_active
    announcement.save()

    status = "activated" if announcement.is_active else "deactivated"
    messages.success(request, f'Announcement "{announcement.title}" has been {status}!')
    return redirect('manage_announcements')


def track_email_view(request, announcement_id, user_id):
    """
    Track when an announcement is viewed from email.
    Returns a 1x1 transparent pixel.
    This view does not require login since it's loaded as an image in emails.
    """
    # 1x1 transparent GIF
    PIXEL_GIF = base64.b64decode(
        'R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'
    )

    try:
        announcement = Announcement.objects.get(id=announcement_id)
        user = ParliamentUser.objects.get(user_id=user_id)

        # Record or update the view
        view, created = UserAnnouncementView.objects.get_or_create(
            user=user,
            announcement=announcement,
            defaults={'view_source': 'email'}
        )

        # If already viewed on site, update to show email view happened
        if not created and view.view_source == 'site':
            # Keep as site view but note they also saw email
            pass
    except (Announcement.DoesNotExist, ParliamentUser.DoesNotExist):
        pass

    return HttpResponse(PIXEL_GIF, content_type='image/gif')


@login_required
@officer_required
def announcement_stats(request, announcement_id):
    """View detailed statistics for an announcement"""
    announcement = get_object_or_404(Announcement, id=announcement_id)
    stats = announcement.get_view_stats()
    viewers = announcement.get_viewers()

    # Get users who haven't viewed
    viewed_user_ids = viewers.values_list('user_id', flat=True)
    target_users = ParliamentUser.objects.filter(member_status='Active')
    if announcement.visible_to:
        visible_types = list(announcement.visible_to)
        if 'Member' in visible_types:
            visible_types.extend(['Chair', 'Officer'])
        target_users = target_users.filter(member_type__in=visible_types)

    non_viewers = target_users.exclude(user_id__in=viewed_user_ids)

    context = {
        'announcement': announcement,
        'stats': stats,
        'viewers': viewers,
        'non_viewers': non_viewers,
    }
    return render(request, 'officer/announcement_stats.html', context)
