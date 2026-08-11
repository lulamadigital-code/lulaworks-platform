"""HTML rendering engine for the Template Studio (Method 2 / Method 3).

A company can build its own document look, or have LulaAI reconstruct an existing
one. Those templates are stored as a structured `design` (branding + ordered,
show/hide-able sections + item columns) on a DocumentTemplateVersion, and rendered
to PDF here via WeasyPrint — coexisting with the ReportLab built-ins.

Two hard rules:
  • The renderer fills real values BY KEY from `build_context`; a template can
    never pull data from another tenant, and every value is HTML-escaped, so a
    stray "{{…}}" or crafted design can't inject markup or leak data.
  • The design is a validated schema, not freeform HTML — the output is always a
    well-formed, safe document.
"""

from __future__ import annotations

import base64
import mimetypes
import os
from html import escape

from django.contrib.staticfiles import finders
from django.utils import timezone

from .models import DEFAULT_DESIGN, TEMPLATE_ITEM_COLUMN_KEYS


# ── Rendering context: the {{field}} library, filled from real data ────────────

def build_context(document, doc_type: str) -> dict:
    """Everything a document template can show, drawn from the same sources the
    ReportLab builders use — so an HTML template and a built-in render identical
    business data. `document` is a Quotation (quotation) or CommercialDocument
    (invoice/delivery)."""
    from apps.identity.profile import document_header, document_terms

    from .pdf import _customer_address, _initials_surname, _name, _scope_text

    company = document.company
    header = document_header(company, kind=doc_type)
    quote = document if doc_type == "quotation" else document.quotation

    prep = quote.prepared_by
    prepared_by = ""
    if prep and prep.get_full_name():
        prepared_by = _initials_surname(_name(prep.get_full_name()))
    elif prep:
        prepared_by = prep.email

    customer = quote.customer
    contact = quote.contact

    # Items — prices on quotation/invoice, quantities only on a delivery note.
    show_prices = doc_type != "delivery"
    items = []
    for ln in quote.lines.all():
        row = {"item_no": str(ln.position), "description": ln.description,
               "qty": f"{ln.qty:g}", "unit": ln.unit}
        if show_prices:
            row["unit_price"] = f"R{ln.effective_unit_price:,.2f}"
            row["amount"] = f"R{ln.line_total:,.2f}"
        else:
            row["unit_price"] = row["amount"] = ""
        items.append(row)

    # Totals.
    financial = {}
    if doc_type == "invoice":
        financial = {
            "subtotal": f"R{quote.subtotal:,.2f}",
            "discount": f"R{quote.discount_amount:,.2f}" if quote.discount_amount else "",
            "vat_label": f"VAT @ {quote.vat_rate:g}%",
            "vat": f"R{quote.vat_amount:,.2f}",
            "total": f"R{quote.invoice_total:,.2f}",
        }
    elif doc_type == "quotation":
        vat_on_quote = quote.vat_amount if quote.vat_mode == "inclusive" else 0
        financial = {
            "subtotal": f"R{quote.subtotal:,.2f}",
            "discount": f"R{quote.discount_amount:,.2f}" if quote.discount_amount else "",
            "vat_label": "VAT",
            "vat": f"R{vat_on_quote:,.2f}",
            "total": f"R{quote.total:,.2f}",
        }

    title = {"quotation": "QUOTATION", "invoice": "TAX INVOICE",
             "delivery": "DELIVERY NOTE"}[doc_type]
    ref = getattr(document, "number", "") or getattr(quote, "number", "")
    po = getattr(document, "purchase_order", None)

    return {
        "doc_type": doc_type,
        "company": {
            "name": header["display_name"], "address": header["address_lines"],
            "email": header["email"], "phone": header["phone"],
            "mobile": header["mobile"], "website": header["website"],
            "vat_number": header["vat_no"], "registration_number": header["registration_no"],
            "tax_number": header["tax_reference_no"],
        },
        "logo_data_uri": _logo_data_uri(header),
        "customer": {
            "name": _name(quote.client_name),
            "address": _customer_address(customer) if customer else "",
            "vat_number": (customer.vat_no if customer else ""),
        },
        "contact": {
            "name": _name(contact.full_name) if contact else "",
            "email": (contact.email if contact else ""),
            "phone": ((contact.telephone or contact.mobile) if contact else ""),
        } if contact else None,
        "document": {
            "reference": ref, "date": document.created_at.strftime("%d/%m/%Y"),
            "prepared_by": prepared_by, "title": title, "type": doc_type,
            "po_number": (po.po_number if po and getattr(po, "po_number", "") else ""),
            "quotation_ref": quote.number if doc_type != "quotation" else "",
            "valid_until": (quote.validity_date.strftime("%d/%m/%Y")
                            if getattr(quote, "validity_date", None) else ""),
        },
        "job": {
            "title": quote.title, "site": (str(quote.customer_site)
                                           if quote.customer_site_id else quote.site),
            "scope_of_work": _scope_text(quote),
        },
        "items": items,
        "financial": financial,
        "banking": header["bank"],
        "terms": document_terms(company, kind=doc_type),
    }


