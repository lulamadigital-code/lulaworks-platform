"""Procurement services: price ledger, anomaly detection, supplier performance
scoring, PO creation, and the 3-way match (PROCUREMENT §6, §9, §10)."""

from decimal import Decimal

from django.db.models import Avg, Sum
from django.utils import timezone

from apps.administration.services import next_number
from apps.core.events import publish

from django.db import transaction

from .models import (
    GRNLine,
    POLine,
    Product,
    ProductAlias,
    ProcurementRequest,
    ProcurementRequestLine,
    ProcurementRequestStatus,
    PurchaseOrder,
    SupplierPrice,
    SupplierRFQStatus,
)


def normalise(text: str) -> str:
    return " ".join(text.lower().split())


# ── Products: one item, many spellings, across suppliers and time ─────────────

def resolve_product(company, user, description, *, create=True):
    """Map a written description to a Product via its aliases; create a new
    Product (and seed alias) the first time we see an item. Returns None for an
    empty description, or when `create=False` and nothing matches."""
    key = normalise(description or "")
    if not key:
        return None
    alias = (ProductAlias.objects.filter(company=company, key=key)
             .select_related("product").first())
    if alias:
        return alias.product
    if not create:
        return None
    product = Product.objects.create(
        company=company, name=description.strip()[:255],
        created_by=user, updated_by=user)
    ProductAlias.objects.create(
        company=company, product=product, label=description.strip()[:255],
        key=key, created_by=user, updated_by=user)
    return product


def add_product_alias(product, user, label):
    """Teach a Product another spelling. If that spelling already belongs to a
    different product, it is re-pointed here (a lightweight merge)."""
    key = normalise(label or "")
    if not key:
        return None
    alias, created = ProductAlias.objects.get_or_create(
        company=product.company, key=key,
        defaults={"product": product, "label": label.strip()[:255],
                  "created_by": user, "updated_by": user})
    if not created and alias.product_id != product.id:
        # Re-point the alias and its prices at this product (merge).
        SupplierPrice.objects.filter(product_id=alias.product_id,
                                     item_key=key).update(product=product)
        alias.product = product
        alias.label = label.strip()[:255]
        alias.save(update_fields=["product", "label", "updated_at"])
    return alias


def merge_products(keep, drop, user):
    """Fold `drop` into `keep`: move its aliases and prices, then delete it."""
    if keep.id == drop.id:
        return keep
    drop.aliases.update(product=keep)
    drop.prices.update(product=keep)
    drop.delete()
    return keep


_STALE_DAYS = 182  # ~6 months → a recorded price is no longer reliable


def product_intelligence(product):
    """Everything a product page answers: who sells it, who's cheapest, how often
    and when we bought it, the average/low/high, the price trend, and whether the
    latest price is stale."""
    from collections import OrderedDict

    prices = list(product.prices.select_related("supplier").order_by("-date", "-created_at"))
    amounts = [p.unit_price for p in prices]
    today = timezone.localdate()
    last = prices[0].date if prices else None

    # Latest price per supplier → cheapest first (the comparison table).
    latest_by_supplier = {}
    for p in prices:            # already newest-first
        latest_by_supplier.setdefault(p.supplier_id, p)
    comparison = sorted(latest_by_supplier.values(), key=lambda p: p.unit_price)

    # Monthly average → trend + % change.
    monthly = OrderedDict()
    for p in reversed(prices):  # oldest-first
        monthly.setdefault(p.date.strftime("%Y-%m"), []).append(p.unit_price)
    trend = [{"month": m, "avg": (sum(v) / len(v)).quantize(Decimal("0.01"))}
             for m, v in monthly.items()]
    pct_change = None
    if len(trend) >= 2 and trend[0]["avg"]:
        pct_change = ((trend[-1]["avg"] - trend[0]["avg"]) / trend[0]["avg"] * 100
                      ).quantize(Decimal("0.1"))

    return {
        "product": product,
        "times_bought": len(prices),
        "last_bought": last,
        "days_since": (today - last).days if last else None,
        "is_stale": bool(last and (today - last).days > _STALE_DAYS),
        "avg": (sum(amounts) / len(amounts)).quantize(Decimal("0.01")) if amounts else None,
        "lowest": min(amounts) if amounts else None,
        "highest": max(amounts) if amounts else None,
        "supplier_count": len(latest_by_supplier),
        "comparison": comparison,      # SupplierPrice rows, cheapest first
        "cheapest": comparison[0] if comparison else None,
        "trend": trend,
        "pct_change": pct_change,
        "prices": prices,
    }


