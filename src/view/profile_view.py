"""
The member's own Profile page (``profile``).

v4.4.0 — the reading and saving moved into helpers that the web view and the
app's Profile screen (src/mobile/views_profile.py) both call, so the two can't
drift: the same validation, the same activity log entries, the same email
confirmation. The web view keeps only what is web-only (messages, redirects,
AJAX replies, the password form and the remember-device cookie).

Helpers:

* ``take_photo_removed_notice(user)`` — whether an admin removed the photo
  since the member last looked (and clears the flag).
* ``remove_profile_picture`` / ``save_profile_picture`` — the photo.
* ``save_account_fields`` — username, preferred name, email (a change goes
  through the set_email confirmation), phone.
* ``save_public_profile`` — bio, chapter info, socials, other email, big.
* ``add_role_history`` / ``edit_role_history`` / ``delete_role_history``,
  ``add_custom_social`` / ``delete_custom_social``,
  ``add_initiation_chapter`` / ``delete_initiation_chapter``,
  ``add_academic_item`` / ``delete_academic_item`` — the "Additional details"
  lists, each saved on its own.
* ``profile_security_state(user)`` — 2FA, backup codes, passkeys and the
  recent / failed logins the Security card shows.

Every helper takes the page's own POST keys (``rh_role_name``, ``cs_index``…)
and raises ``ProfileEditError`` for anything the page shows as an error. The
plain-text rules (src/profile_text.py) are checked by the caller first.
"""
import os
import uuid
from ..models import ActivityLog, ParliamentUser, UserPreferences, validate_profile_picture
from ..decorators import log_function_call
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.contrib.auth import update_session_auth_hash
from django.core.exceptions import ValidationError
from src.feature_flag_decorators import require_page_enabled
import logging

logger = logging.getLogger('function_calls')

#: ai_type -> the ParliamentUser list field it edits.
ACADEMIC_FIELDS = {'major': 'majors', 'minor': 'minors', 'concentration': 'concentrations'}


