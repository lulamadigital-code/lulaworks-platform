"""Real-time task chat consumer.

One WebSocket group per task ("task_chat_<id>"). A connected participant/manager
receives new messages the instant they're saved — the HTTP create path
broadcasts (see work_execution.broadcast_task_message). Sending still goes
through the HTTP endpoint (it handles photos, validation and the offline outbox),
so this consumer is receive-only; the 10s HTTP poll remains as a fallback where
the WS service isn't reachable.
"""

import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer


class TaskChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.task_id = self.scope["url_route"]["kwargs"]["task_id"]
        self.user = self.scope.get("user")
        if not self.user or not self.user.is_authenticated:
            await self.close()
            return
        if not await self._can_access():
            await self.close()
            return
        self.group = f"task_chat_{self.task_id}"
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if getattr(self, "group", None):
            await self.channel_layer.group_discard(self.group, self.channel_name)

    # Broadcast handler — the create path sends {"type": "chat.message", ...}.
    async def chat_message(self, event):
        await self.send(text_data=json.dumps(
            {"type": "message", "message": event["message"]}))

    # A newly-filed field report on this task.
    async def chat_report(self, event):
        await self.send(text_data=json.dumps(
            {"type": "report", "report": event["report"]}))

    @database_sync_to_async
    def _can_access(self):
        from apps.core.context import tenant_scope

        from .models import Task
        from .work_execution import can_access_task_chat
        # active_company scopes the tenant; the task must be in it and accessible.
        company_id = getattr(self.user, "active_company_id", None)
        if not company_id:
            return False
        with tenant_scope(company_id):
            task = Task.objects.filter(pk=self.task_id).first()
            if task is None:
                return False
            return can_access_task_chat(self.user, task)
