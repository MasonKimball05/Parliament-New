"""
Expo push sender (mobile plan, Phase 1).

Talks to Expo's HTTP/2 push API directly with ``requests`` rather than the
community-maintained ``exponent-server-sdk``: the API is two JSON endpoints.

* ``send`` — POST a JSON array of up to 100 messages; the response holds one
  *ticket* per message, in order. ``status: ok`` carries a ticket id (stored
  as a ``MobilePushTicket`` for the receipt check); ``status: error`` with
  ``details.error == 'DeviceNotRegistered'`` means the app is gone, so the
  device's push token is cleared at once.
* ``getReceipts`` — POST up to 1000 ticket ids; used by
  ``tasks.check_expo_push_receipts`` (src/tasks/mobile_push.py).

``Authorization: Bearer <EXPO_ACCESS_TOKEN>`` is sent when the setting is
set. With Enhanced Push Security on for the Expo project (it must be — system
check src.W020), Expo refuses sends without it, so a leaked
``ExponentPushToken`` alone cannot push to a member's phone.

Network errors, timeouts, HTTP 429 and 5xx raise ``ExpoTransientError`` so the
caller (a Celery task) can retry; other request-level failures are logged and
not retried — sending the same bad request again cannot succeed.

Message fields used are Expo's documented ones: ``to, title, body, data,
sound, badge, channelId``. Expo has no field for iOS ``thread-id``, so
notifications are not grouped per conversation on iOS (Android groups by app).
"""
import logging
import re

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

SEND_URL = 'https://exp.host/--/api/v2/push/send'
RECEIPTS_URL = 'https://exp.host/--/api/v2/push/getReceipts'
SEND_BATCH = 100
RECEIPT_BATCH = 1000
TIMEOUT = 10  # seconds, connect and read

_TOKEN_RE = re.compile(r'^Expo(nent)?PushToken\[[^\[\]\s]+\]$')


class ExpoTransientError(Exception):
    """A failure worth retrying: network, timeout, 429 or 5xx."""


def is_valid_push_token(token):
    return (isinstance(token, str) and len(token) <= 255
            and bool(_TOKEN_RE.match(token)))


def _headers():
    headers = {
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip, deflate',
        'Content-Type': 'application/json',
    }
    access_token = getattr(settings, 'EXPO_ACCESS_TOKEN', '') or ''
    if access_token:
        headers['Authorization'] = f'Bearer {access_token}'
    return headers


def _post(url, payload):
    """POST JSON to Expo; return the decoded body. Raises ExpoTransientError."""
    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=TIMEOUT)
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise ExpoTransientError(f'network error: {exc}') from exc
    except requests.RequestException as exc:
        raise ExpoTransientError(f'request failed: {exc}') from exc
    status = resp.status_code
    if status == 429 or status >= 500:
        raise ExpoTransientError(f'Expo returned HTTP {status}')
    try:
        body = resp.json()
    except ValueError:
        body = None
    if status >= 400 or not isinstance(body, dict):
        logger.error(f'[expo] request refused: HTTP {status} {str(body)[:300]}')
        return None
    if body.get('errors'):
        logger.error(f'[expo] request-level errors: {str(body["errors"])[:300]}')
    return body


def build_message(token, title, body, data=None, badge=None):
    msg = {
        'to': token,
        'title': title,
        'body': body,
        'data': data or {},
        'sound': 'default',
        'channelId': 'default',
    }
    if badge is not None:
        msg['badge'] = badge
    return msg


def live_push_devices(user_ids):
    """
    The devices of ``user_ids`` that may receive a push: not revoked, with a
    push token, AND passing ``MobileDevice.still_allowed`` (password changed,
    quarantined, Removed, refresh expired, past max age, 2FA now needed all
    sign a device out without setting ``revoked_at``). A device that fails has
    its push token cleared, so a signed-out phone stops showing pushes and the
    next run doesn't re-check it. Every push recipient selection goes through
    here.
    """
    from src.models.mobile import MobileDevice

    devices = list(
        MobileDevice.objects
        .filter(user_id__in=list(user_ids), revoked_at__isnull=True)
        .exclude(push_token='')
        .select_related('user')
        .order_by('pk')
    )
    live, dead = [], []
    for device in devices:
        ok, _reason = device.still_allowed()
        (live if ok else dead).append(device)
    if dead:
        MobileDevice.objects.filter(pk__in=[d.pk for d in dead]).update(push_token='')
        logger.info(f'[expo] cleared push token of {len(dead)} signed-out device(s)')
    return live


def clear_push_token(device_id, token):
    """Clear a device's token, but only if it is still ``token``."""
    from src.models.mobile import MobileDevice
    return MobileDevice.objects.filter(pk=device_id, push_token=token).update(push_token='')


def send_to_devices(devices, title, body, data=None, badge=None, sent_device_ids=None):
    """
    Send one notification to each device (objects with ``pk`` and
    ``push_token``), in batches of 100.

    ``sent_device_ids`` (a set, mutated) collects devices Expo accepted a
    message for — ok or a per-message error, either way not to be re-sent — so
    a caller retrying after ``ExpoTransientError`` can skip them. Returns the
    number of ok tickets.
    """
    from src.models.mobile import MobilePushTicket

    if sent_device_ids is None:
        sent_device_ids = set()
    devices = [d for d in devices if d.push_token and d.pk not in sent_device_ids]
    ok = 0
    for start in range(0, len(devices), SEND_BATCH):
        batch = devices[start:start + SEND_BATCH]
        messages = [build_message(d.push_token, title, body, data, badge) for d in batch]
        resp = _post(SEND_URL, messages)
        tickets = (resp or {}).get('data')
        if not isinstance(tickets, list):
            # Refused outright (logged by _post). Not retryable, so mark the
            # batch done rather than loop on it.
            sent_device_ids.update(d.pk for d in batch)
            continue
        rows = []
        for device, ticket in zip(batch, tickets):
            sent_device_ids.add(device.pk)
            if not isinstance(ticket, dict):
                continue
            if ticket.get('status') == 'ok' and ticket.get('id'):
                ok += 1
                rows.append(MobilePushTicket(
                    ticket_id=str(ticket['id'])[:100], device_id=device.pk,
                    push_token=device.push_token))
            elif ticket.get('status') == 'error':
                error = (ticket.get('details') or {}).get('error')
                if error == 'DeviceNotRegistered':
                    clear_push_token(device.pk, device.push_token)
                    logger.info(f'[expo] device {device.pk} not registered; push token cleared')
                else:
                    logger.warning(
                        f'[expo] ticket error for device {device.pk}: {error} '
                        f'{str(ticket.get("message", ""))[:200]}')
        if rows:
            MobilePushTicket.objects.bulk_create(rows, ignore_conflicts=True)
    return ok


def fetch_receipts(ticket_ids):
    """
    Fetch receipts for up to 1000 ``ticket_ids`` (one request). Returns a dict
    of ticket id -> receipt for the ids Expo answered for (a receipt that is
    not ready yet is simply absent). Raises ExpoTransientError.
    """
    ticket_ids = list(ticket_ids)
    if len(ticket_ids) > RECEIPT_BATCH:
        raise ValueError(f'at most {RECEIPT_BATCH} ids per getReceipts request')
    if not ticket_ids:
        return {}
    resp = _post(RECEIPTS_URL, {'ids': ticket_ids})
    data = (resp or {}).get('data')
    return data if isinstance(data, dict) else {}