def products_overview(company, *, q="", category=""):
    """List products with quick stats for the Products page. `q` matches the name
    or any alias (so 'pipe' finds 'Hydraulic Pipe 50mm')."""
    products = Product.objects.all()
    if category:
        products = products.filter(category=category)
    if q:
        key = normalise(q)
        ids = set(products.filter(name__icontains=q).values_list("id", flat=True))
        ids |= set(ProductAlias.objects.filter(company=company, key__icontains=key)
                   .values_list("product_id", flat=True))
        products = products.filter(id__in=ids)
    products = products.prefetch_related("prices__supplier").order_by("name")

    rows = []
    today = timezone.localdate()
    for p in products:
        prices = list(p.prices.all())
        last = max((x.date for x in prices), default=None)
        rows.append({
            "product": p,
            "times_bought": len(prices),
            "suppliers": len({x.supplier_id for x in prices}),
            "last_bought": last,
            "is_stale": bool(last and (today - last).days > _STALE_DAYS),
            "avg": (sum(x.unit_price for x in prices) / len(prices)).quantize(Decimal("0.01"))
            if prices else None,
        })
    return rows


def spend_by_category(company):
    """Actual spend grouped by product category, from the material lines we've
    captured on receipts. Uncategorised items fall under 'Other'."""
    from apps.execution.models import TaskReportItem

    totals = {}
    for item in TaskReportItem.objects.all():
        product = resolve_product(company, None, item.description, create=False)
        cat = (product.category if product and product.category else "other")
        totals[cat] = totals.get(cat, Decimal("0")) + (item.line_total or Decimal("0"))
    return sorted(({"category": k, "total": v} for k, v in totals.items()),
                  key=lambda r: r["total"], reverse=True)


# ── Price ledger + anomaly (PROCUREMENT §10) ──────────────────────────────────

def record_supplier_prices(company, supplier_quote) -> int:
    """A confirmed supplier quote feeds the append-only price ledger."""
    today = timezone.localdate()
    n = 0
    for line in supplier_quote.lines.all():
        SupplierPrice.objects.create(
            company=company, supplier=supplier_quote.supplier,
            product=resolve_product(company, None, line.description),
            item_key=normalise(line.description), description=line.description,
            unit=line.unit, unit_price=line.unit_price, date=today,
        )
        n += 1
    return n


def learn_from_receipt(company, user, *, supplier_name, items, date=None, currency="ZAR"):
    """Turn a confirmed purchase receipt into supplier knowledge.

    Matches the seller into the Suppliers database (adding it the first time we
    ever buy from them), then records each purchased line in the append-only
    price ledger — so next time we know where we bought this and what we paid.
    `items` is an iterable of objects/dicts with description, unit, unit_price.
    Returns (supplier, prices_recorded, supplier_created)."""
    from .models import Supplier

    name = (supplier_name or "").strip()
    if not name:
        return None, 0, False
    supplier = Supplier.objects.filter(company=company, name__iexact=name).first()
    created = False
    if supplier is None:
        supplier = Supplier.objects.create(
            company=company, name=name, notes="Added automatically from a receipt.",
            created_by=user, updated_by=user)
        created = True

    if isinstance(date, str):
        from django.utils.dateparse import parse_date
        date = parse_date(date)
    day = date or timezone.localdate()
    n = 0
    for item in items:
        get = (lambda k: item.get(k)) if isinstance(item, dict) else (lambda k: getattr(item, k, None))
        desc = (get("description") or "").strip()
        price = get("unit_price") or 0
        if not desc or Decimal(str(price)) <= 0:
            continue
        SupplierPrice.objects.create(
            company=company, supplier=supplier,
            product=resolve_product(company, user, desc),
            item_key=normalise(desc), description=desc,
            unit=get("unit") or "each", unit_price=Decimal(str(price)),
            currency=currency or "ZAR", date=day)
        n += 1
    if created or n:
        publish("SupplierLearnedFromReceipt", company=company, subject=supplier,
                actor=user, payload={"supplier": supplier.name, "prices": n,
                                     "new_supplier": created})
    return supplier, n, created


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


