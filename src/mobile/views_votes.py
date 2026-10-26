"""
The app's Votes tab: chapter ballots (mobile plan, Phase 3).

* ``GET  votes/`` — legislation on the ballot (available, not closed, not
  tabled/removed/pending), with whether voting has opened and whether the
  member has voted, plus the member's own eligibility (``me``).
* ``GET  votes/<id>/`` — one bill: description, options, threshold, document.
* ``POST votes/<id>/cast/`` — ``{"choices": [...]}`` (or ``"choice": "yes"``)
  plus re-auth: ``"password"`` OR ``"reauth_grant"`` (from the passkey flow).
  Returns the vote receipt.
* ``POST votes/reauth/passkey/begin/`` → ``{challenge_id, options}``
* ``POST votes/reauth/passkey/complete/`` ``{challenge_id, credential}`` →
  ``{reauth_grant}``: one-shot, 120 s, this device and member only.

The ballot itself is ``src/ballots.py``, the website's code: the same
attendance window, validation, locked insert, activity log and receipt.
Re-auth mirrors the web (``src/view/webauthn.py``): the password check is the
SAME function with the same per-member failure limit, and the passkey path
uses the same failure counter and user-verification requirement; where the web
keeps its grant in the session, the app gets a device-bound ticket
(``MobileAuthTicket.PURPOSE_VOTE_GRANT``).

Order matches the web: eligibility (member type, attendance) before re-auth,
so a member who can't vote anyway isn't asked to type a password.

Not here (web view): the author's own unreleased bills, uploading and
managing legislation, the officer attendance panel, receipt verification.
Committee ballots come with ``committees/<code>/votes/``.
"""
import json
import logging

from django.core.cache import cache
from django.http import Http404
from django.urls import reverse
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.response import Response

from src import ballots
from src.mobile.api import MobileAPIView, check_web_page
from src.models import Legislation, Vote
from src.models.mobile import MobileAuthTicket

security_logger = logging.getLogger('security')


class VoteRefused(exceptions.APIException):
    """A ballot or re-auth refusal: ``{"error": code, "detail": message}``."""
    status_code = 400
    default_code = 'vote_refused'

    def __init__(self, code, message, status=400):
        super().__init__(message, code=code)
        self.status_code = status


#: BallotError code → HTTP status.
_STATUS = {
    'not_eligible': 403, 'not_present': 403,
    'already_voted': 409, 'voting_closed': 409, 'not_started': 409, 'not_open': 409,
    'invalid_choice': 400,
}


def _on_ballot():
    """Bills the vote page lists to everyone (minus the author's own drafts)."""
    return (Legislation.objects
            .filter(available_at__lte=timezone.now(), voting_closed=False)
            .exclude(status__in=Legislation.CLOSED_STATUSES))


def _options(leg):
    if leg.vote_mode == 'plurality':
        return list(leg.plurality_options or [])
    return ['yes', 'no'] + (['abstain'] if leg.allow_abstain else [])


def _summary(leg, voted_ids):
    return {
        'id': leg.id,
        'title': leg.title,
        'vote_mode': leg.vote_mode,
        'voting_open': leg.voting_has_started(),
        'voting_starts_at': leg.get_voting_start_time().isoformat(),
        'voting_ends_at': leg.voting_ends_at.isoformat() if leg.voting_ends_at else None,
        'anonymous': leg.anonymous_vote,
        'voted': leg.id in voted_ids,
    }


class _VotesBase(MobileAPIView):
    web_page = 'vote'


class VoteListView(_VotesBase):

    def get(self, request):
        user = request.user
        rows = list(_on_ballot()
                    .only('id', 'title', 'vote_mode', 'voting_manual_open', 'voting_starts_at',
                          'available_at', 'voting_ends_at', 'anonymous_vote')
                    .order_by('-available_at'))
        voted = set(Vote.objects.filter(user=user, legislation__in=rows)
                    .values_list('legislation_id', flat=True))
        attendance = ballots.attendance_ok(user)
        return Response({
            'me': {
                'can_vote': bool(user.can_vote),
                'attendance_ok': attendance,
                'can_cast_now': bool(user.can_vote) and attendance,
                'has_passkeys': user.webauthn_credentials.exists(),
            },
            'votes': [_summary(leg, voted) for leg in rows],
        })


