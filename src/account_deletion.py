"""
Self-service account deletion (App Store guideline 5.1.1(v)).

The chapter keeps a permanent MEMBERSHIP RECORD: who was a member and what
they took part in. Deleting an account removes the ACCOUNT and the member's
personal contact/profile data, not that record.

Timeline
--------
* ``request_deletion`` (immediately): every way of signing in is revoked
  (password made unusable, passkeys, authenticator and backup codes, API
  tokens, calendar feed, web sessions, web push, every app install and its
  tickets) and ``is_active`` goes False, so web login, the app's
  ``still_allowed`` check and password reset all refuse the account. The
  member then shows as a "Former member" (record only, no contact details or
  photo — like the 1800s founders) and drops out of every by-status list
  (``ParliamentUserQuerySet.filter``). Contact/profile data is NOT erased
  yet, so a restore loses nothing. The member gets a confirmation email, the
  admins an in-app notification and an email.
* ``restore_account`` (within ``PURGE_AFTER``): admin-only, at the member's
  request. Reactivates the account with a random password; the member uses
  "Forgot password" to sign in again. Revoked devices/sessions stay revoked.
* ``purge_due_deletions`` (nightly, ``tasks.purge_deleted_accounts``):
  permanently erases the personal data (``purge_account``). Only a restore
  stops it. Idempotent.
* ``rebuild_account`` (after the purge): admin-only, at the member's request
  through the chapter. Reattaches sign-in (a new email, optionally a new
  username) to the kept record and emails a set-password link. Nothing that
  was erased comes back.

Not the same as ``ParliamentUser.anonymize()`` (the admin action), which also
blanks the NAME and roll number.
"""
import logging
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string

logger = logging.getLogger('admin_actions')

#: How long a deleted account can be restored before its data is erased.
PURGE_AFTER = timedelta(days=30)

#: Shown wherever a closed account appears (directory, cards, house map).
FORMER_MEMBER_LABEL = 'Former member'

#: Profile/contact fields the purge erases, and the value each gets.
PURGED_FIELDS = {
    'email': None,
    'other_email': None,
    'phone_number': '',
    'instagram': '',
    'twitter': '',
    'linkedin': '',
    'snapchat': '',
    'facebook': '',
    'about_me': '',
    'email_flagged': False,
    'email_flagged_reason': '',   # bounce text quotes the address
    'email_flagged_at': None,
}
#: JSON fields reset to their field default.
PURGED_JSON_FIELDS = ('custom_socials', 'majors', 'minors', 'concentrations', 'onboarding_data')


class DeletionRefused(Exception):
    """A refusal with a machine ``code`` and a member-facing ``message``."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ── Dates ───────────────────────────────────────────────────────────────────

def purge_on(user):
    """The local date the account's data is erased (None if not deleted)."""
    if not user.deletion_requested_at:
        return None
    return timezone.localdate(user.deletion_requested_at) + PURGE_AFTER


def _purge_cutoff(now=None):
    """Requests made before this instant are due: requested on day X, erased on day X+30."""
    today = timezone.localdate(now or timezone.now())
    first_not_due = today - PURGE_AFTER + timedelta(days=1)
    return timezone.make_aware(datetime.combine(first_not_due, time.min))


def _long(d):
    return f'{d:%B} {d.day}, {d.year}'


def is_pending(user):
    return bool(user.deletion_requested_at) and not user.account_deleted_at


def is_purged(user):
    return bool(user.account_deleted_at)


# ── Request ─────────────────────────────────────────────────────────────────

def _revoke_sign_in(user, reason='account deleted'):
    """Every way this account can sign in, gone now. Safe to repeat."""
    from src.models import MobileAuthTicket, MobileDevice, MobilePushTicket
    from src.models.users import UserSession

    MobileDevice.revoke_all_for(user, reason)
    MobilePushTicket.objects.filter(device__user=user).delete()
    MobileAuthTicket.objects.filter(user=user).delete()
    # Passkeys, TOTP/backup codes, API tokens, web push, calendar feed. The
    # admin-set 2FA requirement is a policy, not a credential: kept.
    user._delete_login_credentials(keep_requirement=True)
    for key in list(UserSession.objects.filter(user=user).values_list('session_key', flat=True)):
        UserSession.delete_session_from_store(key)
    UserSession.objects.filter(user=user).delete()


