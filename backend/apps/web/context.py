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
    "work": "jobs", "work_new": "jobs", "work_detail": "jobs",
    "work_start": "jobs", "work_complete": "jobs", "work_transition": "jobs",
    "work_subtask_add": "jobs", "work_checklist_add": "jobs",
    "work_checklist_toggle": "jobs", "work_comment_add": "jobs",
    "work_file_add": "jobs", "work_member": "jobs", "work_link": "jobs",
    "work_decompose": "jobs", "work_decompose_apply": "jobs",
    "notifications": "notifications",
    "people": "people", "people_add": "people", "people_role": "people",
    "people_status": "people", "person_detail": "people",
    "profile": "profile",
    "company_profile": "company", "company_bank": "company",
    "company_contact": "company", "company_document": "company",
    "company_hours_page": "company", "company_hours": "company",
    "company_tax": "company",
    "ai_settings": "company", "ai_provider_toggle": "company",
    "ai_provider_priority": "company", "ai_provider_test": "company",
    "email_history": "company", "email_detail": "company", "email_resend": "company",
    "doc_templates": "company", "doc_template_create": "company",
    "doc_template_edit": "company", "doc_template_default": "company",
    "doc_template_preview": "company", "quotation_set_template": "quotations",
    "doc_template_duplicate": "company", "doc_template_archive": "company",
    "doc_template_restore": "company", "doc_template_build_new": "company",
    "doc_template_builder": "company",
    "billing": "billing", "billing_change_plan": "billing",
    "billing_cancel": "billing", "billing_buy_credits": "billing",
    # CRM — customers are one part of it; leads/pipeline/activities are the rest.
    "customers": "crm", "customer_detail": "crm",
    "customer_edit": "crm",
    "customer_create": "crm", "customer_contact_save": "crm",
    "customer_department": "crm", "customer_contact_detail": "crm",
    "crm_hub": "crm", "crm_search": "crm", "crm_reports": "crm",
    "crm_leads": "crm", "crm_lead_create": "crm", "crm_lead_detail": "crm",
    "crm_lead_convert": "crm", "crm_lead_lost": "crm",
    "crm_pipeline": "crm", "crm_opportunity_create": "crm",
    "crm_opportunity_detail": "crm", "crm_opportunity_stage": "crm",
    "crm_opportunity_move": "crm",
    "crm_activities": "crm", "crm_activity_schedule": "crm",
    "crm_activity_complete": "crm", "crm_interaction_log": "crm",
    "crm_note_add": "crm",
    "crm_customer_sites": "crm", "crm_customer_site_delete": "crm",
    "crm_customer_contacts": "crm", "crm_customer_contact_status": "crm",
    "rfq": "rfq", "rfq_detail": "rfq", "rfq_upload": "rfq", "rfq_approve": "rfq",
    "rfq_line": "rfq",
    "quotations": "quotations", "quotation_detail": "quotations",
    "quotation_edit": "quotations", "quotation_pdf": "quotations",
    "quotation_send": "quotations", "commercial_document_send": "quotations",
    "quotation_excel": "quotations", "quotation_po_extract": "quotations",
    "quotation_new": "quotations", "quotation_header": "quotations",
    "quotation_extract": "quotations",
    "quotation_line": "quotations", "quotation_section": "quotations",
    "quotation_transition": "quotations", "quotation_revise": "quotations",
    "quotation_po": "quotations", "quotation_award": "quotations",
    "quotation_create_invoice": "quotations", "quotation_create_delivery": "quotations",
    "commercial_document_pdf": "quotations", "commercial_document_detail": "quotations",
    "commercial_document_excel": "quotations", "commercial_document_transition": "quotations",
    "quotation_suggest": "quotations", "quotation_template": "quotations",
    "quotation_line_move": "quotations", "quotation_document": "quotations",
    "quotation_lines_bulk": "quotations",
    "projects": "projects", "project_detail": "projects", "readiness_partial": "projects",
    "project_override": "projects", "project_progress_claim": "projects",
    "compliance_item_approve": "projects",
    "estimates": "estimates", "estimate_detail": "estimates",
    "estimate_approve": "estimates", "estimate_revise": "estimates",
    "procurement": "procurement", "procurement_prices": "procurement",
    "suppliers": "procurement", "supplier_detail": "procurement",
    "purchase_orders": "procurement", "po_detail": "procurement",
    "po_approve": "procurement", "po_receive": "procurement",
    "products": "procurement", "product_detail": "procurement",
    "requests": "procurement", "request_new": "procurement", "request_detail": "procurement",
    "commercial": "commercial", "invoice_payment": "commercial",
    "lulama": "lulama",
}


def nav_flags(request):
    user = getattr(request, "user", None)
    signed_in = bool(user and user.is_authenticated)
    can = bool(signed_in and user.has_perm_code("finance.view_money"))
    can_proc = bool(signed_in and user.has_perm_code("procurement.manage"))
    rm = getattr(request, "resolver_match", None)
    section = _SECTIONS.get(rm.url_name, "") if rm else ""

    unread = 0
    if signed_in:
        from apps.execution.services import unread_count
        unread = unread_count(user)

    return {"perms_money": can, "perms_procurement": can_proc,
            "has_logo": has_logo_file(),
            "logo_static": logo_static_name(), "nav_section": section,
            "unread_notifications": unread}
