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
    ALLOWED_FONT_FAMILIES,
    ALLOWED_FONTS,
    ALLOWED_FOOTER_LAYOUTS,
    ALLOWED_HEADER_STYLES,
    ALLOWED_LOGO_POSITIONS,
    ALLOWED_LOGO_SIZES,
    ALLOWED_SECTION_STYLES,
    ALLOWED_TABLE_STYLES,
    ALLOWED_TOTALS_STYLES,
    DEFAULT_CONFIG,
    LOGO_HEIGHT_MAX,
    LOGO_HEIGHT_MIN,
    TEMPLATE_ITEM_COLUMNS,
    TEMPLATE_FIELD_LIBRARY,
    TEMPLATE_SECTIONS,
    BaseLayout,
    DocumentTemplate,
    DocumentType,
    TemplateEngine,
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
    from apps.quotes.models import FAMILY_BY_KEY, TEMPLATE_FAMILIES

    company = request.user.active_company
    if dts.seed_document_templates(company, actor=request.user) == 0:
        dts.sync_builtin_templates(company, actor=request.user)

    family_order = {key: i for i, (key, *_rest) in enumerate(TEMPLATE_FAMILIES)}

    def _decorate(t):
        # Attach the family's tag chips for the gallery card; order built-in
        # families by the catalogue, custom/imported templates after them.
        meta = FAMILY_BY_KEY.get(t.family)
        t.tags = meta[2] if meta else []
        t.sort_key = (0, family_order.get(t.family, 999)) if t.family else (1, t.name.lower())
        return t

    groups = []
    for value, label in DocumentType.choices:
        active = sorted((_decorate(t) for t in dts.templates_for(company, value)),
                        key=lambda t: t.sort_key)
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
    # HTML-engine templates are edited in the visual builder, not the config form.
    if tpl.engine == TemplateEngine.HTML:
        return redirect("web:doc_template_builder", pk=pk)
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
def template_build_new(request):
    """Create a fresh HTML-engine template (Method 2 — 'build your own') and open
    it in the visual builder."""
    if not _can(request.user):
        messages.error(request, "You do not have permission to add templates.")
        return redirect("web:doc_templates")
    doc_type = request.POST.get("doc_type", "")
    name = (request.POST.get("name") or "").strip() or "My template"
    try:
        tpl = dts.create_html_template(
            request.user.active_company, request.user,
            doc_type=doc_type, name=name)
    except dts.TemplateError as exc:
        messages.error(request, str(exc))
        return redirect("web:doc_templates")
    messages.success(request, "Template created — design it below.")
    return redirect("web:doc_template_builder", pk=tpl.id)


@login_required
def template_builder(request, pk):
    """The no-code visual builder for an HTML template: branding, section
    show/hide/reorder, item columns, header/footer notes, with a PDF preview."""
    tpl = get_object_or_404(DocumentTemplate.objects.all(), pk=pk)
    if tpl.engine != TemplateEngine.HTML:
        return redirect("web:doc_template_edit", pk=pk)

    if request.method == "POST":
        if not _can(request.user):
            messages.error(request, "You do not have permission to edit templates.")
            return redirect("web:doc_template_builder", pk=pk)
        try:
            dts.update_html_design(
                tpl, request.user, design=_design_from_post(request.POST),
                name=request.POST.get("name", tpl.name),
                description=request.POST.get("description", tpl.description))
        except dts.TemplateError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Template saved.")
            return redirect("web:doc_template_builder", pk=pk)

    from apps.quotes import document_capabilities as caps

    design = dts.current_design(tpl)
    branding = design.get("branding", {})
    # Only the sections this DOCUMENT TYPE is allowed to show appear in the builder —
    # so a quotation template can't be given a delivery acknowledgement, and a
    # delivery note can't be given totals. The signature block is relabelled to
    # what it actually means for this type.
    allowed = caps.allowed_sections(tpl.doc_type)
    signoff = caps.signoff_mode(tpl.doc_type)
    sig_label = {"compiled": "Compiled-by",
                 "delivery": "Delivery acknowledgement"}.get(signoff, "Sign-off")
    labels = dict(TEMPLATE_SECTIONS)
    labels["signature"] = f"{sig_label} area"
    ordered = design.get("sections") or [{"key": k, "visible": True} for k, _ in TEMPLATE_SECTIONS]
    sections = [{"key": s["key"], "label": labels.get(s["key"], s["key"]),
                 "visible": s.get("visible", True)} for s in ordered
                if s["key"] in labels and s["key"] in allowed]

    # A delivery note is quantity-based (Ordered/Delivered/Outstanding) — never let
    # the builder offer price columns for it.
    priced = caps.allows_prices(tpl.doc_type)
    active_cols = set(design.get("columns") or [])
    columns = [{"key": k, "label": lbl, "on": k in active_cols}
               for k, lbl in TEMPLATE_ITEM_COLUMNS
               if priced or k not in ("unit_price", "amount")]

    doc_type_note = {
        "quotation": "This is a QUOTATION — a commercial offer. It shows pricing and "
                     "an optional customer-acceptance area, never a delivery receipt.",
        "invoice": "This is a TAX INVOICE — a payment document. It shows pricing and "
                   "banking, never a delivery acknowledgement.",
        "delivery": "This is a DELIVERY NOTE — it records physical delivery with "
                    "Ordered / Delivered / Outstanding quantities, never prices.",
    }.get(tpl.doc_type, "")
    return render(request, "web/doctemplates/builder.html", {
        "tpl": tpl, "design": design, "branding": branding,
        "sections": sections, "columns": columns, "doc_type_note": doc_type_note,
        "fonts": ALLOWED_FONT_FAMILIES, "logo_positions": ALLOWED_LOGO_POSITIONS,
        "logo_sizes": ALLOWED_LOGO_SIZES, "header_styles": ALLOWED_HEADER_STYLES,
        "footer_layouts": ALLOWED_FOOTER_LAYOUTS,
        "logo_h_min": LOGO_HEIGHT_MIN, "logo_h_max": LOGO_HEIGHT_MAX,
        "table_styles": ALLOWED_TABLE_STYLES, "totals_styles": ALLOWED_TOTALS_STYLES,
        "section_styles": ALLOWED_SECTION_STYLES,
        "field_library": TEMPLATE_FIELD_LIBRARY,
        "can_manage": _can(request.user),
    })


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


