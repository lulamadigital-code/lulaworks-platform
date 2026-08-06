"""Gateway registry. `get_gateway()` returns the configured provider; providers
register by their `code`. Adding a provider = drop a new module + register()."""

from django.conf import settings

from .base import PaymentGateway  # noqa: F401  (re-exported)

_REGISTRY: dict = {}


def register(gateway_cls):
    _REGISTRY[gateway_cls.code] = gateway_cls
    return gateway_cls


def get_gateway(code: str = None) -> PaymentGateway:
    """The gateway to use. Explicit `code` wins; otherwise settings.PAYMENT_GATEWAY;
    otherwise 'mock' (safe offline default)."""
    _ensure_loaded()
    code = code or getattr(settings, "PAYMENT_GATEWAY", "mock") or "mock"
    cls = _REGISTRY.get(code) or _REGISTRY["mock"]
    return cls()


def available_gateways() -> list:
    _ensure_loaded()
    return sorted(_REGISTRY)


def _ensure_loaded():
    # Import provider modules so their @register runs (idempotent).
    if not _REGISTRY:
        from . import mock, paystack_gateway, stripe_gateway  # noqa: F401
