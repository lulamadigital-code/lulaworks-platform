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


class SSOProtocol(models.TextChoices):
    SAML = "saml", "SAML 2.0"
    OIDC = "oidc", "OpenID Connect"


class SSOStatus(models.TextChoices):
    NOT_CONFIGURED = "not_configured", "Not configured"
    CONFIGURED = "configured", "Configured (draft)"
    ACTIVATION_REQUESTED = "activation_requested", "Activation requested"
    ACTIVE = "active", "Active"   # OIDC live: members of allowed domains can sign in via SSO


class SSOConfig(TenantBaseModel):
    """A tenant's Single Sign-On configuration — the metadata their identity
    provider (Okta/Azure AD/Google/etc.) needs to federate with Lulaworks.

    This is a CONFIGURATION SURFACE only: it captures and stores IdP details and
    lets an admin request activation. It does NOT itself authenticate anyone —
    no SAML/OIDC handshake is wired yet — so it never reports SSO as live. Only
    non-secret metadata is stored here (SAML is public metadata; the OIDC client
    secret is exchanged directly with Lulaworks at activation, never persisted in
    this draft)."""
    protocol = models.CharField(max_length=8, choices=SSOProtocol.choices,
                                default=SSOProtocol.SAML)
    status = models.CharField(max_length=24, choices=SSOStatus.choices,
                              default=SSOStatus.NOT_CONFIGURED)

    # SAML (all public metadata — safe to store)
    saml_idp_entity_id = models.CharField(max_length=300, blank=True)
    saml_idp_sso_url = models.URLField(max_length=500, blank=True)
    saml_idp_x509_cert = models.TextField(blank=True)   # PEM/base64 signing cert

    # OIDC. Issuer + client id are not secrets; the client secret is stored
    # ENCRYPTED (Fernet, key derived from SECRET_KEY) and only ever read to sign
    # the server-side token exchange — never displayed.
    oidc_issuer = models.URLField(max_length=500, blank=True)
    oidc_client_id = models.CharField(max_length=300, blank=True)
    oidc_client_secret_enc = models.TextField(blank=True)

    # Email domains whose users are expected to sign in via SSO (comma-separated).
    allowed_domains = models.CharField(max_length=500, blank=True)
    notes = models.TextField(blank=True)

    requested_at = models.DateTimeField(null=True, blank=True)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company"], name="one_sso_config_per_company"),
        ]

    def __str__(self):
        return f"SSO ({self.get_protocol_display()}) — {self.company_id}"

    @property
    def is_requested(self) -> bool:
        return self.status == SSOStatus.ACTIVATION_REQUESTED

    @property
    def is_active(self) -> bool:
        return self.status == SSOStatus.ACTIVE

    def domain_list(self):
        return [d.strip().lower() for d in self.allowed_domains.split(",") if d.strip()]

    # ── OIDC client secret (encrypted at rest) ────────────────────────────────
    @property
    def has_client_secret(self) -> bool:
        return bool(self.oidc_client_secret_enc)

    def set_client_secret(self, raw: str):
        from .crypto import encrypt
        self.oidc_client_secret_enc = encrypt(raw) if raw else ""

    def get_client_secret(self) -> str:
        from .crypto import decrypt
        return decrypt(self.oidc_client_secret_enc)

    @property
    def oidc_ready(self) -> bool:
        """Enough OIDC detail present to attempt a real handshake."""
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret_enc)
