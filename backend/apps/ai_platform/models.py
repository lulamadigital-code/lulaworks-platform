"""AI credit engine (DATA_MODEL §9; AI_PLATFORM §8).

Credits as an append-only LEDGER (auditable, no lost updates); usage logged per
request. Provider adapters (Claude/OpenAI/Gemini) arrive in Phase 8 — the
foundation is the metering + gateway interface.
"""

import uuid

from django.conf import settings
from django.db import models

from apps.core.models import PlatformBaseModel, TenantBaseModel


class PromptTemplate(PlatformBaseModel):
    """Versioned prompt registry (AI_PLATFORM §6) — prompts are never hardcoded
    in application code. Each agent owns versioned prompts; only one is active."""

    agent = models.CharField(max_length=48)  # rfq_extraction, quote_writer, ...
    key = models.CharField(max_length=64)
    version = models.CharField(max_length=16, default="v1")
    content = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["agent", "-version"]
        constraints = [
            models.UniqueConstraint(fields=["agent", "version"], name="unique_prompt_version")
        ]

    def __str__(self):
        return f"{self.agent}/{self.version}"


class AIProviderSetting(PlatformBaseModel):
    """Admin-tunable provider configuration — everything EXCEPT the API key.

    Keys never live here (or anywhere in the DB): they stay in the environment /
    secrets manager, so there is nothing sensitive to encrypt or leak. This holds
    only the non-secret switches an admin flips without a deploy: whether a
    provider is enabled, its tie-break priority, and an optional model override.
    Platform-level (one row per provider) because the keys it complements are
    global to the deployment.
    """

    provider = models.CharField(max_length=24, unique=True)  # gemini|claude|openai
    enabled = models.BooleanField(default=True)
    priority = models.PositiveSmallIntegerField(default=100)  # lower = tried first
    model_override = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["priority", "provider"]

    def __str__(self):
        return f"{self.provider} ({'on' if self.enabled else 'off'})"


class AICreditLedger(models.Model):
    """Append-only credit ledger. Balance = latest balance_after."""

    class EntryType(models.TextChoices):
        ALLOCATION = "allocation", "Monthly allocation"
        TOPUP = "topup", "Purchased top-up"
        CONSUMPTION = "consumption", "Consumption"
        ADJUSTMENT = "adjustment", "Adjustment"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("identity.Company", on_delete=models.CASCADE, related_name="+")
    entry_type = models.CharField(max_length=16, choices=EntryType.choices)
    credits = models.DecimalField(max_digits=12, decimal_places=2)  # +/-
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    source = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "created_at"])]

    def __str__(self):
        return f"{self.entry_type} {self.credits} → {self.balance_after}"


class AIUsageLog(models.Model):
    """Per-request AI usage (append-only, high-volume → partition by month)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("identity.Company", on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    provider = models.CharField(max_length=24)  # claude|openai|gemini
    agent = models.CharField(max_length=48, blank=True)
    #: The task category routed (extraction/reasoning/…) and the feature name.
    task = models.CharField(max_length=32, blank=True)
    prompt_name = models.CharField(max_length=64, blank=True)
    #: Correlates every attempt of one logical request (primary + any failovers).
    request_id = models.UUIDField(null=True, blank=True, db_index=True)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    cost = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    credits_used = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    execution_ms = models.PositiveIntegerField(default=0)
    #: ok | failover (this provider failed, trying the next) | error (all failed)
    status = models.CharField(max_length=16, default="ok")
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.provider}/{self.agent} {self.credits_used}cr"


class ApprovalStatus(models.TextChoices):
    DRAFT = "draft", "Draft (awaiting human review)"
    APPROVED = "approved", "Approved by human"
    REJECTED = "rejected", "Rejected by human"


class AIInteraction(TenantBaseModel):
    """The AI audit record + governance state (AI_PLATFORM §8-9). Every LulaAI/agent
    run is a DRAFT until a human decides. Rejected suggestions are kept — they feed
    the prompt learning loop. The AI never commits a business side-effect: approving
    an interaction records the human's acceptance of the DRAFT, it does not execute.
    """

    request_text = models.TextField()
    agent = models.CharField(max_length=48, default="lulaai")  # lulaai = orchestrator
    prompt_version = models.CharField(max_length=16, default="v1")
    provider = models.CharField(max_length=24, default="deterministic")
    result = models.JSONField(default=dict, blank=True)      # the consolidated draft
    confidence = models.DecimalField(max_digits=4, decimal_places=3, default=0)  # 0-1
    approval_status = models.CharField(
        max_length=12, choices=ApprovalStatus.choices, default=ApprovalStatus.DRAFT
    )
    # Optional business entity the interaction concerns (e.g. a project).
    entity_type = models.CharField(max_length=48, blank=True)
    entity_id = models.UUIDField(null=True, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "approval_status"])]

    def __str__(self):
        return f"{self.agent}: {self.request_text[:40]} [{self.approval_status}]"
