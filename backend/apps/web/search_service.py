"""Global search — ONE permission-aware, tenant-scoped implementation shared by
the web console and the mobile/JSON API.

Each group is searched only when the user is allowed to see it (the model managers
already scope to the active company). Results are returned as neutral entity refs
`{type, id, title, sub}`; the web view maps `type` → its detail URL, the API hands
`type`+`id` to the app so it can open its own screen. No business data is
invented and nothing money-bearing is placed in a title/sub.
"""
from django.db.models import Q

#: entity type → web detail route name, for the console to build hrefs.
WEB_ROUTES = {
    "customer": "web:customer_detail",
    "project": "web:project_detail",
    "job": "web:work_detail",
    "quotation": "web:quotation_detail",
    "commercial_document": "web:commercial_document_detail",
    "supplier": "web:supplier_detail",
}


def global_search(user, q, *, per_group=6):
    """Return [{label, type, items:[{type, id, title, sub}]}] for query `q`.
    Empty list for queries shorter than 2 characters."""
    q = (q or "").strip()
    groups = []
    if len(q) < 2:
        return groups

    can = user.has_perm_code

    def grp(label, type_, items):
        if items:
            groups.append({"label": label, "type": type_, "items": items})

    if (can("customers.manage") or can("crm.manage") or can("quotes.create")
            or can("projects.create")):
        from apps.customers.models import Customer
        rows = Customer.objects.filter(
            Q(name__icontains=q) | Q(trading_name__icontains=q)
            | Q(code__icontains=q) | Q(registration_no__icontains=q))[:per_group]
        grp("Customers", "customer",
            [{"type": "customer", "id": str(c.pk), "title": c.name,
              "sub": c.trading_name or c.code or c.industry} for c in rows])

    if can("projects.view"):
        from apps.projects.models import Project
        rows = Project.objects.filter(
            Q(number__icontains=q) | Q(title__icontains=q)
            | Q(client_name__icontains=q))[:per_group]
        grp("Projects", "project",
            [{"type": "project", "id": str(p.pk),
              "title": f"{p.number} · {p.title}".strip(" ·"),
              "sub": p.client_name} for p in rows])
        from apps.execution.models import Task
        trows = Task.objects.filter(
            Q(name__icontains=q) | Q(client_name__icontains=q))[:per_group]
        grp("Jobs", "job",
            [{"type": "job", "id": str(t.pk), "title": t.name,
              "sub": t.client_name or ""} for t in trows])

    if can("quotes.create") or can("quotes.approve") or can("quotes.download"):
        from apps.quotes.models import Quotation
        rows = Quotation.objects.filter(
            Q(number__icontains=q) | Q(title__icontains=q)
            | Q(client_name__icontains=q))[:per_group]
        grp("Quotations", "quotation",
            [{"type": "quotation", "id": str(r.pk),
              "title": f"{r.number} · {r.client_name}".strip(" ·"),
              "sub": r.title} for r in rows])

    if can("finance.view_money") or can("invoices.approve") or can("quotes.download"):
        from apps.quotes.models import CommercialDocument
        rows = (CommercialDocument.objects.filter(number__icontains=q)
                .select_related("quotation")[:per_group])
        grp("Tax invoices", "commercial_document",
            [{"type": "commercial_document", "id": str(d.pk), "title": d.number,
              "sub": getattr(d.quotation, "client_name", "") if d.quotation_id else ""}
             for d in rows])

    if can("procurement.manage"):
        from apps.procurement.models import Supplier
        rows = Supplier.objects.filter(
            Q(name__icontains=q) | Q(contact_person__icontains=q)
            | Q(email__icontains=q))[:per_group]
        grp("Suppliers", "supplier",
            [{"type": "supplier", "id": str(s.pk), "title": s.name,
              "sub": s.contact_person or s.email} for s in rows])

    return groups