def request_deletion(user, via, request=None):
    """
    Close ``user``'s account now. Returns the purge date. Raises
    DeletionRefused('not_allowed') for a site admin and
    ('already_deleted') if it is already closed.
    """
    if user.is_admin:
        raise DeletionRefused(
            'not_allowed',
            "Site administrators can't delete their own account. Ask another admin to "
            "remove your admin access first, then try again.")
    if user.deletion_requested_at:
        raise DeletionRefused('already_deleted', 'This account has already been deleted.')

    roles = [r.name for r in user.roles.all()]
    now = timezone.now()
    with transaction.atomic():
        user.deletion_requested_at = now
        user.deletion_requested_via = (via or '')[:10]
        user.is_active = False
        user.set_unusable_password()
        user.save(update_fields=['deletion_requested_at', 'deletion_requested_via', 'is_active', 'password'])
        _revoke_sign_in(user)

    when = purge_on(user)
    _email_member_confirmation(user, when)
    _notify_admins(user, roles, when, via)
    from src.models import log_admin_action
    log_admin_action(
        actor=user, action='account_deletion_requested', request=request, target_user=user,
        target_repr=str(user),
        detail=f'{user.get_display_name()} deleted their account ({via}). '
               f'Data erased on {when.isoformat()} unless restored.'
               + (f' Roles held: {", ".join(roles)}.' if roles else ''),
    )
    logger.info('ACCOUNT DELETION requested by %s via %s; purge on %s', user.pk, via, when)
    return when


def _email_member_confirmation(user, when):
    """Sent while the address is still on file."""
    if not user.email:
        return
    body = (
        f'Hi {user.get_display_name()},\n\n'
        f'Your Parliament account has been deleted. It is closed now: you have been '
        f'signed out of the website and the app everywhere, and you can no longer sign in.\n\n'
        f'Your personal data (email and phone, social handles, photo, bio and studies, '
        f'sign-in history and settings) will be permanently removed on '
        f'{_long(when)}.\n\n'
        f'The chapter keeps its membership record: your name, roll number, pledge class, '
        f'house and family line, roles held, and your part in chapter votes, attendance, '
        f'minutes and other records.\n\n'
        f'If this was a mistake, contact a chapter officer within 30 days (before '
        f'{_long(when)}) and they can restore your account.\n\n'
        f'— Alpha Mu Parliament'
    )
    try:
        send_mail('[Parliament] Your account has been deleted', body,
                  settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=True)
    except Exception as exc:   # never let mail stop the deletion
        logger.warning('account deletion confirmation email failed for %s: %s', user.pk, exc)


def _admins(exclude=None):
    from src.models import ParliamentUser
    qs = ParliamentUser.objects.filter(is_admin=True, is_active=True, deletion_requested_at__isnull=True)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    return qs


def _notify_admins(user, roles, when, via):
    from django.urls import reverse
    from src.notification_service import create_notification
    from src.notifications import get_site_url

    name = user.get_display_name()
    roll = f' (#{user.role_number})' if user.role_number else ''
    roles_line = (f'They currently hold: {", ".join(roles)}. Appoint a successor.'
                  if roles else 'They hold no current roles.')
    message = (f'{name}{roll} deleted their account from the {"app" if via == "app" else "website"}. '
               f'They are signed out everywhere. Their personal data is erased on '
               f'{when.isoformat()}; restore only if they ask. {roles_line}')
    link = reverse('admin_v2_account_deletions')
    for admin in _admins(exclude=user):
        try:
            create_notification(admin, 'security', 'A member deleted their account', message, link=link,
                                push_title='Account deleted', push_body='A member deleted their account.')
        except Exception as exc:
            logger.warning('account deletion admin notification failed for %s: %s', admin.pk, exc)
    recipients = [a.email for a in _admins(exclude=user) if a.email]
    if recipients:
        try:
            send_mail(f'[Parliament] {name} deleted their account',
                      f'{message}\n\nPending deletions: {get_site_url()}{link}',
                      settings.DEFAULT_FROM_EMAIL, recipients, fail_silently=True)
        except Exception as exc:
            logger.warning('account deletion admin email failed: %s', exc)


# ── Restore (before the purge) ──────────────────────────────────────────────