def sample_context(company, doc_type: str) -> dict:
    """A context of clearly-SAMPLE data for previewing a template that has no real
    document yet (the import review screen, a blank builder). Uses the company's
    own identity/logo/banking so branding shows true, with placeholder customer
    and line items — plainly marked SAMPLE so it can't be mistaken for a real doc."""
    from apps.identity.profile import document_header

    header = document_header(company, kind=doc_type)
    show_prices = doc_type != "delivery"
    rows = [("Centrifugal pump overhaul — strip, inspect, rebuild", "2", 18500),
            ("Mechanical seal replacement kit", "2", 4200),
            ("Site labour — millwright (per shift)", "6", 3800)]
    items = []
    for i, (desc, qty, price) in enumerate(rows, start=1):
        row = {"item_no": str(i), "description": desc, "qty": qty, "unit": "ea"}
        row["unit_price"] = f"R{price:,.2f}" if show_prices else ""
        row["amount"] = f"R{price * int(qty):,.2f}" if show_prices else ""
        items.append(row)
    financial = {} if doc_type == "delivery" else {
        "subtotal": "R68,200.00", "discount": "", "vat_label": "VAT @ 15%",
        "vat": "R10,230.00", "total": "R78,430.00"}
    title = {"quotation": "QUOTATION", "invoice": "TAX INVOICE",
             "delivery": "DELIVERY NOTE"}[doc_type]
    return {
        "doc_type": doc_type,
        "company": {
            "name": header["display_name"], "address": header["address_lines"],
            "email": header["email"], "phone": header["phone"], "mobile": header["mobile"],
            "website": header["website"], "vat_number": header["vat_no"],
            "registration_number": header["registration_no"],
            "tax_number": header["tax_reference_no"],
        },
        "logo_data_uri": _logo_data_uri(header),
        "customer": {"name": "SAMPLE — Customer (Pty) Ltd",
                     "address": "1 Sample Road, Johannesburg", "vat_number": "4990000000"},
        "contact": {"name": "Sample Contact", "email": "buyer@example.co.za", "phone": "011 000 0000"},
        "document": {"reference": "SAMPLE-0001",
                     "date": timezone.now().strftime("%d/%m/%Y"),
                     "prepared_by": "A. Preparer", "title": title, "type": doc_type,
                     "po_number": "", "quotation_ref": "" if doc_type == "quotation" else "QT-SAMPLE",
                     "valid_until": ""},
        "job": {"title": "Sample scope", "site": "Sample Site",
                "scope_of_work": "Sample scope of work — this is a preview with placeholder data."},
        "items": items,
        "financial": financial,
        "banking": header["bank"],
        "terms": "This is sample terms text shown only in the template preview.",
    }


