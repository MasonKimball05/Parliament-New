"""
Mobile app push housekeeping (mobile plan, Phase 1).

check_expo_push_receipts — every 15 minutes: fetch Expo receipts for tickets
at least 15 minutes old, clear the push token of any device whose receipt
says DeviceNotRegistered, delete the processed tickets, and drop tickets older
than 24 hours whatever happened (Expo only keeps receipts for about a day).
See src/mobile/push.py.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

RECEIPT_DELAY = timedelta(minutes=15)
TICKET_MAX_AGE = timedelta(hours=24)
#: Upper bound per run so a backlog can't make one run unbounded.
MAX_TICKETS_PER_RUN = 10000


@shared_task(name='tasks.check_expo_push_receipts')
def check_expo_push_receipts():
    from src.mobile import push as expo
    from src.models.mobile import MobilePushTicket

    now = timezone.now()
    stale, _ = MobilePushTicket.objects.filter(created_at__lt=now - TICKET_MAX_AGE).delete()

    due = list(
        MobilePushTicket.objects
        .filter(created_at__lte=now - RECEIPT_DELAY)
        .order_by('created_at')
        .values_list('pk', 'ticket_id', 'device_id', 'push_token')[:MAX_TICKETS_PER_RUN]
    )
    processed = cleared = 0
    for start in range(0, len(due), expo.RECEIPT_BATCH):
        chunk = due[start:start + expo.RECEIPT_BATCH]
        try:
            receipts = expo.fetch_receipts([t[1] for t in chunk])
        except expo.ExpoTransientError as exc:
            # Next run tries again; the 24 h sweep bounds how long that lasts.
            logger.warning(f'[expo] receipt fetch failed, will retry next run: {exc}')
            break
        done_pks = []
        for pk, ticket_id, device_id, token in chunk:
            receipt = receipts.get(ticket_id)
            if receipt is None:
                continue  # not ready yet
            done_pks.append(pk)
            if receipt.get('status') == 'error':
                error = (receipt.get('details') or {}).get('error')
                if error == 'DeviceNotRegistered':
                    cleared += expo.clear_push_token(device_id, token)
                else:
                    logger.warning(
                        f'[expo] receipt error for device {device_id}: {error} '
                        f'{str(receipt.get("message", ""))[:200]}')
        MobilePushTicket.objects.filter(pk__in=done_pks).delete()
        processed += len(done_pks)

    if processed or cleared or stale:
        logger.info(
            f'[expo] receipts: processed={processed} tokens_cleared={cleared} '
            f'stale_deleted={stale}')
    return {'processed': processed, 'cleared': cleared, 'stale': stale}
