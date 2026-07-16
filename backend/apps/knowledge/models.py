"""Knowledge Platform — Project DNA (ARCHITECTURE §7; RFQ_INTELLIGENCE §7).

Project DNA is the permanent, versioned master identity minted from an APPROVED
RFQ extraction (human-verified truth). Every downstream module reads it — the
purest "enter once, reuse everywhere".

Tenant-private by default. Semantic similarity search (pgvector embedding) is
deferred until the pgvector extension is provisioned; the structured DNA is
captured now.
"""

from django.db import models

from apps.core.models import TenantBaseModel


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
