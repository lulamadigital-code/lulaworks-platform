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

from . import document_capabilities as caps
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

    # Items — prices on quotation/invoice; a delivery note carries QUANTITIES only
    # (ordered / delivered / outstanding), never prices. Ordered comes from the
    # quotation; delivered defaults to full and outstanding to zero — the normal
    # complete delivery — mirroring the ReportLab delivery-note builder.
    show_prices = caps.allows_prices(doc_type)
    items = []
    for ln in quote.lines.all():
        row = {"item_no": str(ln.position), "description": ln.description,
               "qty": f"{ln.qty:g}", "unit": ln.unit}
        if show_prices:
            row["unit_price"] = f"R{ln.effective_unit_price:,.2f}"
            row["amount"] = f"R{ln.line_total:,.2f}"
        else:
            row["unit_price"] = row["amount"] = ""
            row["ordered"] = f"{ln.qty:g}"
            row["delivered"] = f"{ln.qty:g}"
            row["outstanding"] = "0"
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

    # The unit-price column heading follows the quotation's JOB TYPE ("Rate" for
    # service/time work, "Unit price" for goods); an invoice always reads "Rate".
    job_key = quote.quotation_type.key if getattr(quote, "quotation_type_id", None) else None
    price_lbl = "Rate" if doc_type == "invoice" else caps.price_label(job_key)

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
        "price_label": price_lbl,
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
        if not show_prices:
            row["ordered"] = qty
            row["delivered"] = qty
            row["outstanding"] = "0"
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
        # Sample previews (thumbnails / builder / import review) show a clear
        # PLACEHOLDER when the company has no logo yet, so the logo slot, size and
        # position are visible. A real document never does this — it prints the
        # company name as text (see build_context / _logo_data_uri).
        "logo_data_uri": _logo_data_uri(header) or _placeholder_logo_data_uri(),
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
        "price_label": "Rate" if doc_type == "invoice" else "Unit price",
        "financial": financial,
        # Placeholder banking (and terms) when the company hasn't set its own, so
        # the preview shows those slots. A real document only shows configured
        # banking / terms.
        "banking": header["bank"] or _placeholder_banking(),
        "terms": "This is sample terms text shown only in the template preview.",
    }


def _placeholder_banking() -> dict:
    """Sample banking shown ONLY in a preview when the company has no bank account
    yet — so the banking slot is visible. Never used on a real document."""
    return {"bank_name": "Your bank (set in Company Profile)",
            "account_name": "Your Company (Pty) Ltd", "account_number": "0000000000",
            "branch_code": "000000", "branch_name": "", "account_type": "Cheque",
            "swift_code": "", "currency": "ZAR"}


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


