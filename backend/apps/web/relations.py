"""Cross-module Related Records — the business transaction graph.

Every major record (quotation, customer PO, job, invoice) already carries the
foreign keys that connect it to what came before and after; this surfaces them
as one consistent "Related" panel so a person sees the whole chain

    Customer → RFQ → Quotation → Customer PO → Job → Delivery/Invoice

without hunting through modules. Read-only, defensive — it must never break a
detail page, so any lookup that fails is simply dropped.
"""
from django.urls import reverse


def _u(name, pk):
    try:
        return reverse(name, args=[pk])
    except Exception:                                # noqa: BLE001
        return ""


def _item(label, sub, name, pk):
    url = _u(name, pk)
    return {"label": str(label), "sub": sub, "url": url} if url else None


def _customer_item(c):
    return _item(getattr(c, "display_name", "") or c.name, "Customer",
                 "web:customer_detail", c.pk)


def _quote_item(q):
    return _item(q.number, q.client_name or "Quotation", "web:quotation_detail", q.pk)


def _rfq_item(r):
    return _item(getattr(r, "number", "") or "RFQ", "RFQ", "web:rfq_detail", r.pk)


def _po_item(p):
    return _item(p.po_number, "Customer PO", "web:customer_po_detail", p.pk)


def _job_item(j):
    return _item(j.number, getattr(j, "title", "") or "Job", "web:project_detail", j.pk)


def _comdoc_item(d):
    return _item(d.number, d.get_kind_display(), "web:commercial_document_detail", d.pk)


def _supplier_po_item(p):
    return _item(p.number, "Supplier PO", "web:po_detail", p.pk)


def related_records(obj) -> list:
    """Sections of related records for `obj`. Each: {title, items:[{label,sub,url}]}."""
    from apps.projects.models import Project
    from apps.quotes.models import (CommercialDocument, CustomerPurchaseOrder,
                                    Quotation)

    sections = []

    def add(title, items):
        items = [i for i in items if i]
        if items:
            sections.append({"title": title, "items": items})

    try:
        if isinstance(obj, Quotation):
            add("Customer", [_customer_item(obj.customer)] if obj.customer_id else [])
            add("RFQ", [_rfq_item(obj.source_rfq)] if obj.source_rfq_id else [])
            add("Customer POs", [_po_item(p) for p in obj.customer_pos.all()])
            add("Jobs", [_job_item(j) for j in obj.projects.all()])
            add("Invoices & delivery notes",
                [_comdoc_item(d) for d in obj.commercial_documents.all()])

        elif isinstance(obj, CustomerPurchaseOrder):
            q = obj.quotation
            add("Customer", [_customer_item(q.customer)] if q and q.customer_id else [])
            add("Quotation", [_quote_item(q)] if q else [])
            if q:
                add("Jobs", [_job_item(j) for j in q.projects.all()])
                add("Invoices & delivery notes",
                    [_comdoc_item(d) for d in q.commercial_documents.all()])

        elif isinstance(obj, Project):
            add("Customer", [_customer_item(obj.customer)] if obj.customer_id else [])
            add("Quotation", [_quote_item(obj.quotation)] if obj.quotation_id else [])
            if obj.quotation_id:
                add("Customer POs", [_po_item(p) for p in obj.quotation.customer_pos.all()])
                add("Invoices & delivery notes",
                    [_comdoc_item(d) for d in obj.quotation.commercial_documents.all()])
            add("Supplier POs", [_supplier_po_item(p) for p in obj.procurement_requests.all()])

        elif isinstance(obj, CommercialDocument):
            q = obj.quotation
            add("Customer", [_customer_item(q.customer)] if q and q.customer_id else [])
            add("Quotation", [_quote_item(q)] if q else [])
            if q:
                add("Customer POs", [_po_item(p) for p in q.customer_pos.all()])
                add("Jobs", [_job_item(j) for j in q.projects.all()])
    except Exception:                                # noqa: BLE001
        return sections
    return sections
