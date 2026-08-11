"""Emailing quotations, tax invoices and delivery notes to customers.

The document email is user-initiated ("Send by email"), but it goes through the
same platform service as everything else: branded shell, logged history,
retriable delivery. The PDF is attached by SPEC — the worker regenerates it from
the live record at delivery — and the recipient is suggested from the CRM
(document routing by responsibility), so a quotation defaults to the contact who
actually approves quotations, not just "the customer".
"""

from apps.notifications.attachments import register_attachment_builder
from apps.notifications.models import EmailCategory
from apps.notifications.service import send_email


# ── Attachment builders (registered at app startup) ───────────────────────────

def _quotation_pdf(quote_id):
    from .models import Quotation
    from .pdf import quotation_pdf_bytes
    quote = Quotation.objects.filter(id=quote_id).first()
    return quotation_pdf_bytes(quote) if quote else b""


def _commercial_pdf(doc_id):
    from .models import CommercialDocument
    from .pdf import delivery_note_pdf_bytes, invoice_pdf_bytes
    doc = CommercialDocument.objects.filter(id=doc_id).first()
    if not doc:
        return b""
    return (invoice_pdf_bytes(doc) if doc.kind == "invoice"
            else delivery_note_pdf_bytes(doc))


def register_builders():
    """Called from QuotesConfig.ready — makes these document kinds attachable."""
    register_attachment_builder("quotation_pdf", _quotation_pdf)
    register_attachment_builder("commercial_pdf", _commercial_pdf)


# ── Recipient routing ─────────────────────────────────────────────────────────

def suggested_recipient(customer, routing_kind) -> str:
    """The email a document should default to, using the CRM responsibility
    routing (the person who approves quotations / receives invoices), then the
    customer's general address."""
    if customer is None:
        return ""
    try:
        from apps.customers.services import route_document
        routed = route_document(customer, routing_kind)
        if routed["to"]:
            return routed["to"][0].reach
        return routed.get("customer_email", "") or ""
    except Exception:
        return getattr(customer, "email", "") or ""


# ── Send actions ──────────────────────────────────────────────────────────────

def send_quotation(quote, sent_by, *, to="", message="") -> object:
    """Email a quotation with its PDF attached. Records the send on the
    quotation (EmailLog entity link)."""
    to = (to or "").strip() or suggested_recipient(quote.customer, "quotation")
    if not to:
        from apps.identity.services import MemberError
        raise MemberError("No recipient — add a customer contact or type an address.")
    company = quote.company
    name = f"Quotation {quote.number}.pdf"
    return send_email(
        to=to, subject=f"Quotation {quote.number} from {company.name}",
        template="document", company=company, sent_by=sent_by,
        category=EmailCategory.DOCUMENT, related=quote,
        attachment_specs=[{"kind": "quotation_pdf", "id": str(quote.id), "name": name}],
        context={
            "heading": f"Quotation {quote.number}",
            "doc_label": "quotation", "reference": quote.number,
            "message": message.strip(),
            "body": (f"Please find quotation {quote.number} attached"
                     + (f" for {quote.title}." if quote.title else ".")),
        })


def send_commercial_document(doc, sent_by, *, to="", message="") -> object:
    """Email a tax invoice or delivery note with its PDF attached."""
    quote = doc.quotation
    is_invoice = doc.kind == "invoice"
    routing = "invoice" if is_invoice else "progress_report"
    to = (to or "").strip() or suggested_recipient(quote.customer, routing)
    if not to:
        from apps.identity.services import MemberError
        raise MemberError("No recipient — add a customer contact or type an address.")
    company = quote.company
    label = "Tax invoice" if is_invoice else "Delivery note"
    name = f"{label} {doc.number}.pdf"
    return send_email(
        to=to, subject=f"{label} {doc.number} from {company.name}",
        template="document", company=company, sent_by=sent_by,
        category=EmailCategory.DOCUMENT, related=doc,
        attachment_specs=[{"kind": "commercial_pdf", "id": str(doc.id), "name": name}],
        context={
            "heading": f"{label} {doc.number}",
            "doc_label": label.lower(), "reference": doc.number,
            "message": message.strip(),
            "body": f"Please find {label.lower()} {doc.number} attached.",
        })


def send_history(entity_type, entity_id):
    """Every email sent for a given document, newest first."""
    from apps.notifications.models import EmailLog
    return EmailLog.objects.filter(entity_type=entity_type, entity_id=entity_id)