def _placeholder_logo_data_uri() -> str:
    """A neutral "YOUR LOGO" placeholder (SVG data: URI) shown only in SAMPLE
    previews when the company hasn't uploaded a logo — so the reader sees where the
    logo will sit and how big it is. Never used on a real document."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='240' height='84' "
        "viewBox='0 0 240 84'>"
        "<rect x='1.5' y='1.5' width='237' height='81' rx='10' fill='#eef1f5' "
        "stroke='#c3ccd6' stroke-width='1.5' stroke-dasharray='6 4'/>"
        "<text x='120' y='40' font-family='Helvetica,Arial,sans-serif' font-size='19' "
        "font-weight='700' fill='#8a94a1' text-anchor='middle'>YOUR LOGO</text>"
        "<text x='120' y='59' font-family='Helvetica,Arial,sans-serif' font-size='10' "
        "fill='#a6afba' text-anchor='middle'>upload one in Company Profile</text>"
        "</svg>")
    data = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{data}"


# ── Design → HTML → PDF ────────────────────────────────────────────────────────

def render_html_pdf(document, doc_type: str, design: dict) -> bytes:
    """Render `document` to PDF using an HTML-engine `design`."""
    from weasyprint import HTML

    context = build_context(document, doc_type)
    caps.assert_renderable(context, doc_type)   # never emit a misleading document
    html = design_to_html(design or DEFAULT_DESIGN, context)
    return HTML(string=html).write_pdf()


def render_design_preview_pdf(company, doc_type: str, design: dict, *,
                              compact: bool = True) -> bytes:
    """Render a design to PDF with SAMPLE data — for the import review screen, the
    blank builder and gallery previews. `compact` (default) presents a tidy
    one-pager for thumbnails; pass compact=False for the full "Preview PDF" button,
    which shows the true comfortable spacing a real document uses."""
    from weasyprint import HTML

    html = design_to_html(design or DEFAULT_DESIGN,
                          sample_context(company, doc_type), compact=compact)
    return HTML(string=html).write_pdf()


def design_to_html(design: dict, context: dict, *, compact: bool = False) -> str:
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
        "logo_pos": logo_pos, "logo_size": branding.get("logo_size") or "medium",
        "logo_height": branding.get("logo_height") or 0,
        "header_style": header_style,
        "table_style": design.get("table_style") or "lines",
        "totals_style": design.get("totals_style") or "plain",
        "title_style": design.get("section_title_style") or "plain",
    }

    # Document-type capabilities are authoritative: whatever a template lists, only
    # the sections this document type is ALLOWED to show are rendered. This is what
    # stops a quotation from ever showing a delivery acknowledgement, or a delivery
    # note from showing totals/banking — regardless of the stored design.
    doc_type = context.get("doc_type") or caps.QUOTATION
    sections = design.get("sections") or DEFAULT_DESIGN["sections"]
    requested = [e.get("key") for e in sections if e.get("visible", True)]
    visible = caps.filter_sections(doc_type, requested)
    css = _base_css(accent, secondary, font, st, compact=compact)

    # A running footer repeated on EVERY page: company + document reference on the
    # left, "Page N of M" on the right — so a multi-page document stays identified
    # and numbered. Values are baked in as escaped CSS strings.
    ident = f"{context['company']['name']} · {context['document']['reference']}"
    css += (f"@page {{ @bottom-left {{ content: '{_css_string(ident)}';"
            " font-size: 8pt; color: #8a8a8a; }"
            " @bottom-right { content: 'Page ' counter(page) ' of ' counter(pages);"
            " font-size: 8pt; color: #8a8a8a; } }")

    # Footer layout: when "split" and both the sign-off and banking are shown, put
    # them side by side on one line to save space (only a quotation shows both).
    footer_layout = design.get("footer_layout") or "stacked"

    def _render(keys):
        pair = (footer_layout == "split" and "signature" in keys and "banking" in keys)
        out, paired = [], False
        for k in keys:
            if pair and k in ("signature", "banking"):
                if not paired:
                    sig = _s_signature(context, st) or ""
                    bank = _s_banking(context, st) or ""
                    out.append(f"<div class='pairrow'><div class='pcol'>{sig}</div>"
                               f"<div class='pcol pcol-bank'>{bank}</div></div>")
                    paired = True
                continue
            if k in _SECTION_BUILDERS:
                out.append(_SECTION_BUILDERS[k](context, st) or "")
        return "".join(out)

    # The 'sidebar' header wraps the WHOLE page in two columns: the identity lives
    # in a coloured left rail, everything else in the main column.
    if header_style == "sidebar":
        rail = _s_letterhead(context, st)
        main = _render([k for k in visible if k != "letterhead"])
        body = f"<div class='sidebar-layout'><aside class='rail'>{rail}</aside><main>{main}</main></div>"
    else:
        body = _render(visible)

    # A wrapper class keyed on the header style lets the CSS restyle the title/meta
    # and information blocks per family — the structural differences (e.g. Elevate's
    # centred hero title, Ledger's boxed reference) live in `hs-*` rules.
    return (f"<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{css}</style></head><body>"
            f"<div class='doc hs-{escape(header_style)}'>{body}</div>"
            f"</body></html>")


def _css_string(text: str) -> str:
    """Escape a value for use inside a CSS `content: '...'` string — so a company
    name with an apostrophe or backslash can't break out of the rule."""
    return (str(text).replace("\\", "\\\\").replace("'", "\\'")
            .replace("\n", " ").replace("\r", " "))


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


