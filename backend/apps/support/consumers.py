"""Real-time support chat consumer.

One WebSocket group per ticket ("support_<id>"). A connected client receives new
messages the instant they're saved (the save path broadcasts — see
services.add_message), plus typing and presence signals. Sending still goes through
the HTTP endpoint (handles files, CSRF, validation); this consumer also accepts a
`message` frame for a pure-WS send. Customers never receive internal notes.

The HTTP polling endpoints remain as a fallback, so chat still works where the WS
service isn't reachable.
"""

import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer


class SupportChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.ticket_id = self.scope["url_route"]["kwargs"]["ticket_id"]
        self.user = self.scope.get("user")
        if not self.user or not self.user.is_authenticated:
            await self.close()
            return
        auth = await self._authorize()
        if not auth:
            await self.close()
            return
        self.is_support, self.company_id = auth
        self.group = f"support_{self.ticket_id}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()
        await self._broadcast_presence(True)

    async def disconnect(self, code):
        if getattr(self, "group", None):
            await self._broadcast_presence(False)
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def receive(self, text_data=None, bytes_data=None):
        try:
            data = json.loads(text_data or "{}")
        except ValueError:
            return
        kind = data.get("type")
        if kind == "message":
            body = (data.get("body") or "").strip()
            internal = bool(data.get("internal")) and self.is_support
            if body:
                # add_message broadcasts to the group itself — no group_send here.
                await self._save(body, internal)
        elif kind == "typing":
            await self.channel_layer.group_send(self.group, {
                "type": "chat.typing",
                "is_support": self.is_support,
                "origin": self.channel_name,
            })

    # ── group event handlers ────────────────────────────────────────────────
    async def chat_message(self, event):
        # A customer never receives internal notes.
        if event.get("internal") and not self.is_support:
            return
        await self.send(text_data=json.dumps({"type": "message", "message": event["message"]}))

    async def chat_typing(self, event):
        if event.get("origin") == self.channel_name:      # don't echo to the typist
            return
        await self.send(text_data=json.dumps({
            "type": "typing", "is_support": event.get("is_support")}))

    async def chat_presence(self, event):
        if event.get("origin") == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            "type": "presence", "is_support": event.get("is_support"),
            "online": event.get("online")}))

    async def _broadcast_presence(self, online):
        await self.channel_layer.group_send(self.group, {
            "type": "chat.presence", "is_support": self.is_support,
            "online": online, "origin": self.channel_name})

    # ── DB helpers (sync, wrapped) ──────────────────────────────────────────
    @database_sync_to_async
    def _authorize(self):
        """(is_support, company_id) if this user may join the ticket, else None."""
        from .models import SupportTicket
        ticket = SupportTicket.all_objects.select_related("created_by").filter(
            pk=self.ticket_id).first()
        if ticket is None:
            return None
        u = self.user
        if getattr(u, "can_platform", None) and u.can_platform("support"):
            return (True, ticket.company_id)
        # Customer: must own the ticket, or manage the company AND be in it.
        owns = ticket.created_by_id == u.id
        manages = (getattr(u, "has_perm_code", None) and u.has_perm_code("company.manage")
                   and getattr(u, "active_company_id", None) == ticket.company_id)
        if owns or manages:
            return (False, ticket.company_id)
        return None

    @database_sync_to_async
    def _save(self, body, internal):
        from apps.core.context import tenant_scope

        from . import services as support
        from .models import SupportTicket
        with tenant_scope(self.company_id):
            ticket = SupportTicket.all_objects.filter(pk=self.ticket_id).first()
            if ticket is None:
                return
            try:
                support.add_message(ticket=ticket, sender=self.user, body=body,
                                    from_support=self.is_support, is_internal=internal)
            except support.SupportError:
                return
