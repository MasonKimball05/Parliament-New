"""
v3.14.0 — WebSocket vote events.

Server-side helper that pings the `vote_updates` channels group whenever a
vote opens, closes, or receives a ballot. The payload is deliberately just an
event name + legislation id: clients react by re-running their existing tally
poll (which enforces per-user visibility), so no vote data ever travels over
the socket and nothing new can leak.

Failure is always non-fatal — if the channel layer is missing or Redis is
down, pages fall back to the 15-second poller.
"""
import logging

logger = logging.getLogger(__name__)

VOTE_GROUP = 'vote_updates'


def broadcast_vote_event(event, legislation_id):
    """event: 'opened' | 'closed' | 'tally'"""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is None:
            return
        async_to_sync(layer.group_send)(VOTE_GROUP, {
            'type': 'vote.event',
            'event': event,
            'leg_id': legislation_id,
        })
    except Exception as exc:  # never break the request over a push failure
        logger.debug(f'vote event broadcast failed ({event}, {legislation_id}): {exc}')
