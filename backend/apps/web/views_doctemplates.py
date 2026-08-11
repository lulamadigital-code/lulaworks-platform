"""Document Designer web UI — manage a company's document templates.

Companies keep their own look for quotations, tax invoices and delivery notes.
This is the no-code surface: list the templates per document type, pick the
default, edit the switches, and preview the result as a real PDF. Writes go
through apps.quotes.document_templates so validation lives in one place.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.quotes import document_templates as dts
from apps.quotes.models import (
    ALLOWED_FONTS,
    ALLOWED_LOGO_POSITIONS,
    DEFAULT_CONFIG,
    BaseLayout,
    DocumentTemplate,
    DocumentType,
)

#: The config switches, grouped for the form. (key, label, kind).
_BRANDING_FIELDS = [
    ("accent_color", "Accent colour", "color"),
    ("secondary_color", "Secondary colour", "color"),
    ("font", "Font", "font"),
    ("logo_position", "Logo position", "logo"),
]
_LAYOUT_FIELDS = [
    ("header_note", "Header note", "text"),
    ("footer_note", "Footer note", "text"),
    ("watermark_text", "Watermark text", "text"),
    ("show_watermark", "Show watermark", "bool"),
    ("page_numbering", "Page numbering", "bool"),
]
_VISIBILITY_FIELDS = [
    ("show_banking", "Banking details", "bool"),
    ("show_signature", "Signature area", "bool"),
    ("show_terms", "Terms & conditions", "bool"),
    ("show_vat_number", "VAT number", "bool"),
    ("show_registration_number", "Company registration", "bool"),
    ("show_customer_po", "Customer PO / reference", "bool"),
    ("show_project_reference", "Project reference", "bool"),
    ("show_qr", "QR code", "bool"),
]


def _can(user):
    # Document branding is a company-admin concern — reuse the customer/company
    # management gate the rest of the settings pages use.
    return user.has_perm_code("projects.create")


@login_required
def templates_list(request):
    """One section per document type. Seeds the built-in library on first view
    (and tops up any newly shipped built-ins) so a company always opens to a full
    set of looks. Active templates show as a gallery; archived ones collapse below."""
    company = request.user.active_company
    if dts.seed_document_templates(company, actor=request.user) == 0:
        dts.sync_builtin_templates(company, actor=request.user)
    groups = []
    for value, label in DocumentType.choices:
        active = dts.templates_for(company, value)
        archived = [t for t in dts.templates_for(company, value, include_archived=True)
                    if t.is_archived]
        groups.append({
            "doc_type": value, "label": label,
            "templates": active, "archived": archived,
        })
    return render(request, "web/doctemplates/list.html", {
        "groups": groups,
        "doc_types": DocumentType.choices,
        "layouts": BaseLayout.choices,
        "can_manage": _can(request.user),
    })


@login_required
@require_POST
def template_create(request):
    if not _can(request.user):
        messages.error(request, "You do not have permission to add templates.")
        return redirect("web:doc_templates")
    try:
        tpl = dts.create_template(
            request.user.active_company, request.user,
            doc_type=request.POST.get("doc_type", ""),
            name=request.POST.get("name", ""),
            base_layout=request.POST.get("base_layout", BaseLayout.CLASSIC),
        )
    except dts.TemplateError as exc:
        messages.error(request, str(exc))
        return redirect("web:doc_templates")
    messages.success(request, f"Template “{tpl.name}” created.")
    return redirect("web:doc_template_edit", pk=tpl.id)


@login_required
def template_edit(request, pk):
    tpl = get_object_or_404(DocumentTemplate.objects.all(), pk=pk)
    if request.method == "POST":
        if not _can(request.user):
            messages.error(request, "You do not have permission to edit templates.")
            return redirect("web:doc_template_edit", pk=pk)
        config = _config_from_post(request.POST)
        try:
            dts.update_template(
                tpl, request.user,
                name=request.POST.get("name", tpl.name),
                base_layout=request.POST.get("base_layout", tpl.base_layout),
                config=config)
        except dts.TemplateError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Template saved.")
            return redirect("web:doc_template_edit", pk=pk)

    cfg = tpl.merged_config()
    return render(request, "web/doctemplates/edit.html", {
        "tpl": tpl, "cfg": cfg,
        "layouts": BaseLayout.choices,
        "fonts": ALLOWED_FONTS,
        "logo_positions": ALLOWED_LOGO_POSITIONS,
        "branding_fields": _with_values(_BRANDING_FIELDS, cfg),
        "layout_fields": _with_values(_LAYOUT_FIELDS, cfg),
        "visibility_fields": _with_values(_VISIBILITY_FIELDS, cfg),
        "can_manage": _can(request.user),
    })


@login_required
@require_POST
def template_set_default(request, pk):
    tpl = get_object_or_404(DocumentTemplate.objects.all(), pk=pk)
    if not _can(request.user):
        messages.error(request, "You do not have permission to change the default.")
        return redirect("web:doc_templates")
    dts.set_default_template(tpl)
    messages.success(request, f"“{tpl.name}” is now the default {tpl.get_doc_type_display().lower()}.")
    return redirect("web:doc_templates")


@login_required
@require_POST
def template_duplicate(request, pk):
    tpl = get_object_or_404(DocumentTemplate.objects.all(), pk=pk)
    if not _can(request.user):
        messages.error(request, "You do not have permission to duplicate templates.")
        return redirect("web:doc_templates")
    copy = dts.duplicate_template(tpl, request.user)
    messages.success(request, f"Duplicated “{tpl.name}”. Customise your copy.")
    return redirect("web:doc_template_edit", pk=copy.id)


@login_required
@require_POST
def template_archive(request, pk):
    tpl = get_object_or_404(DocumentTemplate.objects.all(), pk=pk)
    if not _can(request.user):
        messages.error(request, "You do not have permission to archive templates.")
        return redirect("web:doc_templates")
    try:
        dts.archive_template(tpl, request.user)
    except dts.TemplateError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"“{tpl.name}” archived.")
    return redirect("web:doc_templates")


@login_required
@require_POST
def template_restore(request, pk):
    tpl = get_object_or_404(DocumentTemplate.objects.all(), pk=pk)
    if not _can(request.user):
        messages.error(request, "You do not have permission to restore templates.")
        return redirect("web:doc_templates")
    dts.restore_template(tpl, request.user)
    messages.success(request, f"“{tpl.name}” restored.")
    return redirect("web:doc_templates")


@login_required
@require_POST
def quotation_set_template(request, pk):
    """Per-document override: point one quotation at a specific template (or back
    to the company default). Blocked once the quotation is locked."""
    from apps.quotes.models import Quotation
    quote = get_object_or_404(Quotation.objects.all(), pk=pk)
    if not _can(request.user):
        messages.error(request, "You do not have permission to change the template.")
        return redirect("web:quotation_detail", pk=pk)
    tid = request.POST.get("template", "")
    if tid:
        quote.template = get_object_or_404(
            DocumentTemplate.objects.filter(doc_type=DocumentType.QUOTATION), pk=tid)
    else:
        quote.template = None      # fall back to the company default
    quote.save(update_fields=["template"])
    messages.success(request, "Document template updated for this quotation.")
    return redirect("web:quotation_detail", pk=pk)


@login_required
def template_preview(request, pk):
    """Render a real PDF with this template applied, using the company's most
    recent document of the matching type — so what you see is what you'll send.
    Falls back to a message when there's nothing yet to preview."""
    tpl = get_object_or_404(DocumentTemplate.objects.all(), pk=pk)
    company = request.user.active_company
    pdf = _preview_pdf(company, tpl)
    if pdf is None:
        messages.info(request, "Create a quotation first to preview this template.")
        return redirect("web:doc_template_edit", pk=pk)
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="preview-{tpl.name}.pdf"'
    return resp


