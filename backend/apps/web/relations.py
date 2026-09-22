"""Cross-module Related Records — the business transaction graph.

Every major record (quotation, customer PO, job, invoice, payment) already
carries the foreign keys that connect it to what came before and after; this
surfaces them as one consistent "Related" panel so a person sees the whole chain

    Customer → RFQ → Quotation → Customer PO → Job → Delivery/Invoice → Payment

without hunting through modules. Read-only, defensive — it must never break a
detail page, so a failed lookup is logged and dropped rather than raised.

Permission-aware (Business History §5/§46): the graph is never a side channel.
Money-bearing links — invoices and payments — are shown only to a user who holds
`finance.view_money`; delivery notes and operational records stay visible to
anyone who can open the page. Callers pass the request user; with no user the
financial sections are withheld (the safe default for API/LulaAI paths).
"""
import logging

from django.urls import reverse

logger = logging.getLogger(__name__)

#: Deterministic cap per section — a customer may have thousands of records, so
#: the panel shows the most-recent slice and flags that more exist ("more").
_MAX_PER_SECTION = 20


# record type → the web detail route that renders it. The API returns the
# type + id so a client (Flutter) can open its OWN screen for that type.
_WEB_ROUTE = {
    "customer": "web:customer_detail",
    "rfq": "web:rfq_detail",
    "quotation": "web:quotation_detail",
    "customer_po": "web:customer_po_detail",
    "job": "web:project_detail",
    "commercial_document": "web:commercial_document_detail",
    "supplier_po": "web:po_detail",
    # A payment has no standalone page — it opens the invoice it settles.
    "payment": "web:commercial_document_detail",
}


def _item(label, sub, rtype, pk, *, route_pk=None):
    """One related record. `route_pk` lets a type resolve to another record's
    page (a payment opens its invoice) while keeping its own type + id."""
    try:
        url = reverse(_WEB_ROUTE[rtype], args=[route_pk if route_pk is not None else pk])
    except Exception:                                # noqa: BLE001
        logger.debug("related_records: no route for %s/%s", rtype, pk, exc_info=True)
        url = ""
    return {"label": str(label), "sub": sub, "type": rtype, "id": str(pk),
            "url": url}


def _order(qs):
    """Deterministic newest-first ordering with a stable id tie-break (§4.1)."""
    return qs.order_by("-created_at", "-id")


def _limited(rows_qs):
    """(rows, has_more) — a capped, deterministic fetch. Reads one past the cap
    to know whether to flag "more" without a second COUNT query (§4.2)."""
    rows = list(rows_qs[:_MAX_PER_SECTION + 1])
    return rows[:_MAX_PER_SECTION], len(rows) > _MAX_PER_SECTION


def _customer_item(c):
    return _item(getattr(c, "display_name", "") or c.name, "Customer", "customer", c.pk)


def _quote_item(q):
    return _item(q.number, q.client_name or "Quotation", "quotation", q.pk)


def _rfq_item(r):
    return _item(getattr(r, "number", "") or "RFQ", "RFQ", "rfq", r.pk)


def _po_item(p):
    return _item(p.po_number, "Customer PO", "customer_po", p.pk)


def _job_item(j):
    return _item(j.number, getattr(j, "title", "") or "Job", "job", j.pk)


def _comdoc_item(d):
    return _item(d.number, d.get_kind_display(), "commercial_document", d.pk)


def _supplier_po_item(p):
    return _item(p.number, "Supplier PO", "supplier_po", p.pk)


def _payment_item(p):
    """A recorded customer payment (POP). The amount is money — only reached when
    the caller already holds `finance.view_money` (the section is gated), so it is
    safe to show. Opens the invoice it was booked against."""
    return _item(f"{p.amount:,.2f}", f"Payment · {p.date:%Y-%m-%d}",
                 "payment", p.pk, route_pk=p.document_id)


def _can_money(user) -> bool:
    """Whether this user may see money-bearing links (invoices, payments)."""
    return bool(user is not None
                and getattr(user, "is_authenticated", False)
                and user.has_perm_code("finance.view_money"))


