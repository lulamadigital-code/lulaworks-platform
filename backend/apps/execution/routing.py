"""WebSocket routes for the execution app (task chat)."""

from django.urls import re_path

from .consumers import NotificationConsumer, TaskChatConsumer

websocket_urlpatterns = [
    re_path(r"^ws/task-chat/(?P<task_id>[0-9a-fA-F-]{36})/$",
            TaskChatConsumer.as_asgi()),
    re_path(r"^ws/notifications/$", NotificationConsumer.as_asgi()),
]
