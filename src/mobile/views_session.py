"""
Short-lived tickets the app trades its access token for (mobile plan, Phase 1).

* ``POST /api/mobile/v1/auth/web-session/`` → ``{code}``: a 60-second,
  single-use code bound to THIS device. The app's web view posts it, together
  with the same device's access token (form field ``access_token`` or the
  ``Authorization: Bearer`` header), to ``/m/handoff/``
  (``src/mobile/handoff.py``), which turns it into an ordinary site session.
* ``POST /api/mobile/v1/ws-ticket/`` → ``{ticket}``: a 30-second, single-use
  websocket ticket bound to this device. The app offers it as the subprotocol
  ``parliament.ticket.<ticket>`` (alongside plain ``parliament.ticket``, which
  the server echoes); see ``src/mobile/ws.py``. Never in the URL: query
  strings end up in access logs.

Both views are ``MobileAPIView``s (``MobileTokenAuthentication`` only, so a
session cookie can never mint either; ``{"error": ...}`` errors).
"""
from rest_framework.response import Response

from src.mobile.api import MobileAPIView
from src.models.mobile import MobileAuthTicket


class _DeviceTicketView(MobileAPIView):
    purpose = None
    key = None

    def post(self, request):
        device = request.mobile_device
        raw, ticket = MobileAuthTicket.issue(self.purpose, user=device.user, device=device)
        response = Response({self.key: raw, 'expires_at': ticket.expires_at.isoformat()})
        response['Cache-Control'] = 'no-store'
        return response


class WebSessionCodeView(_DeviceTicketView):
    purpose = MobileAuthTicket.PURPOSE_WEB_HANDOFF
    key = 'code'


class WsTicketView(_DeviceTicketView):
    purpose = MobileAuthTicket.PURPOSE_WS
    key = 'ticket'
