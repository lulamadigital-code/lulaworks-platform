"""Deterministic RFQ/PO extraction (RFQ_INTELLIGENCE §0, §4-5).

Ported and validated against real Sibanye/Western Platinum Coupa documents
(PO 5502442801): "PO NUMBER", "DATE yyyy/mm/dd", "CONTACT"/"Attn:" labels, and
SA number formatting (comma decimal, space thousands — "29 160,00").

This is the deterministic-first layer: exact, free, no AI credits. The AI
extractor (Phase-2 follow-on) is the fallback for variable/scanned layouts,
behind the same interface. Every field carries a confidence score.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pdfplumber


@dataclass
class ExtractedValue:
    value: str
    confidence: float
    method: str = "deterministic"
    source_text: str = ""


@dataclass
class ExtractedLine:
    description: str
    qty: Decimal
    unit: str
    unit_price: Decimal | None = None


@dataclass
class Extraction:
    fields: dict = field(default_factory=dict)   # key -> ExtractedValue
    lines: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    text: str = ""


PO_NUMBER_RE = re.compile(
    r"(?:PO\s*NUMBER|Purchase Order\s*#?|PO\s*(?:No\.?|#))\s*:?\s*(\d{6,12})", re.IGNORECASE
)
DATE_RE = re.compile(
    r"(?:Order\s+Date|DATE)\s*:?\s*(\d{4}[/-]\d{2}[/-]\d{2}|\d{2}[/-]\d{2}[/-]\d{4})",
    re.IGNORECASE,
)
CONTACT_RE = re.compile(r"(?:CONTACT|Requester|Attn)\s*:?\s*([A-Za-z][A-Za-z .'-]{2,60})")
_MONEY = r"R?\s?[\d][\d\s]*[.,]\d{2}"
LINE_RE = re.compile(
    rf"^(\d{{1,3}})\s+(.+?)\s+(\d[\d\s]*(?:[.,]\d+)?)\s+([A-Za-z][A-Za-z/]{{0,9}})"
    rf"\s+({_MONEY})\s+({_MONEY})$"
)
LINE_RE_NO_TOTAL = re.compile(
    rf"^(\d{{1,3}})\s+(.+?)\s+(\d[\d\s]*(?:[.,]\d+)?)\s+([A-Za-z][A-Za-z/]{{0,9}})"
    rf"(?:\s+({_MONEY}))?$"
)


def to_decimal(raw) -> Decimal:
    """Parse SA or US numbers. SA: '29 160,00'. US: '29,160.00'."""
    if raw is None:
        return Decimal("0")
    s = str(raw).replace("R", "").strip().replace(" ", "")
    if not s:
        return Decimal("0")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".") if re.search(r",\d{1,2}$", s) else s.replace(",", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")


def _pdfplumber_text(pdf_source) -> str:
    with pdfplumber.open(pdf_source) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def _ocr_text(pdf_bytes: bytes) -> str:
    """OCR a scanned/image PDF (RFQ_INTELLIGENCE §3, decision 15: Tesseract
    first). Lazy-imported so it's not a hard dependency — returns '' if the
    OCR toolchain (pytesseract + pdf2image + tesseract binary) is unavailable."""
    try:
        import pdf2image
        import pytesseract
    except ImportError:
        return ""
    try:
        images = pdf2image.convert_from_bytes(pdf_bytes, dpi=200)
    except Exception:
        return ""
    return "\n".join(pytesseract.image_to_string(img) for img in images)


def extract_text(pdf_source) -> str:
    """Text layer first (free, exact); OCR fallback for scanned/image PDFs.

    Resilient to a non-PDF or corrupt upload: a pdfplumber failure is treated as
    'no text layer' and falls through to OCR / empty (the caller warns), so a bad
    file never 500s the upload."""
    try:
        text = _pdfplumber_text(pdf_source)
    except Exception:
        text = ""
    if text.strip():
        return text
    # No text layer → scanned. Re-read bytes for OCR.
    if hasattr(pdf_source, "seek"):
        pdf_source.seek(0)
        data = pdf_source.read()
    else:
        with open(pdf_source, "rb") as fh:
            data = fh.read()
    return _ocr_text(data)


def extract_rfq(pdf_path) -> Extraction:
    """Extract from a FILE (PDF text layer, else OCR)."""
    text = extract_text(pdf_path)
    if not text.strip():
        result = Extraction()
        result.warnings.append("No text extracted — scanned image? OCR/AI fallback needed.")
        return result
    return parse_rfq_text(text)


def parse_rfq_text(text: str) -> Extraction:
    """Parse RFQ text that is ALREADY in hand — a PDF's text layer, an OCR pass,
    or an RFQ someone pasted in from an email or WhatsApp message.

    Only the structured table is turned into line items here. Loose prose ("4 x
    mechanical seals") is deliberately NOT auto-converted: it is offered as a
    reviewable suggestion instead (see `suggestions.py`), because inferring line
    items from a sentence is a guess and guesses need a human tick.
    """
    result = Extraction()
    result.text = text
    if not text.strip():
        result.warnings.append("Nothing to parse — the text was empty.")
        return result

    if m := PO_NUMBER_RE.search(text):
        result.fields["po_number"] = ExtractedValue(m.group(1), 1.0, source_text=m.group(0))
    else:
        result.warnings.append("PO/reference number not found — enter manually.")
    if m := DATE_RE.search(text):
        result.fields["order_date"] = ExtractedValue(m.group(1), 0.95, source_text=m.group(0))
    if m := CONTACT_RE.search(text):
        result.fields["contact"] = ExtractedValue(m.group(1).strip(), 0.8, source_text=m.group(0))
    if m := re.search(r"Ship\s*To\b.*?\n(.+)", text, re.IGNORECASE):
        result.fields["ship_to"] = ExtractedValue(m.group(1).strip(), 0.7, source_text=m.group(0))

    in_table = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if re.match(r"^Line\s+Description", line, re.IGNORECASE):
            in_table = True
            continue
        if not in_table:
            continue
        if re.match(r"^(Sub\s*)?Total\b", line, re.IGNORECASE):
            break
        m = LINE_RE.match(line)
        if m:
            _no, desc, qty, unit, price, _total = m.groups()
        else:
            m = LINE_RE_NO_TOTAL.match(line)
            if not m:
                continue
            _no, desc, qty, unit, price = m.groups()
        result.lines.append(
            ExtractedLine(
                description=desc.strip(), qty=to_decimal(qty), unit=unit,
                unit_price=to_decimal(price) if price else None,
            )
        )

    if not result.lines:
        result.warnings.append("No line items recognised — review and add manually.")
    return result


# ── Informal RFQs: "please quote 4 x mechanical seals" ────────────────────────
#
# An RFQ often arrives as an email or a WhatsApp message rather than a formal
# purchase order. These patterns pull a quantity/unit/description out of a line
# of prose. Everything found here is a SUGGESTION for a human to confirm — the
# rigid table parser above is the only path that creates line items directly.

_UNIT_WORDS = (
    r"each|ea|off|no|nr|pcs|pc|piece|pieces|unit|units|set|sets|pair|pairs|"
    r"m|mm|metre|metres|meter|meters|km|kg|g|t|ton|tons|l|lt|litre|litres|"
    r"box|boxes|roll|rolls|drum|drums|bag|bags|length|lengths|hour|hours|hr|hrs|day|days"
)

#: "4 x mechanical seal", "10off gaskets", "2 × pump", "5 sets of bearings"
_QTY_FIRST = re.compile(
    rf"^(?P<qty>\d+(?:[.,]\d+)?)\s*(?:(?P<unit>{_UNIT_WORDS})\b)?\s*"
    rf"(?:x|×|of|off)?\s*(?P<desc>[A-Za-z][^\n]{{2,}})$",
    re.IGNORECASE,
)
#: "mechanical seal - 4", "gaskets: 10 each", "pump x 2"
_QTY_LAST = re.compile(
    rf"^(?P<desc>[A-Za-z][^\n]{{2,}}?)\s*(?:[-–—:]|x|×)\s*"
    rf"(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>{_UNIT_WORDS})?\.?$",
    re.IGNORECASE,
)
#: "qty 3 valve", "quantity: 6 bolts"
_QTY_LABEL = re.compile(
    rf"^(?:qty|quantity)\s*:?\s*(?P<qty>\d+(?:[.,]\d+)?)\s*"
    rf"(?P<unit>{_UNIT_WORDS})?\s*(?:x|×|of)?\s*(?P<desc>[A-Za-z][^\n]{{2,}})$",
    re.IGNORECASE,
)

#: Lines that are conversation, not items.
_NOT_AN_ITEM = re.compile(
    r"^(hi|hello|hey|dear|good\s+(morning|afternoon|day)|thanks|thank you|regards|"
    r"kind regards|best regards|please|kindly|could you|can you|we (need|require|would)|"
    r"quote|quotation|rfq|attention|attn|from|to|subject|sent|date|cc|bcc|"
    r"urgent|asap|delivery|deliver|note|nb|ps)\b",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^\s*(?:[-*•·–—]|\(?\d{1,2}[.)])\s*")


def _clean_description(text: str) -> str:
    text = text.strip(" \t-–—:;,.")
    text = re.sub(r"\s{2,}", " ", text)
    return text


def parse_loose_lines(text: str) -> list[ExtractedLine]:
    """Best-effort line items from informal text. Tuned to under-report rather
    than over-report: a missed item costs one manual entry, an invented item
    could end up priced into a quotation."""
    found: list[ExtractedLine] = []
    seen: set[str] = set()

    for raw in text.splitlines():
        line = _BULLET.sub("", raw.strip())
        if not line or len(line) > 160:
            continue
        if _NOT_AN_ITEM.match(line) or line.endswith(":") or line.endswith("?"):
            continue
        if not re.search(r"[A-Za-z]{3,}", line):
            continue

        for pattern in (_QTY_LABEL, _QTY_FIRST, _QTY_LAST):
            m = pattern.match(line)
            if not m:
                continue
            desc = _clean_description(m.group("desc"))
            # A description that is only a unit word is a parse artefact.
            if len(desc) < 3 or re.fullmatch(_UNIT_WORDS, desc, re.IGNORECASE):
                break
            key = desc.lower()
            if key in seen:
                break
            seen.add(key)
            found.append(ExtractedLine(
                description=desc,
                qty=to_decimal(m.group("qty")) or Decimal("1"),
                unit=(m.group("unit") or "each").lower(),
                unit_price=None,
            ))
            break
    return found
