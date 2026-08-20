"""WebSocket routes for the support app."""

from django.urls import re_path

from .consumers import SupportChatConsumer

websocket_urlpatterns = [
    re_path(r"^ws/support/(?P<ticket_id>[0-9a-fA-F-]{36})/$", SupportChatConsumer.as_asgi()),
]
