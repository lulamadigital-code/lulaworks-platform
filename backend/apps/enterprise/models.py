"""Enterprise governance data — the audit trail and programmatic API keys.

Both are tenant-scoped (a company only ever sees its own) and both are gated on
Enterprise entitlements (`audit_log`, `api_access`) at the surfaces that read or
authenticate with them — the *data* is captured regardless, so switching a tenant
onto Enterprise instantly reveals a real history rather than an empty page.

Audit events are append-only: they inherit soft-delete for the tenant manager's
sake but nothing in the app ever edits or deletes one.
"""
from django.conf import settings
from django.db import models

from apps.core.models import TenantBaseModel


class AuditAction(models.TextChoices):
    LOGIN = "login", "Signed in"
    LOGIN_FAILED = "login_failed", "Failed sign-in"
    LOGOUT = "logout", "Signed out"
    USER_INVITED = "user_invited", "User invited"
    USER_REMOVED = "user_removed", "User removed"
    ROLE_CHANGED = "role_changed", "Role changed"
    PLAN_CHANGED = "plan_changed", "Plan changed"
    API_KEY_CREATED = "api_key_created", "API key created"
    API_KEY_REVOKED = "api_key_revoked", "API key revoked"
    DATA_EXPORT = "data_export", "Data exported"
    SETTINGS_CHANGED = "settings_changed", "Settings changed"
    SSO_CONFIGURED = "sso_configured", "SSO configured"


#: Security-relevant actions, surfaced first in the audit viewer's filter.
SECURITY_ACTIONS = {
    AuditAction.LOGIN, AuditAction.LOGIN_FAILED, AuditAction.LOGOUT,
    AuditAction.ROLE_CHANGED, AuditAction.API_KEY_CREATED,
    AuditAction.API_KEY_REVOKED, AuditAction.SSO_CONFIGURED,
}


class AuditEvent(TenantBaseModel):
    """One recorded action in a tenant's history. Actor details are snapshotted
    (``actor_label``) so the row still reads correctly if the user is later
    removed."""
    action = models.CharField(max_length=32, choices=AuditAction.choices, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")
    actor_label = models.CharField(max_length=200, blank=True)   # "Name <email>" snapshot
    summary = models.CharField(max_length=300)                   # human-readable line
    target_type = models.CharField(max_length=60, blank=True)    # e.g. "membership"
    target_id = models.CharField(max_length=120, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["company", "-created_at"]),
            models.Index(fields=["company", "action"]),
        ]

    def __str__(self):
        return f"{self.get_action_display()} — {self.summary}"

    @property
    def is_security(self) -> bool:
        return self.action in SECURITY_ACTIONS


class ApiKey(TenantBaseModel):
    """A tenant's programmatic API key. Only the SHA-256 hash is stored; the raw
    token is shown once at creation and never again. Authentication additionally
    requires the tenant to still hold the ``api_access`` entitlement, so losing
    Enterprise cleanly disables every key without deleting it."""
    name = models.CharField(max_length=120)                      # human label
    token_prefix = models.CharField(max_length=16, db_index=True)  # visible id, e.g. lwk_ab12cd34
    last_four = models.CharField(max_length=4)                   # for display
    key_hash = models.CharField(max_length=64)                  # sha256 hex of full token
    scopes = models.JSONField(default=list, blank=True)          # reserved for future scoping
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "revoked_at"])]

    def __str__(self):
        return f"{self.name} ({self.token_prefix}…{self.last_four})"

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
