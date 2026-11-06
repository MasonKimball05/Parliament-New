"""
Changing your password and your browser sessions in the app (batch 6), through
the website's own code (src/view/change_password.py, src/view/session_viewer.py).

* ``POST account/password/`` ``{old_password, new_password1, new_password2}``
  → ``{ok: true}``. Django's ``PasswordChangeForm`` (the same validators), the
  same 5-an-hour limit (``too_many_attempts``, 429) and the same side effects
  (``after_password_change``): every browser session and every OTHER app
  install is signed out, the change is logged and a watch flag alerts. As the
  website keeps the session that made the change (``update_session_auth_hash``),
  this keeps the install that made it: its fingerprint is moved to the new
  password, so its access and refresh tokens keep working, while every other
  install's stops matching (``MobileDevice.still_allowed``) and is revoked.
  Errors are ``invalid_input`` with ``fields`` (``{field: [messages]}``). The
  password is never logged or echoed.
* ``GET account/sessions/`` — the member's browser sessions (the Active
  Sessions page's list; the app's own installs are ``devices/``). Identified by
  row id: session keys are bearer secrets and never leave the server.
* ``POST account/sessions/<id>/revoke/`` — log one out (``end_session``).
* ``POST account/sessions/revoke-all/`` — "Log out all other sessions": every
  browser session and every other app install (``end_other_sessions``), this
  install kept.

Two-factor is views_two_factor.py; passkeys are views_passkeys.py (v4.4.3).
"""
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import get_object_or_404
from rest_framework import exceptions
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.models import UserSession
from src.view import session_viewer
from src.view.change_password import after_password_change, count_password_change_attempt

PASSWORD_FIELDS = ('old_password', 'new_password1', 'new_password2')


class TooManyAttempts(exceptions.APIException):
    status_code = 429
    default_code = 'too_many_attempts'
    default_detail = 'Too many password change attempts. Please wait an hour and try again.'


class ChangePasswordView(MobileAPIView):
    web_page = 'change_password'
    web_page_toggle = False   # change_password has no @require_page_enabled

    def post(self, request):
        user = request.user
        data = request.data if isinstance(request.data, dict) else {}
        if not count_password_change_attempt(user):
            raise TooManyAttempts()
        form = PasswordChangeForm(user=user, data={
            f: data.get(f) if isinstance(data.get(f), str) else '' for f in PASSWORD_FIELDS})
        if not form.is_valid():
            fields = {f: [str(m) for m in errors] for f, errors in form.errors.items()}
            first = next(iter(fields.values()), ['Please correct the error below.'])[0]
            return Response({'error': 'invalid_input', 'detail': first, 'fields': fields}, status=400)
        form.save()
        # The app's update_session_auth_hash: this install stays signed in.
        device = request.mobile_device
        device.auth_fingerprint = user.get_session_auth_hash()
        device.save(update_fields=['auth_fingerprint'])
        after_password_change(request._request, user, keep_mobile_device_id=device.pk)
        return Response({'ok': True})


def _session_json(s):
    return {
        'id': s.id,
        'device_type': s.device_type,
        'browser': s.browser,
        'operating_system': s.operating_system,
        'ip_address': s.ip_address,
        'location': s.location,
        'created_at': s.created_at.isoformat(),
        'last_activity': s.last_activity.isoformat(),
    }


class _Sessions(MobileAPIView):
    web_page = 'session_list'
    web_page_toggle = False   # session_list has no @require_page_enabled


class SessionListView(_Sessions):

    def get(self, request):
        return Response({'sessions': [_session_json(s) for s in session_viewer.sessions_for(request.user)]})


class SessionRevokeView(_Sessions):
    web_page = 'revoke_session'

    def post(self, request, session_id):
        session_viewer.end_session(request.user, get_object_or_404(UserSession, pk=session_id, user=request.user))
        return Response({'ok': True})


class SessionRevokeAllView(_Sessions):
    web_page = 'revoke_all_sessions'

    def post(self, request):
        count = session_viewer.end_other_sessions(request.user, keep_mobile_device_id=request.mobile_device.pk)
        return Response({'ok': True, 'revoked_count': count})
