"""Ambient tenant context (DATA_MODEL §1).

The current tenant is held in a contextvar set by TenantMiddleware from the
JWT, and read by TenantManager to auto-scope every query. Celery tasks set it
explicitly from their payload. This is what makes "developers never remember
to filter" true — isolation is the default, enforced in one place.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_current_company_id: ContextVar[object | None] = ContextVar("current_company_id", default=None)
# When True, queries bypass tenant scoping (explicit, audited platform/system ops only).
_system_mode: ContextVar[bool] = ContextVar("system_mode", default=False)


def set_current_company(company_id) -> None:
    _current_company_id.set(company_id)


def get_current_company():
    return _current_company_id.get()


def clear_current_company() -> None:
    _current_company_id.set(None)


def in_system_mode() -> bool:
    return _system_mode.get()


@contextmanager
def system_scope():
    """Explicit, audited cross-tenant access for platform/Super-Admin ops."""
    token = _system_mode.set(True)
    try:
        yield
    finally:
        _system_mode.reset(token)


@contextmanager
def tenant_scope(company_id):
    """Bind a tenant for a block (used by Celery tasks and tests)."""
    token = _current_company_id.set(company_id)
    try:
        yield
    finally:
        _current_company_id.reset(token)