def restore_account(user, by, request=None):
    """Reopen a pending deletion at the member's request. Raises DeletionRefused('not_pending')."""
    if not is_pending(user):
        raise DeletionRefused('not_pending', 'This account has no pending deletion to restore.')
    requested = user.deletion_requested_at
    user.deletion_requested_at = None
    user.deletion_requested_via = ''
    user.is_active = True
    # A usable (random, unknown) password so the normal "Forgot password"
    # flow works — Django's reset form skips unusable passwords.
    user.set_password(get_random_string(48))
    user.save(update_fields=['deletion_requested_at', 'deletion_requested_via', 'is_active', 'password'])
    from src.models import log_admin_action
    log_admin_action(
        actor=by, action='account_restored', request=request, target_user=user, target_repr=str(user),
        detail=f'{by.get_display_name()} restored {user.get_display_name()}\'s account at the member\'s '
               f'request (deleted {timezone.localdate(requested).isoformat()}). Signed-out devices stay '
               f'signed out; the member resets their password to sign in.',
    )
    if user.email:
        from src.notifications import get_site_url
        try:
            send_mail('[Parliament] Your account has been restored',
                      f'Hi {user.get_display_name()},\n\nAt your request, a chapter officer restored your '
                      f'Parliament account. To sign in, set a new password with "Forgot password" at '
                      f'{get_site_url()}/password-reset/ — your old password, passkeys and app sign-ins '
                      f'no longer work.\n\n— Alpha Mu Parliament',
                      settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=True)
        except Exception as exc:
            logger.warning('restore email failed for %s: %s', user.pk, exc)
    return user


# ── Purge (at 30 days) ──────────────────────────────────────────────────────

def _end_current_roles(user):
    """
    v4.4.1: a purged account holds no office. Its current roles end the way
    the officers' transition tool ends them (src/view/officer/transitions.py):
    the role is removed and any open role-history row with that exact name is
    closed with the semester in progress, so the record still shows they held
    it. Not done at the deletion request, so officers see who to replace during
    the 30 days and a restore brings the account back as it was. Returns the
    names of the roles ended.
    """
    from src.models import RoleHistory
    from src.utils.semester import current_semester

    names = sorted(user.roles.values_list('name', flat=True))
    if names:
        RoleHistory.objects.filter(user=user, role_name__in=names, end_semester='').update(
            end_semester=current_semester())
        user.roles.clear()
    return names


def purge_account(user):
    """
    Permanently erase the deleted account's personal data, keeping the
    membership record. Idempotent: running it again re-applies the same
    erase. Returns True if this call did the first purge.
    """
    from src.models import (
        ActivityLog, AdminActionLog, LoginAlert, LoginHistory, MobileDevice, PageVisit,
    )
    from src.models.security import EmailVerificationToken
    from src.models.users import UserPreferences

    if not user.deletion_requested_at:
        raise DeletionRefused('not_deleted', 'This account has not been deleted.')
    first = user.account_deleted_at is None

    with transaction.atomic():
        _revoke_sign_in(user)
        MobileDevice.objects.filter(user=user).delete()
        LoginAlert.objects.filter(user=user).delete()
        LoginHistory.objects.filter(user=user).delete()
        PageVisit.objects.filter(user=user).delete()
        UserPreferences.objects.filter(user=user).delete()
        EmailVerificationToken.objects.filter(user=user).delete()
        # Keep what they did; drop where they did it from.
        ActivityLog.objects.filter(user=user).update(ip_address=None, user_agent='')
        AdminActionLog.objects.filter(actor=user).update(ip_address=None)

        for field, value in PURGED_FIELDS.items():
            setattr(user, field, value)
        for field in PURGED_JSON_FIELDS:
            setattr(user, field, user._meta.get_field(field).get_default())
        ended_roles = _end_current_roles(user)
        if user.profile_picture:
            try:
                user.profile_picture.delete(save=False)
            except Exception as exc:
                logger.warning('purge: could not delete profile picture of %s: %s', user.pk, exc)
        user.profile_picture = None
        user.username = f'deleted-{user.user_id}'
        user.is_active = False
        user.set_unusable_password()
        if first:
            user.account_deleted_at = timezone.now()
        user.save()

    if first:
        from src.models import log_admin_action
        roles_note = f' Roles ended: {", ".join(ended_roles)}.' if ended_roles else ''
        log_admin_action(actor=None, action='account_purged', target_user=user, target_repr=str(user),
                         detail=f'Personal data of {user.get_display_name()} erased '
                                f'{PURGE_AFTER.days} days after account deletion. Membership record kept.'
                                f'{roles_note}')
        logger.info('ACCOUNT PURGED: %s', user.pk)
    return first