def _base_css(accent, secondary, font, st, compact=False) -> str:
    hs, ts, tot = st["header_style"], st["table_style"], st["totals_style"]
    tis = st["title_style"]

    # Spacing profile. Real documents render at COMFORTABLE spacing and paginate
    # naturally when long; only the SAMPLE preview (gallery thumbnail / preview
    # screen) uses the COMPACT profile so it presents as a tidy one-pager.
    if compact:
        S = dict(page="12mm 12mm 14mm", body_fs="11.5px", body_lh="1.32", p_m="1.5px",
                 h1_fs="20px", h1_m="6px 0 2px", sec_top="9px", band_pad="11px 14px",
                 plain_pb="6px", th_pad="4px 8px", td_pad="3px 8px", meta_top="8px",
                 items_top="7px", totals_top="6px", totals_td="2.5px 8px",
                 boxes_top="10px", foot_top="10px")
    else:
        S = dict(page="16mm 12mm 16mm", body_fs="12px", body_lh="1.45", p_m="3px",
                 h1_fs="22px", h1_m="12px 0 4px", sec_top="14px", band_pad="14px 16px",
                 plain_pb="8px", th_pad="6px 8px", td_pad="5px 8px", meta_top="12px",
                 items_top="10px", totals_top="8px", totals_td="3px 8px",
                 boxes_top="14px", foot_top="16px")

    # Letterhead per header style.
    lh = {
        "band": f".letterhead{{background:{accent};color:#fff;padding:{S['band_pad']};border-radius:4px;}}"
                f".letterhead .muted{{color:rgba(255,255,255,.85);}}",
        "plain": f".letterhead{{border-bottom:2.5px solid {accent};padding-bottom:{S['plain_pb']};}}",
        "minimal": ".letterhead{border-bottom:1px solid #ddd;padding-bottom:10px;}"
                   ".letterhead .coname{font-weight:600;letter-spacing:.5px;}",
        "centered": f".letterhead{{text-align:center;border-bottom:2px solid {accent};padding-bottom:10px;}}"
                    ".letterhead .lh-row{justify-content:center;flex-direction:column;gap:6px;}",
        "split": f".letterhead{{background:{accent};color:#fff;padding:11px 14px;border-radius:4px;"
                 f"border-bottom:6px solid {secondary};}}.letterhead .muted{{color:rgba(255,255,255,.85);}}",
        "sidebar": "",     # styled via .rail below
        # 'hero' — a quiet, centred identity; the DRAMA is the big centred title
        # band the meta section becomes (see hs-hero overrides). Premium & spacious.
        "hero": ".letterhead{text-align:center;padding-bottom:4px;}"
                ".letterhead .lh-row{flex-direction:column;gap:8px;}"
                ".letterhead .coname{font-size:18px;letter-spacing:1px;}",
        # 'ledger' — corporate letterhead with an accent underline; the reference/
        # date sit in a bordered box (hs-ledger overrides) for a finance look.
        "ledger": f".letterhead{{border-bottom:2.5px solid {accent};padding-bottom:8px;}}"
                  ".letterhead .coname{font-weight:bold;}",
    }.get(hs, "")

    # Identity text hugs the edge opposite the logo, so the letterhead spans the
    # full width and stays balanced (logo left → details right, and vice-versa).
    # The logo/identity DOM order (in _s_letterhead) does the actual placement,
    # since WeasyPrint's flexbox ignores CSS `order`.
    ident_align = {"left": "right", "right": "left", "center": "center"}.get(
        st["logo_pos"], "right")
    # Logo size — an exact height in px (the slider) drives it; otherwise a named
    # preset. Width is generous so a wide wordmark grows with the height rather than
    # being clipped, letting the logo be made genuinely large.
    lh_px = st.get("logo_height") or 0
    if not lh_px:
        lh_px = {"small": 46, "medium": 68, "large": 100, "xlarge": 140}.get(
            st.get("logo_size", "medium"), 68)
    logo_dims = (f"{int(lh_px)}px", f"{min(int(lh_px * 4.6), 460)}px")
    # Logo centred → stack the letterhead in a centred column, whatever the header.
    center_lh = (".letterhead .lh-row{flex-direction:column;align-items:center;text-align:center;}"
                 ".letterhead .ident{text-align:center;}") if st["logo_pos"] == "center" else ""

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

    # Section titles per style — quiet uppercase labels for a professional read.
    st_top = S["sec_top"]
    title = {
        "plain": f".section-title{{color:{accent};font-weight:700;text-transform:uppercase;"
                 f"letter-spacing:.07em;margin:{st_top} 0 4px;font-size:10.5px;}}",
        "bar": f".section-title{{background:{accent};color:#fff;font-weight:700;text-transform:uppercase;"
               f"letter-spacing:.06em;margin:{st_top} 0 5px;padding:4px 9px;border-radius:3px;font-size:10.5px;}}",
        "underline": f".section-title{{color:{accent};font-weight:700;text-transform:uppercase;"
                     f"letter-spacing:.07em;margin:{st_top} 0 4px;font-size:10.5px;"
                     f"border-bottom:1.5px solid {accent};padding-bottom:3px;}}",
    }.get(tis, "")

    return f"""
    @page {{ size: A4; margin: {S['page']}; }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: {font}, Arial, sans-serif; color:#111; font-size:{S['body_fs']};
            line-height:{S['body_lh']}; margin:0; }}
    p {{ margin: {S['p_m']} 0; }}
    /* Multi-page: repeat the item-table header on each page and never split a row
       or orphan the totals from the table. */
    table.items thead {{ display: table-header-group; }}
    table.items tbody tr {{ page-break-inside: avoid; }}
    .totals, .boxes {{ page-break-inside: avoid; }}
    .section-title {{ page-break-after: avoid; }}
    h1.title {{ color:{accent}; font-size:{S['h1_fs']}; margin:{S['h1_m']}; }}
    .muted {{ color:#555; }}
    .nb {{ white-space:nowrap; }}
    {lh}
    .letterhead .lh-row {{ display:flex; align-items:flex-start; gap:24px; justify-content:space-between; }}
    .letterhead img.logo {{ height:auto; max-height:{logo_dims[0]}; max-width:{logo_dims[1]};
                            object-fit:contain; }}
    .letterhead .ident {{ text-align:{ident_align}; line-height:1.42; }}
    {center_lh}
    .coname {{ font-size:17px; font-weight:700; letter-spacing:.01em; }}
    .hnote {{ margin-top:5px; font-weight:600; font-size:11px; opacity:.9; }}
    .sidebar-layout {{ display:flex; gap:0; align-items:stretch; }}
    .sidebar-layout .rail {{ width:32%; background:{accent}; color:#fff; padding:18px 14px; border-radius:4px 0 0 4px; }}
    .sidebar-layout .rail .muted {{ color:rgba(255,255,255,.85); }}
    .sidebar-layout .rail .lh-row {{ flex-direction:column; align-items:flex-start; gap:12px; }}
    .sidebar-layout .rail .ident {{ text-align:left; }}
    .sidebar-layout .rail img.logo {{ max-width:160px; max-height:58px; height:auto;
                                      object-fit:contain; margin-bottom:4px; }}
    .sidebar-layout .rail .coname {{ font-size:15px; line-height:1.25; }}
    .sidebar-layout main {{ width:68%; padding:8px 0 0 18px; }}
    .meta {{ display:flex; justify-content:space-between; align-items:flex-start;
             gap:24px; margin-top:{S['meta_top']}; }}
    .meta .title-box {{ flex:1; }}
    .meta .detail-box {{ text-align:right; min-width:38%; }}
    .meta .detail-box p {{ margin:1.5px 0; }}
    .meta .dl {{ color:#8a8a8a; text-transform:uppercase; font-size:9px;
                 letter-spacing:.05em; margin-right:8px; }}
    .meta .dv {{ font-weight:600; }}
    .party-name {{ font-weight:700; font-size:13px; margin-bottom:1px; }}
    table.items {{ width:100%; border-collapse:collapse; margin-top:{S['items_top']}; }}
    table.items th {{ text-align:left; padding:{S['th_pad']}; font-size:10px;
                      text-transform:uppercase; letter-spacing:.03em; font-weight:700; }}
    table.items td {{ padding:{S['td_pad']}; }}
    table.items td.num, table.items th.num {{ text-align:right; }}
    {table}
    .totals {{ width:44%; margin-left:auto; margin-top:{S['totals_top']}; border-collapse:collapse; }}
    .totals td {{ padding:{S['totals_td']}; }}
    .totals td:first-child {{ text-align:left; color:#555; }}
    .totals td:last-child {{ text-align:right; font-weight:600; }}
    {totals}
    {title}
    .boxes {{ display:flex; gap:12px; margin-top:{S['boxes_top']}; }}
    .boxes .b {{ flex:1; border:1px solid #ccc; border-radius:4px; padding:8px 10px; font-size:11px; }}
    /* The compiled-by box is ~58% wide on its own; when paired with banking it
       fills its column instead. */
    .boxes .signoff-box {{ flex:0 0 58%; }}
    .sig-name {{ margin-top:10px; min-height:13px; }}
    .sig-line {{ border-bottom:1px solid #999; margin-top:12px; }}
    .sig-line.short {{ width:60%; }}
    .sig-cap {{ font-size:9px; color:#777; margin-top:2px; text-transform:uppercase; letter-spacing:.03em; }}
    .sig-two {{ display:flex; gap:12px; }}
    .sig-two > div {{ flex:1; }}
    /* Split footer: sign-off and banking on one line. */
    .pairrow {{ display:flex; gap:14px; align-items:stretch; margin-top:{S['boxes_top']}; }}
    .pairrow .pcol {{ flex:1; min-width:0; }}
    .pairrow .boxes {{ margin-top:0; height:100%; }}
    .pairrow .boxes .b, .pairrow .boxes .signoff-box {{ flex:1 1 auto; }}
    .pairrow .pcol-bank {{ border:1px solid #ccc; border-radius:4px; padding:8px 10px; }}
    .pairrow .pcol-bank .section-title {{ margin-top:0; }}
    .foot {{ margin-top:{S['foot_top']}; font-size:10.5px; color:#555; }}

    /* Per-family structural overrides — these are what make one family look
       genuinely different, not merely recoloured. */
    /* Centred families (hero / centered): the whole letterhead centres, so the
       company details sit centred under the logo, not flush-left. */
    .hs-hero .letterhead .ident, .hs-centered .letterhead .ident {{ text-align:center; }}
    /* Elevate: a large, centred document title in its own band. */
    .hs-hero .meta {{ flex-direction:column; align-items:center; text-align:center;
        gap:4px; border-top:2px solid {accent}; border-bottom:2px solid {accent};
        padding:12px 0; margin:14px 0 6px; }}
    .hs-hero .title-box {{ flex:none; }}
    .hs-hero .detail-box {{ text-align:center; min-width:0; }}
    .hs-hero .detail-box p {{ display:inline-block; margin:0 9px; }}
    .hs-hero h1.title {{ font-size:30px; letter-spacing:3px; text-transform:uppercase; margin:0 0 4px; }}
    .hs-hero .section-title {{ text-align:center; }}
    /* Ledger: the reference/date sit in a bordered card on the right. */
    .hs-ledger .detail-box {{ border:1.5px solid {accent}; border-radius:4px;
        padding:9px 12px; background:#f8fafa; }}
    .hs-ledger h1.title {{ font-size:22px; margin-top:0; }}
    """


