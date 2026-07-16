"""AI credit engine (DATA_MODEL §9; AI_PLATFORM §8).

Credits as an append-only LEDGER (auditable, no lost updates); usage logged per
request. Provider adapters (Claude/OpenAI/Gemini) arrive in Phase 8 — the
foundation is the metering + gateway interface.
"""

import uuid

from django.conf import settings
from django.db import models


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


class AIUsageLog(models.Model):
    """Per-request AI usage (append-only, high-volume → partition by month)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey("identity.Company", on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    provider = models.CharField(max_length=24)  # claude|openai|gemini
    agent = models.CharField(max_length=48, blank=True)
    tokens_in = models.PositiveIntegerField(default=0)
    tokens_out = models.PositiveIntegerField(default=0)
    cost = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    credits_used = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    execution_ms = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, default="ok")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
