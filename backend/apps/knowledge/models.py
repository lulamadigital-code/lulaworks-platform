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


def history_import_upload_path(instance, filename):
    """Store imported historical documents under the tenant, by date."""
    return f"history_imports/{instance.company_id}/{filename}"


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


# ── Historical Business Import (AI OS §5, §22) ────────────────────────────────
# "Bring Your Business History": a contractor uploads its existing documents;
# we classify, extract entities, resolve them against the ERP, and stage the
# results for human confirmation before anything is written. The staging models
# make the pipeline safe — nothing enters the ERP until a person confirms.

class ImportBatch(TenantBaseModel):
    """One upload session of historical documents."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        REVIEW = "review", "Ready for review"
        COMMITTED = "committed", "Committed"
        FAILED = "failed", "Failed"

    label = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    document_count = models.PositiveIntegerField(default=0)
    entity_count = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    def __str__(self):
        return self.label or f"Import {self.pk}"


class ImportedDocument(TenantBaseModel):
    """One document inside a batch — classified, with its extracted text kept
    for entity extraction and later re-processing."""

    class DocType(models.TextChoices):
        QUOTATION = "quotation", "Quotation"
        CUSTOMER_PO = "customer_po", "Customer PO"
        SUPPLIER_QUOTE = "supplier_quote", "Supplier Quotation"
        SUPPLIER_INVOICE = "supplier_invoice", "Supplier Invoice"
        INVOICE = "invoice", "Customer Invoice"
        DELIVERY_NOTE = "delivery_note", "Delivery Note"
        RFQ = "rfq", "RFQ / Tender"
        JOB_REPORT = "job_report", "Job Report"
        PRICE_LIST = "price_list", "Price List"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        EXTRACTING = "extracting", "Extracting"
        MATCHING = "matching", "Matching"
        COMPLETED = "completed", "Completed"
        NEEDS_REVIEW = "needs_review", "Needs review"
        DUPLICATE = "duplicate", "Duplicate"
        FAILED = "failed", "Failed"

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="documents")
    job = models.ForeignKey("HistoricalJob", on_delete=models.SET_NULL, null=True,
                            blank=True, related_name="documents")
    filename = models.CharField(max_length=255)
    file = models.FileField(upload_to=history_import_upload_path, null=True, blank=True)
    file_hash = models.CharField(max_length=64, blank=True, db_index=True)  # SHA-256 of bytes
    duplicate_of = models.ForeignKey("self", on_delete=models.SET_NULL, null=True,
                                     blank=True, related_name="duplicates")
    doc_type = models.CharField(max_length=20, choices=DocType.choices, default=DocType.OTHER)
    doc_type_confidence = models.FloatField(default=0.0)
    text = models.TextField(blank=True)          # extracted plain text (capped on ingest)
    text_chars = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    failed = models.BooleanField(default=False)  # kept in sync with status==FAILED
    processing_error = models.TextField(blank=True)
    attempts = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["company", "file_hash"]),
                   models.Index(fields=["batch", "status"])]

    def __str__(self):
        return f"{self.filename} ({self.get_doc_type_display()})"


class StagedEntity(TenantBaseModel):
    """A company/person found in an imported document, resolved against the ERP
    and awaiting human confirmation. Nothing is written to the ERP until this is
    confirmed — the guardrail that keeps historical import honest."""

    class Kind(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        SUPPLIER = "supplier", "Supplier"
        CONTACT = "contact", "Contact"

    class Verdict(models.TextChoices):
        MATCHED = "matched", "Matched"
        REVIEW = "review", "Needs review"
        NEW = "new", "New"

    class Review(models.TextChoices):
        PENDING = "pending", "Pending"
        LINKED = "linked", "Linked to existing"
        CREATED = "created", "Created new"
        REJECTED = "rejected", "Rejected"

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="entities")
    document = models.ForeignKey(ImportedDocument, on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name="entities")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    raw_name = models.CharField(max_length=255)
    email = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=64, blank=True)
    reference = models.CharField(max_length=64, blank=True)   # reg/VAT if seen
    dedup_key = models.CharField(max_length=255, blank=True, db_index=True)  # one row per real entity
    mentions = models.PositiveIntegerField(default=1)         # how many docs referenced it
    verdict = models.CharField(max_length=12, choices=Verdict.choices)
    confidence = models.FloatField(default=0.0)
    match_id = models.CharField(max_length=64, blank=True)    # candidate ERP id
    match_label = models.CharField(max_length=255, blank=True)
    review_status = models.CharField(max_length=12, choices=Review.choices,
                                     default=Review.PENDING)
    resolved_id = models.CharField(max_length=64, blank=True) # ERP id after commit

    class Meta:
        indexes = [models.Index(fields=["batch", "kind", "verdict"]),
                   models.Index(fields=["batch", "kind", "dedup_key"])]
        constraints = [
            # One staged entity per real thing in a batch (de-dup across documents).
            models.UniqueConstraint(
                fields=["batch", "kind", "dedup_key"],
                condition=models.Q(is_deleted=False) & ~models.Q(dedup_key=""),
                name="uniq_staged_entity_per_batch"),
        ]

    def __str__(self):
        return f"{self.raw_name} [{self.kind}/{self.verdict}]"


class HistoricalJob(TenantBaseModel):
    """A past job reconstructed from imported documents that belong together —
    the quotation, its customer PO, supplier quotes, delivery note and invoice
    that were one piece of work (AI OS §12). Proposed by the reconstructor with
    a confidence; a human confirms before it becomes company knowledge."""

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        CONFIRMED = "confirmed", "Confirmed"
        DISMISSED = "dismissed", "Dismissed"

    batch = models.ForeignKey(ImportBatch, on_delete=models.CASCADE, related_name="jobs")
    title = models.CharField(max_length=200)
    reference = models.CharField(max_length=120, blank=True)   # the shared ref that grouped them
    customer_id = models.CharField(max_length=64, blank=True)  # resolved customer, if known
    customer_name = models.CharField(max_length=255, blank=True)
    confidence = models.FloatField(default=0.0)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PROPOSED)
    evidence = models.TextField(blank=True)                    # human-readable "why these belong"

    class Meta:
        indexes = [models.Index(fields=["batch", "status"])]

    def __str__(self):
        return self.title or f"Historical job {self.pk}"