# ── helpers ───────────────────────────────────────────────────────────────────

def _config_from_post(post) -> dict:
    """Assemble a config dict from the edit form. Checkboxes absent from POST
    read as False; text/colour/select come through as-is. Validation happens in
    the service (clean_config)."""
    cfg = {}
    for key, default in DEFAULT_CONFIG.items():
        if isinstance(default, bool):
            cfg[key] = key in post
        else:
            cfg[key] = post.get(key, "")
    return cfg


def _with_values(fields, cfg):
    return [{"key": k, "label": lbl, "kind": kind, "value": cfg.get(k)}
            for (k, lbl, kind) in fields]


def _preview_pdf(company, tpl):
    from apps.quotes.models import CommercialDocument, Quotation
    from apps.quotes.pdf import (
        delivery_note_pdf_bytes,
        invoice_pdf_bytes,
        quotation_pdf_bytes,
    )
    if tpl.doc_type == DocumentType.QUOTATION:
        quote = Quotation.objects.filter(company=company).order_by("-created_at").first()
        if not quote:
            return None
        quote.template = tpl                      # in-memory override, not saved
        return quotation_pdf_bytes(quote)
    # Invoice / delivery preview off the newest matching commercial document.
    kind = "invoice" if tpl.doc_type == DocumentType.INVOICE else "delivery"
    doc = (CommercialDocument.objects.filter(company=company, kind=kind)
           .order_by("-created_at").first())
    if not doc:
        return None
    doc.template = tpl
    return invoice_pdf_bytes(doc) if kind == "invoice" else delivery_note_pdf_bytes(doc)
