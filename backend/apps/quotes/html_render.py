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
    """The COMPANY'S OWN logo as a data: URI so WeasyPrint needs no filesystem
    access. Empty string when the company hasn't uploaded one — a customer-facing
    document must never fall back to the LulaWorks mark; the company name prints as
    text instead."""
    path = None
    logo = header.get("logo")
    if logo:
        try:
            path = logo.path if os.path.exists(logo.path) else None
        except (ValueError, NotImplementedError):
            path = None
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
    Deterministic, fully escaped — no user markup is ever interpolated raw. The
    structural knobs (header layout, table/totals/section-title style) are what
    make one design genuinely look different from another, not just recoloured."""
    design = design or DEFAULT_DESIGN
    branding = {**DEFAULT_DESIGN["branding"], **(design.get("branding") or {})}
    accent = branding.get("accent_color") or "#0E6E6E"
    secondary = branding.get("secondary_color") or _shade(accent)
    font = branding.get("font_family") or "Helvetica"
    header_style = branding.get("header_style") or "band"
    logo_pos = branding.get("logo_position") or "left"

    st = {
        "accent": accent, "secondary": secondary,
        "cols": design.get("columns") or list(TEMPLATE_ITEM_COLUMN_KEYS),
        "hnote": design.get("header_note", ""), "fnote": design.get("footer_note", ""),
        "logo_pos": logo_pos, "header_style": header_style,
        "table_style": design.get("table_style") or "lines",
        "totals_style": design.get("totals_style") or "plain",
        "title_style": design.get("section_title_style") or "plain",
    }

    sections = design.get("sections") or DEFAULT_DESIGN["sections"]
    visible = [e.get("key") for e in sections if e.get("visible", True)]
    css = _base_css(accent, secondary, font, st)

    # The 'sidebar' header wraps the WHOLE page in two columns: the identity lives
    # in a coloured left rail, everything else in the main column.
    if header_style == "sidebar":
        rail = _s_letterhead(context, st)
        main = "".join(_SECTION_BUILDERS[k](context, st) or ""
                       for k in visible if k != "letterhead" and k in _SECTION_BUILDERS)
        body = f"<div class='sidebar-layout'><aside class='rail'>{rail}</aside><main>{main}</main></div>"
    else:
        body = "".join(_SECTION_BUILDERS[k](context, st) or ""
                       for k in visible if k in _SECTION_BUILDERS)

    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{css}</style></head><body>{body}</body></html>")


def _shade(hexcolor: str) -> str:
    """A darker companion of the accent — the default 'secondary' colour used by
    the split header and highlighted totals when the template sets none."""
    try:
        h = hexcolor.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        r, g, b = (int(c * 0.72) for c in (r, g, b))
        return f"#{r:02X}{g:02X}{b:02X}"
    except (ValueError, IndexError):
        return "#222222"


def _base_css(accent, secondary, font, st) -> str:
    hs, ts, tot = st["header_style"], st["table_style"], st["totals_style"]
    tis = st["title_style"]

    # Letterhead per header style.
    lh = {
        "band": f".letterhead{{background:{accent};color:#fff;padding:14px 16px;border-radius:4px;}}"
                f".letterhead .muted{{color:rgba(255,255,255,.85);}}",
        "plain": f".letterhead{{border-bottom:2.5px solid {accent};padding-bottom:8px;}}",
        "minimal": ".letterhead{border-bottom:1px solid #ddd;padding-bottom:10px;}"
                   ".letterhead .coname{font-weight:600;letter-spacing:.5px;}",
        "centered": f".letterhead{{text-align:center;border-bottom:2px solid {accent};padding-bottom:10px;}}"
                    ".letterhead .lh-row{justify-content:center;flex-direction:column;gap:6px;}",
        "split": f".letterhead{{background:{accent};color:#fff;padding:14px 16px;border-radius:4px;"
                 f"border-bottom:6px solid {secondary};}}.letterhead .muted{{color:rgba(255,255,255,.85);}}",
        "sidebar": "",     # styled via .rail below
    }.get(hs, "")

    justify = {"left": "flex-start", "center": "center", "right": "flex-end"}.get(st["logo_pos"], "flex-start")
    logo_order = "1" if st["logo_pos"] == "left" else "2"
    ident_order = "2" if st["logo_pos"] == "left" else "1"

    # Item table per table style.
    table = {
        "lines": f"table.items th{{background:{accent};color:#fff;}}"
                 "table.items td{border-bottom:1px solid #e6e6e6;}",
        "striped": f"table.items th{{background:{accent};color:#fff;}}"
                   "table.items tr:nth-child(even) td{background:#f5f7f7;}"
                   "table.items td{border-bottom:1px solid #eee;}",
        "bordered": f"table.items th{{background:{accent};color:#fff;border:1px solid {accent};}}"
                    "table.items td{border:1px solid #d8dede;}",
        "plain": f"table.items th{{color:{accent};border-bottom:2px solid {accent};}}"
                 "table.items td{border-bottom:1px solid #eee;}",
    }.get(ts, "")

    # Totals per totals style.
    totals = {
        "plain": f".totals tr.grand td{{border-top:2px solid {accent};color:{accent};font-weight:bold;font-size:13px;}}",
        "boxed": "table.totals{border:1px solid #ccd; border-radius:4px; background:#f7f9f9; padding:4px;}"
                 f".totals tr.grand td{{border-top:1px solid {accent};color:{accent};font-weight:bold;font-size:13px;}}",
        "highlighted": f".totals tr.grand td{{background:{accent};color:#fff;font-weight:bold;font-size:13px;"
                       "padding:6px 8px;border-radius:3px;}",
    }.get(tot, "")

    # Section titles per style.
    title = {
        "plain": f".section-title{{color:{accent};font-weight:bold;margin:14px 0 4px;font-size:12.5px;}}",
        "bar": f".section-title{{background:{accent};color:#fff;font-weight:bold;margin:14px 0 6px;"
               "padding:4px 8px;border-radius:3px;font-size:12px;}",
        "underline": f".section-title{{color:{accent};font-weight:bold;margin:14px 0 4px;font-size:12.5px;"
                     f"border-bottom:1.5px solid {accent};padding-bottom:2px;}}",
    }.get(tis, "")

    return f"""
    @page {{ size: A4; margin: 14mm 12mm; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: {font}, Arial, sans-serif; color:#111; font-size:12px; margin:0; }}
    h1.title {{ color:{accent}; font-size:22px; margin:14px 0 4px; }}
    .muted {{ color:#555; }}
    {lh}
    .letterhead .lh-row {{ display:flex; align-items:center; gap:16px; justify-content:{justify}; }}
    .letterhead img.logo {{ max-height:46px; max-width:150px; order:{logo_order}; }}
    .letterhead .ident {{ order:{ident_order}; }}
    .coname {{ font-size:16px; font-weight:bold; }}
    .sidebar-layout {{ display:flex; gap:0; align-items:stretch; }}
    .sidebar-layout .rail {{ width:32%; background:{accent}; color:#fff; padding:18px 14px; border-radius:4px 0 0 4px; }}
    .sidebar-layout .rail .muted {{ color:rgba(255,255,255,.85); }}
    .sidebar-layout .rail .lh-row {{ flex-direction:column; align-items:flex-start; gap:12px; }}
    .sidebar-layout .rail img.logo {{ max-width:130px; max-height:44px; }}
    .sidebar-layout .rail .coname {{ font-size:15px; line-height:1.25; }}
    .sidebar-layout main {{ width:68%; padding:8px 0 0 18px; }}
    .meta {{ display:flex; justify-content:space-between; gap:20px; margin-top:12px; }}
    .meta .box p {{ margin:1px 0; }}
    table.items {{ width:100%; border-collapse:collapse; margin-top:10px; }}
    table.items th {{ text-align:left; padding:6px 8px; font-size:11px; }}
    table.items td {{ padding:5px 8px; }}
    table.items td.num, table.items th.num {{ text-align:right; }}
    {table}
    .totals {{ width:46%; margin-left:auto; margin-top:8px; border-collapse:collapse; }}
    .totals td {{ padding:3px 8px; text-align:right; }}
    {totals}
    {title}
    .boxes {{ display:flex; gap:12px; margin-top:14px; }}
    .boxes .b {{ flex:1; border:1px solid #ccc; border-radius:4px; padding:10px; font-size:11px; }}
    .foot {{ margin-top:16px; font-size:10.5px; color:#555; }}
    """


# ── Section builders. Each takes (ctx, st) and returns a fragment or "". ────────

def _title(text, st):
    return f"<div class='section-title'>{escape(text)}</div>"


def _s_letterhead(ctx, st):
    c = ctx["company"]
    lines = "".join(f"<p class='muted'>{escape(x)}</p>" for x in c["address"])
    for label, key in (("Tel ", "phone"), ("Email ", "email"),
                       ("VAT ", "vat_number"), ("Reg ", "registration_number")):
        if c.get(key):
            lines += f"<p class='muted'>{escape(label)}{escape(c[key])}</p>"
    logo = (f"<img class='logo' src='{ctx['logo_data_uri']}'>"
            if ctx["logo_data_uri"] else "")
    ident = f"<div class='ident'><div class='coname'>{escape(c['name'])}</div>{lines}</div>"
    note = f"<p class='muted' style='margin-top:6px;'><b>{escape(st['hnote'])}</b></p>" if st["hnote"] else ""
    return f"<div class='letterhead'><div class='lh-row'>{logo}{ident}</div>{note}</div>"


def _s_document_meta(ctx, st):
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


def _s_parties(ctx, st):
    cu = ctx["customer"]
    parts = [_title("Bill to", st), f"<p><b>{escape(cu['name'])}</b></p>"]
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


def _s_scope(ctx, st):
    scope = ctx["job"]["scope_of_work"]
    if not scope:
        return ""
    return _title("Scope of work", st) + f"<p>{escape(scope)}</p>"


def _s_items(ctx, st):
    from .models import TEMPLATE_ITEM_COLUMNS
    labels = dict(TEMPLATE_ITEM_COLUMNS)
    active = [c for c in st["cols"] if c in TEMPLATE_ITEM_COLUMN_KEYS
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


def _s_totals(ctx, st):
    f = ctx["financial"]
    if not f:
        return ""
    rows = [f"<tr><td>Subtotal</td><td>{escape(f['subtotal'])}</td></tr>"]
    if f.get("discount"):
        rows.append(f"<tr><td>Discount</td><td>-{escape(f['discount'])}</td></tr>")
    rows.append(f"<tr><td>{escape(f['vat_label'])}</td><td>{escape(f['vat'])}</td></tr>")
    rows.append(f"<tr class='grand'><td>TOTAL</td><td>{escape(f['total'])}</td></tr>")
    return f"<table class='totals'>{''.join(rows)}</table>"


def _s_banking(ctx, st):
    b = ctx["banking"]
    if not b:
        return ""
    fields = [("Bank", "bank_name"), ("Account name", "account_name"),
              ("Account number", "account_number"), ("Branch code", "branch_code")]
    rows = "".join(f"<p><b>{escape(lbl)}:</b> {escape(str(b.get(k, '')))}</p>"
                   for lbl, k in fields if b.get(k))
    return _title("Banking details", st) + rows


def _s_terms(ctx, st):
    terms = ctx["terms"]
    if not terms:
        return ""
    paras = "".join(f"<p class='muted'>{escape(line)}</p>"
                    for line in terms.splitlines() if line.strip())
    return _title("Terms & conditions", st) + paras


def _s_signature(ctx, st):
    return ("<div class='boxes'>"
            "<div class='b'><b>Prepared by</b><br><br>Signature: ____________________<br>"
            f"Date: {escape(ctx['document']['date'])}</div>"
            "<div class='b'><b>Received in good order by</b><br><br>"
            "Signature: ____________________<br>Date: ______________</div></div>")


def _s_footer(ctx, st):
    ref = ctx["document"]["reference"]
    note = f"{escape(st['fnote'])}<br>" if st["fnote"] else ""
    return (f"<div class='foot'>{note}Please use reference "
            f"<b>{escape(ref)}</b> when making payment.</div>")


_SECTION_BUILDERS = {
    "letterhead": _s_letterhead, "document_meta": _s_document_meta,
    "parties": _s_parties, "scope": _s_scope, "items": _s_items,
    "totals": _s_totals, "banking": _s_banking, "terms": _s_terms,
    "signature": _s_signature, "footer": _s_footer,
}