def _logo_data_uri(header) -> str:
    """The company logo as a data: URI so WeasyPrint needs no filesystem access.
    Falls back to the bundled mark; empty string when there's nothing to show."""
    path = None
    logo = header.get("logo")
    if logo:
        try:
            path = logo.path if os.path.exists(logo.path) else None
        except (ValueError, NotImplementedError):
            path = None
    path = path or finders.find("web/logo.png")
    if not path or not os.path.exists(path):
        return ""
    try:
        mime = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as fh:
            data = base64.b64encode(fh.read()).decode("ascii")
        return f"data:{mime};base64,{data}"
    except OSError:
        return ""


# ── Design → HTML → PDF ────────────────────────────────────────────────────────

def render_html_pdf(document, doc_type: str, design: dict) -> bytes:
    """Render `document` to PDF using an HTML-engine `design`."""
    from weasyprint import HTML

    context = build_context(document, doc_type)
    html = design_to_html(design or DEFAULT_DESIGN, context)
    return HTML(string=html).write_pdf()


def render_design_preview_pdf(company, doc_type: str, design: dict) -> bytes:
    """Render a design to PDF with SAMPLE data — for the import review screen and
    a blank builder, where there's no real document to fill it with yet."""
    from weasyprint import HTML

    html = design_to_html(design or DEFAULT_DESIGN, sample_context(company, doc_type))
    return HTML(string=html).write_pdf()


def design_to_html(design: dict, context: dict) -> str:
    """Turn a validated design + a real-data context into a complete HTML string.
    Deterministic, fully escaped — no user markup is ever interpolated raw."""
    design = design or DEFAULT_DESIGN
    branding = {**DEFAULT_DESIGN["branding"], **(design.get("branding") or {})}
    accent = branding.get("accent_color") or "#0E6E6E"
    font = branding.get("font_family") or "Helvetica"
    header_style = branding.get("header_style") or "band"
    logo_pos = branding.get("logo_position") or "left"
    columns = design.get("columns") or list(TEMPLATE_ITEM_COLUMN_KEYS)

    sections = design.get("sections") or DEFAULT_DESIGN["sections"]
    body_parts = []
    for entry in sections:
        if not entry.get("visible", True):
            continue
        block = _SECTION_BUILDERS.get(entry.get("key"))
        if block is None:
            continue
        html = block(context, accent, columns,
                     design.get("header_note", ""), design.get("footer_note", ""))
        if html:
            body_parts.append(html)

    css = _base_css(accent, font, header_style, logo_pos)
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{css}</style></head><body>{''.join(body_parts)}</body></html>")


def _base_css(accent, font, header_style, logo_pos) -> str:
    band = ("" if header_style != "band" else
            f".letterhead{{background:{accent};color:#fff;padding:14px 16px;border-radius:4px;}}"
            f".letterhead .muted{{color:rgba(255,255,255,.85);}}")
    rule = ("" if header_style == "band" else
            f".letterhead{{border-bottom:2.5px solid {accent};padding-bottom:8px;}}")
    justify = {"left": "flex-start", "center": "center", "right": "flex-end"}.get(logo_pos, "flex-start")
    order = "1" if logo_pos == "left" else "2"
    return f"""
    @page {{ size: A4; margin: 14mm 12mm; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: {font}, Arial, sans-serif; color:#111; font-size:12px; margin:0; }}
    h1.title {{ color:{accent}; font-size:22px; margin:14px 0 4px; }}
    .muted {{ color:#555; }}
    {band}{rule}
    .letterhead .lh-row {{ display:flex; align-items:center; gap:16px; }}
    .letterhead img.logo {{ max-height:46px; max-width:150px; order:{order}; }}
    .letterhead .ident {{ order:{'2' if logo_pos=='left' else '1'}; }}
    .letterhead.center .lh-row {{ justify-content:center; flex-direction:column; text-align:center; }}
    .lh-justify {{ justify-content:{justify}; }}
    .coname {{ font-size:16px; font-weight:bold; }}
    .meta {{ display:flex; justify-content:space-between; gap:20px; margin-top:12px; }}
    .meta .box p {{ margin:1px 0; }}
    table.items {{ width:100%; border-collapse:collapse; margin-top:10px; }}
    table.items th {{ background:{accent}; color:#fff; text-align:left; padding:6px 8px; font-size:11px; }}
    table.items td {{ padding:5px 8px; border-bottom:1px solid #e6e6e6; }}
    table.items td.num, table.items th.num {{ text-align:right; }}
    .totals {{ width:44%; margin-left:auto; margin-top:8px; }}
    .totals td {{ padding:3px 8px; text-align:right; }}
    .totals tr.grand td {{ border-top:2px solid {accent}; color:{accent}; font-weight:bold; font-size:13px; }}
    .section-title {{ color:{accent}; font-weight:bold; margin:14px 0 4px; font-size:12.5px; }}
    .boxes {{ display:flex; gap:12px; margin-top:14px; }}
    .boxes .b {{ flex:1; border:1px solid #ccc; border-radius:4px; padding:10px; font-size:11px; }}
    .foot {{ margin-top:16px; font-size:10.5px; color:#555; }}
    """