class VoteDetailView(_VotesBase):

    def get(self, request, legislation_id):
        leg = _on_ballot().filter(pk=legislation_id).first()
        if leg is None:
            raise Http404
        voted = set(Vote.objects.filter(user=request.user, legislation=leg)
                    .values_list('legislation_id', flat=True))
        return Response({'vote': {
            **_summary(leg, voted),
            'description': leg.description,
            'options': _options(leg),
            'votes_allowed': (leg.plurality_votes_allowed or 1) if leg.vote_mode == 'plurality' else 1,
            'allow_abstain': leg.allow_abstain,
            'required_percentage': leg.required_percentage if leg.vote_mode == 'percentage' else None,
            'required_number': leg.required_number if leg.vote_mode == 'piecewise' else None,
            'document_url': (reverse('view_document', args=[leg.id]) if leg.document else None),
        }})


def _confirm_identity(request, data):
    """Password or passkey grant, exactly one. Raises VoteRefused."""
    from src.view.webauthn import VOTE_PW_LOCKED_MSG, check_vote_password
    password, grant = data.get('password'), data.get('reauth_grant')
    if bool(password) == bool(grant):
        raise VoteRefused('reauth_required', 'Confirm with your password or a passkey.', 401)
    if grant:
        ticket = MobileAuthTicket.consume(MobileAuthTicket.PURPOSE_VOTE_GRANT, grant,
                                          device=request.mobile_device)
        if ticket is None or ticket.user_id != request.user.pk:
            raise VoteRefused('reauth_failed',
                              'Passkey confirmation expired or missing — please try again.', 401)
        return
    ok, message = check_vote_password(request.user, password, request._request)
    if not ok:
        locked = message == VOTE_PW_LOCKED_MSG
        raise VoteRefused('reauth_locked' if locked else 'reauth_failed', message,
                          429 if locked else 401)


class VoteCastView(_VotesBase):

    def post(self, request, legislation_id):
        user = request.user
        # The web's cast endpoint is restrictable by its own url name.
        check_web_page(user, 'cast_vote', toggle=False)
        data = request.data if isinstance(request.data, dict) else {}
        try:
            ballots.check_eligible(user)
        except ballots.BallotError as exc:
            raise VoteRefused(exc.code, str(exc), _STATUS[exc.code])
        _confirm_identity(request, data)

        leg = Legislation.objects.filter(pk=legislation_id).first()
        if leg is None:
            raise Http404
        choices = data.get('choices')
        if choices is None and data.get('choice') is not None:
            choices = [data.get('choice')]
        if not isinstance(choices, list) or not all(isinstance(c, str) for c in choices):
            raise VoteRefused('invalid_choice', 'Send "choices" as a list of options.')
        try:
            result = ballots.cast(user, leg, choices, request._request)
        except ballots.BallotError as exc:
            raise VoteRefused(exc.code, str(exc), _STATUS[exc.code])
        return Response({'ok': True, 'receipt': result['receipt'],
                         'choices_count': len(result['votes'])})


# ── Passkey re-auth ─────────────────────────────────────────────────────────

def _reauth_counter(user):
    """The web's passkey re-auth failure counter: one limit for both."""
    from src.view.webauthn import _REAUTH_MAX_FAILURES, _REAUTH_WINDOW_SECONDS
    return f'passkey_reauth_attempts_{user.pk}', _REAUTH_MAX_FAILURES, _REAUTH_WINDOW_SECONDS


def _check_not_locked(user):
    key, limit, _ = _reauth_counter(user)
    if cache.get(key, 0) >= limit:
        raise VoteRefused('reauth_locked', 'Too many failed attempts. Try again in 15 minutes.', 429)


