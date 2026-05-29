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
    extended_profile_submitted = 'extended_profile_submit' in request.POST
    role_history_add = 'role_history_add_submit' in request.POST
    role_history_delete = 'role_history_delete_submit' in request.POST
    custom_social_add = 'custom_social_add_submit' in request.POST
    custom_social_delete = 'custom_social_delete_submit' in request.POST
    initiation_chapter_add = 'initiation_chapter_add_submit' in request.POST
    initiation_chapter_delete = 'initiation_chapter_delete_submit' in request.POST

    password_form = PasswordChangeForm(user)

    if request.method == 'POST':
        if profile_picture_submitted:
            action = request.POST.get('action', '')

            # Handle profile picture removal
            if action == 'remove' or 'remove_profile_picture' in request.POST:
                if user.profile_picture:
                    user.profile_picture.delete()
                    user.save()
                    logger.info(f"{user.username} removed their profile picture")
                    ActivityLog.log_activity(
                        action_type='profile_picture_changed',
                        user=request.user,
                        description=f'{user.name} removed their profile picture',
                        request=request,
                        object_type='ParliamentUser',
                        object_id=user.pk,
                        object_repr=user.name,
                        metadata={'action': 'removed'},
                    )
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
                    ActivityLog.log_activity(
                        action_type='profile_picture_changed',
                        user=request.user,
                        description=f'{user.name} uploaded a new profile picture',
                        request=request,
                        object_type='ParliamentUser',
                        object_id=user.pk,
                        object_repr=user.name,
                        metadata={'action': 'uploaded', 'filename': request.FILES['profile_picture'].name},
                    )
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
            new_phone = request.POST.get('phone_number', '').strip()

            changes_made = False
            changes_list = []  # audit trail

            # Update username if changed
            if new_username and new_username != user.username:
                changes_list.append({'field': 'username', 'old': user.username, 'new': new_username})
                logger.info(f"{user.username} changed username to {new_username}")
                user.username = new_username
                changes_made = True

            # Update preferred name if changed (allow empty string to clear it)
            if new_preferred_name != user.preferred_name:
                old_preferred = user.preferred_name or "(not set)"
                changes_list.append({'field': 'preferred_name', 'old': user.preferred_name or '', 'new': new_preferred_name})
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

                changes_list.append({'field': 'email', 'old': user.email or '', 'new': new_email})
                old_email = user.email or "(not set)"
                logger.info(f"{user.username} changed email from '{old_email}' to '{new_email or '(not set)'}'")
                user.email = new_email if new_email else None
                changes_made = True

            # Update phone number if changed (allow empty string to clear it)
            current_phone = user.phone_number or ''
            if new_phone != current_phone:
                changes_list.append({'field': 'phone', 'old': user.phone_number or '', 'new': new_phone})
                logger.info(f"{user.username} updated phone number")
                user.phone_number = new_phone if new_phone else ''
                changes_made = True

            if changes_made:
                user.save()
                ActivityLog.log_activity(
                    action_type='profile_updated',
                    user=request.user,
                    description=f'{user.name} updated their profile ({", ".join(c["field"] for c in changes_list)})',
                    request=request,
                    object_type='ParliamentUser',
                    object_id=user.pk,
                    object_repr=user.name,
                    metadata={'changes': changes_list},
                )
                messages.success(request, "Profile updated successfully.")
            else:
                messages.info(request, "No changes were made.")

            return redirect('profile')

        elif notification_prefs_submitted:
            prefs, _ = UserPreferences.objects.get_or_create(user=user)
            current_prefs = prefs.prefs or UserPreferences.get_defaults()
            current_notifications = current_prefs.get('notifications', UserPreferences.get_defaults()['notifications'])
            new_notifications = {
                'announcements': request.POST.get('notify_announcements') == 'on',
                'legislation': request.POST.get('notify_legislation') == 'on',
                'events': request.POST.get('notify_events') == 'on',
            }
            current_notifications.update(new_notifications)
            prefs.prefs = {**current_prefs, 'notifications': current_notifications}
            prefs.save(update_fields=['prefs'])
            logger.info(f"{user.username} updated notification preferences")
            ActivityLog.log_activity(
                action_type='preferences_updated',
                user=request.user,
                description=f'{user.name} updated notification preferences',
                request=request,
                object_type='ParliamentUser',
                object_id=user.pk,
                object_repr=user.name,
                metadata={'notifications': new_notifications},
            )
            messages.success(request, "Notification preferences updated.")
            return redirect('profile')

        elif extended_profile_submitted:
            # Save bio, academics, chapter info, socials, other email, big brother
            user.about_me = request.POST.get('about_me', '').strip()
            user.major = request.POST.get('major', '').strip()
            user.minor = request.POST.get('minor', '').strip()
            user.concentration = request.POST.get('concentration', '').strip()
            user.pledge_class = request.POST.get('pledge_class', '').strip()
            user.pledge_class_greek = request.POST.get('pledge_class_greek', '').strip()
            user.graduation_semester = request.POST.get('graduation_semester', '').strip()
            raw_year = request.POST.get('graduation_year', '').strip()
            user.graduation_year = int(raw_year) if raw_year.isdigit() else None
            user.instagram = request.POST.get('instagram', '').strip().lstrip('@')
            user.twitter = request.POST.get('twitter', '').strip().lstrip('@')
            user.linkedin = request.POST.get('linkedin', '').strip().lstrip('@')
            user.snapchat = request.POST.get('snapchat', '').strip().lstrip('@')
            user.facebook = request.POST.get('facebook', '').strip().lstrip('@')
            new_other_email = request.POST.get('other_email', '').strip()
            user.other_email = new_other_email if new_other_email else None
            big_bro_id = request.POST.get('big_brother', '').strip()
            if big_bro_id:
                try:
                    user.big_brother = ParliamentUser.objects.get(user_id=big_bro_id)
                except ParliamentUser.DoesNotExist:
                    pass
            else:
                user.big_brother = None
            user.save()
            from src.house_utils import inherit_house_from_big
            inherit_house_from_big(user, user.big_brother)
            messages.success(request, 'Public profile updated.')
            return redirect('profile')

        elif role_history_add:
            role_name = request.POST.get('rh_role_name', '').strip()
            start_sem = request.POST.get('rh_start_semester', '').strip()
            end_sem = request.POST.get('rh_end_semester', '').strip()
            if role_name and start_sem:
                from src.models import RoleHistory
                RoleHistory.objects.create(user=user, role_name=role_name, start_semester=start_sem, end_semester=end_sem)
                messages.success(request, 'Role history entry added.')
            else:
                messages.error(request, 'Role name and start semester are required.')
            return redirect('profile')

        elif custom_social_add:
            platform = request.POST.get('cs_platform', '').strip()
            handle = request.POST.get('cs_handle', '').strip().lstrip('@')
            if platform and handle:
                socials = list(user.custom_socials or [])
                socials.append({'platform': platform, 'handle': handle})
                user.custom_socials = socials
                user.save(update_fields=['custom_socials'])
                messages.success(request, f'{platform} added.')
            else:
                messages.error(request, 'Platform name and handle are required.')
            return redirect('profile')

        elif custom_social_delete:
            idx = request.POST.get('cs_index', '').strip()
            if idx.isdigit():
                socials = list(user.custom_socials or [])
                i = int(idx)
                if 0 <= i < len(socials):
                    socials.pop(i)
                    user.custom_socials = socials
                    user.save(update_fields=['custom_socials'])
                    messages.success(request, 'Custom social removed.')
            return redirect('profile')

        elif initiation_chapter_add:
            school = request.POST.get('ic_school', '').strip()
            chapter = request.POST.get('ic_chapter', '').strip()
            if school and chapter:
                chapters = list(user.initiation_chapters or [])
                chapters.append({'school': school, 'chapter': chapter})
                user.initiation_chapters = chapters
                user.save(update_fields=['initiation_chapters'])
                messages.success(request, f'{chapter} at {school} added.')
            else:
                messages.error(request, 'School and chapter name are required.')
            return redirect('profile')

        elif initiation_chapter_delete:
            idx = request.POST.get('ic_index', '').strip()
            if idx.isdigit():
                chapters = list(user.initiation_chapters or [])
                i = int(idx)
                if 0 <= i < len(chapters):
                    chapters.pop(i)
                    user.initiation_chapters = chapters
                    user.save(update_fields=['initiation_chapters'])
                    messages.success(request, 'Initiation chapter removed.')
            return redirect('profile')

        elif role_history_delete:
            rh_id = request.POST.get('rh_id', '').strip()
            if rh_id:
                from src.models import RoleHistory
                RoleHistory.objects.filter(id=rh_id, user=user).delete()
                messages.success(request, 'Role history entry removed.')
            return redirect('profile')

        elif password_form_submitted:
            password_form = PasswordChangeForm(user, request.POST)
            if password_form.is_valid():
                logger.info(f"{request.user.username} changed their password")
                user = password_form.save()
                update_session_auth_hash(request, user)
                ActivityLog.log_activity(
                    action_type='password_changed',
                    user=request.user,
                    description=f'{request.user.name} changed their password via profile page',
                    request=request,
                    object_type='ParliamentUser',
                    object_id=request.user.pk,
                    object_repr=request.user.name,
                )
                messages.success(request, "Password changed successfully.")
                try:
                    watch_flag = getattr(request.user, 'watch_flag', None)
                    if watch_flag and watch_flag.is_active:
                        from src.security_notifications import send_watch_flag_password_change_alert
                        from src.utils.security_utils import get_client_ip
                        send_watch_flag_password_change_alert(
                            watched_user=request.user,
                            changed_by_user=request.user,
                            ip_address=get_client_ip(request) or 'unknown',
                            watch_reason=watch_flag.reason,
                        )
                except Exception:
                    pass
                return redirect('profile')
            else:
                messages.error(request, "Please correct the errors below.")

    # Get or create notification preferences
    notif_prefs, _ = UserPreferences.objects.get_or_create(user=user)

    # Check 2FA status
    from django_otp import user_has_device
    from django_otp.plugins.otp_static.models import StaticDevice
    has_2fa = user_has_device(user)
    backup_device = StaticDevice.objects.filter(user=user, name='backup', confirmed=True).first()
    backup_codes_remaining = backup_device.token_set.count() if backup_device else 0

    from src.models import RoleHistory
    role_histories = RoleHistory.objects.filter(user=user)
    eligible_big_bros = (
        ParliamentUser.objects
        .exclude(user_id=user.user_id)
        .order_by('name')
    )

    return render(request, 'profile.html', {
        'user': user,
        'password_form': password_form,
        'notif_prefs': notif_prefs,
        'has_2fa': has_2fa,
        'backup_codes_remaining': backup_codes_remaining,
        'role_histories': role_histories,
        'eligible_big_bros': eligible_big_bros,
    })