#: What the importer accepts. PDFs give the richest layout signal; images degrade
#: gracefully; DOCX carries no reliable layout (a standard look is applied).
_IMPORT_EXTS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx", ".doc"}
_IMPORT_MAX_BYTES = 15 * 1024 * 1024


@login_required
def template_import_start(request):
    """Method 3 — upload an existing document; LulaAI reconstructs its structure."""
    from apps.quotes.models import TemplateImport
    from apps.quotes.template_import import IMPORT_CREDIT_ESTIMATE, run_import

    if request.method == "POST":
        if not _can(request.user):
            messages.error(request, "You do not have permission to import templates.")
            return redirect("web:doc_templates")
        upload = request.FILES.get("document")
        doc_type = request.POST.get("doc_type", "")
        if not upload:
            messages.error(request, "Choose a document to import.")
            return redirect("web:doc_template_import")
        import os
        ext = os.path.splitext(upload.name)[1].lower()
        if ext not in _IMPORT_EXTS:
            messages.error(request, "Unsupported file type. Upload a PDF, image or Word document.")
            return redirect("web:doc_template_import")
        if upload.size > _IMPORT_MAX_BYTES:
            messages.error(request, "That file is too large (max 15 MB).")
            return redirect("web:doc_template_import")
        if doc_type not in dict(DocumentType.choices):
            messages.error(request, "Choose which document type this is.")
            return redirect("web:doc_template_import")

        ti = TemplateImport.objects.create(
            company=request.user.active_company, doc_type=doc_type,
            source_file=upload, original_name=upload.name[:255],
            created_by=request.user, updated_by=request.user)
        run_import(ti, request.user)
        return redirect("web:doc_template_import_review", pk=ti.id)

    return render(request, "web/doctemplates/import.html", {
        "doc_types": DocumentType.choices,
        "credit_estimate": IMPORT_CREDIT_ESTIMATE,
        "can_manage": _can(request.user),
    })


@login_required
def template_import_review(request, pk):
    """Side-by-side: the original vs the reconstructed template, with the warnings
    LulaAI flagged for confirmation. Save it as a company template, or open it in
    the builder to adjust first."""
    from apps.quotes.models import TemplateImport
    ti = get_object_or_404(TemplateImport.objects.all(), pk=pk)
    return render(request, "web/doctemplates/import_review.html", {
        "ti": ti, "can_manage": _can(request.user),
    })


@login_required
@require_POST
def template_import_save(request, pk):
    from apps.quotes.models import TemplateImport
    from apps.quotes.template_import import save_as_template
    ti = get_object_or_404(TemplateImport.objects.all(), pk=pk)
    if not _can(request.user):
        messages.error(request, "You do not have permission to save templates.")
        return redirect("web:doc_templates")
    if ti.status != TemplateImport.Status.READY:
        messages.error(request, "This import isn’t ready to save.")
        return redirect("web:doc_template_import_review", pk=pk)
    tpl = save_as_template(ti, request.user, name=request.POST.get("name", ""))
    messages.success(request, "Saved as a company template — fine-tune it in the builder.")
    return redirect("web:doc_template_builder", pk=tpl.id)


