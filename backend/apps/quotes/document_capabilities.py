"""Document-type capabilities — the single source of truth for what each document
type is *allowed* to contain, independent of any visual template.

The rule this enforces:

    DOCUMENT TYPE controls business meaning.   TEMPLATE controls appearance.

A template only chooses layout, colour and section order. It can never make a
document semantically wrong — e.g. a QUOTATION can never show "Received in good
order by" (a delivery-note concept), and a DELIVERY NOTE can never show prices.
Both renderers (ReportLab and HTML) consult this module, so a stray template
config or an AI-imported design cannot leak the wrong content.
"""

from __future__ import annotations

QUOTATION = "quotation"
INVOICE = "invoice"
DELIVERY = "delivery"

#: Sign-off treatments — what the signature block *means* for each document type.
SIGNOFF_COMPILED = "compiled"       # quotation: supplier "compiled by" only
SIGNOFF_NONE = "none"               # invoice: no acknowledgement / sign-off box
SIGNOFF_DELIVERY = "delivery"       # delivery note: received-in-good-order + delivered-by

#: How the item table reads.
ITEMS_PRICED = "priced"             # quotation / invoice: qty · unit · price · amount
ITEMS_DELIVERY = "delivery"         # delivery note: ordered · delivered · outstanding · unit

#: The HTML-engine section keys (mirror models.TEMPLATE_SECTION_KEYS).
_ALL_SECTIONS = ("letterhead", "document_meta", "parties", "scope", "items",
                 "totals", "banking", "terms", "signature", "footer")

#: Per-type capability profile. `allowed_sections` is authoritative: a section not
#: listed is NEVER rendered for that type, whatever a template says.
_CAP = {
    QUOTATION: {
        "title": "QUOTATION",
        "terms_kind": "quotation",
        "allow_prices": True,
        "item_mode": ITEMS_PRICED,
        "signoff": SIGNOFF_COMPILED,
        "allowed_sections": {"letterhead", "document_meta", "parties", "scope",
                             "items", "totals", "banking", "terms", "signature", "footer"},
    },
    INVOICE: {
        "title": "TAX INVOICE",
        "terms_kind": "invoice",
        "allow_prices": True,
        "item_mode": ITEMS_PRICED,
        # A tax invoice carries a supplier "compiled by" sign-off (who issued it) —
        # but NEVER a delivery acknowledgement ("received in good order").
        "signoff": SIGNOFF_COMPILED,
        "allowed_sections": {"letterhead", "document_meta", "parties", "scope",
                             "items", "totals", "banking", "terms", "signature", "footer"},
    },
    DELIVERY: {
        "title": "DELIVERY NOTE",
        "terms_kind": "delivery",
        "allow_prices": False,
        "item_mode": ITEMS_DELIVERY,
        "signoff": SIGNOFF_DELIVERY,
        # No totals and no banking — nothing is priced or paid against a delivery
        # note. Its item table carries quantities (ordered / delivered / outstanding).
        "allowed_sections": {"letterhead", "document_meta", "parties", "scope",
                             "items", "terms", "signature", "footer"},
    },
}

#: Content that must NEVER appear on each type — checked against rendered output in
#: tests/QA as a backstop to the structural rules above. Lower-cased substrings.
_FORBIDDEN_TEXT = {
    QUOTATION: ("received in good order", "goods received", "proof of delivery",
                "delivered by", "quantity delivered", "quantity outstanding"),
    INVOICE: ("received in good order", "goods received", "proof of delivery",
              "delivered by", "quantity outstanding"),
    DELIVERY: ("unit price", "total due", "subtotal", "amount due"),
}


#: Job types whose quotation price column reads "Rate" (time / service based)
#: rather than "Unit price" (goods). A Labour-Hire or Plant-Hire quote quotes a
#: rate; a Supply quote quotes a unit price. Keeps the item table honest to the
#: kind of work without inventing empty columns.
_RATE_JOB_TYPES = {
    "labour_hire", "plant_hire", "mechanical_repair", "electrical_repair",
    "maintenance", "project_management", "inspection", "emergency",
    "preventative", "transport",
}


def price_label(job_type_key) -> str:
    """The column heading for the unit-price column, chosen by the quotation's job
    type: "Rate" for service/time work, "Unit price" for goods."""
    return "Rate" if (job_type_key or "") in _RATE_JOB_TYPES else "Unit price"


class DocumentValidationError(Exception):
    """A document cannot be rendered because it would be semantically wrong or is
    missing required information. The message is safe to show the user."""


def _cap(doc_type: str) -> dict:
    if doc_type not in _CAP:
        raise DocumentValidationError(f"Unknown document type “{doc_type}”.")
    return _CAP[doc_type]


def title(doc_type: str) -> str:
    return _cap(doc_type)["title"]


def terms_kind(doc_type: str) -> str:
    return _cap(doc_type)["terms_kind"]


def allows_prices(doc_type: str) -> bool:
    return _cap(doc_type)["allow_prices"]


def item_mode(doc_type: str) -> str:
    return _cap(doc_type)["item_mode"]


def signoff_mode(doc_type: str) -> str:
    return _cap(doc_type)["signoff"]


def allowed_sections(doc_type: str) -> set:
    return set(_cap(doc_type)["allowed_sections"])


def is_section_allowed(doc_type: str, key: str) -> bool:
    return key in _cap(doc_type)["allowed_sections"]


def filter_sections(doc_type: str, keys) -> list:
    """Keep only the sections this document type is allowed to show, in the given
    order. This is the gate that stops a template from rendering a forbidden block."""
    allowed = _cap(doc_type)["allowed_sections"]
    return [k for k in keys if k in allowed]


def forbidden_text(doc_type: str) -> tuple:
    return _FORBIDDEN_TEXT.get(doc_type, ())


def validate_render_context(context: dict, doc_type: str) -> list:
    """Return a list of human-readable problems that must block PDF generation —
    missing required information, or prohibited content leaking into the data.
    Empty list = safe to render. Kept conservative so every valid existing
    document passes; the structural section rules do the heavy lifting."""
    cap = _cap(doc_type)
    errors = []
    company = (context.get("company") or {})
    document = (context.get("document") or {})
    customer = (context.get("customer") or {})

    if not (company.get("name") or "").strip():
        errors.append("The company name is missing — set it in the Company Profile.")
    if not (document.get("reference") or "").strip():
        errors.append("The document has no reference number.")
    if not (customer.get("name") or "").strip():
        errors.append("The customer is missing.")

    # A delivery note must never carry prices — defensive check on the data itself,
    # not just the layout, so a mis-built context can't slip a price through.
    if not cap["allow_prices"]:
        for it in (context.get("items") or []):
            if (it.get("unit_price") or it.get("amount")):
                errors.append("A delivery note cannot show prices.")
                break
    return errors


def assert_renderable(context: dict, doc_type: str) -> None:
    """Raise DocumentValidationError if the document would be misleading. Call this
    immediately before generating a PDF."""
    errors = validate_render_context(context, doc_type)
    if errors:
        raise DocumentValidationError(" ".join(errors))
