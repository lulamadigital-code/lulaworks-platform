"""RFQ Intelligence (RFQ_INTELLIGENCE §3-6).

Resumable pipeline record + the Confidence Engine (every extracted field carries
value + confidence + method + source + review status) + extracted BOQ lines.
"""

from django.conf import settings
from django.db import models

from apps.core.models import TenantBaseModel


class RFQStatus(models.TextChoices):
    UPLOADED = "uploaded", "Uploaded"
    EXTRACTED = "extracted", "Extracted"
    IN_REVIEW = "in_review", "In review"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class RFQDocument(TenantBaseModel):
    source_file = models.ForeignKey(
        "storage.StorageFile", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    original_name = models.CharField(max_length=255, blank=True)
    doc_class = models.CharField(max_length=40, default="rfq")
    status = models.CharField(
        max_length=16, choices=RFQStatus.choices, default=RFQStatus.UPLOADED
    )
    extracted_text = models.TextField(blank=True)
    warnings = models.JSONField(default=list, blank=True)
    quotation = models.ForeignKey(
        "quotes.Quotation", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    # An RFQ comes FROM someone, in a department, at a customer. Recording only
    # the company loses the person who can answer questions about it.
    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="rfqs",
    )
    department = models.ForeignKey(
        "customers.CustomerDepartment", on_delete=models.SET_NULL, null=True,
        blank=True, related_name="rfqs",
    )
    released_by = models.ForeignKey(
        "customers.CustomerContact", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="rfqs_released",
    )
    customer_reference = models.CharField(max_length=64, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"RFQ {self.original_name or self.id} ({self.status})"


class ExtractedField(TenantBaseModel):
    """Confidence Engine record (RFQ_INTELLIGENCE §6): original AI/deterministic
    value + confidence + final approved value — the learning substrate."""

    rfq = models.ForeignKey(RFQDocument, on_delete=models.CASCADE, related_name="fields")
    key = models.CharField(max_length=48)  # po_number, order_date, contact, ship_to...
    value = models.CharField(max_length=1000, blank=True)
    approved_value = models.CharField(max_length=1000, blank=True)
    confidence = models.FloatField(default=0)
    method = models.CharField(max_length=24, default="deterministic")
    source_text = models.CharField(max_length=500, blank=True)
    review_status = models.CharField(max_length=16, default="needs_review")

    class Meta:
        ordering = ["rfq", "key"]
        constraints = [
            models.UniqueConstraint(fields=["rfq", "key"], name="unique_field_per_rfq")
        ]

    def __str__(self):
        return f"{self.key}={self.value} ({self.confidence:.0%})"


class RFQLineItem(TenantBaseModel):
    rfq = models.ForeignKey(RFQDocument, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    description = models.CharField(max_length=500)
    qty = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unit = models.CharField(max_length=32, default="each")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    confidence = models.FloatField(default=0)

    class Meta:
        ordering = ["rfq", "position"]

    def __str__(self):
        return self.description[:60]