# ── Section builders. Each takes (ctx, st) and returns a fragment or "". ────────

def _title(text, st):
    return f"<div class='section-title'>{escape(text)}</div>"


def _s_letterhead(ctx, st):
    c = ctx["company"]
    # Compact identity: address on one line, contact grouped, statutory numbers
    # grouped — four tidy lines instead of ten, so the letterhead reads like a
    # professional letterhead, not a stacked list.
    def _row(tokens):
        # Join with dot separators; each token stays on one line (a URL or phone
        # number never breaks mid-way), so only the separators are break points.
        toks = [f"<span class='nb'>{escape(t)}</span>" for t in tokens if t]
        return " · ".join(toks)

    addr = escape(", ".join(x for x in c["address"] if x))
    contact = _row([f"Tel {c['phone']}" if c.get("phone") else "",
                    f"Cell {c['mobile']}" if c.get("mobile") else "",
                    c.get("email", ""), c.get("website", "")])
    statutory = _row([f"VAT {c['vat_number']}" if c.get("vat_number") else "",
                      f"Reg {c['registration_number']}" if c.get("registration_number") else ""])
    lines = "".join(f"<p class='muted'>{x}</p>"
                    for x in (addr, contact, statutory) if x)
    logo = (f"<img class='logo' src='{ctx['logo_data_uri']}'>"
            if ctx["logo_data_uri"] else "")
    ident = f"<div class='ident'><div class='coname'>{escape(c['name'])}</div>{lines}</div>"
    note = f"<p class='hnote'>{escape(st['hnote'])}</p>" if st["hnote"] else ""
    # DOM order carries the logo position — WeasyPrint's flexbox ignores CSS
    # `order`, so a logo-right template puts the identity first, then the logo.
    row = f"{ident}{logo}" if st.get("logo_pos") == "right" else f"{logo}{ident}"
    return f"<div class='letterhead'><div class='lh-row'>{row}</div>{note}</div>"


