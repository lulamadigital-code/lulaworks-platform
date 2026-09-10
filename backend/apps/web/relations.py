"""Cross-module Related Records — the business transaction graph.

Every major record (quotation, customer PO, job, invoice) already carries the
foreign keys that connect it to what came before and after; this surfaces them
as one consistent "Related" panel so a person sees the whole chain

    Customer → RFQ → Quotation → Customer PO → Job → Delivery/Invoice

without hunting through modules. Read-only, defensive — it must never break a
detail page, so any lookup that fails is simply dropped.
"""
from django.urls import reverse


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
}


def _item(label, sub, rtype, pk):
    try:
        url = reverse(_WEB_ROUTE[rtype], args=[pk])
    except Exception:                                # noqa: BLE001
        url = ""
    return {"label": str(label), "sub": sub, "type": rtype, "id": str(pk),
            "url": url}


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

        else:  # Customer
            from apps.customers.models import Customer
            if isinstance(obj, Customer):
                add("Quotations", [_quote_item(q) for q in
                                   Quotation.objects.filter(customer=obj)[:20]])
                add("Jobs", [_job_item(j) for j in
                             Project.objects.filter(customer=obj)[:20]])
                add("Customer POs", [_po_item(p) for p in
                                     CustomerPurchaseOrder.objects
                                     .filter(quotation__customer=obj)[:20]])
    except Exception:                                # noqa: BLE001
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