# ── Section builders. Each returns an HTML fragment or "" (nothing to show). ────

def _s_letterhead(ctx, accent, cols, hnote, fnote):
    c = ctx["company"]
    lines = "".join(f"<p class='muted'>{escape(x)}</p>" for x in c["address"])
    for label, key in (("Tel ", "phone"), ("Email ", "email"),
                       ("VAT ", "vat_number"), ("Reg ", "registration_number")):
        if c.get(key):
            lines += f"<p class='muted'>{escape(label)}{escape(c[key])}</p>"
    logo = (f"<img class='logo' src='{ctx['logo_data_uri']}'>"
            if ctx["logo_data_uri"] else "")
    ident = f"<div class='ident'><div class='coname'>{escape(c['name'])}</div>{lines}</div>"
    note = f"<p class='muted' style='margin-top:6px;'><b>{escape(hnote)}</b></p>" if hnote else ""
    return (f"<div class='letterhead'><div class='lh-row lh-justify'>{logo}{ident}</div>{note}</div>")


def _s_document_meta(ctx, accent, cols, hnote, fnote):
    d = ctx["document"]
    right = [f"<h1 class='title'>{escape(d['title'])}</h1>",
             f"<p><b>Reference:</b> {escape(d['reference'])}</p>",
             f"<p><b>Date:</b> {escape(d['date'])}</p>"]
    if d["quotation_ref"]:
        right.append(f"<p><b>Quotation ref:</b> {escape(d['quotation_ref'])}</p>")
    if d["po_number"]:
        right.append(f"<p><b>PO number:</b> {escape(d['po_number'])}</p>")
    if d["prepared_by"]:
        right.append(f"<p><b>Prepared by:</b> {escape(d['prepared_by'])}</p>")
    if d["valid_until"]:
        right.append(f"<p><b>Valid until:</b> {escape(d['valid_until'])}</p>")
    return f"<div class='meta'><div class='box'></div><div class='box'>{''.join(right)}</div></div>"


def _s_parties(ctx, accent, cols, hnote, fnote):
    cu = ctx["customer"]
    parts = [f"<div class='section-title'>Bill to</div><p><b>{escape(cu['name'])}</b></p>"]
    if cu["address"]:
        parts.append(f"<p class='muted'>{escape(cu['address'])}</p>")
    if cu["vat_number"]:
        parts.append(f"<p class='muted'>VAT: {escape(cu['vat_number'])}</p>")
    if ctx["contact"]:
        ct = ctx["contact"]
        parts.append(f"<p>Contact: {escape(ct['name'])}"
                     + (f" · {escape(ct['email'])}" if ct["email"] else "")
                     + (f" · {escape(ct['phone'])}" if ct["phone"] else "") + "</p>")
    return "".join(parts)