class ProfileEditError(Exception):
    """
    A profile edit that must not be saved. ``status`` is the AJAX/API status;
    ``level`` the web message's level ('error' or 'info'); ``quiet`` means the
    web page redirects without a message (only its AJAX callers hear about it);
    ``with_success`` adds ``"success": false`` to the page's AJAX reply.
    """

    def __init__(self, message, status=400, level='error', quiet=False, with_success=False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.level = level
        self.quiet = quiet
        self.with_success = with_success


# ── Helpers shared with the app (src/mobile/views_profile.py, v4.4.0) ────────

def take_photo_removed_notice(user):
    """True once after an admin removed the member's photo; clears the flag."""
    if not user.profile_picture_removed_by_admin:
        return False
    user.profile_picture_removed_by_admin = False
    user.save(update_fields=['profile_picture_removed_by_admin'])
    return True


def remove_profile_picture(request, user):
    """Remove the photo. False when there was none."""
    if not user.profile_picture:
        return False
    user.profile_picture.delete()
    user.save(update_fields=['profile_picture'])
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
    return True


def save_profile_picture(request, user, upload):
    """Replace the photo with ``upload``. May raise ValidationError."""
    # v4.4.0: the field's checks (type, extension, 5 MB). save() never runs
    # model validators, so until now they only ran in the admin.
    validate_profile_picture(upload)
    # Delete old profile picture if exists
    if user.profile_picture:
        user.profile_picture.delete()

    original_name = upload.name
    # 10-29-26: a fresh name every time. The old file is deleted
    # first, so a second "image.jpg" would otherwise reuse the
    # same name, and the app (which caches photos by name, see
    # mobile.views_directory.photo_path) would keep the old one.
    upload.name = f'{uuid.uuid4().hex[:16]}{os.path.splitext(original_name)[1].lower()}'
    user.profile_picture = upload
    user.save(update_fields=['profile_picture'])
    logger.info(f"{user.username} uploaded a new profile picture")
    ActivityLog.log_activity(
        action_type='profile_picture_changed',
        user=request.user,
        description=f'{user.name} uploaded a new profile picture',
        request=request,
        object_type='ParliamentUser',
        object_id=user.pk,
        object_repr=user.name,
        metadata={'action': 'uploaded', 'filename': original_name},
    )


def save_account_fields(request, user, data, keep_changes_on_error=False):
    """
    Save username, preferred name, email and phone from ``data`` (the
    "Account settings" form's keys). Returns ``{'changes': [...],
    'email_pending': address or None}``: changing an existing email only sends
    the set_email confirmation link, and the address updates when it's clicked.

    On an email error, ``keep_changes_on_error`` (the web form's non-AJAX
    submit) still saves the other fields first, as the page always has.
    """
    new_username = data.get('username')
    new_preferred_name = data.get('preferred_name', '').strip()
    new_email = data.get('email', '').strip().lower()
    new_phone = data.get('phone_number', '').strip()

    changes_made = False
    changes_list = []  # audit trail
    pending_email = None  # set if email change requires confirmation

    def refuse(message):
        if keep_changes_on_error and changes_made:
            # Save any non-email changes already made before returning
            user.save(update_fields=['username', 'preferred_name', 'phone_number'])
        raise ProfileEditError(message)

    # v4.4.0: a taken username was an unhandled IntegrityError (a 500) here.
    if new_username and new_username != user.username and (
            ParliamentUser.objects.filter(username=new_username).exclude(pk=user.pk).exists()):
        raise ProfileEditError('That username is taken.')

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
        user.preferred_name = new_preferred_name if new_preferred_name else ''
        changes_made = True

    # Update email if changed
    current_email = (user.email or '').lower()
    if new_email != current_email:
        # Check if email is already taken by another user
        if new_email and ParliamentUser.objects.filter(email__iexact=new_email).exclude(user_id=user.user_id).exists():
            refuse('This email address is already in use by another user.')

        if user.email and new_email:
            # Changing an existing email — require confirmation via set_email flow
            from src.view.set_email import _send_email_confirmation
            result = _send_email_confirmation(request, user, new_email)
            if result.get('error'):
                refuse(result['error'])
            # Email is NOT saved yet — record pending state and fall through
            # so any other field changes (username, preferred name, phone) are still saved
            pending_email = new_email
        else:
            # First-time set or clearing — save immediately
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
        user.save(update_fields=['username', 'preferred_name', 'email', 'phone_number'])
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
    return {'changes': changes_list, 'email_pending': pending_email}


def save_public_profile(user, data):
    """
    Save the "Public profile" form: bio, chapter info, socials, other email,
    big brother (majors/minors/concentrations are the academic_item helpers).
    """
    # v3.15.0: canonicalize class + auto-fill its greek from the registry
    from src.pledge_classes import apply_to_fields, is_original_founder
    new_class, new_greek = apply_to_fields(
        data.get('pledge_class', ''),
        data.get('pledge_class_greek', ''))
    # The 1800s founders' Beta Blue badge is set only by an admin
    # (mark_original_founders / admin-v2), never self-claimed here.
    if (is_original_founder(new_class, new_greek)
            and not is_original_founder(user.pledge_class, user.pledge_class_greek)):
        raise ProfileEditError('"Original Founder" is reserved for the chapter\'s 1800s founders.',
                               with_success=True)
    user.about_me = data.get('about_me', '').strip()
    user.pledge_class, user.pledge_class_greek = new_class, new_greek
    user.graduation_semester = data.get('graduation_semester', '').strip()
    raw_year = data.get('graduation_year', '').strip()
    user.graduation_year = int(raw_year) if raw_year.isdigit() else None
    user.instagram = data.get('instagram', '').strip().lstrip('@')
    user.twitter = data.get('twitter', '').strip().lstrip('@')
    user.linkedin = data.get('linkedin', '').strip().lstrip('@')
    user.snapchat = data.get('snapchat', '').strip().lstrip('@')
    user.facebook = data.get('facebook', '').strip().lstrip('@')
    new_other_email = data.get('other_email', '').strip()
    user.other_email = new_other_email if new_other_email else None
    # v3.39.0 — once the education committee has revealed his big,
    # the pledge (and later the brother) can't change it here; the
    # field isn't rendered, so an absent value must not clear it
    # either. Education and the admin-v2 profile editor still can.
    from src.big_reveal import profile_big_is_locked
    if not profile_big_is_locked(user):
        big_bro_id = data.get('big_brother', '').strip()
        if big_bro_id:
            try:
                user.big_brother = ParliamentUser.objects.get(user_id=big_bro_id)
            except ParliamentUser.DoesNotExist:
                pass
        else:
            user.big_brother = None
    user.save(update_fields=[
        'about_me', 'pledge_class', 'pledge_class_greek',
        'graduation_semester', 'graduation_year',
        'instagram', 'twitter', 'linkedin', 'snapchat', 'facebook',
        'other_email', 'big_brother',
    ])
    from src.house_utils import inherit_house_from_big
    inherit_house_from_big(user, user.big_brother)


def _role_fields(data):
    return (data.get('rh_role_name', '').strip(), data.get('rh_start_semester', '').strip(),
            data.get('rh_end_semester', '').strip())


def add_role_history(user, data):
    """A RoleHistory row from ``rh_role_name``, ``rh_start_semester``, ``rh_end_semester``."""
    role_name, start_sem, end_sem = _role_fields(data)
    if not (role_name and start_sem):
        raise ProfileEditError('Role name and start semester are required.')
    from src.models import RoleHistory
    return RoleHistory.objects.create(user=user, role_name=role_name, start_semester=start_sem, end_semester=end_sem)


def edit_role_history(user, data):
    """Update the member's own ``rh_id`` row; returns (id, role_name, start, end)."""
    rh_id = data.get('rh_id', '').strip()
    role_name, start_sem, end_sem = _role_fields(data)
    if not (rh_id and role_name and start_sem):
        raise ProfileEditError('Role name and start semester are required.')
    from src.models import RoleHistory
    # Scoped to `user=user` same as the delete below — a member can only edit
    # their own entries, not anyone else's by guessing an id.
    updated = RoleHistory.objects.filter(id=rh_id, user=user).update(
        role_name=role_name, start_semester=start_sem, end_semester=end_sem,
    )
    if not updated:
        raise ProfileEditError('Role history entry not found.', status=404)
    return int(rh_id), role_name, start_sem, end_sem


def delete_role_history(user, data):
    """Delete the member's own ``rh_id`` row. None when no id was given."""
    rh_id = data.get('rh_id', '').strip()
    if not rh_id:
        return None
    from src.models import RoleHistory
    return RoleHistory.objects.filter(id=rh_id, user=user).delete()[0]


def add_custom_social(user, data):
    """Append ``cs_platform`` / ``cs_handle``; returns (platform, handle, index)."""
    platform = data.get('cs_platform', '').strip()
    handle = data.get('cs_handle', '').strip().lstrip('@')
    if not (platform and handle):
        raise ProfileEditError('Platform name and handle are required.')
    socials = list(user.custom_socials or [])
    socials.append({'platform': platform, 'handle': handle})
    user.custom_socials = socials
    user.save(update_fields=['custom_socials'])
    return platform, handle, len(socials) - 1


def _pop_list_item(user, field, raw_index, error):
    """Remove item ``raw_index`` of the list ``field``; False when out of range."""
    if not raw_index.isdigit():
        raise ProfileEditError(error, quiet=True)
    items = list(getattr(user, field) or [])
    i = int(raw_index)
    if not 0 <= i < len(items):
        return False
    items.pop(i)
    setattr(user, field, items)
    user.save(update_fields=[field])
    return True


def delete_custom_social(user, data):
    return _pop_list_item(user, 'custom_socials', data.get('cs_index', '').strip(), 'Invalid index.')


def add_initiation_chapter(user, data):
    """Append ``ic_school`` / ``ic_chapter`` (+ ``ic_role_number``); returns (entry, index)."""
    school = data.get('ic_school', '').strip()
    chapter = data.get('ic_chapter', '').strip()
    role_num = data.get('ic_role_number', '').strip()
    if not (school and chapter):
        raise ProfileEditError('School and chapter name are required.')
    chapters = list(user.initiation_chapters or [])
    entry = {'school': school, 'chapter': chapter}
    if role_num:
        entry['role_number'] = role_num
    chapters.append(entry)
    user.initiation_chapters = chapters
    user.save(update_fields=['initiation_chapters'])
    return entry, len(chapters) - 1


def delete_initiation_chapter(user, data):
    return _pop_list_item(user, 'initiation_chapters', data.get('ic_index', '').strip(), 'Invalid index.')


def add_academic_item(user, data):
    """Add ``ai_value`` to the ``ai_type`` list; returns (value, type, index)."""
    ai_type = data.get('ai_type', '').strip()
    ai_value = data.get('ai_value', '').strip()
    if not (ai_type in ACADEMIC_FIELDS and ai_value):
        raise ProfileEditError('Type and value are required.')
    field = ACADEMIC_FIELDS[ai_type]
    items = list(getattr(user, field) or [])
    if ai_value in items:
        raise ProfileEditError(f'{ai_value} is already listed.', level='info')
    items.append(ai_value)
    setattr(user, field, items)
    user.save(update_fields=[field])
    return ai_value, ai_type, len(items) - 1


def delete_academic_item(user, data):
    ai_type = data.get('ai_type', '').strip()
    ai_index = data.get('ai_index', '').strip()
    if ai_type not in ACADEMIC_FIELDS:
        raise ProfileEditError('Invalid type or index.', quiet=True)
    return _pop_list_item(user, ACADEMIC_FIELDS[ai_type], ai_index, 'Invalid type or index.')


def profile_security_state(user):
    """2FA, backup codes, passkeys and recent / failed logins (the Security card)."""
    from django_otp import user_has_device
    from django_otp.plugins.otp_static.models import StaticDevice
    from src.models.webauthn import WebAuthnCredential
    has_2fa = user_has_device(user)
    backup_device = StaticDevice.objects.filter(user=user, name='backup', confirmed=True).first()
    backup_codes_remaining = backup_device.token_set.count() if backup_device else 0
    backup_codes_warning = (
        has_2fa and (
            not backup_device
            or backup_codes_remaining == 0
            or not user.backup_codes_acknowledged
            or backup_codes_remaining <= 2
        )
    )

    # v3.15.0 QOL: recent-logins card. Successful logins only; city/region +
    # browser/OS are shown, never the (encrypted) raw IP. Default ordering
    # is -timestamp; [:5] keeps it one cheap indexed query.
    recent_logins = list(user.login_history.filter(status='success')[:5])

    # v3.15.9: surface failed/blocked attempts too — the signal that lets a
    # member catch someone guessing at their account. Count + latest timestamp
    # over the last 30 days; still no raw IPs exposed.
    from django.utils import timezone as _tz
    from datetime import timedelta as _td
    _failed_qs = user.login_history.filter(
        status__in=['failed', 'blocked'],
        timestamp__gte=_tz.now() - _td(days=30),
    )
    failed_login_count_30d = _failed_qs.count()
    last_failed_login = _failed_qs.first() if failed_login_count_30d else None

    return {
        'has_2fa': has_2fa,
        'backup_codes_remaining': backup_codes_remaining,
        'backup_codes_warning': backup_codes_warning,
        'passkeys': list(WebAuthnCredential.objects.filter(user=user)),
        'recent_logins': recent_logins,
        'failed_login_count_30d': failed_login_count_30d,
        'last_failed_login': last_failed_login,
    }


# ── The page ─────────────────────────────────────────────────────────────────

def _refused(request, is_ajax, e):
    """The page's reply to a ProfileEditError."""
    if is_ajax:
        body = {'success': False, 'error': e.message} if e.with_success else {'error': e.message}
        return JsonResponse(body, status=e.status)
    if not e.quiet:
        (messages.info if e.level == 'info' else messages.error)(request, e.message)
    return redirect('profile')


@login_required
@require_page_enabled('profile')
@log_function_call
def profile_view(request):
    user = request.user

    # Check if profile picture was removed by admin
    if take_photo_removed_notice(user):
        messages.warning(
            request,
            "Your profile picture was removed by an administrator. "
            "Please upload a new, appropriate profile picture."
        )

    profile_form_submitted = 'profile_submit' in request.POST
    password_form_submitted = 'password_submit' in request.POST
    profile_picture_submitted = 'profile_picture_submit' in request.POST
    extended_profile_submitted = 'extended_profile_submit' in request.POST
    role_history_add = 'role_history_add_submit' in request.POST
    role_history_edit = 'role_history_edit_submit' in request.POST
    role_history_delete = 'role_history_delete_submit' in request.POST
    custom_social_add = 'custom_social_add_submit' in request.POST
    custom_social_delete = 'custom_social_delete_submit' in request.POST
    initiation_chapter_add = 'initiation_chapter_add_submit' in request.POST
    initiation_chapter_delete = 'initiation_chapter_delete_submit' in request.POST
    academic_item_add = 'academic_item_add_submit' in request.POST
    academic_item_delete = 'academic_item_delete_submit' in request.POST

    password_form = PasswordChangeForm(user)

    if request.method == 'POST':
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        post = request.POST

        # v3.44.9 — the short profile fields are plain text (see
        # src/profile_text.py). Checked once here, before any branch saves.
        # The password form is left alone: a password may contain anything.
        if not password_form_submitted:
            from src.profile_text import profile_text_error
            text_error = profile_text_error(post, user.other_email, user=user)
            if text_error:
                if is_ajax:
                    return JsonResponse({'success': False, 'error': text_error}, status=400)
                messages.error(request, text_error)
                return redirect('profile')

        try:
            if profile_picture_submitted:
                action = post.get('action', '')

                # Handle profile picture removal
                if action == 'remove' or 'remove_profile_picture' in post:
                    if remove_profile_picture(request, user):
                        messages.success(request, "Profile picture removed successfully.")
                    else:
                        messages.info(request, "No profile picture to remove.")
                    return redirect('profile')

                # Handle profile picture upload
                elif request.FILES.get('profile_picture'):
                    try:
                        save_profile_picture(request, user, request.FILES['profile_picture'])
                        messages.success(request, "Profile picture uploaded successfully.")
                    except ValidationError as e:
                        messages.error(request, str(e))
                    return redirect('profile')
                else:
                    messages.warning(request, "No file selected.")
                    return redirect('profile')

            elif profile_form_submitted:
                result = save_account_fields(request, user, post, keep_changes_on_error=not is_ajax)
                pending_email = result['email_pending']
                if pending_email and not is_ajax:
                    messages.success(
                        request,
                        f'A confirmation link has been sent to {pending_email}. '
                        f'Your email address will update when you click it.'
                    )
                if is_ajax:
                    response_data = {'success': True}
                    if not result['changes']:
                        response_data['message'] = 'No changes were made.'
                    if pending_email:
                        response_data['email_pending'] = True
                        response_data['email_pending_address'] = pending_email
                    return JsonResponse(response_data)
                if result['changes']:
                    messages.success(request, "Profile updated successfully.")
                elif not pending_email:   # v3.37.0: the "link sent" message already said what happened
                    messages.info(request, "No changes were made.")
                return redirect('profile')

            elif extended_profile_submitted:
                save_public_profile(user, post)
                if is_ajax:
                    return JsonResponse({'success': True})
                messages.success(request, 'Public profile updated.')
                return redirect('profile')

            elif role_history_add:
                rh = add_role_history(user, post)
                if is_ajax:
                    return JsonResponse({'success': True, 'id': rh.id, 'role_name': rh.role_name, 'start_semester': rh.start_semester, 'end_semester': rh.end_semester or ''})
                messages.success(request, 'Role history entry added.')
                return redirect('profile')

            elif role_history_edit:
                rh_id, role_name, start_sem, end_sem = edit_role_history(user, post)
                if is_ajax:
                    return JsonResponse({
                        'success': True, 'id': rh_id, 'role_name': role_name,
                        'start_semester': start_sem, 'end_semester': end_sem or '',
                    })
                messages.success(request, 'Role history entry updated.')
                return redirect('profile')

            elif custom_social_add:
                platform, handle, index = add_custom_social(user, post)
                if is_ajax:
                    return JsonResponse({'success': True, 'platform': platform, 'handle': handle, 'index': index})
                messages.success(request, f'{platform} added.')
                return redirect('profile')

            elif custom_social_delete:
                if delete_custom_social(user, post):
                    if is_ajax:
                        return JsonResponse({'success': True})
                    messages.success(request, 'Custom social removed.')
                return redirect('profile')

            elif initiation_chapter_add:
                entry, index = add_initiation_chapter(user, post)
                if is_ajax:
                    return JsonResponse({'success': True, 'school': entry['school'], 'chapter': entry['chapter'], 'role_number': entry.get('role_number', ''), 'index': index})
                messages.success(request, f'{entry["chapter"]} at {entry["school"]} added.')
                return redirect('profile')

            elif initiation_chapter_delete:
                if delete_initiation_chapter(user, post):
                    if is_ajax:
                        return JsonResponse({'success': True})
                    messages.success(request, 'Initiation chapter removed.')
                return redirect('profile')

            elif academic_item_add:
                value, ai_type, index = add_academic_item(user, post)
                if is_ajax:
                    return JsonResponse({'success': True, 'value': value, 'type': ai_type, 'index': index})
                messages.success(request, f'{value} added.')
                return redirect('profile')

            elif academic_item_delete:
                if delete_academic_item(user, post):
                    if is_ajax:
                        return JsonResponse({'success': True})
                    messages.success(request, 'Removed.')
                return redirect('profile')

            elif role_history_delete:
                if delete_role_history(user, post) is not None:
                    if is_ajax:
                        return JsonResponse({'success': True})
                    messages.success(request, 'Role history entry removed.')
                return redirect('profile')
        except ProfileEditError as e:
            return _refused(request, is_ajax, e)

        if password_form_submitted:
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
                if is_ajax:
                    return JsonResponse({'success': True})
                messages.success(request, "Password changed successfully.")
                return redirect('profile')
            else:
                if is_ajax:
                    return JsonResponse({'errors': password_form.errors.as_json()}, status=400)
                messages.error(request, "Please correct the errors below.")

    user.refresh_from_db()

    # Get or create notification preferences
    notif_prefs, _ = UserPreferences.objects.get_or_create(user=user)

    security = profile_security_state(user)

    # Check if this browser has a valid remember-device cookie
    from src.view.two_factor import _REMEMBER_COOKIE_NAME, _parse_remember_cookie
    from django.core import signing
    two_factor_device_remembered = False
    if security['has_2fa']:
        cookie = request.COOKIES.get(_REMEMBER_COOKIE_NAME)
        if cookie:
            try:
                u_pk, _ = _parse_remember_cookie(cookie)
                # Compared as strings for the same reason the middleware does:
                # this pk is a CharField and a generated id is not numeric.
                two_factor_device_remembered = (str(u_pk) == str(user.pk))
            except (signing.BadSignature, signing.SignatureExpired, ValueError):
                pass

    passkeys = security['passkeys']
    passkey_count = len(passkeys)
    show_passkey_nudge = (passkey_count == 0) and (user == request.user)

    from src.models import RoleHistory
    role_histories = RoleHistory.objects.filter(user=user)
    from src.big_reveal import profile_big_is_locked
    eligible_big_bros = (
        ParliamentUser.objects
        .exclude(user_id=user.user_id)
        .order_by('name')
    )

    academic_sections = [
        ('Major', 'major', list(user.majors or [])),
        ('Minor', 'minor', list(user.minors or [])),
        ('Concentration', 'concentration', list(user.concentrations or [])),
    ]

    # v3.15.0: canonical pledge-class options for the datalist dropdown
    from src.pledge_classes import all_classes
    pledge_class_choices = all_classes()

    return render(request, 'profile.html', {
        'user': user,
        'password_form': password_form,
        'notif_prefs': notif_prefs,
        'has_2fa': security['has_2fa'],
        'backup_codes_remaining': security['backup_codes_remaining'],
        'backup_codes_acknowledged': user.backup_codes_acknowledged,
        'backup_codes_warning': security['backup_codes_warning'],
        'two_factor_device_remembered': two_factor_device_remembered,
        'passkeys': passkeys,
        'passkey_count': passkey_count,
        'show_passkey_nudge': show_passkey_nudge,
        'role_histories': role_histories,
        'eligible_big_bros': eligible_big_bros,
        'big_locked': profile_big_is_locked(user),
        'academic_sections': academic_sections,
        'recent_logins': security['recent_logins'],
        'failed_login_count_30d': security['failed_login_count_30d'],
        'last_failed_login': security['last_failed_login'],
        'pledge_class_choices': pledge_class_choices,
    })
