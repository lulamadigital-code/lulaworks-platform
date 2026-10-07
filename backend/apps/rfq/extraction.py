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


# ── Columnar priced tables (local, deterministic — no AI) ─────────────────────
#
# Most supplier invoices and quotes are TABLES, not one-money-per-line prose. The
# single-line regex (_PRICED_LINE in historical_import) needs a trailing amount
# WITH cents on one physical line, so columnar / OCR'd / no-cents layouts slip
# past it and price history stays empty. These parsers read the table grid
# instead — first from a PDF's real table structure (pdfplumber), and, as a
# text-only fallback that also works on OCR output and spreadsheet rows, from
# cells split on tabs / runs of 2+ spaces. Conservative by design: a line must
# look like a priced item (a text label plus either two money columns — unit
# price and amount — or a single money column that carries cents), so a stray
# number is never mistaken for a price. Never invents a price.

#: A cell that is a money amount: optional R/ZAR, digits with space/comma
#: grouping, optional 1-2 decimal places.
_CELL_MONEY = re.compile(r"^(?:R|ZAR)?\s*\d[\d  ]*(?:[.,]\d{1,2})?$", re.IGNORECASE)
#: A bare quantity cell (a small count, not a money amount).
_CELL_QTY = re.compile(r"^\d{1,4}(?:[.,]\d{1,3})?$")
#: A short unit-of-measure cell (ea, m, kg, hrs …).
_CELL_UNIT = re.compile(rf"^(?:{_UNIT_WORDS})$", re.IGNORECASE)
#: A combined "quantity unit" cell written as one token, e.g. "5 m", "2 each" —
#: a qty column, NOT part of the description.
_CELL_QTY_UNIT = re.compile(rf"^(\d+(?:[.,]\d+)?)\s+({_UNIT_WORDS})$", re.IGNORECASE)
#: Header / totals / tax rows that are not line items.
_TABLE_SKIP = re.compile(
    r"^(line|item|description|qty|quantity|unit|rate|price|amount|total|sub[\s-]*total|"
    r"vat|tax|discount|balance|deposit|nett?|excl|incl|grand\s+total|r\s*$)",
    re.IGNORECASE)


def _is_money_cell(cell: str) -> bool:
    """A cell is money if it looks like an amount AND is unambiguous: it carries
    cents, a currency mark, or a thousands grouping — so a bare count ('10') is
    read as a quantity, never a price."""
    c = cell.strip()
    if not _CELL_MONEY.match(c):
        return False
    return bool(re.search(r"[.,]\d{1,2}$", c)       # has cents
                or re.match(r"^(?:R|ZAR)", c, re.IGNORECASE)  # currency mark
                or re.search(r"\d[\s ,]\d{3}\b", c))     # thousands grouping


def _split_cells(line: str) -> list[str]:
    """Columns from a text row: split on tabs or runs of 2+ spaces."""
    return [c.strip() for c in re.split(r"\t|\s{2,}", line.strip()) if c.strip()]


def _line_from_cells(cells: list[str]) -> ExtractedLine | None:
    """Turn one table row's cells into a priced line, or None if it isn't one."""
    if len(cells) < 2:
        return None
    money_idx = [i for i, c in enumerate(cells) if _is_money_cell(c)]
    if not money_idx:
        return None
    monies = [to_decimal(cells[i]) for i in money_idx]
    monies = [m for m in monies if m and m > 0]
    if not monies:
        return None
    # A single money column is accepted only when it carries cents (a deliberate
    # amount, not a rounded count); two+ columns (unit price + amount) are a
    # strong priced-row signal on their own.
    if len(monies) == 1 and not re.search(r"[.,]\d{1,2}$", cells[money_idx[0]].strip()):
        return None
    # Classify the non-money cells: a quantity ("10" or a "5 m" qty+unit token),
    # a standalone unit, else description text — wherever they sit, since a
    # quantity column often leads the row before the description.
    qty: Decimal | None = None
    unit: str | None = None
    desc_cells = []
    for i, c in enumerate(cells):
        if i in money_idx:
            continue
        mqu = _CELL_QTY_UNIT.match(c)
        if mqu:
            qty = to_decimal(mqu.group(1)) or qty
            unit = (mqu.group(2) or "").lower() or unit
            continue
        if _CELL_QTY.match(c):
            qty = to_decimal(c) or qty
            continue
        if _CELL_UNIT.match(c):
            unit = c.lower()
            continue
        if re.search(r"[A-Za-z]", c):
            desc_cells.append(c)
    qty = qty or Decimal("1")
    unit = unit or "each"
    desc = _clean_description(" ".join(desc_cells))
    if len(desc) < 3 or _TABLE_SKIP.match(desc):
        return None
    # Unit price: with two+ money columns it's the one that reconciles to the
    # amount (unit_price × qty ≈ total); otherwise the smaller (price ≤ amount),
    # or the single amount.
    if len(monies) >= 2:
        total = max(monies)
        unit_price = min(monies)
        if qty and qty > 0:
            best = min(monies, key=lambda m: abs(m * qty - total))
            if total and abs(best * qty - total) <= (total * Decimal("0.02")):
                unit_price = best
    else:
        unit_price = monies[0]
    return ExtractedLine(description=desc, qty=qty, unit=unit, unit_price=unit_price)


def parse_table_lines(text: str) -> list[ExtractedLine]:
    """Priced line items from columnar text (a PDF's flat text, OCR output, or a
    spreadsheet's tab-delimited rows). Deterministic and conservative — see the
    section note. De-duplicated by description."""
    out: list[ExtractedLine] = []
    seen: set[str] = set()
    for raw in (text or "").splitlines():
        if not raw.strip():
            continue
        line = _line_from_cells(_split_cells(raw))
        if not line:
            continue
        key = line.description.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def extract_table_lines(pdf_source) -> list[ExtractedLine]:
    """Priced line items from a PDF's real table structure (pdfplumber), which
    preserves the column grid even when the flat text run-together. Falls back to
    nothing (callers still have parse_table_lines over the text). Never raises."""
    out: list[ExtractedLine] = []
    seen: set[str] = set()
    try:
        with pdfplumber.open(pdf_source) as pdf:
            for page in pdf.pages:
                for table in (page.extract_tables() or []):
                    for row in table:
                        cells = [str(c).strip() for c in row if c not in (None, "")]
                        line = _line_from_cells(cells)
                        if not line:
                            continue
                        key = line.description.lower()
                        if key in seen:
                            continue
                        seen.add(key)
                        out.append(line)
    except Exception:                                # noqa: BLE001
        return out
    return out