def related_records(obj, user=None) -> list:
    """Sections of related records for `obj`, permission-filtered for `user`.

    Each section: {title, items:[{label,sub,type,id,url}], more:bool}. `more` is
    True when further records exist beyond the shown slice. Financial sections
    (invoices, payments) are present only when the user holds `finance.view_money`.
    """
    from apps.projects.models import Project
    from apps.quotes.models import (CommercialDocument, CommercialDocumentPayment,
                                    CustomerPurchaseOrder, Quotation)

    can_money = _can_money(user)
    sections = []

    def add(title, items, more=False):
        items = [i for i in items if i]
        if items:
            sections.append({"title": title, "items": items, "more": bool(more)})

    def add_qs(title, qs, item_fn):
        """A capped, ordered, deterministic section from a queryset."""
        rows, more = _limited(_order(qs))
        add(title, [item_fn(r) for r in rows], more)

    def _invoices_qs(quote):
        return CommercialDocument.objects.filter(
            quotation=quote, kind=CommercialDocument.Kind.INVOICE)

    def _deliveries_qs(quote):
        return CommercialDocument.objects.filter(
            quotation=quote, kind=CommercialDocument.Kind.DELIVERY)

    def _payments_qs(quote):
        return CommercialDocumentPayment.objects.filter(
            document__quotation=quote).order_by("-date", "-id")

    def add_commercial(quote):
        """Delivery notes are operational (always shown); invoices and payments
        are financial (shown only to finance.view_money) — so the graph can't leak
        billing to a field worker who opens the same job (§5)."""
        add_qs("Delivery notes", _deliveries_qs(quote), _comdoc_item)
        if can_money:
            add_qs("Invoices", _invoices_qs(quote), _comdoc_item)
            rows, more = _limited(_payments_qs(quote))
            add("Payments", [_payment_item(p) for p in rows], more)

    try:
        if isinstance(obj, Quotation):
            add("Customer", [_customer_item(obj.customer)] if obj.customer_id else [])
            add("RFQ", [_rfq_item(obj.source_rfq)] if obj.source_rfq_id else [])
            add_qs("Customer POs", obj.customer_pos.all(), _po_item)
            add_qs("Jobs", obj.projects.all(), _job_item)
            add_commercial(obj)

        elif isinstance(obj, CustomerPurchaseOrder):
            q = obj.quotation
            add("Customer", [_customer_item(q.customer)] if q and q.customer_id else [])
            add("Quotation", [_quote_item(q)] if q else [])
            if q:
                add_qs("Jobs", q.projects.all(), _job_item)
                add_commercial(q)

        elif isinstance(obj, Project):
            add("Customer", [_customer_item(obj.customer)] if obj.customer_id else [])
            add("Quotation", [_quote_item(obj.quotation)] if obj.quotation_id else [])
            if obj.quotation_id:
                add_qs("Customer POs", obj.quotation.customer_pos.all(), _po_item)
                add_commercial(obj.quotation)
            add_qs("Supplier POs", obj.procurement_requests.all(), _supplier_po_item)

        elif isinstance(obj, CommercialDocument):
            q = obj.quotation
            add("Customer", [_customer_item(q.customer)] if q and q.customer_id else [])
            add("Quotation", [_quote_item(q)] if q else [])
            if q:
                add_qs("Customer POs", q.customer_pos.all(), _po_item)
                add_qs("Jobs", q.projects.all(), _job_item)
            # This invoice's own payments (financial).
            if can_money and obj.kind == CommercialDocument.Kind.INVOICE:
                rows, more = _limited(obj.payments.all().order_by("-date", "-id"))
                add("Payments", [_payment_item(p) for p in rows], more)

        else:  # Customer
            from apps.customers.models import Customer
            if isinstance(obj, Customer):
                add_qs("Quotations", Quotation.objects.filter(customer=obj), _quote_item)
                add_qs("Jobs", Project.objects.filter(customer=obj), _job_item)
                add_qs("Customer POs",
                       CustomerPurchaseOrder.objects.filter(quotation__customer=obj),
                       _po_item)
    except Exception:                                # noqa: BLE001 - never break a page
        logger.exception("related_records failed for %s#%s",
                         type(obj).__name__, getattr(obj, "pk", None))
        return sections
    return sections


# record type → model, for resolving an API subject by type + id.
def resolve_subject(rtype, pk):
    from apps.customers.models import Customer
    from apps.projects.models import Project
    from apps.quotes.models import (CommercialDocument, CustomerPurchaseOrder,
                                    Quotation)
    model = {"quotation": Quotation, "customer_po": CustomerPurchaseOrder,
             "job": Project, "commercial_document": CommercialDocument,
             "customer": Customer}.get(rtype)
    return model.objects.filter(pk=pk).first() if model else None
