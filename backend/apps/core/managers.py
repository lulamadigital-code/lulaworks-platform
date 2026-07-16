"""Tenant-scoping + soft-delete managers (DATA_MODEL §1-2).

Fail-closed: if there is no tenant in context and we are not in an explicit
system scope, a tenant-scoped query raises rather than leaking cross-tenant
rows.
"""

from django.db import models

from .context import get_current_company, in_system_mode


class TenantMissingError(RuntimeError):
    """Raised when a tenant-scoped query runs without a tenant in context."""


class SoftDeleteQuerySet(models.QuerySet):
    def delete(self):
        # Soft delete by default (DATA_MODEL §2). Use .hard_delete() for real removal.
        return super().update(is_deleted=True)

    def hard_delete(self):
        return super().delete()

    def alive(self):
        return self.filter(is_deleted=False)


class TenantQuerySet(SoftDeleteQuerySet):
    def for_company(self, company):
        return self.filter(company=company)


class TenantManager(models.Manager):
    """Default manager: auto-scopes to the current tenant AND hides soft-deleted
    rows. Bypassed only inside an explicit `system_scope()`."""

    def get_queryset(self):
        qs = TenantQuerySet(self.model, using=self._db).filter(is_deleted=False)
        if in_system_mode():
            return qs
        company_id = get_current_company()
        if company_id is None:
            raise TenantMissingError(
                f"No tenant in context for {self.model.__name__}. Use an authenticated "
                "request, tenant_scope(), or system_scope() for platform ops."
            )
        return qs.filter(company_id=company_id)


class AllTenantsManager(models.Manager):
    """Explicit cross-tenant / includes-deleted access for platform ops, admin,
    migrations and tests. Never the default."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db)


class SoftDeleteManager(models.Manager):
    """For platform (non-tenant) tables: soft-delete aware, no tenant scoping."""

    def get_queryset(self):
        return SoftDeleteQuerySet(self.model, using=self._db).filter(is_deleted=False)
