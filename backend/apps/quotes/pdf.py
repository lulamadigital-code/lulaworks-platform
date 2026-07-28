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
    KeepInFrame,
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


def _initials_surname(full_name: str) -> str:
    """"Ronny Maluleke" → "R. Maluleke"; "Ronny James Maluleke" → "R.J. Maluleke".
    The sign-off line asks for initials and surname, so that is what it shows."""
    parts = (full_name or "").split()
    if len(parts) < 2:
        return full_name or ""
    initials = "".join(p[0].upper() + "." for p in parts[:-1])
    return f"{initials} {parts[-1]}"


def _customer_address(customer) -> str:
    if not customer:
        return ""
    parts = [customer.physical_address or customer.postal_address,
             customer.city, customer.postal_code]
    return ", ".join(p for p in parts if p)


def _build_one_page(doc, story):
    """Build the document forced onto a single page: the whole story is wrapped
    in a frame the size of the page's content area and shrunk to fit only if it
    would otherwise overflow. A normal-length quotation, invoice or delivery note
    is rendered untouched; an unusually long one is scaled down rather than
    spilling onto a second page."""
    frame = KeepInFrame(doc.width, doc.height, story, mode="shrink")
    doc.build([frame])


def _scope_text(quote) -> str:
    """The Scope of Work shown below Prepared By. The quotation's title *is* its
    scope of work, so it is used when the dedicated scope field is left empty."""
    return (quote.scope_of_work or "").strip() or (quote.title or "").strip()


def _prepared_by_lines(quote, small):
    """The 'Prepared By' block — the logged-in estimator who owns the document:
    full name and position only (cell and email are omitted). Reads the person's
    job title from their membership in this company. Shared by the quotation,
    invoice and delivery note so the block is identical everywhere."""
    prep = quote.prepared_by
    if not prep:
        return []
    esc = escape
    lines = [Paragraph(
        f"<b>Prepared By:</b> {esc(prep.get_full_name() or prep.email)}", small)]
    from apps.identity.models import Membership
    m = Membership.objects.filter(user=prep, company_id=quote.company_id).first()
    if m and m.job_title:
        lines.append(Paragraph(f"Position: {esc(m.job_title)}", small))
    return lines


def _terms_flowables(company, kind, small, muted):
    """The company's standard Terms & Conditions for this document type, pulled
    from Company Profile → Commercial Document Settings and auto-inserted. Each
    non-blank line of the stored text becomes a paragraph. Empty when unset."""
    from apps.identity.profile import document_terms
    text = document_terms(company, kind=kind)
    if not text:
        return []
    out = [Paragraph("<b>Terms &amp; Conditions</b>", small), Spacer(1, 1.5 * mm)]
    for para in text.splitlines():
        para = para.strip()
        if para:
            out.append(Paragraph(escape(para), muted))
    out.append(Spacer(1, 5 * mm))
    return out


def _signoff_column(small, muted, *, compiled_label, prep_name, today,
                    received_label="Received in Good Order By:"):
    """The sign-off content — compiled by (pre-filled) then received by (blank
    for a signature). Shared by the two-box footer and the delivery note's
    banking-free sign-off box."""
    return [
        Paragraph(f"<b>{escape(compiled_label)}</b>", small), Spacer(1, 1.5 * mm),
        Paragraph(f"Initials &amp; Surname: <b>{escape(prep_name)}</b>"
                  f"&nbsp;&nbsp;&nbsp;&nbsp;Date: <b>{escape(today)}</b>", small),
        Spacer(1, 6 * mm),
        Paragraph(f"<b>{escape(received_label)}</b>", small), Spacer(1, 1.5 * mm),
        Paragraph("Initials &amp; Surname: ________________"
                  "&nbsp;&nbsp;&nbsp;Date: ____________", muted),
    ]


