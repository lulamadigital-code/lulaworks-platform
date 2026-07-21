"""Quotation PDF — the customer-facing document (BUSINESS_WORKFLOW: quote sent to
the client). Pure-Python ReportLab (no system libraries), so it renders inside the
slim container. Selling price only — never cost or margin (Golden Rule at the
document boundary)."""

from io import BytesIO

from django.contrib.staticfiles import finders
from reportlab.lib import colors, utils
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

BRAND = colors.HexColor("#0E6E6E")


def _logo_flowable(max_h=16 * mm):
    """The real logo image if one has been dropped into static (web/logo.png);
    else None (the caller falls back to the company name as text)."""
    path = finders.find("web/logo.png")
    if not path:
        return None
    reader = utils.ImageReader(path)
    iw, ih = reader.getSize()
    return Image(path, width=max_h * iw / ih, height=max_h)


def quotation_pdf_bytes(quote) -> bytes:
    company = quote.company
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=18 * mm, bottomMargin=18 * mm,
                            leftMargin=18 * mm, rightMargin=18 * mm,
                            title=f"Quotation {quote.number}")
    styles = getSampleStyleSheet()
    h = styles["Heading1"]
    h.textColor = BRAND
    small = styles["BodyText"]
    muted = styles["BodyText"].clone("muted")
    muted.textColor = colors.HexColor("#5b6b6a")

    # Every company fact on this page comes from the Company Profile — the one
    # place it is entered. Adding a field to the letterhead is a change there,
    # not here, and never a re-typing job for the user.
    from apps.identity.profile import document_header
    header = document_header(company, kind="invoice")

    logo = _logo_flowable()
    # With no logo the name already stands in for it on the left, so repeating
    # it at the top of the identity block prints the company name twice.
    identity = [Paragraph(f"<b>{header['display_name']}</b>", small)] if logo else []
    if header["registration_no"]:
        identity.append(Paragraph(f"Reg. {header['registration_no']}", muted))
    if header["vat_no"]:
        identity.append(Paragraph(f"VAT {header['vat_no']}", muted))
    for line in header["address_lines"]:
        identity.append(Paragraph(line, muted))
    reach = " · ".join(x for x in (header["phone"], header["email"],
                                   header["website"]) if x)
    if reach:
        identity.append(Paragraph(reach, muted))

    head_tbl = Table(
        [[logo if logo is not None else Paragraph(header["display_name"], h), identity]],
        colWidths=[80 * mm, 94 * mm])
    head_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
    ]))

    story = [
        head_tbl,
        Spacer(1, 4 * mm),
        Paragraph("QUOTATION", styles["Heading2"]),
        Spacer(1, 6 * mm),
    ]

    # Their vendor code and their own reference go in the meta block: it is how
    # the person receiving this matches it to what they asked for, and how their
    # accounts payable system finds us. A quotation missing them can sit
    # unmatched for weeks.
    meta = [
        [Paragraph("<b>Quotation</b>", small), quote.number,
         Paragraph("<b>Date</b>", small), quote.created_at.strftime("%Y-%m-%d")],
        [Paragraph("<b>Client</b>", small), quote.client_name,
         Paragraph("<b>Valid until</b>", small),
         quote.validity_date.strftime("%Y-%m-%d") if quote.validity_date else "—"],
        [Paragraph("<b>Project</b>", small), quote.title or "—",
         Paragraph("<b>Site</b>", small),
         str(quote.customer_site) if quote.customer_site_id else (quote.site or "—")],
    ]
    if quote.vendor_number or quote.customer_reference:
        meta.append([
            Paragraph("<b>Vendor no.</b>", small), quote.vendor_number or "—",
            Paragraph("<b>Your ref.</b>", small), quote.customer_reference or "—",
        ])
    meta_tbl = Table(meta, colWidths=[26 * mm, 62 * mm, 26 * mm, 60 * mm])
    meta_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
    ]))
    story += [meta_tbl, Spacer(1, 8 * mm)]

    # Description must wrap within its column → wrap it in a Paragraph (plain
    # strings overflow reportlab table cells).
    cell = small.clone("cell")
    cell.fontSize = 9
    cell.leading = 11
    rows = [["#", "Description", "Qty", "Unit", "Unit price", "Line total"]]
    for ln in quote.lines.all():
        rows.append([str(ln.position), Paragraph(ln.description, cell), f"{ln.qty:g}",
                     ln.unit, f"R {ln.unit_price:,.2f}", f"R {ln.line_total:,.2f}"])
    if len(rows) == 1:
        rows.append(["", Paragraph("No line items.", cell), "", "", "", ""])

    tbl = Table(rows, colWidths=[10 * mm, 78 * mm, 16 * mm, 18 * mm, 26 * mm, 26 * mm],
                repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f6f6")]),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#dfe6e6")),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story += [tbl, Spacer(1, 6 * mm)]

    totals = [
        ["Subtotal", f"R {quote.subtotal:,.2f}"],
        [f"VAT ({quote.vat_rate:g}%)", f"R {quote.vat_amount:,.2f}"],
        ["Total", f"R {quote.total:,.2f}"],
    ]
    tot_tbl = Table(totals, colWidths=[52 * mm, 34 * mm], hAlign="RIGHT")
    tot_tbl.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("LINEABOVE", (0, -1), (-1, -1), 0.8, BRAND),
        ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story += [tot_tbl, Spacer(1, 10 * mm)]

    if quote.notes:
        story += [Paragraph("<b>Notes</b>", small), Paragraph(quote.notes, muted),
                  Spacer(1, 6 * mm)]

    # Banking — a quotation a client cannot pay from is an unfinished document.
    bank = header["bank"]
    if bank:
        rows = [
            ["Bank", bank["bank_name"], "Account name", bank["account_name"]],
            ["Account", bank["account_number"], "Branch code", bank["branch_code"] or "—"],
        ]
        if bank["swift_code"]:
            rows.append(["SWIFT", bank["swift_code"], "Currency", bank["currency"]])
        bank_tbl = Table(rows, colWidths=[24 * mm, 58 * mm, 26 * mm, 58 * mm])
        bank_tbl.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 8.5),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#5b6b6a")),
            ("TEXTCOLOR", (2, 0), (2, -1), colors.HexColor("#5b6b6a")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story += [Paragraph("<b>Banking details</b>", small), bank_tbl,
                  Spacer(1, 6 * mm)]
    story.append(Paragraph(
        "Prices exclude items not listed above. E&amp;OE. "
        "Generated by LulaWorks.", muted))

    doc.build(story)
    return buf.getvalue()
