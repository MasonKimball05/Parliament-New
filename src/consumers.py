import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async


class ChatConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time chat.

    Clients connect at /ws/chat/<channel_id>/. The consumer joins the
    corresponding channel group and receives broadcast events from the
    HTTP send/edit/delete views. Messages are sent via HTTP POST (for
    auth, CSRF, and push-notification dispatch) — the consumer is
    receive-only from the client's perspective.

    Group name format: chat_{channel_id}
    """

    async def connect(self):
        self.channel_id = self.scope['url_route']['kwargs']['channel_id']
        self.group_name = f'chat_{self.channel_id}'

        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            # Reject by closing without accepting — Channels sends HTTP 403 response
            await self.close()
            return

        has_access = await self._check_read_permission(user, self.channel_id)
        if not has_access:
            await self.close()
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        # Messages are sent via HTTP POST (auth, CSRF, push dispatch).
        # The only client→server WS event is typing indicators.
        try:
            data = json.loads(text_data)
        except (json.JSONDecodeError, ValueError):
            return

        if data.get('type') == 'typing':
            user = self.scope['user']
            await self.channel_layer.group_send(
                self.group_name,
                {
                    'type': 'chat.typing',
                    'user_id': str(user.pk),
                    'name': getattr(user, 'name', user.username),
                }
            )

    # ── Group event handlers ──────────────────────────────────────────────────
    # Django Channels converts dots in `type` to underscores when dispatching,
    # so 'chat.message' → chat_message, 'chat.edit' → chat_edit, etc.

    async def chat_message(self, event):
        """Broadcast a new message to all connected clients in the group."""
        await self.send(text_data=json.dumps({
            'type': 'message',
            'message': event['message'],
        }))

    async def chat_edit(self, event):
        """Broadcast a message edit to all connected clients in the group."""
        await self.send(text_data=json.dumps({
            'type': 'edit',
            'message': event['message'],
        }))

    async def chat_delete(self, event):
        """Broadcast a message deletion to all connected clients in the group."""
        await self.send(text_data=json.dumps({
            'type': 'delete',
            'message_id': event['message_id'],
        }))

    async def chat_typing(self, event):
        """Broadcast a typing indicator to all clients in the group."""
        await self.send(text_data=json.dumps({
            'type': 'typing',
            'user_id': event['user_id'],
            'name': event['name'],
        }))

    # ── Helpers ───────────────────────────────────────────────────────────────

    @database_sync_to_async
    def _check_read_permission(self, user, channel_id):
        from src.models import ChatChannel
        try:
            channel = ChatChannel.objects.get(id=channel_id)
            return channel.can_read(user)
        except ChatChannel.DoesNotExist:
            return False


class VoteConsumer(AsyncWebsocketConsumer):
    """
    v3.14.0 — live vote-page updates.

    Clients connect at /ws/votes/ and join the shared `vote_updates` group.
    Receive-only: the server broadcasts {'event': 'opened'|'closed'|'tally',
    'leg_id': N} pings via src.utils.vote_events.broadcast_vote_event, and the
    page reacts by re-running its tally poll (which enforces per-user
    visibility server-side). No ballot data travels over the socket.
    """

    GROUP = 'vote_updates'

    async def connect(self):
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close()
            return
        await self.channel_layer.group_add(self.GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.GROUP, self.channel_name)

    async def receive(self, text_data):
        pass  # receive-only

    async def vote_event(self, event):
        await self.send(text_data=json.dumps({
            'event': event['event'],
            'leg_id': event['leg_id'],
        }))