# ── Procurement Requests (internal requisition → optional approval) ────────────

def procurement_approval_required(company) -> bool:
    """Whether a request must be approved before it can be purchased. Optional
    per company — off by default (small contractors buy directly)."""
    from apps.administration.models import CompanySettings
    s = CompanySettings.objects.filter(company=company).first()
    return bool(s and (s.approval_rules or {}).get("procurement_required"))


def _last_price_for(company, description):
    """Best estimate for a line — the most recent price we've paid, if any."""
    product = resolve_product(company, None, description, create=False)
    qs = SupplierPrice.objects.filter(company=company)
    qs = qs.filter(product=product) if product else qs.filter(item_key=normalise(description))
    row = qs.order_by("-date").first()
    return row.unit_price if row else Decimal("0")


@transaction.atomic
def create_request(company, user, *, title, lines, task=None, project=None,
                   notes="", needed_by=None):
    """Raise an internal request for what a task needs. Each line's estimated
    price is prefilled from the last price we paid for that item."""
    if task is not None and project is None:
        project = task.project
    req = ProcurementRequest.objects.create(
        company=company, number=next_number(company, "procurement_request"),
        title=title, task=task, project=project, notes=notes, needed_by=needed_by,
        requested_by=user, created_by=user, updated_by=user)
    for ln in lines or []:
        desc = (ln.get("description") or "").strip()
        if not desc:
            continue
        est = ln.get("est_unit_price")
        est = Decimal(str(est)) if est not in (None, "") else _last_price_for(company, desc)
        ProcurementRequestLine.objects.create(
            company=company, request=req, description=desc,
            product=resolve_product(company, user, desc),
            quantity=Decimal(str(ln.get("quantity") or 1)),
            unit=ln.get("unit") or "each", est_unit_price=est,
            created_by=user, updated_by=user)
    publish("ProcurementRequestCreated", company=company, subject=req, actor=user,
            payload={"number": req.number, "task": str(task.id) if task else None})
    return req


def submit_request(req, user):
    """Send a draft for approval — or auto-approve it when the company doesn't
    require approval."""
    if req.status != ProcurementRequestStatus.DRAFT:
        return req
    if procurement_approval_required(req.company):
        req.status = ProcurementRequestStatus.SUBMITTED
    else:
        req.status = ProcurementRequestStatus.APPROVED
        req.approved_at = timezone.now()
    req.updated_by = user
    req.save(update_fields=["status", "approved_at", "updated_by", "updated_at"])
    publish("ProcurementRequestSubmitted", company=req.company, subject=req, actor=user,
            payload={"status": req.status})
    return req


def approve_request(req, user):
    if req.status != ProcurementRequestStatus.SUBMITTED:
        return req
    req.status = ProcurementRequestStatus.APPROVED
    req.approved_by = user
    req.approved_at = timezone.now()
    req.updated_by = user
    req.save(update_fields=["status", "approved_by", "approved_at", "updated_by", "updated_at"])
    publish("ProcurementRequestApproved", company=req.company, subject=req, actor=user)
    return req


def reject_request(req, user, reason=""):
    if req.status != ProcurementRequestStatus.SUBMITTED:
        return req
    req.status = ProcurementRequestStatus.REJECTED
    req.rejected_reason = reason[:255]
    req.updated_by = user
    req.save(update_fields=["status", "rejected_reason", "updated_by", "updated_at"])
    publish("ProcurementRequestRejected", company=req.company, subject=req, actor=user)
    return req


def fulfil_request(req, user):
    """Mark an approved request as bought — the 'purchase happened' step that
    closes the loop back to the task."""
    if req.status != ProcurementRequestStatus.APPROVED:
        return req
    req.lines.update(fulfilled=True)
    req.status = ProcurementRequestStatus.FULFILLED
    req.updated_by = user
    req.save(update_fields=["status", "updated_by", "updated_at"])
    publish("ProcurementRequestFulfilled", company=req.company, subject=req, actor=user)
    return req