def _s_document_meta(ctx, st):
    """The document header row: the big document TITLE on the left, its reference
    details in a neat right-aligned column — the balanced masthead a professional
    document leads with."""
    d = ctx["document"]
    detail = [("Reference", d["reference"]), ("Date", d["date"])]
    if d["valid_until"]:
        detail.append(("Valid until", d["valid_until"]))
    if d["quotation_ref"]:
        detail.append(("Quotation", d["quotation_ref"]))
    if d["po_number"]:
        detail.append(("PO number", d["po_number"]))
    if d["prepared_by"]:
        detail.append(("Prepared by", d["prepared_by"]))
    rows = "".join(f"<p><span class='dl'>{escape(k)}</span>"
                   f"<span class='dv'>{escape(v)}</span></p>" for k, v in detail)
    return (f"<div class='meta'>"
            f"<div class='title-box'><h1 class='title'>{escape(d['title'])}</h1></div>"
            f"<div class='detail-box'>{rows}</div></div>")


def _s_parties(ctx, st):
    cu = ctx["customer"]
    parts = [_title("Bill to", st), f"<p class='party-name'>{escape(cu['name'])}</p>"]
    if cu["address"]:
        parts.append(f"<p class='muted'>{escape(cu['address'])}</p>")
    bits = []
    if cu["vat_number"]:
        bits.append(f"VAT {escape(cu['vat_number'])}")
    if ctx["contact"]:
        ct = ctx["contact"]
        c = "Attn: " + escape(ct["name"])
        if ct["email"]:
            c += f" · {escape(ct['email'])}"
        if ct["phone"]:
            c += f" · {escape(ct['phone'])}"
        bits.append(c)
    if bits:
        parts.append(f"<p class='muted'>{' &nbsp;·&nbsp; '.join(bits)}</p>")
    return "".join(parts)


