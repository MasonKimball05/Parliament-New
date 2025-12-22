from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from src.models import Announcement
from src.forms import AnnouncementForm
from src.decorators import log_function_call

def is_officer_or_chair(user):
    return user.member_type in ['Chair', 'Officer']

@login_required
@user_passes_test(is_officer_or_chair)
@log_function_call
def manage_announcements(request):
    """View to manage all announcements"""
    announcements = Announcement.objects.all().order_by('-posted_at')
    return render(request, 'officer/manage_announcements.html', {
        'announcements': announcements
    })

@login_required
@user_passes_test(is_officer_or_chair)
@log_function_call
def create_announcement(request):
    """View to create a new announcement"""
    if request.method == 'POST':
        form = AnnouncementForm(request.POST)
        if form.is_valid():
            announcement = form.save(commit=False)
            announcement.posted_by = request.user
            announcement.save()
            messages.success(request, 'Announcement created successfully!')
            return redirect('manage_announcements')
    else:
        form = AnnouncementForm(initial={'is_active': True})

    return render(request, 'officer/create_announcement.html', {
        'form': form
    })

@login_required
@user_passes_test(is_officer_or_chair)
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
@user_passes_test(is_officer_or_chair)
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
@user_passes_test(is_officer_or_chair)
@log_function_call
def toggle_announcement_status(request, announcement_id):
    """View to toggle announcement active status"""
    announcement = get_object_or_404(Announcement, id=announcement_id)
    announcement.is_active = not announcement.is_active
    announcement.save()

    status = "activated" if announcement.is_active else "deactivated"
    messages.success(request, f'Announcement "{announcement.title}" has been {status}!')
    return redirect('manage_announcements')
