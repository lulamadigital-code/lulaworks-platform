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


def extract_text(pdf_path) -> str:
    with pdfplumber.open(pdf_path) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def extract_rfq(pdf_path) -> Extraction:
    result = Extraction()
    text = extract_text(pdf_path)
    result.text = text
    if not text.strip():
        result.warnings.append("No text extracted — scanned image? OCR/AI fallback needed.")
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
