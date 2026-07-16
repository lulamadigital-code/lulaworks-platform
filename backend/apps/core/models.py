"""Base models — every entity inherits these (DATA_MODEL §2).

- UUID primary keys (global uniqueness, no enumeration)
- audit stamps (created/updated at + by)
- soft delete
- tenant (company) for business entities

TenantBaseModel is the default for business data (auto-scoped). PlatformBaseModel
is for non-tenant platform tables (Company, Plan, Permission, ...).
"""

import uuid

from django.conf import settings
from django.db import models

from .context import get_current_company
from .managers import AllTenantsManager, SoftDeleteManager, TenantManager


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class AuditedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+", editable=False,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+", editable=False,
    )

    class Meta:
        abstract = True


class SoftDeleteModel(models.Model):
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+", editable=False,
    )

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False, hard=False):
        from django.utils import timezone

        if hard:
            return super().delete(using=using, keep_parents=keep_parents)
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=["is_deleted", "deleted_at"])


class PlatformBaseModel(UUIDModel, AuditedModel, SoftDeleteModel):
    """Non-tenant platform tables (Company, Plan, Permission, ...)."""

    objects = SoftDeleteManager()
    all_objects = AllTenantsManager()

    class Meta:
        abstract = True


class TenantBaseModel(UUIDModel, AuditedModel, SoftDeleteModel):
    """Every per-tenant business entity. Default manager auto-scopes to the
    current tenant and hides soft-deleted rows (fail-closed)."""

    company = models.ForeignKey(
        "identity.Company", on_delete=models.CASCADE, related_name="+", db_index=True
    )

    objects = TenantManager()
    all_objects = AllTenantsManager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        # Stamp the tenant from context if unset; refuse a cross-tenant write.
        current = get_current_company()
        if self.company_id is None and current is not None:
            self.company_id = current
        super().save(*args, **kwargs)


class DomainEvent(models.Model):
    """Transactional outbox (DATA_MODEL §13). Written in the same transaction as
    the state change; a relay dispatches to subscribers (Celery) and webhooks.
    Append-only; partition by month at scale."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(
        "identity.Company", on_delete=models.CASCADE, related_name="+", null=True
    )
    type = models.CharField(max_length=80, db_index=True)
    subject_type = models.CharField(max_length=80, blank=True)
    subject_id = models.UUIDField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["occurred_at"]
        indexes = [models.Index(fields=["type", "dispatched_at"])]

    def __str__(self):
        return f"{self.type} @ {self.occurred_at:%Y-%m-%d %H:%M}"
