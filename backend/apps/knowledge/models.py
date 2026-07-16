"""Knowledge Platform — Project DNA (ARCHITECTURE §7; RFQ_INTELLIGENCE §7).

Project DNA is the permanent, versioned master identity minted from an APPROVED
RFQ extraction (human-verified truth). Every downstream module reads it — the
purest "enter once, reuse everywhere".

Tenant-private by default. Semantic similarity search (pgvector embedding) is
deferred until the pgvector extension is provisioned; the structured DNA is
captured now.
"""

from django.db import models

from apps.core.models import PlatformBaseModel, TenantBaseModel


class ProjectDNA(TenantBaseModel):
    # Source of the DNA (the approved quotation/opportunity for now; the Project
    # once created on award).
    quotation = models.ForeignKey(
        "quotes.Quotation", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="project_dna",
    )
    version = models.PositiveIntegerField(default=1)

    # The structured project identity (RFQ_INTELLIGENCE §7).
    client_name = models.CharField(max_length=255, blank=True)
    site = models.CharField(max_length=255, blank=True)
    work_type = models.CharField(max_length=120, blank=True)
    scope = models.TextField(blank=True)
    materials = models.JSONField(default=list, blank=True)
    equipment = models.JSONField(default=list, blank=True)
    labour = models.JSONField(default=list, blank=True)
    risks = models.JSONField(default=list, blank=True)
    compliance_profile = models.JSONField(default=list, blank=True)
    commercial_terms = models.JSONField(default=dict, blank=True)
    estimated_value = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    ai_summary = models.TextField(blank=True)
    # embedding = VectorField(...)  # deferred until pgvector is available

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"DNA {self.client_name} · {self.work_type or 'general'} v{self.version}"


# ─────────────────────────── TIER 1: PRIVATE (per-tenant) ───────────────────────────
# A contractor's own knowledge. TenantBaseModel = auto-scoped, never crosses tenants.

class ClientProfile(TenantBaseModel):
    name = models.CharField(max_length=255)
    required_documents = models.JSONField(default=list, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField(default=30)
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="unique_client_profile")
        ]

    def __str__(self):
        return self.name


class MineProfile(TenantBaseModel):
    name = models.CharField(max_length=255)
    required_inductions = models.JSONField(default=list, blank=True)
    ppe_standards = models.JSONField(default=list, blank=True)
    permit_types = models.JSONField(default=list, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="unique_mine_profile")
        ]

    def __str__(self):
        return self.name


class WorkTypeTemplate(TenantBaseModel):
    name = models.CharField(max_length=120)
    typical_labour_hours = models.DecimalField(max_digits=8, decimal_places=1, default=0)
    common_hazards = models.JSONField(default=list, blank=True)
    recommended_compliance = models.JSONField(default=list, blank=True)

    def __str__(self):
        return self.name


class KnowledgeConfig(PlatformBaseModel):
    """Per-tenant opt-in to contribute to shared/aggregate knowledge (default
    OFF — a contractor keeps everything private and still gets their own back)."""

    company = models.OneToOneField(
        "identity.Company", on_delete=models.CASCADE, related_name="knowledge_config"
    )
    contribute_shared = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.company}: contribute={self.contribute_shared}"


# ───────────────── TIER 2: SHARED-ENTITY (de-identified, opt-in) ─────────────────
# Facts about EXTERNAL shared entities (mines/clients), never a contractor's secret.
# Source company is tracked internally for corroboration but NEVER exposed.

class SharedEntityFact(PlatformBaseModel):
    entity_type = models.CharField(max_length=24)  # mine | client | site
    entity_key = models.CharField(max_length=255)  # normalised name
    fact_type = models.CharField(max_length=48)     # e.g. required_document
    fact_value = models.CharField(max_length=500)
    corroboration_count = models.PositiveIntegerField(default=0)  # distinct companies
    confidence = models.FloatField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["entity_type", "entity_key", "fact_type", "fact_value"],
                name="unique_shared_fact",
            )
        ]

    def __str__(self):
        return f"{self.entity_key}:{self.fact_type}={self.fact_value} ({self.corroboration_count})"


class SharedFactContribution(models.Model):
    """Internal: which companies corroborated a fact. Never exposed via API —
    it only powers de-duplicated corroboration counting."""

    fact = models.ForeignKey(SharedEntityFact, on_delete=models.CASCADE, related_name="+")
    company = models.ForeignKey("identity.Company", on_delete=models.CASCADE, related_name="+")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["fact", "company"], name="unique_fact_contribution")
        ]

    def __str__(self):
        return f"contribution {self.fact_id}"


# ─────────────── TIER 3: AGGREGATE-ONLY (cross-tenant stats, k-anonymity) ───────────────

class AggregateSample(models.Model):
    """Internal raw samples. Exposed only as aggregates past the min-N threshold."""

    metric_key = models.CharField(max_length=64)   # e.g. labour_hours
    bucket = models.CharField(max_length=120)       # e.g. work_type:pump_replacement
    value = models.DecimalField(max_digits=14, decimal_places=2)
    company = models.ForeignKey("identity.Company", on_delete=models.CASCADE, related_name="+")

    class Meta:
        indexes = [models.Index(fields=["metric_key", "bucket"])]

    def __str__(self):
        return f"{self.metric_key}/{self.bucket}={self.value}"