def _signoff_box(brand, small, muted, *, compiled_label, prep_name, today,
                 received_label="Received in Good Order By:", width=186 * mm):
    """A single bordered sign-off box, no banking — used by the delivery note."""
    signoff = _signoff_column(small, muted, compiled_label=compiled_label,
                              prep_name=prep_name, today=today,
                              received_label=received_label)
    box = Table([[signoff]], colWidths=[width], hAlign="LEFT")
    box.setStyle(TableStyle([
        ("BOX", (0, 0), (0, 0), 1, brand), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 9), ("RIGHTPADDING", (0, 0), (0, 0), 9),
        ("TOPPADDING", (0, 0), (0, 0), 9), ("BOTTOMPADDING", (0, 0), (0, 0), 9),
    ]))
    return box


def _signoff_banking_boxes(header, brand, small, muted, *, compiled_label,
                           prep_name, today,
                           received_label="Received in Good Order By:"):
    """The two bordered boxes at the foot of a commercial document: a sign-off
    (compiled by, then received by) on the left and BANKING DETAILS on the right.
    Shared by the quotation and the tax invoice so they look identical."""
    def M(t):
        return Paragraph(escape(str(t)), muted)

    signoff = _signoff_column(small, muted, compiled_label=compiled_label,
                              prep_name=prep_name, today=today,
                              received_label=received_label)
    bank = header["bank"]
    title = Paragraph("<b>BANKING DETAILS</b>", small)
    if bank:
        bank_rows = [title, Spacer(1, 1.5 * mm),
                     M(f"Account Holder: {bank['account_name']}"),
                     M(f"Bank Name: {bank['bank_name']}"
                       + (f" ({bank['account_type']})" if bank['account_type'] else "")),
                     M(f"Account No: {bank['account_number']}"),
                     M(f"Branch Code: {bank['branch_code'] or '—'}")]
    else:
        bank_rows = [title, Spacer(1, 1.5 * mm),
                     M("Add a bank account in the Company Profile.")]

    footer = Table([[signoff, "", bank_rows]], colWidths=[104 * mm, 6 * mm, 76 * mm])
    footer.setStyle(TableStyle([
        ("BOX", (0, 0), (0, 0), 1, brand), ("BOX", (2, 0), (2, 0), 1, brand),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (0, 0), 9), ("RIGHTPADDING", (0, 0), (0, 0), 9),
        ("TOPPADDING", (0, 0), (0, 0), 9), ("BOTTOMPADDING", (0, 0), (0, 0), 9),
        ("LEFTPADDING", (2, 0), (2, 0), 9), ("RIGHTPADDING", (2, 0), (2, 0), 9),
        ("TOPPADDING", (2, 0), (2, 0), 9), ("BOTTOMPADDING", (2, 0), (2, 0), 9),
        ("LEFTPADDING", (1, 0), (1, 0), 0), ("RIGHTPADDING", (1, 0), (1, 0), 0),
    ]))
    return footer


