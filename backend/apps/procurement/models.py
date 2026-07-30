"""Procurement Intelligence (PROCUREMENT.md / Module 6).

⚠️ Naming: PurchaseOrder here is OUTBOUND (us → supplier), a *separate* entity
from the inbound client award. Supplier costs are financial — Golden-Rule gated.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.models import TenantBaseModel


def supplier_doc_upload_path(instance, filename):
    return f"c/{instance.company_id}/supplier_docs/{filename}"


class Supplier(TenantBaseModel):
    name = models.CharField(max_length=255)
    registration_no = models.CharField(max_length=64, blank=True)
    vat_no = models.CharField(max_length=32, blank=True)
    categories = models.JSONField(default=list, blank=True)  # ["Steel", "Bearings", ...]
    payment_terms = models.CharField(max_length=32, default="credit")
    our_account_no = models.CharField(max_length=64, blank=True)
    bank_name = models.CharField(max_length=128, blank=True)
    bank_account_no = models.CharField(max_length=64, blank=True)  # encrypted at rest in prod
    bee_level = models.PositiveSmallIntegerField(null=True, blank=True)
    insurance_expiry = models.DateField(null=True, blank=True)
    contact_person = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    preferred = models.BooleanField(default=False)
    performance_score = models.DecimalField(max_digits=5, decimal_places=2, default=0)  # 0-100
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="unique_supplier")
        ]

    def __str__(self):
        return self.name


class SupplierPrice(TenantBaseModel):
    """Append-only historical price ledger (PROCUREMENT §10) — feeds estimation
    and quote-anomaly detection."""

    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="prices")
    item_key = models.CharField(max_length=200, db_index=True)  # normalised description
    description = models.CharField(max_length=500)
    unit = models.CharField(max_length=32, default="each")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=8, default="ZAR")
    date = models.DateField()

    class Meta:
        ordering = ["-date"]
        indexes = [models.Index(fields=["company", "item_key"])]

    def __str__(self):
        return f"{self.description[:40]} @ {self.unit_price}"


class SupplierDocument(TenantBaseModel):
    """A file kept against a supplier — a quote, catalogue, BEE certificate,
    banking confirmation, or an old invoice uploaded to seed prices."""

    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE,
                                 related_name="documents")
    file = models.FileField(upload_to=supplier_doc_upload_path)
    name = models.CharField(max_length=255)
    doc_type = models.CharField(max_length=40, blank=True)  # invoice|quote|certificate|other
    content_type = models.CharField(max_length=120, blank=True)
    size_bytes = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name


# ─────────────────────── Supplier RFQ (outbound) → Supplier Quote (in) ───────────────────────

class SupplierRFQStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SENT = "sent", "Sent"
    RESPONDED = "responded", "Responded"
    EXPIRED = "expired", "Expired"


class SupplierRFQ(TenantBaseModel):
    quotation = models.ForeignKey(
        "quotes.Quotation", on_delete=models.CASCADE, null=True, blank=True,
        related_name="supplier_rfqs",
    )
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="rfqs")
    number = models.CharField(max_length=32)
    status = models.CharField(
        max_length=16, choices=SupplierRFQStatus.choices, default=SupplierRFQStatus.DRAFT
    )
    respond_by = models.DateField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.number


class SupplierRFQLine(TenantBaseModel):
    rfq = models.ForeignKey(SupplierRFQ, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=500)
    qty = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unit = models.CharField(max_length=32, default="each")

    def __str__(self):
        return self.description[:60]


class SupplierQuote(TenantBaseModel):
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="quotes")
    rfq = models.ForeignKey(
        SupplierRFQ, on_delete=models.SET_NULL, null=True, blank=True, related_name="quotes"
    )
    reference = models.CharField(max_length=64, blank=True)
    validity_date = models.DateField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return f"{self.supplier} · {self.reference or self.received_at:%Y-%m-%d}"


class SupplierQuoteLine(TenantBaseModel):
    supplier_quote = models.ForeignKey(
        SupplierQuote, on_delete=models.CASCADE, related_name="lines"
    )
    description = models.CharField(max_length=500)
    qty = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unit = models.CharField(max_length=32, default="each")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    lead_time_days = models.PositiveSmallIntegerField(null=True, blank=True)

    def __str__(self):
        return self.description[:60]


# ─────────────────────── Purchase Order (OUTBOUND) → GRN → 3-way match ───────────────────────

class POStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PENDING_APPROVAL = "pending_approval", "Pending approval"
    APPROVED = "approved", "Approved"
    SENT = "sent", "Sent"
    ACKNOWLEDGED = "acknowledged", "Acknowledged"
    PARTIALLY_DELIVERED = "partially_delivered", "Partially delivered"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class PurchaseOrder(TenantBaseModel):
    """Outbound order to a supplier. NOT the inbound client award."""

    number = models.CharField(max_length=32)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    quotation = models.ForeignKey(
        "quotes.Quotation", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="purchase_orders",
    )
    source_quote = models.ForeignKey(
        SupplierQuote, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    status = models.CharField(max_length=20, choices=POStatus.choices, default=POStatus.DRAFT)
    delivery_address = models.CharField(max_length=255, blank=True)
    payment_terms = models.CharField(max_length=32, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["company", "number"], name="unique_po_number")
        ]

    def __str__(self):
        return self.number

    @property
    def total(self) -> Decimal:
        return sum((line.line_total for line in self.lines.all()), Decimal("0.00"))


class POLine(TenantBaseModel):
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.CASCADE, related_name="lines"
    )
    position = models.PositiveSmallIntegerField(default=0)
    description = models.CharField(max_length=500)
    qty = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unit = models.CharField(max_length=32, default="each")
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["purchase_order", "position"]

    def __str__(self):
        return self.description[:60]

    @property
    def line_total(self) -> Decimal:
        return (self.qty * self.unit_price).quantize(Decimal("0.01"))

    @property
    def qty_received(self) -> Decimal:
        from django.db.models import Sum
        agg = GRNLine.objects.filter(po_line=self).aggregate(t=Sum("qty_received"))
        return agg["t"] or Decimal("0")

    @property
    def outstanding(self) -> Decimal:
        return self.qty - self.qty_received


class GRN(TenantBaseModel):
    """Goods Received Note against a PO (partial deliveries supported)."""

    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="grns")
    seq = models.PositiveSmallIntegerField(default=1)
    date = models.DateField()
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["purchase_order", "seq"]

    def __str__(self):
        return f"{self.purchase_order.number}-GRN{self.seq}"


class GRNLine(TenantBaseModel):
    grn = models.ForeignKey(GRN, on_delete=models.CASCADE, related_name="lines")
    po_line = models.ForeignKey(POLine, on_delete=models.SET_NULL, null=True, related_name="+")
    description = models.CharField(max_length=500)
    qty_received = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    condition = models.CharField(max_length=24, default="good")  # good | damaged | short

    def __str__(self):
        return f"{self.description[:40]} x{self.qty_received}"


class SupplierInvoice(TenantBaseModel):
    """Supplier's invoice — the ACTUAL cost, and the invoice leg of the 3-way
    match (PROCUREMENT §9)."""

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="invoices")
    purchase_order = models.ForeignKey(
        PurchaseOrder, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices"
    )
    invoice_no = models.CharField(max_length=64, blank=True)
    date = models.DateField()
    total_excl = models.DecimalField(max_digits=14, decimal_places=2)
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.supplier} {self.invoice_no or self.date}"
