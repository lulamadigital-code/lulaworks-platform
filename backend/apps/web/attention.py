"""Attention Centre — cross-module exception detection.

Reads over the business graph and surfaces what needs a human: commercial
mismatches (a PO that differs from its quotation, an unmatched PO), operational
slips (overdue work, quotes awaiting sign-off), financial exposure (unpaid
invoices) and setup gaps that block invoicing. Everything is PERMISSION-GATED —
a person only ever sees exceptions for data their role can access — and grouped
by severity so the dashboard reads 🔴 critical / 🟠 needs attention at a glance.

Deterministic and read-only; each item links to the record that fixes it.
"""
from django.urls import reverse


def _url(name, **kw):
    try:
        return reverse(name, kwargs=kw) if kw else reverse(name)
    except Exception:                                # noqa: BLE001
        return ""


def attention_items(company, user) -> dict:
    """{critical:[…], warning:[…], counts:{…}, total:N}. Each item:
    {severity, category, title, detail, url}."""
    items = []
    can = user.has_perm_code

    def add(severity, category, title, detail, url=""):
        items.append({"severity": severity, "category": category,
                      "title": title, "detail": detail, "url": url})

    # ── Operations ────────────────────────────────────────────────────────────
    if can("projects.view"):
        from apps.execution.models import Task
        overdue = sum(1 for t in Task.objects.only("due_date", "status").all()
                      if t.is_overdue)
        if overdue:
            add("critical", "Operations", f"{overdue} task{'s' if overdue != 1 else ''} overdue",
                "Past their due date and not complete.", _url("web:work"))

        from apps.quotes.models import Quotation, QuotationStatus
        awaiting = Quotation.objects.filter(status__in=[
            QuotationStatus.MANAGER_APPROVAL, QuotationStatus.COMMERCIAL_APPROVAL]).count()
        if awaiting:
            add("warning", "Commercial",
                f"{awaiting} quotation{'s' if awaiting != 1 else ''} awaiting approval",
                "Waiting for sign-off before they can be issued.", _url("web:quotations"))

    # ── Commercial: purchase orders ───────────────────────────────────────────
    if can("quotes.create") or can("quotes.approve") or can("quotes.download"):
        from apps.quotes.models import CustomerPurchaseOrder
        from apps.quotes.services import po_variance
        unmatched = CustomerPurchaseOrder.objects.filter(quotation__isnull=True).count()
        if unmatched:
            add("warning", "Commercial",
                f"{unmatched} purchase order{'s' if unmatched != 1 else ''} unmatched",
                "Not yet linked to a quotation.", _url("web:customer_pos"))
        # PO ↔ quotation value variance (the commercial-relationship check)
        matched = (CustomerPurchaseOrder.objects.select_related("quotation")
                   .filter(quotation__isnull=False)[:200])
        for po in matched:
            v = po_variance(po)
            if v and v.get("has_variance"):
                add("warning", "Commercial", f"PO {po.po_number} differs from its quotation",
                    v["message"], _url("web:customer_po_detail", pk=po.pk))

    # ── Financial ─────────────────────────────────────────────────────────────
    if can("finance.view_money"):
        from apps.finance.models import Invoice, InvoiceStatus
        unpaid = (Invoice.objects.exclude(status__in=[InvoiceStatus.PAID, InvoiceStatus.DRAFT])
                  .count())
        if unpaid:
            add("warning", "Financial", f"{unpaid} invoice{'s' if unpaid != 1 else ''} unpaid",
                "Issued but not yet fully paid.", _url("web:invoices"))

    # ── Setup / documents ─────────────────────────────────────────────────────
    if can("company.manage"):
        from apps.identity.company_setup import status as setup_status
        s = setup_status(company)
        if not s["required_complete"]:
            add("warning", "Setup",
                "Company setup incomplete",
                f"{s['items_remaining']} item(s) left — required before invoicing.",
                s["settings_url"])

    # ── Relationships ─────────────────────────────────────────────────────────
    if can("customers.manage"):
        from datetime import timedelta

        from django.utils import timezone

        from apps.customers.models import Customer
        cutoff = timezone.now() - timedelta(days=30)
        stale = Customer.objects.filter(updated_at__lt=cutoff).count()
        if stale:
            add("info", "Relationships",
                f"{stale} customer{'s' if stale != 1 else ''} not contacted in 30 days",
                "No recent recorded activity.", _url("web:crm_hub"))

    critical = [i for i in items if i["severity"] == "critical"]
    warning = [i for i in items if i["severity"] == "warning"]
    info = [i for i in items if i["severity"] == "info"]
    return {
        "critical": critical, "warning": warning, "info": info,
        "counts": {"critical": len(critical), "warning": len(warning), "info": len(info)},
        "total": len(items),
    }