def quotation_pdf_bytes(quote) -> bytes:
    company = quote.company
    buf = BytesIO()
    # Narrow margins so the content fills the page width (usable width ≈ 186mm).
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=14 * mm, bottomMargin=12 * mm,
                            leftMargin=12 * mm, rightMargin=12 * mm,
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
        ident.append(P(f"Tel {header['phone']}", muted))
    if header["mobile"]:
        ident.append(P(f"Cell {header['mobile']}", muted))
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
    head = Table([[ident, logo or ""]], colWidths=[110 * mm, 76 * mm])
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
    right.extend(_prepared_by_lines(quote, small))
    # Scope of work sits directly below Prepared by — the quotation title when no
    # separate scope was entered.
    scope = _scope_text(quote)
    if scope:
        right.append(Spacer(1, 1 * mm))
        right.append(L("Scope of Work:", scope))
    if quote.customer_reference:
        right.append(P(f"Your reference: {quote.customer_reference}", small))
    if quote.validity_date:
        right.append(P(f"Valid until: {quote.validity_date:%d/%m/%Y}", small))

    meta = Table([[left, right]], colWidths=[100 * mm, 86 * mm])
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
    tbl = Table(rows, colWidths=[14 * mm, 85 * mm, 20 * mm, 20 * mm, 23.5 * mm, 23.5 * mm],
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

    # ── Totals. On an exclusive quote VAT is not part of the quotation total
    # (it is added on the tax invoice), so the VAT line reads R0.00.
    vat_on_quote = quote.vat_amount if quote.vat_mode == "inclusive" else 0
    totals = [["SUBTOTAL", f"R{quote.subtotal:,.2f}"],
              ["VAT", f"R{vat_on_quote:,.2f}"],
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

    # Standard terms & conditions, configured once per company and inserted here.
    story += _terms_flowables(company, "quotation", small, muted)

    # ── Sign-off and banking — two separate boxes. "Compiled by" fills itself
    # in; "received in good order" is left blank for the customer to sign.
    prep_name = _initials_surname(prep.get_full_name()) if prep and prep.get_full_name() \
        else (prep.email if prep else "")
    footer = _signoff_banking_boxes(
        header, brand, small, muted, compiled_label="Quotation Compiled By:",
        prep_name=prep_name, today=quote.created_at.strftime("%d/%m/%Y"))
    story += [footer, Spacer(1, 6 * mm)]

    story.append(P(f"Please use document number ({quote.number}) for reference "
                   "when making payments.", muted))

    _build_one_page(doc, story)
    return buf.getvalue()


# ── Tax invoice and delivery note, generated from the quotation ───────────────
#
# Both reuse the quotation's letterhead helpers and its data, so nothing is
# re-keyed and every document carries the parent commercial reference.

def _letterhead(company, brand, header, coname, small, muted, title, title_text):
    """The shared top of every commercial document: company identity, big logo,
    the bold rule, and the document title in the right column."""
    esc = escape
    ident = [Paragraph(f"<b>{esc(header['display_name'])}</b>", coname)]
    for line in header["address_lines"]:
        ident.append(Paragraph(esc(line), muted))
    if header["phone"]:
        ident.append(Paragraph(f"Tel {esc(header['phone'])}", muted))
    if header["mobile"]:
        ident.append(Paragraph(f"Cell {esc(header['mobile'])}", muted))
    if header["email"]:
        ident.append(Paragraph(f"Email {esc(header['email'])}", muted))
    if header["tax_reference_no"]:
        ident.append(Paragraph(f"Tax No: {esc(header['tax_reference_no'])}", muted))
    if header["vat_no"]:
        ident.append(Paragraph(f"Vat No: {esc(header['vat_no'])}", muted))
    if header["registration_no"]:
        ident.append(Paragraph(f"Company Reg: {esc(header['registration_no'])}", muted))

    logo = _logo_flowable(header)
    head = Table([[ident, logo or ""]], colWidths=[110 * mm, 76 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    return [head, Spacer(1, 3 * mm),
            HRFlowable(width="100%", thickness=2.2, color=brand, spaceBefore=2, spaceAfter=6)]


def _doc_styles(brand):
    styles = getSampleStyleSheet()
    small = styles["BodyText"].clone("small"); small.fontSize = 9; small.leading = 11.5
    muted = small.clone("muted"); muted.textColor = MUTED
    title = styles["Heading1"].clone("dtitle")
    title.textColor = brand; title.alignment = 0; title.fontSize = 20
    coname = small.clone("coname"); coname.fontSize = 15; coname.leading = 18
    return small, muted, title, coname


def invoice_pdf_bytes(doc) -> bytes:
    """A tax invoice built from the quotation: same items and prices, VAT added
    (an exclusive quote defers VAT to here), the parent reference and the PO."""
    from apps.identity.profile import document_header

    quote = doc.quotation
    company = quote.company
    brand = _brand_color(company)
    header = document_header(company, kind="invoice")
    small, muted, title, coname = _doc_styles(brand)

    def P(t, s=small):
        return Paragraph(escape(str(t)), s)

    def L(label, value, s=small):
        return Paragraph(f"<b>{escape(label)}</b> {escape(str(value))}", s)

    buf = BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, topMargin=14 * mm, bottomMargin=12 * mm,
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            title=f"Tax invoice {doc.number}")
    story = _letterhead(company, brand, header, coname, small, muted, title, "TAX INVOICE")

    po = doc.purchase_order
    left = [L("Bill to:", quote.client_name)]
    addr = _customer_address(quote.customer)
    if addr:
        left.append(P(f"Address: {addr}", muted))
    if quote.customer_id and quote.customer.vat_no:
        left.append(P(f"VAT No: {quote.customer.vat_no}", muted))
    if quote.contact:
        left.append(P(f"Contact Person: {quote.contact.full_name}", small))
        tel = quote.contact.telephone or quote.contact.mobile
        if tel:
            left.append(P(f"Tel: {tel}", muted))
        if quote.contact.email:
            left.append(P(f"Email: {quote.contact.email}", muted))

    right = [Paragraph("TAX INVOICE", title), Spacer(1, 2 * mm),
             L("Invoice No:", doc.number),
             P(f"Date: {doc.created_at:%d/%m/%Y}", small),
             L("Quotation ref:", quote.number)]
    # The PO number the customer submitted — the invoice references their order.
    if po and po.po_number:
        right.append(L("PO Number:", po.po_number))
    right.extend(_prepared_by_lines(quote, small))
    # Scope of work sits directly below Prepared by, as on the quotation.
    scope = _scope_text(quote)
    if scope:
        right.append(Spacer(1, 1 * mm))
        right.append(L("Scope of Work:", scope))
    meta = Table([[left, right]], colWidths=[100 * mm, 86 * mm])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [meta, Spacer(1, 6 * mm)]

    cell = small.clone("cell")
    rows = [["Item", "Description", "Qty", "Unit", "Unit Price", "Amount"]]
    for ln in quote.lines.all():
        rows.append([str(ln.position), Paragraph(escape(ln.description), cell),
                     f"{ln.qty:g}", ln.unit, f"R{ln.effective_unit_price:,.2f}",
                     f"R{ln.line_total:,.2f}"])
    tbl = Table(rows, colWidths=[14 * mm, 85 * mm, 20 * mm, 20 * mm, 23.5 * mm, 23.5 * mm],
                repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), brand), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [tbl]

    # VAT is added here (deferred from an exclusive quotation).
    totals = [["SUBTOTAL", f"R{quote.net_total:,.2f}"],
              [f"VAT@{quote.vat_rate:g}%", f"R{quote.vat_amount:,.2f}"],
              ["TOTAL", f"R{quote.invoice_total:,.2f}"]]
    tot = Table(totals, colWidths=[45 * mm, 33.5 * mm], hAlign="RIGHT")
    tot.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9.5),
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, brand), ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("TEXTCOLOR", (0, -1), (-1, -1), brand)]))
    story += [tot, Spacer(1, 8 * mm)]

    # Standard invoice terms, configured once per company and inserted here.
    story += _terms_flowables(company, "invoice", small, muted)

    # Same boxed sign-off + banking as the quotation, worded for an invoice.
    prep = quote.prepared_by
    prep_name = _initials_surname(prep.get_full_name()) if prep and prep.get_full_name() \
        else (prep.email if prep else "")
    footer = _signoff_banking_boxes(
        header, brand, small, muted, compiled_label="Invoice Compiled By:",
        prep_name=prep_name, today=doc.created_at.strftime("%d/%m/%Y"),
        received_label="Received By:")
    story += [footer, Spacer(1, 6 * mm),
              P(f"Please use invoice number ({doc.number}) as the payment "
                "reference. E&amp;OE.", muted)]
    _build_one_page(pdf, story)
    return buf.getvalue()


