"""Template context processor — exposes the Golden-Rule flag to every page so the
nav can hide money-only surfaces (the view still enforces it authoritatively),
plus whether a real logo image is present (else the SVG mark is used)."""

from django.contrib.staticfiles import finders


def has_logo_file() -> bool:
    """True once someone drops apps/web/static/web/logo.png (or .svg) into place —
    the header/login then use the real file instead of the recreated SVG mark."""
    return bool(finders.find("web/logo.png") or finders.find("web/logo.svg"))


def logo_static_name() -> str:
    return "web/logo.png" if finders.find("web/logo.png") else "web/logo.svg"


_SECTIONS = {
    "dashboard": "dashboard",
    "work": "work", "work_new": "work", "work_detail": "work",
    "work_start": "work", "work_complete": "work", "work_transition": "work",
    "work_subtask_add": "work", "work_checklist_add": "work",
    "work_checklist_toggle": "work", "work_comment_add": "work",
    "work_file_add": "work", "work_member": "work", "work_link": "work",
    "work_decompose": "work", "work_decompose_apply": "work",
    "notifications": "notifications",
    "people": "people", "people_add": "people", "people_role": "people",
    "people_status": "people", "person_detail": "people",
    "profile": "profile",
    "company_profile": "company", "company_bank": "company",
    "company_contact": "company", "company_document": "company",
    "company_hours_page": "company", "company_hours": "company",
    "customers": "customers", "customer_detail": "customers",
    "customer_edit": "customers",
    "customer_create": "customers", "customer_contact_save": "customers",
    "customer_department": "customers", "customer_contact_detail": "customers",
    "rfq": "rfq", "rfq_detail": "rfq", "rfq_upload": "rfq", "rfq_approve": "rfq",
    "rfq_line": "rfq",
    "quotations": "quotations", "quotation_detail": "quotations",
    "quotation_edit": "quotations", "quotation_pdf": "quotations",
    "quotation_excel": "quotations", "quotation_po_extract": "quotations",
    "quotation_new": "quotations", "quotation_header": "quotations",
    "quotation_extract": "quotations",
    "quotation_line": "quotations", "quotation_section": "quotations",
    "quotation_transition": "quotations", "quotation_revise": "quotations",
    "quotation_po": "quotations", "quotation_award": "quotations",
    "quotation_create_invoice": "quotations", "quotation_create_delivery": "quotations",
    "commercial_document_pdf": "quotations",
    "quotation_suggest": "quotations", "quotation_template": "quotations",
    "quotation_line_move": "quotations", "quotation_document": "quotations",
    "quotation_lines_bulk": "quotations",
    "projects": "projects", "project_detail": "projects", "readiness_partial": "projects",
    "project_override": "projects", "project_progress_claim": "projects",
    "compliance_item_approve": "projects",
    "estimates": "estimates", "estimate_detail": "estimates",
    "estimate_approve": "estimates", "estimate_revise": "estimates",
    "suppliers": "procurement", "purchase_orders": "procurement", "po_detail": "procurement",
    "po_approve": "procurement", "po_receive": "procurement",
    "commercial": "commercial", "invoice_payment": "commercial",
    "lulama": "lulama",
}


def nav_flags(request):
    user = getattr(request, "user", None)
    signed_in = bool(user and user.is_authenticated)
    can = bool(signed_in and user.has_perm_code("finance.view_money"))
    rm = getattr(request, "resolver_match", None)
    section = _SECTIONS.get(rm.url_name, "") if rm else ""

    unread = 0
    if signed_in:
        from apps.execution.services import unread_count
        unread = unread_count(user)

    return {"perms_money": can, "has_logo": has_logo_file(),
            "logo_static": logo_static_name(), "nav_section": section,
            "unread_notifications": unread}
