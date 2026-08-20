"""ASGI entrypoint.

HTTP is still served by the WSGI app under gunicorn in production; this ASGI app
adds a WebSocket protocol handler for the real-time support chat. A dedicated
Daphne process runs this to serve only /ws/ (nginx proxies WebSocket upgrades to
it), so the HTTP path is unaffected.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

# The Django HTTP application must be built before importing anything that touches
# models/apps (Channels routing imports consumers which import models).
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack           # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from apps.support.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
    ),
})
