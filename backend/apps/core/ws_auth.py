"""JWT auth for WebSockets.

The mobile app authenticates with a JWT bearer, not a session cookie, so the
default Channels AuthMiddlewareStack (session-based) leaves it anonymous. This
middleware reads an access token from the connection's query string
(`?token=<access>`) and resolves the user, overriding the session user only when
a valid token is present — so the session-based support chat keeps working
unchanged, and the JWT-based mobile chat works too.
"""

from urllib.parse import parse_qs

from channels.db import database_sync_to_async


@database_sync_to_async
def _user_from_token(raw_token):
    from django.contrib.auth import get_user_model
    from rest_framework_simplejwt.exceptions import TokenError
    from rest_framework_simplejwt.tokens import AccessToken
    try:
        token = AccessToken(raw_token)
        user_id = token["user_id"]
    except (TokenError, KeyError):
        return None
    User = get_user_model()
    return User.objects.filter(id=user_id, is_active=True).first()


class JWTAuthMiddleware:
    """Populate scope['user'] from a `?token=` access token when present."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        query = parse_qs((scope.get("query_string") or b"").decode())
        token = (query.get("token") or [None])[0]
        if token:
            user = await _user_from_token(token)
            if user is not None:
                scope = dict(scope)
                scope["user"] = user
        return await self.inner(scope, receive, send)
