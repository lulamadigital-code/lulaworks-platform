"""Quotation PDF — the customer-facing document, laid out to match the format
LulaWorks contractors already issue (validated against a real Lulama Projects →
Western Platinum quotation): company identity with tax/VAT/registration numbers
and the customer's supplier number, a two-column client/quotation block, the
priced item table, totals, sign-off lines, and banking details.

Pure-Python ReportLab (no system libraries), so it renders inside the slim
container. Selling price only — never cost or margin (the Financial Golden Rule
at the document boundary)."""

import os
import re
from io import BytesIO
from xml.sax.saxutils import escape

from django.contrib.staticfiles import finders
from reportlab.lib import colors, utils
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

DEFAULT_BRAND = colors.HexColor("#0E6E6E")
MUTED = colors.HexColor("#5b6b6a")
LINE = colors.HexColor("#dfe6e6")

_HEX = re.compile(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def _brand_color(company):
    """The company's own brand colour if it set a valid one, else the LulaWorks
    teal — so a bad or empty value never breaks the document."""
    raw = (getattr(company, "brand_primary", "") or "").strip()
    if _HEX.match(raw):
        try:
            return colors.HexColor(raw[:7])   # ignore any alpha for print
        except ValueError:
            pass
    return DEFAULT_BRAND


def _logo_flowable(header, max_h=60 * mm, max_w=70 * mm):
    """The company's uploaded logo (Company Profile → branding, falling back to
    the main logo), else the bundled static mark, else None (the caller prints
    the company name as text instead). Sized to fill the letterhead — big and
    visible — but capped in both height and width so a wide or tall logo keeps
    its aspect ratio without overrunning the header."""
    path = None
    logo = header.get("logo")
    if logo:
        try:
            path = logo.path if os.path.exists(logo.path) else None
        except (ValueError, NotImplementedError):
            path = None
    path = path or finders.find("web/logo.png")
    if not path:
        return None
    try:
        reader = utils.ImageReader(path)
        iw, ih = reader.getSize()
        scale = min(max_h / ih, max_w / iw)      # fit within both bounds
        return Image(path, width=iw * scale, height=ih * scale)
    except Exception:
        return None


def _customer_address(customer) -> str:
    if not customer:
        return ""
    parts = [customer.physical_address or customer.postal_address,
             customer.city, customer.postal_code]
    return ", ".join(p for p in parts if p)


def quotation_pdf_bytes(quote) -> bytes:
    company = quote.company
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=16 * mm, bottomMargin=14 * mm,
                            leftMargin=16 * mm, rightMargin=16 * mm,
                            title=f"Quotation {quote.number}")
    brand = _brand_color(company)
    styles = getSampleStyleSheet()
    body = styles["BodyText"]
    small = body.clone("small"); small.fontSize = 9; small.leading = 11.5
    muted = small.clone("muted"); muted.textColor = MUTED
    title = styles["Heading1"].clone("qtitle")
    title.textColor = brand; title.alignment = 0     # left, aligned with the meta
    title.fontSize = 20; title.spaceAfter = 2
    coname = small.clone("coname"); coname.fontSize = 15; coname.leading = 18

    # Every company fact comes from the Company Profile — one place, never re-typed.
    from apps.identity.profile import document_header
    header = document_header(company, kind="quotation")

    def P(text, style=small):
        return Paragraph(escape(str(text)), style)

    def L(label, value, style=small):
        """A bold label with escaped free-text value — so the label markup is
        honoured but the value can never inject tags."""
        return Paragraph(f"<b>{escape(label)}</b> {escape(str(value))}", style)

    # ── Company identity (top-left) + logo (top-right) ───────────────────────
    ident = [Paragraph(f"<b>{escape(header['display_name'])}</b>", coname)]
    for line in header["address_lines"]:
        ident.append(P(line, muted))
    if header["phone"]:
        ident.append(P(header["phone"], muted))
    if header["email"]:
        ident.append(P(f"Email {header['email']}", muted))
    if header["tax_reference_no"]:
        ident.append(P(f"Tax No: {header['tax_reference_no']}", muted))
    if header["vat_no"]:
        ident.append(P(f"Vat No: {header['vat_no']}", muted))
    if header["registration_no"]:
        ident.append(P(f"Company Reg: {header['registration_no']}", muted))
    # The number THIS customer files us under — how their accounts payable finds
    # us. Snapshotted onto the quotation, but fall back to the customer record so
    # it still prints if the snapshot was empty at creation.
    vendor = quote.vendor_number or (
        quote.customer.vendor_number if quote.customer_id else "")
    if vendor:
        who = quote.customer.display_name if quote.customer_id else quote.client_name
        ident.append(P(f"{who} Supplier No: {vendor}", small))

    # Big logo, top-right; the company identity fills the left.
    logo = _logo_flowable(header)
    head = Table([[ident, logo or ""]], colWidths=[105 * mm, 73 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    # A bold brand-coloured rule separates the letterhead from the client block.
    story = [head, Spacer(1, 3 * mm),
             HRFlowable(width="100%", thickness=2.2, color=brand,
                        spaceBefore=2, spaceAfter=6)]

    # ── Two-column client / quotation block ──────────────────────────────────
    contact = quote.contact
    left = [L("Client :", quote.client_name)]
    addr = _customer_address(quote.customer)
    if addr:
        left.append(P(f"Address: {addr}", muted))
    if quote.customer_id and quote.customer.vat_no:
        left.append(P(f"VAT No: {quote.customer.vat_no}", muted))
    site = str(quote.customer_site) if quote.customer_site_id else quote.site
    if site:
        left.append(P(f"Ship to / Site: {site}", muted))
    if quote.department_id:
        left.append(P(f"Department: {quote.department.name}", muted))
    if contact:
        left.append(P(f"Contact Person: {contact.full_name}", small))
        tel = contact.telephone or contact.mobile
        if tel:
            left.append(P(f"Tel: {tel}", muted))
        if contact.email:
            left.append(P(f"Email: {contact.email}", muted))

    prep = quote.prepared_by
    right = [Paragraph("QUOTATION", title), Spacer(1, 2 * mm),
             L("Quotation No:", quote.number),
             P(f"Date: {quote.created_at:%d/%m/%Y}", small)]
    if prep:
        right.append(P(f"Prepared by: {prep.get_full_name() or prep.email}", small))
    if quote.customer_reference:
        right.append(P(f"Your reference: {quote.customer_reference}", small))
    if quote.validity_date:
        right.append(P(f"Valid until: {quote.validity_date:%d/%m/%Y}", small))
    if quote.scope_of_work:
        right.append(Spacer(1, 1 * mm))
        right.append(L("Scope of Work:", quote.scope_of_work))

    meta = Table([[left, right]], colWidths=[96 * mm, 82 * mm])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [meta, Spacer(1, 6 * mm)]

    # ── Item table ───────────────────────────────────────────────────────────
    cell = small.clone("cell")
    rows = [["Item No", "Description of job", "Quantity", "Unit", "Unit Price", "Amount"]]
    for ln in quote.lines.all():
        # The price the customer actually pays — cost + markup when no explicit
        # price was set — so the Unit Price column and the Amount agree.
        rows.append([str(ln.position), Paragraph(escape(ln.description), cell),
                     f"{ln.qty:g}", ln.unit, f"R{ln.effective_unit_price:,.2f}",
                     f"R{ln.line_total:,.2f}"])
    if len(rows) == 1:
        rows.append(["", Paragraph("No line items.", cell), "", "", "", ""])
    tbl = Table(rows, colWidths=[14 * mm, 79 * mm, 20 * mm, 18 * mm, 23.5 * mm, 23.5 * mm],
                repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), brand),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (3, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [tbl]

    # ── Totals (right-aligned, matching SUBTOTAL / VAT@15% / TOTAL) ───────────
    totals = [["SUBTOTAL", f"R{quote.subtotal:,.2f}"],
              [f"VAT@{quote.vat_rate:g}%", f"R{quote.vat_amount:,.2f}"],
              ["TOTAL", f"R{quote.total:,.2f}"]]
    tot = Table(totals, colWidths=[45 * mm, 33.5 * mm], hAlign="RIGHT")
    tot.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, brand),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("TEXTCOLOR", (0, -1), (-1, -1), brand),
    ]))
    story += [tot, Spacer(1, 8 * mm)]

    # Optional exclusions/assumptions, kept compact and only when present.
    for label, text in (("Exclusions:", quote.exclusions), ("Assumptions:", quote.assumptions)):
        if text:
            story.append(L(label, text, muted))
    if quote.exclusions or quote.assumptions:
        story.append(Spacer(1, 4 * mm))

    # ── Sign-off (left) + banking (right), inside one bordered box ───────────
    # "Compiled by" is filled in for us — who prepared it and today's date. The
    # "received" line is left blank for the customer to sign. Each keeps its
    # Initials & Surname and Date on a single line.
    prep_name = (prep.get_full_name() or prep.email) if prep else ""
    today = quote.created_at.strftime("%d/%m/%Y")
    signoff = [
        Paragraph("<b>Quotation Compiled By:</b>", small), Spacer(1, 1.5 * mm),
        Paragraph(f"Initials &amp; Surname: <b>{escape(prep_name)}</b>"
                  f"&nbsp;&nbsp;&nbsp;&nbsp;Date: <b>{today}</b>", small),
        Spacer(1, 5 * mm),
        Paragraph("<b>Received in Good Order By:</b>", small), Spacer(1, 1.5 * mm),
        Paragraph("Initials &amp; Surname: ____________________"
                  "&nbsp;&nbsp;&nbsp;Date: ____________", muted),
    ]

    bank = header["bank"]
    banking_title = Paragraph("<b>BANKING DETAILS</b>", small)
    if bank:
        bank_rows = [banking_title, Spacer(1, 1.5 * mm),
                     P(f"Account Holder: {bank['account_name']}", muted),
                     P(f"Bank Name: {bank['bank_name']}"
                       + (f" ({bank['account_type']})" if bank['account_type'] else ""), muted),
                     P(f"Account No: {bank['account_number']}", muted),
                     P(f"Branch Code: {bank['branch_code'] or '—'}", muted)]
    else:
        bank_rows = [banking_title, Spacer(1, 1.5 * mm),
                     P("Add a bank account in the Company Profile.", muted)]

    signbox = Table([[signoff, bank_rows]], colWidths=[100 * mm, 78 * mm])
    signbox.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 1, brand),
        ("LINEBEFORE", (1, 0), (1, 0), 0.6, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story += [signbox, Spacer(1, 6 * mm)]

    story.append(P(f"Please use document number ({quote.number}) for reference "
                   "when making payments.", muted))

    doc.build(story)
    return buf.getvalue()
