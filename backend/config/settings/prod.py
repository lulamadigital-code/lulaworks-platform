"""Production settings. Security hardened; HTTPS enforcement is env-gated so the
same image serves both the initial HTTP-only bring-up and the final TLS
deployment (DATA_MODEL §15)."""

from decouple import config

from .base import *  # noqa: F401,F403  # noqa: E402

DEBUG = False

# The container / load-balancer healthcheck hits the app on localhost over HTTP.
# Always allow the loopback names so the probe never 400s on DisallowedHost,
# whatever public ALLOWED_HOSTS the operator configured. These are only
# reachable from inside the container, so this doesn't widen the attack surface.
for _loopback in ("localhost", "127.0.0.1"):
    if _loopback not in ALLOWED_HOSTS:  # noqa: F405
        ALLOWED_HOSTS = ALLOWED_HOSTS + [_loopback]  # noqa: F405

# HTTPS enforcement. Secure by default; set SECURE_SSL_REDIRECT=False for the
# very first HTTP-only bring-up (before a TLS cert exists), then flip it back on
# once HTTPS is live. Cookie-Secure + HSTS ride the same flag so we never mark a
# cookie Secure-only (the browser would drop it) while still on plain HTTP.
_HTTPS = config("SECURE_SSL_REDIRECT", default=True, cast=bool)

SECURE_SSL_REDIRECT = _HTTPS
# Health probes (nginx / the load balancer) hit /health/ over plain HTTP on the
# internal network — they must get a 200, never a redirect to HTTPS.
SECURE_REDIRECT_EXEMPT = [r"^health/?$"]

SESSION_COOKIE_SECURE = _HTTPS
CSRF_COOKIE_SECURE = _HTTPS

SECURE_HSTS_SECONDS = 31536000 if _HTTPS else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# nginx terminates TLS and forwards the original scheme in X-Forwarded-Proto.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True

# S3 storage + CloudWatch logging wired here when the AWS account is provisioned.