def delivery_note_pdf_bytes(doc) -> bytes:
    """A delivery note built from the quotation — operational quantities, never
    prices. Ordered comes from the quotation; Delivered/Outstanding are filled
    in on delivery."""
    from apps.identity.profile import document_header

    quote = doc.quotation
    company = quote.company
    brand = _brand_color(company)
    header = document_header(company, kind="report")
    small, muted, title, coname = _doc_styles(brand)

    def P(t, s=small):
        return Paragraph(escape(str(t)), s)

    def L(label, value, s=small):
        return Paragraph(f"<b>{escape(label)}</b> {escape(str(value))}", s)

    buf = BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, topMargin=14 * mm, bottomMargin=12 * mm,
                            leftMargin=12 * mm, rightMargin=12 * mm,
                            title=f"Delivery note {doc.number}")
    story = _letterhead(company, brand, header, coname, small, muted, title, "DELIVERY NOTE")

    po = doc.purchase_order
    ship_to = doc.delivery_address or (
        str(quote.customer_site) if quote.customer_site_id else quote.site)
    left = [L("Client :", quote.client_name)]
    if ship_to:
        left.append(P(f"Deliver to: {ship_to}", muted))
    if quote.contact:
        left.append(P(f"Contact Person: {quote.contact.full_name}", small))
        tel = quote.contact.telephone or quote.contact.mobile
        if tel:
            left.append(P(f"Tel: {tel}", muted))
        if quote.contact.email:
            left.append(P(f"Email: {quote.contact.email}", muted))

    right = [Paragraph("DELIVERY NOTE", title), Spacer(1, 2 * mm),
             L("Delivery note:", doc.number),
             P(f"Date: {doc.created_at:%d/%m/%Y}", small),
             L("Quotation ref:", quote.number)]
    if po:
        right.append(L("PO Number:", po.po_number))
    if doc.delivery_date:
        right.append(L("Delivery date:", f"{doc.delivery_date:%d/%m/%Y}"))
    if doc.driver:
        right.append(L("Driver:", doc.driver))
    right.extend(_prepared_by_lines(quote, small))
    # Scope of work sits directly below Prepared by, as on the other documents.
    scope = _scope_text(quote)
    if scope:
        right.append(Spacer(1, 1 * mm))
        right.append(L("Scope of Work:", scope))
    meta = Table([[left, right]], colWidths=[100 * mm, 86 * mm])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [meta, Spacer(1, 6 * mm)]

    # Ordered comes from the quotation. Delivered defaults to the full ordered
    # quantity and Outstanding to zero (Ordered − Delivered) — the normal case,
    # a complete delivery; the driver strikes through and adjusts for a partial.
    cell = small.clone("cell")
    rows = [["Item", "Description", "Ordered", "Delivered", "Outstanding"]]
    for ln in quote.lines.all():
        rows.append([str(ln.position), Paragraph(escape(ln.description), cell),
                     f"{ln.qty:g}", f"{ln.qty:g}", "0"])
    tbl = Table(rows, colWidths=[14 * mm, 98 * mm, 24 * mm, 24 * mm, 26 * mm],
                repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), brand), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"), ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story += [tbl, Spacer(1, 8 * mm)]

    if doc.delivery_notes:
        story += [L("Delivery notes:", doc.delivery_notes, muted), Spacer(1, 4 * mm)]

    # Standard delivery terms, then a sign-off box only — a delivery note carries
    # no banking details (nothing is paid against it).
    story += _terms_flowables(company, "delivery", small, muted)
    prep = quote.prepared_by
    prep_name = _initials_surname(prep.get_full_name()) if prep and prep.get_full_name() \
        else (prep.email if prep else "")
    footer = _signoff_box(
        brand, small, muted, compiled_label="Delivery Compiled By:",
        prep_name=prep_name, today=doc.created_at.strftime("%d/%m/%Y"),
        received_label="Received in Good Order By:", width=104 * mm)
    story += [footer]
    _build_one_page(pdf, story)
    return buf.getvalue()
