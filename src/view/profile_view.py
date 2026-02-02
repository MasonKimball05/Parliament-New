from ..models import *
from ..decorators import *
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import render, redirect
from django.contrib.auth import update_session_auth_hash
from django.core.exceptions import ValidationError
from src.feature_flag_decorators import require_page_enabled
import logging

logger = logging.getLogger('function_calls')

@login_required
@require_page_enabled('profile')
@log_function_call
def profile_view(request):
    user = request.user

    # Check if profile picture was removed by admin
    if user.profile_picture_removed_by_admin:
        messages.warning(
            request,
            "Your profile picture was removed by an administrator. "
            "Please upload a new, appropriate profile picture."
        )
        user.profile_picture_removed_by_admin = False
        user.save()

    profile_form_submitted = 'profile_submit' in request.POST
    password_form_submitted = 'password_submit' in request.POST
    profile_picture_submitted = 'profile_picture_submit' in request.POST
    notification_prefs_submitted = 'notification_prefs_submit' in request.POST

    password_form = PasswordChangeForm(user)

    if request.method == 'POST':
        if profile_picture_submitted:
            # Handle profile picture removal
            if 'remove_profile_picture' in request.POST:
                if user.profile_picture:
                    user.profile_picture.delete()
                    user.save()
                    logger.info(f"{user.username} removed their profile picture")
                    messages.success(request, "Profile picture removed successfully.")
                else:
                    messages.info(request, "No profile picture to remove.")
                return redirect('profile')

            # Handle profile picture upload
            elif request.FILES.get('profile_picture'):
                try:
                    # Delete old profile picture if exists
                    if user.profile_picture:
                        user.profile_picture.delete()

                    user.profile_picture = request.FILES['profile_picture']
                    user.save()
                    logger.info(f"{user.username} uploaded a new profile picture")
                    messages.success(request, "Profile picture uploaded successfully.")
                except ValidationError as e:
                    messages.error(request, str(e))
                return redirect('profile')
            else:
                messages.warning(request, "No file selected.")
                return redirect('profile')

        elif profile_form_submitted:
            new_username = request.POST.get('username')
            new_preferred_name = request.POST.get('preferred_name', '').strip()
            new_email = request.POST.get('email', '').strip()

            changes_made = False

            # Update username if changed
            if new_username and new_username != user.username:
                logger.info(f"{user.username} changed username to {new_username}")
                user.username = new_username
                changes_made = True

            # Update preferred name if changed (allow empty string to clear it)
            if new_preferred_name != user.preferred_name:
                old_preferred = user.preferred_name or "(not set)"
                logger.info(f"{user.username} changed preferred name from '{old_preferred}' to '{new_preferred_name or '(not set)'}'")
                user.preferred_name = new_preferred_name if new_preferred_name else None
                changes_made = True

            # Update email if changed (allow empty string to clear it)
            current_email = user.email or ''
            if new_email != current_email:
                # Check if email is already taken by another user
                if new_email and ParliamentUser.objects.filter(email=new_email).exclude(user_id=user.user_id).exists():
                    messages.error(request, "This email address is already in use by another user.")
                    return redirect('profile')

                old_email = user.email or "(not set)"
                logger.info(f"{user.username} changed email from '{old_email}' to '{new_email or '(not set)'}'")
                user.email = new_email if new_email else None
                changes_made = True

            if changes_made:
                user.save()
                messages.success(request, "Profile updated successfully.")
            else:
                messages.info(request, "No changes were made.")

            return redirect('profile')

        elif notification_prefs_submitted:
            prefs, _ = UserPreferences.objects.get_or_create(user=user)
            prefs.notify_announcements = request.POST.get('notify_announcements') == 'on'
            prefs.notify_legislation = request.POST.get('notify_legislation') == 'on'
            prefs.notify_events = request.POST.get('notify_events') == 'on'
            prefs.save(update_fields=['notify_announcements', 'notify_legislation', 'notify_events'])
            logger.info(f"{user.username} updated notification preferences")
            messages.success(request, "Notification preferences updated.")
            return redirect('profile')

        elif password_form_submitted:
            password_form = PasswordChangeForm(user, request.POST)
            if password_form.is_valid():
                # Logs new changes
                logger.info(f"{request.user.username} changed their password")

                user = password_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, "Password changed successfully.")
                return redirect('profile')
            else:
                messages.error(request, "Please correct the errors below.")

    # Get or create notification preferences
    notif_prefs, _ = UserPreferences.objects.get_or_create(user=user)

    return render(request, 'profile.html', {
        'user': user,
        'password_form': password_form,
        'notif_prefs': notif_prefs,
    })