def purge_due_deletions(now=None):
    """Erase every account whose window has ended. Returns how many were purged."""
    from src.models import ParliamentUser
    due = ParliamentUser.objects.filter(
        deletion_requested_at__lt=_purge_cutoff(now), account_deleted_at__isnull=True)
    count = 0
    for user in due:
        try:
            if purge_account(user):
                count += 1
        except Exception as exc:   # one bad row must not stop the rest
            logger.error('purge of %s failed: %s', user.pk, exc)
    return count


# ── Rebuild (after the purge) ───────────────────────────────────────────────

def rebuild_account(user, by, email, username='', request=None):
    """
    Reattach sign-in to a purged membership record, at the member's request
    through the chapter (the officer verifies who is asking). Sets a new
    email (and username), reactivates, and emails a set-password link.
    Nothing erased comes back. Raises DeletionRefused.
    """
    from src.models import ParliamentUser
    from src.models.users import UserPreferences

    if not is_purged(user):
        raise DeletionRefused('not_purged', 'Only an account whose data has been erased can be rebuilt. '
                                            'A pending deletion is restored instead.')
    email = (email or '').strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        raise DeletionRefused('invalid_email', 'Enter a valid email address.')
    if ParliamentUser.objects.filter(email__iexact=email).exclude(pk=user.pk).exists():
        raise DeletionRefused('email_taken', 'Another account already uses that email address.')
    username = (username or '').strip() or email.split('@')[0]
    if ParliamentUser.objects.filter(username__iexact=username).exclude(pk=user.pk).exists():
        raise DeletionRefused('username_taken', f'The username "{username}" is taken. Enter another.')

    with transaction.atomic():
        user.email = email
        user.username = username[:100]
        user.is_active = True
        user.deletion_requested_at = None
        user.deletion_requested_via = ''
        user.account_deleted_at = None
        user.force_password_change = False
        user.has_default_password = False
        user.set_password(get_random_string(48))   # unknown; replaced through the emailed link
        user.save()
        UserPreferences.objects.get_or_create(user=user)

    _email_set_password_link(user)
    from src.models import log_admin_action
    log_admin_action(
        actor=by, action='account_rebuilt', request=request, target_user=user, target_repr=str(user),
        detail=f'{by.get_display_name()} rebuilt the account for the record of {user.get_display_name()} '
               f'at the member\'s request (username {user.username}). Set-password link emailed. '
               f'Erased data was not restored.',
    )
    return user


def _email_set_password_link(user):
    """The site's standard set-password link (password_reset_confirm)."""
    from django.contrib.auth.tokens import default_token_generator
    from django.urls import reverse
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode
    from src.notifications import get_site_url

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    link = get_site_url() + reverse('password_reset_confirm', args=[uid, token])
    try:
        send_mail('[Parliament] Set up your account',
                  f'Hi {user.get_display_name()},\n\nAt your request, a chapter officer set up a new '
                  f'Parliament account for your membership record. Your username is {user.username}.\n\n'
                  f'Set your password here: {link}\n\nYour chapter history (votes, attendance, roles) is '
                  f'linked to this account. Profile details from your old account were permanently '
                  f'removed and are not restored — add them again from your profile if you like.\n\n'
                  f'— Alpha Mu Parliament',
                  settings.DEFAULT_FROM_EMAIL, [user.email], fail_silently=False)
        return True
    except Exception as exc:
        logger.warning('rebuild set-password email failed for %s: %s', user.pk, exc)
        return False


# ── Display ─────────────────────────────────────────────────────────────────

#: Card keys blanked for a closed account (record only).
_CARD_BLANK = ('email', 'other_email', 'phone', 'profile_picture_url', 'about_me')


def record_only_card(data, member):
    """Reduce a serialized profile card to the membership record (in place)."""
    if not getattr(member, 'deletion_requested_at', None):
        return data
    for key in _CARD_BLANK:
        if key in data:
            data[key] = ''
    for key in ('socials',):
        if key in data:
            data[key] = {}
    for key in ('custom_socials',):
        if key in data:
            data[key] = []
    if 'academics' in data:
        data['academics'] = {'majors': [], 'minors': [], 'concentrations': []}
    data['member_status'] = FORMER_MEMBER_LABEL
    data['former_member'] = True
    return data
