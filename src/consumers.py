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


class ResolutionEditConsumer(AsyncWebsocketConsumer):
    """
    v3.42.0 — live editing on the C&B resolution edit page: presence, field
    locks, and "someone saved" updates. See `src/cnb_live.py` for the design.

    Clients connect at /ws/cnb/resolutions/<id>/. Only people who may edit
    that resolution are accepted (C&B permission or an Editor collaborator),
    and only while it is draft or pending — the same rule as the edit page.

    Saving is still an HTTP POST (auth, CSRF, the conflict check). The socket
    carries lock requests, the server's broadcasts, and (v3.42.2) the lock
    holder's draft text, which is relayed to the other editors for display
    and never stored.

    client → server   {t:'lock', f} · {t:'unlock', f} · {t:'draft', f, v} · {t:'ping'}
                      · {t:'takeover', f} (chair only). `f` is a form field or
                      `amend:<section id>` (the amendment editor).
    server → client   init · join · here · leave · lock · unlock · denied ·
                      draft · saved · amendments
    """

    #: v3.44.0 — True on `ResolutionWatchConsumer` (the `/watch/` route): someone
    #: looking at the resolution's own page. They see who is editing and get
    #: nothing else: no locks, no drafts, no saved text. They are not
    #: announced, cannot lock, and any signed-in member may connect (the same
    #: people who can open that page).
    observer = False

    #: What an observer is sent.
    _OBSERVER_SEES = {'init', 'join', 'here', 'leave'}

    async def connect(self):
        from src import cnb_live
        user = self.scope.get('user')
        if not user or not user.is_authenticated:
            await self.close()
            return
        self.resolution_id = int(self.scope['url_route']['kwargs']['resolution_id'])
        access = await self._access(user, self.resolution_id)
        if access is None or (not self.observer and not access['may_edit']):
            await self.close()
            return

        import secrets
        self.cid = secrets.token_hex(6)
        self.me = {'cid': self.cid, 'uid': str(user.pk), 'name': access['name']}
        self.is_chair = access['is_chair']
        self.held = set()
        self.group = cnb_live.group_name(self.resolution_id)

        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        if self.observer:
            await self._out({'t': 'init', 'me': self.me, 'locks': {}, 'chair': False, 'observer': True})
            # Ask the editors who is there; they answer this channel only.
            await self.channel_layer.group_send(self.group, {'type': 'res.who', 'channel': self.channel_name})
            return
        locks = await database_sync_to_async(cnb_live.current_locks)(self.resolution_id)
        await self._out({'t': 'init', 'me': self.me, 'locks': locks, 'chair': self.is_chair})
        await self.channel_layer.group_send(self.group, {
            'type': 'res.join', 'who': self.me, 'channel': self.channel_name,
        })

    async def disconnect(self, close_code):
        from src import cnb_live
        if not hasattr(self, 'group'):
            return
        if self.observer:
            await self.channel_layer.group_discard(self.group, self.channel_name)
            return
        for field in list(self.held):
            if await database_sync_to_async(cnb_live.release_lock)(self.resolution_id, field, self.cid):
                await self.channel_layer.group_send(self.group, {'type': 'res.unlock', 'f': field, 'cid': self.cid})
        await self.channel_layer.group_send(self.group, {'type': 'res.leave', 'cid': self.cid})
        await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive(self, text_data):
        from src import cnb_live
        try:
            data = json.loads(text_data)
        except (json.JSONDecodeError, ValueError):
            return
        if not isinstance(data, dict) or self.observer:
            return
        kind, field = data.get('t'), data.get('f')

        if kind == 'ping':
            await database_sync_to_async(cnb_live.refresh_locks)(self.resolution_id, list(self.held), self.cid)
            return
        if not cnb_live.lockable(field):
            return

        if kind == 'takeover':
            # v3.44.0 — the chair releases someone else's lock (a field left
            # locked by an open tab, or an amendment someone walked away from).
            if not self.is_chair:
                return
            holder = await database_sync_to_async(cnb_live.force_release)(self.resolution_id, field)
            if holder and holder.get('cid') != self.cid:
                await self.channel_layer.group_send(self.group, {
                    'type': 'res.unlock', 'f': field, 'cid': holder['cid'], 'taken_by': self.me['name'],
                })
            return

        if kind == 'lock':
            holder = await database_sync_to_async(cnb_live.acquire_lock)(self.resolution_id, field, self.me)
            if holder['cid'] == self.cid:
                self.held.add(field)
                await self.channel_layer.group_send(self.group, {'type': 'res.lock', 'f': field, 'who': self.me})
            else:
                await self._out({'t': 'denied', 'f': field, 'who': holder})
        elif kind == 'draft':
            # v3.42.2 — what the lock holder is typing, shown read-only in the
            # others' locked field. Relayed, never stored; only from the holder.
            value = data.get('v')
            if field in self.held and isinstance(value, str) and len(value) <= cnb_live.MAX_DRAFT_CHARS:
                await self.channel_layer.group_send(self.group, {
                    'type': 'res.draft', 'f': field, 'v': value, 'cid': self.cid,
                })
        elif kind == 'unlock':
            self.held.discard(field)
            if await database_sync_to_async(cnb_live.release_lock)(self.resolution_id, field, self.cid):
                await self.channel_layer.group_send(self.group, {'type': 'res.unlock', 'f': field, 'cid': self.cid})

    # ── Group events ──────────────────────────────────────────────────────────

    async def res_who(self, event):
        if not self.observer:
            await self.channel_layer.send(event['channel'], {'type': 'res.here', 'who': self.me})

    async def res_join(self, event):
        if event['who']['cid'] == self.cid:
            return
        if self.observer:
            await self._out({'t': 'join', 'who': event['who']})
            return
        await self._out({'t': 'join', 'who': event['who']})
        # Tell the newcomer we are here (to them only, not the whole group).
        await self.channel_layer.send(event['channel'], {'type': 'res.here', 'who': self.me})

    async def res_here(self, event):
        await self._out({'t': 'here', 'who': event['who']})

    async def res_leave(self, event):
        if event['cid'] != self.cid:
            await self._out({'t': 'leave', 'cid': event['cid']})

    async def res_lock(self, event):
        await self._out({'t': 'lock', 'f': event['f'], 'who': event['who']})

    async def res_unlock(self, event):
        if event.get('taken_by') and event['cid'] == getattr(self, 'cid', None):
            self.held.discard(event['f'])        # it was taken from me
        await self._out({'t': 'unlock', 'f': event['f'], 'cid': event['cid'],
                         'taken_by': event.get('taken_by', '')})

    async def res_draft(self, event):
        if event['cid'] != self.cid:
            await self._out({'t': 'draft', 'f': event['f'], 'v': event['v'], 'cid': event['cid']})

    async def res_saved(self, event):
        await self._out({'t': 'saved', 'by': event['by'], 'uid': event['uid'], 'fields': event['fields']})

    async def res_amendments(self, event):
        await self._out({'t': 'amendments', 'by': event['by'], 'uid': event['uid'], 'what': event.get('what', '')})

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _out(self, payload):
        if self.observer and payload.get('t') not in self._OBSERVER_SEES:
            return
        await self.send(text_data=json.dumps(payload))

    @database_sync_to_async
    def _access(self, user, resolution_id):
        """None if there is no such resolution, else name / may_edit / is_chair."""
        from src.models import Resolution
        from src.view.officer.cnb import _can_edit_resolution
        try:
            resolution = Resolution.objects.get(pk=resolution_id)
        except Resolution.DoesNotExist:
            return None
        return {
            'name': user.get_display_name(),
            'is_chair': bool(user.has_cnb_permission),
            'may_edit': resolution.status in ('draft', 'pending') and _can_edit_resolution(user, resolution),
        }


class ResolutionWatchConsumer(ResolutionEditConsumer):
    """
    v3.44.0 — `/ws/cnb/resolutions/<id>/watch/`: the read-only view of
    `ResolutionEditConsumer` for the resolution's own page. Presence only; see
    `observer` there. A subclass, not `as_asgi(observer=True)`: Channels'
    consumers accept init kwargs and ignore them.
    """
    observer = True