@login_required
def template_import_original(request, pk):
    """Serve the uploaded original inline for the side-by-side view."""
    from django.http import FileResponse
    from apps.quotes.models import TemplateImport
    ti = get_object_or_404(TemplateImport.objects.all(), pk=pk)
    return FileResponse(ti.source_file.open("rb"),
                        filename=ti.original_name or "original")


@login_required
def template_import_preview(request, pk):
    """Render the reconstructed design to a PDF with sample data (no real document
    exists yet), so the user can eyeball the template before saving."""
    from apps.quotes.html_render import render_design_preview_pdf
    from apps.quotes.models import TemplateImport
    ti = get_object_or_404(TemplateImport.objects.all(), pk=pk)
    pdf = render_design_preview_pdf(request.user.active_company, ti.doc_type, ti.design)
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = 'inline; filename="reconstructed.pdf"'
    return resp


@login_required
def template_preview(request, pk):
    """Preview the TEMPLATE — its current saved design rendered with sample data —
    so what you see always reflects the latest edit. (Rendering a real document
    here was wrong: a finalised document is pinned to an older template version, so
    it ignored edits and showed the "old design".) ReportLab templates, which have
    no HTML design, still preview off the newest matching document."""
    from apps.quotes.document_templates import current_design
    from apps.quotes.html_render import render_design_preview_pdf
    from apps.quotes.models import TemplateEngine

    tpl = get_object_or_404(DocumentTemplate.objects.all(), pk=pk)
    company = request.user.active_company
    if tpl.engine == TemplateEngine.HTML:
        pdf = render_design_preview_pdf(company, tpl.doc_type, current_design(tpl),
                                        compact=False)
    else:
        pdf = _preview_pdf(company, tpl)
        if pdf is None:
            messages.info(request, "Create a quotation first to preview this template.")
            return redirect("web:doc_template_edit", pk=pk)
    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="preview-{tpl.name}.pdf"'
    resp["Cache-Control"] = "no-store"      # never show a stale preview
    return resp


@login_required
def template_thumb(request, pk):
    """A lightweight, embeddable HTML render of the template with SAMPLE data — the
    live thumbnail shown on each gallery card. HTML (no WeasyPrint), so a page full
    of these stays cheap. Falls back to a plain note for a ReportLab template, which
    has no HTML design to render inline (its 'Preview' opens a real PDF instead)."""
    from apps.quotes.document_templates import current_design
    from apps.quotes.html_render import design_to_html, sample_context
    from apps.quotes.models import TemplateEngine

    tpl = get_object_or_404(DocumentTemplate.objects.all(), pk=pk)
    company = request.user.active_company
    if tpl.engine == TemplateEngine.HTML:
        html = design_to_html(current_design(tpl), sample_context(company, tpl.doc_type),
                              compact=True)
    else:
        html = ("<!doctype html><meta charset='utf-8'><body "
                "style='font-family:system-ui;color:#94a3b8;display:flex;height:100%;"
                "align-items:center;justify-content:center;text-align:center;padding:20px;'>"
                "<div>Built-in ReportLab layout<br><small>Use “Preview” for a full PDF</small></div>")
    resp = HttpResponse(html, content_type="text/html; charset=utf-8")
    resp["X-Frame-Options"] = "SAMEORIGIN"      # embeddable in our own gallery only
    resp["Cache-Control"] = "no-store"          # always the latest saved design
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


def _design_from_post(post) -> dict:
    """Assemble an HTML-template design from the builder form. Section order comes
    from a per-section number input; validation (colours/fonts/known keys) happens
    in dts.clean_design."""
    from apps.quotes.models import TEMPLATE_SECTION_KEYS
    branding = {
        "accent_color": post.get("accent_color", ""),
        "secondary_color": post.get("secondary_color", ""),
        "font_family": post.get("font_family", ""),
        "logo_position": post.get("logo_position", ""),
        "logo_size": post.get("logo_size", ""),
        "logo_height": post.get("logo_height", "0"),
        "header_style": post.get("header_style", ""),
    }
    ordered = []
    for key in TEMPLATE_SECTION_KEYS:
        try:
            order = int(post.get(f"order_{key}", "999"))
        except (TypeError, ValueError):
            order = 999
        ordered.append((order, {"key": key, "visible": f"visible_{key}" in post}))
    ordered.sort(key=lambda pair: pair[0])
    return {
        "branding": branding,
        "sections": [entry for _, entry in ordered],
        "columns": post.getlist("columns"),
        "table_style": post.get("table_style", ""),
        "totals_style": post.get("totals_style", ""),
        "section_title_style": post.get("section_title_style", ""),
        "footer_layout": post.get("footer_layout", ""),
        "header_note": post.get("header_note", ""),
        "footer_note": post.get("footer_note", ""),
    }


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