def _fail(user, why):
    key, _, window = _reauth_counter(user)
    cache.set(key, cache.get(key, 0) + 1, window)
    security_logger.warning(f'Mobile passkey vote re-auth failed for {user.username}: {why}')
    raise VoteRefused('reauth_failed', 'Passkey verification failed.', 401)


class PasskeyReauthBeginView(_VotesBase):

    def post(self, request):
        import webauthn
        from webauthn import options_to_json
        from webauthn.helpers.structs import (
            PublicKeyCredentialDescriptor, UserVerificationRequirement,
        )
        from src.mobile.views_auth import _b64url
        from src.view.webauthn import rp_config

        _check_not_locked(request.user)
        creds = list(request.user.webauthn_credentials.all())
        if not creds:
            raise VoteRefused('no_passkeys', 'No passkeys registered for this account.')
        rp_id, _ = rp_config(request._request)
        options = webauthn.generate_authentication_options(
            rp_id=rp_id,
            user_verification=UserVerificationRequirement.REQUIRED,
            allow_credentials=[PublicKeyCredentialDescriptor(id=bytes(c.credential_id))
                               for c in creds],
        )
        raw, ticket = MobileAuthTicket.issue(
            MobileAuthTicket.PURPOSE_PASSKEY_REAUTH, user=request.user,
            device=request.mobile_device, payload={'challenge': _b64url(options.challenge)})
        return Response({
            'challenge_id': raw,
            'expires_at': ticket.expires_at.isoformat(),
            'options': json.loads(options_to_json(options)),
        })


class PasskeyReauthCompleteView(_VotesBase):

    def post(self, request):
        import webauthn
        from webauthn import base64url_to_bytes
        from src.mobile.views_auth import expected_passkey_origins
        from src.models.webauthn import WebAuthnCredential
        from src.view.webauthn import rp_config

        user = request.user
        _check_not_locked(user)
        data = request.data if isinstance(request.data, dict) else {}
        challenge_id, credential = data.get('challenge_id'), data.get('credential')
        if not isinstance(challenge_id, str) or not isinstance(credential, (dict, str)):
            raise VoteRefused('invalid_request', 'challenge_id and credential are required.')

        ticket = MobileAuthTicket.consume(MobileAuthTicket.PURPOSE_PASSKEY_REAUTH, challenge_id,
                                          device=request.mobile_device)
        if ticket is None or ticket.user_id != user.pk:
            _fail(user, 'unknown, used or expired challenge')
        try:
            cred = json.loads(credential) if isinstance(credential, str) else credential
            cred_id = base64url_to_bytes(cred['rawId'])
            credential_json = json.dumps(cred)
        except Exception:
            _fail(user, 'malformed credential')
        # The credential must be this member's — no lateral movement.
        db_cred = WebAuthnCredential.objects.filter(credential_id=cred_id, user=user).first()
        if db_cred is None:
            _fail(user, 'credential is not theirs')
        rp_id, _ = rp_config(request._request)
        try:
            verification = webauthn.verify_authentication_response(
                credential=credential_json,
                expected_challenge=base64url_to_bytes(ticket.payload['challenge']),
                expected_rp_id=rp_id,
                expected_origin=expected_passkey_origins(request),
                credential_public_key=bytes(db_cred.public_key),
                credential_current_sign_count=db_cred.sign_count,
                # py_webauthn does NOT check the UV flag unless asked.
                require_user_verification=True,
            )
        except Exception as exc:
            _fail(user, exc)
        db_cred.mark_used(verification.new_sign_count)
        cache.delete(_reauth_counter(user)[0])
        raw, grant = MobileAuthTicket.issue(MobileAuthTicket.PURPOSE_VOTE_GRANT, user=user,
                                            device=request.mobile_device)
        return Response({'reauth_grant': raw, 'expires_at': grant.expires_at.isoformat()})
