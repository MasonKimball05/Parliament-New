"""
Account deletion pages (src/account_deletion.py has the rules).

* ``account/delete/`` — the member's own "Delete account" page, linked from
  Preferences → Security & Privacy. Confirms with the current password (or a
  passkey for a member with no password) and the word DELETE, then signs out.
* ``admin-v2/account-deletions/`` — pending deletions (Restore, at the
  member's request) and erased accounts (Rebuild account, at the member's
  request). Both actions are logged in AdminActionLog.
"""
import logging

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from src import account_deletion as deletion
from src.models import ParliamentUser
from src.view.admin_v2 import require_admin_v2_auth

security_logger = logging.getLogger('security')

_PW_MAX_FAILURES = 5
_PW_WINDOW_SECONDS = 900


def check_delete_password(user, password):
    """(ok, error_message) with a per-member failure limit. Shared with the app."""
    key = f'account_delete_pw_attempts_{user.pk}'
    failures = cache.get(key, 0)
    if failures >= _PW_MAX_FAILURES:
        return False, 'Too many incorrect password attempts. Try again in 15 minutes.'
    if not password or not isinstance(password, str) or not user.check_password(password):
        cache.set(key, failures + 1, _PW_WINDOW_SECONDS)
        security_logger.warning(f'Account deletion: wrong password for {user.username}')
        return False, 'Incorrect password.'
    cache.delete(key)
    return True, None


def _confirm_identity(request):
    user = request.user
    if request.POST.get('auth_method') == 'passkey':
        from src.view.webauthn import consume_passkey_reauth_grant
        if consume_passkey_reauth_grant(request):
            return True, None
        return False, 'Passkey confirmation expired or missing — please try again.'
    if not user.has_usable_password():
        return False, 'Confirm with your passkey.'
    return check_delete_password(user, request.POST.get('password', ''))


@login_required
@require_http_methods(['GET', 'POST'])
def delete_account(request):
    user = request.user
    context = {
        'is_admin_account': user.is_admin,
        'has_password': user.has_usable_password(),
        'has_passkeys': user.webauthn_credentials.exists(),
        'purge_days': deletion.PURGE_AFTER.days,
        'roles': [r.name for r in user.roles.all()],
    }
    if request.method == 'POST':
        error = None
        if user.is_admin:
            error = "Site administrators can't delete their own account. Ask another admin to remove your admin access first."
        elif (request.POST.get('confirm') or '').strip() != 'DELETE':
            error = 'Type DELETE to confirm.'
        else:
            ok, message = _confirm_identity(request)
            if not ok:
                error = message
        if error is None:
            try:
                when = deletion.request_deletion(user, 'web', request=request)
            except deletion.DeletionRefused as exc:
                error = exc.message
        if error:
            context['error'] = error
            return render(request, 'account/delete_account.html', context, status=400)
        logout(request)
        return render(request, 'account/delete_account_done.html', {'purge_on': when})
    return render(request, 'account/delete_account.html', context)


# ── Admin ───────────────────────────────────────────────────────────────────

def account_deletions_data():
    """The admin page's two lists, shared with the app (v4.4.4, src/mobile/views_site_admin_ops.py)."""
    closed = list(ParliamentUser.objects.closed_accounts()
                  .only('user_id', 'name', 'preferred_name', 'role_number', 'pledge_class',
                        'member_status', 'deletion_requested_at', 'deletion_requested_via',
                        'account_deleted_at')
                  .order_by('-deletion_requested_at')[:500])
    pending = [{'member': m, 'purge_on': deletion.purge_on(m)} for m in closed if not m.account_deleted_at]
    purged = [m for m in closed if m.account_deleted_at]
    return {'pending': pending, 'purged': purged, 'purge_days': deletion.PURGE_AFTER.days}


@require_admin_v2_auth
def admin_v2_account_deletions(request):
    from src.admin_console import console_enabled
    return render(request, 'admin_v2/console/account_deletions.html' if console_enabled(request)
                  else 'admin_v2/account_deletions.html', account_deletions_data())


@require_admin_v2_auth
@require_POST
def admin_v2_restore_account(request, user_id):
    member = get_object_or_404(ParliamentUser, user_id=user_id)
    try:
        deletion.restore_account(member, request.user, request=request)
        messages.success(request, f'Restored {member.get_display_name()}\'s account. They set a new password '
                                  f'with "Forgot password" to sign in.')
    except deletion.DeletionRefused as exc:
        messages.error(request, exc.message)
    return redirect('admin_v2_account_deletions')


@require_admin_v2_auth
@require_POST
def admin_v2_rebuild_account(request, user_id):
    member = get_object_or_404(ParliamentUser, user_id=user_id)
    try:
        deletion.rebuild_account(member, request.user, request.POST.get('email', ''),
                                 request.POST.get('username', ''), request=request)
        messages.success(request, f'Rebuilt an account for {member.get_display_name()}. A set-password link '
                                  f'was emailed to {member.email}.')
    except deletion.DeletionRefused as exc:
        messages.error(request, exc.message)
    return redirect('admin_v2_account_deletions')