def _s_scope(ctx, st):
    scope = ctx["job"]["scope_of_work"]
    if not scope:
        return ""
    return _title("Scope of work", st) + f"<p>{escape(scope)}</p>"


def _s_items(ctx, st):
    from .models import TEMPLATE_ITEM_COLUMNS
    # A delivery note is quantity-based: Ordered / Delivered / Outstanding, never
    # priced. Every other type uses the template's chosen priced columns.
    if caps.item_mode(ctx["doc_type"]) == caps.ITEMS_DELIVERY:
        active = ["item_no", "description", "ordered", "delivered", "outstanding", "unit"]
        labels = {"item_no": "No.", "description": "Description", "ordered": "Ordered",
                  "delivered": "Delivered", "outstanding": "Outstanding", "unit": "Unit"}
        num_cols = {"ordered", "delivered", "outstanding"}
    else:
        labels = dict(TEMPLATE_ITEM_COLUMNS)
        # The unit-price heading follows the job type ("Rate" vs "Unit price").
        labels["unit_price"] = ctx.get("price_label") or labels["unit_price"]
        active = [c for c in st["cols"] if c in TEMPLATE_ITEM_COLUMN_KEYS]
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
    # <thead> is repeated on every page by WeasyPrint; rows avoid mid-split.
    return (f"<table class='items'><thead><tr>{head}</tr></thead>"
            f"<tbody>{''.join(body)}</tbody></table>")


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
    """The sign-off block, worded by DOCUMENT TYPE — never a fixed label. A
    quotation offers customer ACCEPTANCE; a delivery note records DELIVERY
    acknowledgement ("received in good order"); an invoice has neither (its
    section is dropped upstream by the capability filter)."""
    mode = caps.signoff_mode(ctx["doc_type"])
    prep = escape(ctx["document"].get("prepared_by", "") or "")
    date = escape(ctx["document"]["date"])
    # Ruled blank lines (a bottom border) that scale to the box width — they never
    # overflow or wrap, however narrow the column (e.g. beside banking in a split
    # footer), unlike a run of underscores.
    def _line(cap, short=False):
        cls = "sig-line short" if short else "sig-line"
        return f"<div class='{cls}'></div><div class='sig-cap'>{cap}</div>"

    if mode == caps.SIGNOFF_COMPILED:
        # A supplier-issued document (quotation / tax invoice) carries only who
        # compiled it — never a customer counter-sign or a delivery receipt.
        label = {"quotation": "Quotation compiled by",
                 "invoice": "Invoice compiled by"}.get(ctx["doc_type"], "Compiled by")
        return ("<div class='boxes'>"
                f"<div class='b signoff-box'><b>{label}</b>"
                f"<div class='sig-name'>{prep}</div>"
                f"{_line('Signature')}{_line('Date &nbsp; ' + date, short=True)}</div></div>")
    if mode == caps.SIGNOFF_DELIVERY:
        # A delivery note records physical hand-over.
        return ("<div class='boxes'>"
                f"<div class='b'><b>Delivered by</b><div class='sig-name'>{prep}</div>"
                f"{_line('Signature')}{_line('Date &nbsp; ' + date, short=True)}</div>"
                "<div class='b'><b>Received in good order by</b>"
                f"{_line('Name')}{_line('Signature')}"
                "<div class='sig-two'>"
                f"<div>{_line('Date')}</div><div>{_line('Time')}</div></div>"
                f"{_line('Comments')}</div></div>")
    return ""      # SIGNOFF_NONE — no sign-off block


def _s_footer(ctx, st):
    ref = ctx["document"]["reference"]
    note = f"{escape(st['fnote'])}<br>" if st["fnote"] else ""
    # The closing line matches the document's purpose: a delivery note isn't paid
    # against, and a quotation is an offer (not yet an invoice), so neither asks
    # for a payment reference.
    dt = ctx["doc_type"]
    if dt == "delivery":
        tail = f"Please quote reference <b>{escape(ref)}</b> on any delivery query."
    elif dt == "quotation":
        tail = f"Please quote reference <b>{escape(ref)}</b> in all correspondence."
    else:
        tail = f"Please use reference <b>{escape(ref)}</b> when making payment."
    return f"<div class='foot'>{note}{tail}</div>"


_SECTION_BUILDERS = {
    "letterhead": _s_letterhead, "document_meta": _s_document_meta,
    "parties": _s_parties, "scope": _s_scope, "items": _s_items,
    "totals": _s_totals, "banking": _s_banking, "terms": _s_terms,
    "signature": _s_signature, "footer": _s_footer,
}