def _s_scope(ctx, accent, cols, hnote, fnote):
    scope = ctx["job"]["scope_of_work"]
    if not scope:
        return ""
    return f"<div class='section-title'>Scope of work</div><p>{escape(scope)}</p>"


def _s_items(ctx, accent, cols, hnote, fnote):
    from .models import TEMPLATE_ITEM_COLUMNS
    labels = dict(TEMPLATE_ITEM_COLUMNS)
    # A delivery note never shows money columns.
    active = [c for c in cols if c in TEMPLATE_ITEM_COLUMN_KEYS
              and not (ctx["doc_type"] == "delivery" and c in ("unit_price", "amount"))]
    if not active:
        active = ["item_no", "description", "qty", "unit"]
    num_cols = {"qty", "unit_price", "amount"}
    head = "".join(f"<th class='{'num' if c in num_cols else ''}'>{escape(labels[c])}</th>"
                   for c in active)
    body = []
    for it in ctx["items"]:
        tds = "".join(f"<td class='{'num' if c in num_cols else ''}'>{escape(str(it.get(c, '')))}</td>"
                      for c in active)
        body.append(f"<tr>{tds}</tr>")
    if not body:
        body.append(f"<tr><td colspan='{len(active)}' class='muted'>No line items.</td></tr>")
    return f"<table class='items'><tr>{head}</tr>{''.join(body)}</table>"


def _s_totals(ctx, accent, cols, hnote, fnote):
    f = ctx["financial"]
    if not f:
        return ""
    rows = [f"<tr><td>Subtotal</td><td>{escape(f['subtotal'])}</td></tr>"]
    if f.get("discount"):
        rows.append(f"<tr><td>Discount</td><td>-{escape(f['discount'])}</td></tr>")
    rows.append(f"<tr><td>{escape(f['vat_label'])}</td><td>{escape(f['vat'])}</td></tr>")
    rows.append(f"<tr class='grand'><td>TOTAL</td><td>{escape(f['total'])}</td></tr>")
    return f"<table class='totals'>{''.join(rows)}</table>"


def _s_banking(ctx, accent, cols, hnote, fnote):
    b = ctx["banking"]
    if not b:
        return ""
    fields = [("Bank", "bank_name"), ("Account name", "account_name"),
              ("Account number", "account_number"), ("Branch code", "branch_code")]
    rows = "".join(f"<p><b>{escape(lbl)}:</b> {escape(str(b.get(k, '')))}</p>"
                   for lbl, k in fields if b.get(k))
    return f"<div class='section-title'>Banking details</div>{rows}"


def _s_terms(ctx, accent, cols, hnote, fnote):
    terms = ctx["terms"]
    if not terms:
        return ""
    paras = "".join(f"<p class='muted'>{escape(line)}</p>"
                    for line in terms.splitlines() if line.strip())
    return f"<div class='section-title'>Terms &amp; conditions</div>{paras}"


def _s_signature(ctx, accent, cols, hnote, fnote):
    return ("<div class='boxes'>"
            "<div class='b'><b>Prepared by</b><br><br>Signature: ____________________<br>"
            f"Date: {escape(ctx['document']['date'])}</div>"
            "<div class='b'><b>Received in good order by</b><br><br>"
            "Signature: ____________________<br>Date: ______________</div></div>")


def _s_footer(ctx, accent, cols, hnote, fnote):
    ref = ctx["document"]["reference"]
    note = f"{escape(fnote)}<br>" if fnote else ""
    return (f"<div class='foot'>{note}Please use reference "
            f"<b>{escape(ref)}</b> when making payment.</div>")


_SECTION_BUILDERS = {
    "letterhead": _s_letterhead, "document_meta": _s_document_meta,
    "parties": _s_parties, "scope": _s_scope, "items": _s_items,
    "totals": _s_totals, "banking": _s_banking, "terms": _s_terms,
    "signature": _s_signature, "footer": _s_footer,
}
