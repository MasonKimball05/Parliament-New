"""
Websocket sign-in for the app (mobile plan, Phase 1).

The app can't hand a socket its bearer token: React Native's ``WebSocket``
can't be trusted to send arbitrary headers on every platform, and a token in
the URL ends up in access logs. Instead it gets a 30-second, single-use ticket
from ``POST /api/mobile/v1/ws-ticket/`` and offers it as a subprotocol::

    new WebSocket(url, ['parliament.ticket', 'parliament.ticket.' + ticket])

``MobileTicketWebsocketMiddleware`` (Parliament/asgi.py, inside
``AuthMiddlewareStack``) consumes the ticket and, if the device is still
allowed, sets ``scope['user']`` to the device's user. The consumers echo plain
``parliament.ticket`` (``accept_kwargs``) — a server must answer with one of
the offered protocols, and echoing the one carrying the secret would put it in
a response header for nothing.

Rules:

* only when the socket has NO signed-in session user — a ticket never
  replaces or upgrades a cookie session;
* never from the query string (not read anywhere);
* a ticket-authenticated socket is NOT necessarily one that passed a second
  factor: a device with ``verified_via='password'`` signed in with a password
  alone, because the member had no second factor and none was required. What
  makes the ticket enough is ``still_allowed``, re-checked here when the
  ticket is consumed: it fails for such a device as soon as the member has a
  confirmed 2FA device or the policy requires one (``second_factor_required``),
  so a ticket socket is never weaker than the website's own 2FA rules.
  ``SecondFactorWebsocketMiddleware`` skips such scopes.

Origin: see ``TicketAwareOriginValidator``.
"""
import logging

from channels.db import database_sync_to_async
from channels.security.websocket import AllowedHostsOriginValidator

logger = logging.getLogger(__name__)

SUBPROTOCOL = 'parliament.ticket'
TICKET_PREFIX = SUBPROTOCOL + '.'
#: Scope key set when the socket was signed in by a ticket.
SCOPE_FLAG = 'mobile_ticket_auth'


def ticket_from_scope(scope):
    for proto in scope.get('subprotocols') or ():
        if isinstance(proto, str) and proto.startswith(TICKET_PREFIX):
            return proto[len(TICKET_PREFIX):]
    return None


def accept_kwargs(scope):
    """``accept()`` kwargs: echo ``parliament.ticket`` when the client offered it."""
    if SUBPROTOCOL in (scope.get('subprotocols') or ()):
        return {'subprotocol': SUBPROTOCOL}
    return {}


def authenticate_ticket(raw):
    """Sync. Consume a websocket ticket; (user, device) or None."""
    from src.models.mobile import MobileAuthTicket

    ticket = MobileAuthTicket.consume(MobileAuthTicket.PURPOSE_WS, raw)
    if ticket is None or ticket.device_id is None:
        return None
    device = ticket.device
    user = device.user
    if ticket.user_id != device.user_id:
        return None
    ok, reason = device.still_allowed(user)
    if not ok:
        logger.info('websocket ticket refused: device %s %s', device.pk, reason)
        return None
    return user, device


class MobileTicketWebsocketMiddleware:
    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        raw = ticket_from_scope(scope)
        user = scope.get('user')
        if raw and not getattr(user, 'is_authenticated', False):
            result = await database_sync_to_async(authenticate_ticket)(raw)
            if result is not None:
                scope = dict(scope, user=result[0], mobile_device=result[1], **{SCOPE_FLAG: True})
        return await self.inner(scope, receive, send)


class TicketAwareOriginValidator:
    """
    ``AllowedHostsOriginValidator``, except that a socket signed in by a
    ticket may arrive with NO ``Origin`` header.

    Browsers always send Origin on a websocket, and that is what makes the
    validator useful: it stops another site's page from opening a socket with
    the member's cookies. React Native's WebSocket normally sends one too
    (Android and iOS both derive it from the URL), but that is library
    behaviour, not a guarantee, and a missing Origin can't come from a
    browser page. A ticket socket carries no ambient credential, so there is
    nothing for a cross-site page to ride on. A *foreign* Origin is still
    refused, ticket or not. Sits inside the ticket middleware so it can see
    the flag.
    """

    def __init__(self, inner):
        self.inner = inner
        self.validator = AllowedHostsOriginValidator(inner)

    async def __call__(self, scope, receive, send):
        has_origin = any(name == b'origin' for name, _ in scope.get('headers', ()))
        if not has_origin and scope.get(SCOPE_FLAG):
            return await self.inner(scope, receive, send)
        return await self.validator(scope, receive, send)
