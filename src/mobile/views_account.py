"""
Deleting your account from the app (App Store guideline 5.1.1(v)).

* ``POST account/delete/`` — ``{"password": "..."}`` or ``{"reauth_grant": "..."}``
  → ``{"ok": true, "purge_on": "YYYY-MM-DD"}``. Closes the account
  (``src/account_deletion.py``): every app install, this one included, is
  signed out server-side as part of the request.
* ``POST account/delete/passkey/begin/`` → ``{challenge_id, options}``
* ``POST account/delete/passkey/complete/`` ``{challenge_id, credential}`` →
  ``{reauth_grant}`` — the Votes tab's passkey re-auth
  (``src/mobile/views_votes.py``), same code, same failure counter, same
  one-shot 120-second device-bound grant, but without the Votes page gate
  (pledges, or a chapter with voting turned off, can still delete).

Errors (never the bare codes the app signs out on): ``reauth_failed`` (wrong
password or a bad/expired passkey grant, 400), ``reauth_locked`` (429),
``password_required`` (400), ``not_allowed`` (a site admin, 403),
``already_deleted`` (400).
"""
from rest_framework import exceptions
from rest_framework.response import Response

from src import account_deletion as deletion
from src.mobile import views_votes
from src.mobile.api import MobileAPIView
from src.models.mobile import MobileAuthTicket


class AccountError(exceptions.APIException):
    status_code = 400
    default_code = 'account_error'

    def __init__(self, code, message, status=400):
        super().__init__(message, code=code)
        self.status_code = status


def _confirm_identity(request, data):
    from src.view.account_deletion import check_delete_password
    password, grant = data.get('password'), data.get('reauth_grant')
    if grant:
        ticket = MobileAuthTicket.consume(MobileAuthTicket.PURPOSE_DELETE_GRANT, grant,
                                          device=request.mobile_device)
        if ticket is None or ticket.user_id != request.user.pk:
            raise AccountError('reauth_failed', 'Passkey confirmation expired or missing — please try again.')
        return
    if not password:
        raise AccountError('password_required', 'Enter your password or confirm with a passkey.')
    ok, message = check_delete_password(request.user, password)
    if not ok:
        locked = 'Too many' in (message or '')
        raise AccountError('reauth_locked' if locked else 'reauth_failed', message, 429 if locked else 400)


class AccountDeleteView(MobileAPIView):

    def post(self, request):
        user = request.user
        data = request.data if isinstance(request.data, dict) else {}
        if user.is_admin:
            raise AccountError('not_allowed', "Site administrators can't delete their own account. "
                                              "Ask another admin to remove your admin access first.", 403)
        _confirm_identity(request, data)
        try:
            when = deletion.request_deletion(user, 'app', request=request._request)
        except deletion.DeletionRefused as exc:
            raise AccountError(exc.code, exc.message, 403 if exc.code == 'not_allowed' else 400)
        return Response({'ok': True, 'purge_on': when.isoformat()})


class AccountPasskeyBeginView(views_votes.PasskeyReauthBeginView):
    web_page = None


class AccountPasskeyCompleteView(views_votes.PasskeyReauthCompleteView):
    web_page = None
    # A grant from voting can't delete an account, nor this one cast a vote.
    grant_purpose = MobileAuthTicket.PURPOSE_DELETE_GRANT
