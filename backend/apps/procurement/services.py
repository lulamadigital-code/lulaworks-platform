"""Procurement services: price ledger, anomaly detection, supplier performance
scoring, PO creation, and the 3-way match (PROCUREMENT §6, §9, §10)."""

from decimal import Decimal

from django.db.models import Avg, Sum
from django.utils import timezone

from apps.administration.services import next_number
from apps.core.events import publish

from .models import (
    GRNLine,
    POLine,
    PurchaseOrder,
    SupplierPrice,
    SupplierRFQStatus,
)


def normalise(text: str) -> str:
    return " ".join(text.lower().split())


# ── Price ledger + anomaly (PROCUREMENT §10) ──────────────────────────────────

def record_supplier_prices(company, supplier_quote) -> int:
    """A confirmed supplier quote feeds the append-only price ledger."""
    today = timezone.localdate()
    n = 0
    for line in supplier_quote.lines.all():
        SupplierPrice.objects.create(
            company=company, supplier=supplier_quote.supplier,
            item_key=normalise(line.description), description=line.description,
            unit=line.unit, unit_price=line.unit_price, date=today,
        )
        n += 1
    return n


def price_anomaly(company, description, proposed_price, *, threshold=Decimal("0.25")):
    """Flag a quote that deviates sharply from the historical average
    (PROCUREMENT §10: "detect unusual quotes")."""
    avg = (
        SupplierPrice.objects.filter(item_key=normalise(description))
        .aggregate(a=Avg("unit_price"))["a"]
    )
    if not avg:
        return {"anomaly": False, "avg": None}
    deviation = (Decimal(proposed_price) - avg) / avg
    return {"anomaly": abs(deviation) >= threshold, "avg": avg, "deviation": deviation}


# ── Supplier performance scoring (PROCUREMENT §6) ─────────────────────────────

def recompute_performance(supplier) -> Decimal:
    """Weighted 0-100 score from RFQ responsiveness, delivery completeness and
    quality. Updated as procurement events land."""
    rfqs = supplier.rfqs.all()
    sent = rfqs.exclude(status=SupplierRFQStatus.DRAFT).count()
    responded = rfqs.filter(status=SupplierRFQStatus.RESPONDED).count()
    responsiveness = Decimal(responded) / sent if sent else Decimal("1")

    ordered = POLine.objects.filter(
        purchase_order__supplier=supplier
    ).aggregate(t=Sum("qty"))["t"] or Decimal("0")
    received = GRNLine.objects.filter(
        grn__purchase_order__supplier=supplier
    ).aggregate(t=Sum("qty_received"))["t"] or Decimal("0")
    completeness = min(received / ordered, Decimal("1")) if ordered else Decimal("1")

    good = GRNLine.objects.filter(
        grn__purchase_order__supplier=supplier, condition="good"
    ).count()
    total_grn = GRNLine.objects.filter(grn__purchase_order__supplier=supplier).count()
    quality = Decimal(good) / total_grn if total_grn else Decimal("1")

    score = (responsiveness * 30 + completeness * 40 + quality * 30)
    supplier.performance_score = score.quantize(Decimal("0.01"))
    supplier.save(update_fields=["performance_score"])
    return supplier.performance_score


# ── Purchase Order (outbound) ─────────────────────────────────────────────────

def create_purchase_order(company, user, *, supplier, quotation=None, lines=None,
                          source_quote=None, delivery_address="") -> PurchaseOrder:
    po = PurchaseOrder.objects.create(
        company=company, number=next_number(company, "po"), supplier=supplier,
        quotation=quotation, source_quote=source_quote, delivery_address=delivery_address,
        payment_terms=supplier.payment_terms, created_by=user, updated_by=user,
    )
    for pos, line in enumerate(lines or [], start=1):
        po.lines.create(
            company=company, position=pos, description=line["description"],
            qty=line.get("qty", 1), unit=line.get("unit", "each"),
            unit_price=line.get("unit_price", 0),
        )
    publish("PurchaseOrderCreated", company=company, subject=po, actor=user,
            payload={"number": po.number, "supplier": supplier.name})
    return po


# ── 3-way match: PO ↔ GRN ↔ Supplier Invoice (PROCUREMENT §9) ─────────────────

def three_way_match(purchase_order) -> dict:
    """Reconcile ordered vs received vs invoiced; flag variances before payment."""
    variances = []
    for line in purchase_order.lines.all():
        received = line.qty_received
        if received != line.qty:
            variances.append({
                "type": "quantity", "line": line.description,
                "ordered": str(line.qty), "received": str(received),
            })
    invoiced = purchase_order.invoices.aggregate(t=Sum("total_excl"))["t"] or Decimal("0")
    po_total = purchase_order.total
    if invoiced and invoiced != po_total:
        variances.append({
            "type": "price", "po_total": str(po_total), "invoiced": str(invoiced),
        })
    return {"matched": not variances, "variances": variances,
            "po_total": po_total, "invoiced": invoiced}
