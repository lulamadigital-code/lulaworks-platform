"""Quotation — the pre-award root of the RFQ-first lifecycle (BUSINESS_WORKFLOW
§5; RFQ-first correction). A Quotation exists before any Project; the Project is
created on award. Phase-1 thin slice: the first real TenantBaseModel business
resource, proving the foundation end-to-end.
"""

from decimal import Decimal

from django.db import models

from apps.core.models import TenantBaseModel


class QuotationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    AWARDED = "awarded", "Awarded"
    LOST = "lost", "Lost"


class Quotation(TenantBaseModel):
    number = models.CharField(max_length=32)
    title = models.CharField(max_length=255, blank=True)
    client_name = models.CharField(max_length=255)
    customer = models.ForeignKey(
        "customers.Customer", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="quotations",
    )
    site = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=16, choices=QuotationStatus.choices, default=QuotationStatus.DRAFT
    )
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("15.00"))
    validity_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="unique_quote_number")
        ]

    def __str__(self):
        return self.number

    @property
    def subtotal(self) -> Decimal:
        return sum((line.line_total for line in self.lines.all()), Decimal("0.00"))

    @property
    def vat_amount(self) -> Decimal:
        return (self.subtotal * self.vat_rate / Decimal("100")).quantize(Decimal("0.01"))

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.vat_amount


class QuotationLine(TenantBaseModel):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lines")
    position = models.PositiveSmallIntegerField(default=0)
    description = models.CharField(max_length=500)
    qty = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unit = models.CharField(max_length=32, default="each")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["quotation", "position"]

    def __str__(self):
        return self.description[:60]

    @property
    def line_total(self) -> Decimal:
        return (self.qty * self.unit_price).quantize(Decimal("0.01"))
