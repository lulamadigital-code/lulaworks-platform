"""Safety & Compliance Intelligence (COMPLIANCE.md / Module 8) — the signature module.

Compliance is a COMPUTED READINESS GATE, not a document store. The engine answers
one question continuously: *can this project legally and safely start today?*

  mandatory ComplianceItems all satisfied (approved + unexpired)  OR  authorised
  override  →  Work Readiness = pass  →  project may enter execution

Every requirement records its SOURCE + CONFIDENCE (Confidence-Engine pattern);
overrides are permanently audited (the gate never silently opens).
"""

from django.conf import settings
from django.db import models

from apps.core.models import TenantBaseModel


class ComplianceCategory(models.TextChoices):
    DOCUMENTATION = "documentation", "Documentation"
    TRAINING = "training", "Training"
    MEDICAL = "medical", "Medical"
    EQUIPMENT = "equipment", "Equipment"
    PERMIT = "permit", "Permit"
    PPE = "ppe", "PPE"
    INDUCTION = "induction", "Induction"
    INSURANCE = "insurance", "Insurance"


class RequirementSource(models.TextChoices):
    CUSTOMER = "customer", "Customer requirement"
    MINE = "mine", "Mine profile"
    SITE = "site", "Site profile"
    RFQ = "rfq", "RFQ requirement"
    CONTRACT = "contract", "Contract"
    WORK_TYPE = "work_type", "Work-type library"
    EQUIPMENT = "equipment", "Equipment requirement"
    POLICY = "policy", "Company policy"
    REGULATORY = "regulatory", "Regulatory knowledge base"
    AI = "ai", "AI recommendation"


class ComplianceRequirement(TenantBaseModel):
    """Library entry — a requirement that MAY apply to a project. The discovery
    engine composes a project-specific checklist by matching `applies_when`
    against the project's work type / mine / site (COMPLIANCE §3-5)."""

    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=16, choices=ComplianceCategory.choices)
    source = models.CharField(max_length=16, choices=RequirementSource.choices)
    is_mandatory = models.BooleanField(default=True)
    # applies_when: {"work_types": [...], "mines": [...], "sites": [...]}.
    # Empty list on a key = "applies to all" for that dimension.
    applies_when = models.JSONField(default=dict, blank=True)
    default_valid_days = models.PositiveIntegerField(null=True, blank=True)  # expiry horizon
    confidence = models.DecimalField(max_digits=4, decimal_places=3, default=1)  # 0-1
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="unique_requirement_code")
        ]

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"

    def applies_to(self, project) -> bool:
        aw = self.applies_when or {}
        for key, value in (("work_types", project.work_type), ("mines", project.mine),
                           ("sites", project.site)):
            wanted = aw.get(key)
            if wanted and value not in wanted:
                return False
        return True


class ItemStatus(models.TextChoices):
    MISSING = "missing", "Missing"
    PENDING = "pending", "Pending"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    EXPIRED = "expired", "Expired"


class ComplianceItem(TenantBaseModel):
    """A requirement instantiated against a project. Its status + expiry drive the
    readiness gate. Carries source + confidence (why it's required)."""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="compliance_items"
    )
    requirement = models.ForeignKey(
        ComplianceRequirement, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    category = models.CharField(max_length=16, choices=ComplianceCategory.choices)
    name = models.CharField(max_length=255)
    source = models.CharField(max_length=16, choices=RequirementSource.choices)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, default=1)
    is_mandatory = models.BooleanField(default=True)
    status = models.CharField(max_length=12, choices=ItemStatus.choices, default=ItemStatus.MISSING)
    document = models.ForeignKey(
        "storage.StorageFile", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    valid_from = models.DateField(null=True, blank=True)
    expiry = models.DateField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["project", "category", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["project", "requirement"], name="unique_item_per_requirement"
            )
        ]

    def __str__(self):
        return f"{self.name} · {self.status}"

    @property
    def is_satisfied(self) -> bool:
        """Satisfied = approved and not past expiry."""
        from django.utils import timezone
        if self.status != ItemStatus.APPROVED:
            return False
        if self.expiry and self.expiry < timezone.localdate():
            return False
        return True


class ComplianceOverride(TenantBaseModel):
    """Authorised passage past the gate — permanently audited (COMPLIANCE §10).
    A null requirement means a whole-project override. Immutable: no update path."""

    project = models.ForeignKey(
        "projects.Project", on_delete=models.CASCADE, related_name="compliance_overrides"
    )
    requirement = models.ForeignKey(
        ComplianceRequirement, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    authorised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    reason = models.TextField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        scope = self.requirement.name if self.requirement else "whole project"
        return f"Override: {scope}"
