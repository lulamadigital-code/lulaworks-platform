"""Document Intelligence — the single place the platform turns a document (or a
block of pasted text) into readable text and candidate line items.

Shared by the RFQ module and the Quotation module, and ready for Purchase Orders
and Contracts, so all of them behave identically and there is one implementation
to maintain rather than four that drift apart.

The line-item parsing itself is NOT reimplemented here: it delegates to
``apps.rfq.extraction``, which is deterministic-first and validated against real
Sibanye/Western Platinum documents. This module adds the two things the RFQ
parser did not need — reading text out of the many shapes a scope arrives in
(PDF, Word, Excel, an image, an email, a zip), and offering related items a
scope implies but did not spell out. Everything it returns is a *suggestion* for
a human to accept, edit or ignore; nothing here writes anything.
"""

import io
import zipfile
from email import message_from_bytes

from apps.rfq.extraction import parse_loose_lines, parse_rfq_text
from apps.rfq.extraction import extract_text as _pdf_text

#: What the upload control accepts. Extensions we can read to some degree; an
#: unknown type is not rejected, it just yields no text (and no items).
SUPPORTED_EXTENSIONS = {
    ".pdf", ".txt", ".csv", ".md", ".docx", ".xlsx", ".xls",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".eml", ".zip",
}


def _ext(name: str) -> str:
    name = (name or "").lower()
    return name[name.rfind("."):] if "." in name else ""


# ── Per-format text extractors (every optional library is lazy + graceful) ────

def _docx_text(data: bytes) -> str:
    try:
        import docx
    except ImportError:
        return ""
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception:
        return ""
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append("\t".join(c.text for c in row.cells))
    return "\n".join(parts)


def _xlsx_text(data: bytes) -> str:
    try:
        import openpyxl
    except ImportError:
        return ""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        return ""
    lines = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c not in (None, "")]
            if cells:
                lines.append("\t".join(cells))
    return "\n".join(lines)


def _image_text(data: bytes) -> str:
    """OCR an image (a photographed scope, a screenshot). Lazy — '' if the OCR
    toolchain is not installed, so a missing binary never 500s the request."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    try:
        return pytesseract.image_to_string(Image.open(io.BytesIO(data)))
    except Exception:
        return ""


def _eml_text(data: bytes) -> str:
    try:
        msg = message_from_bytes(data)
    except Exception:
        return ""
    parts = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() == "text/plain":
            payload = part.get_payload(decode=True)
            if payload:
                parts.append(payload.decode(part.get_content_charset() or "utf-8",
                                             errors="ignore"))
    return "\n".join(parts)


def _plain_text(data: bytes) -> str:
    return data.decode("utf-8", errors="ignore")


def _zip_text(data: bytes) -> str:
    """Read every member we understand and concatenate — a zip of drawings and a
    BOQ becomes one body of text to read items from."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        return ""
    parts = []
    for info in zf.infolist():
        if info.is_dir() or _ext(info.filename) not in SUPPORTED_EXTENSIONS:
            continue
        if _ext(info.filename) == ".zip":      # do not recurse into nested zips
            continue
        try:
            parts.append(_text_from_bytes(info.filename, zf.read(info)))
        except Exception:
            continue
    return "\n".join(p for p in parts if p)


def _text_from_bytes(name: str, data: bytes) -> str:
    ext = _ext(name)
    if ext == ".pdf":
        return _pdf_text(io.BytesIO(data))          # pdfplumber text + OCR fallback
    if ext == ".docx":
        return _docx_text(data)
    if ext in (".xlsx", ".xls"):
        return _xlsx_text(data)
    if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        return _image_text(data)
    if ext == ".eml":
        return _eml_text(data)
    if ext == ".zip":
        return _zip_text(data)
    if ext in (".txt", ".csv", ".md"):
        return _plain_text(data)
    # Unknown type: try to read it as text rather than fail outright.
    return _plain_text(data)


def extract_text_from_upload(uploaded_file) -> str:
    """Text out of an uploaded file, whatever its shape. Never raises on a bad
    file — a corrupt or unreadable upload yields '' and the caller simply finds
    no items."""
    try:
        data = uploaded_file.read()
    except Exception:
        return ""
    return _text_from_bytes(getattr(uploaded_file, "name", ""), data or b"")


# ── Candidate line items ──────────────────────────────────────────────────────

#: Units a job type prices in, used only to default a unit the text did not give.
_TYPE_UNIT = {"labour_hire": "hour", "plant_hire": "day",
              "project_management": "unit", "maintenance": "service"}


def extract_items(text: str, *, type_key: str | None = None) -> list[dict]:
    """Candidate line items from text — the structured table first (rigid,
    high-confidence), then loose prose ("20 conveyor rollers"), de-duplicated by
    description. Returns plain dicts the estimator's grid fills in; the price is
    left blank unless the document actually stated one, because an invented price
    is worse than a blank."""
    if not text or not text.strip():
        return []

    lines = list(parse_rfq_text(text).lines) + list(parse_loose_lines(text))
    default_unit = _TYPE_UNIT.get(type_key or "", "each")

    items, seen = [], set()
    for ln in lines:
        desc = (ln.description or "").strip()
        key = desc.lower()
        if len(desc) < 3 or key in seen:
            continue
        seen.add(key)
        # The loose parser falls back to "each" when the text gave no unit; for
        # a labour or plant job that really means "unspecified", so let the type
        # default win. A unit the text stated explicitly is always kept.
        unit = ln.unit or default_unit
        if unit == "each" and default_unit != "each":
            unit = default_unit
        items.append({
            "description": desc,
            "qty": f"{ln.qty:g}" if ln.qty else "1",
            "unit": unit,
            "unit_price": f"{ln.unit_price:g}" if ln.unit_price else "",
        })
    return items


# ── Related-item suggestions (§7) ─────────────────────────────────────────────
#
# What a scope implies but rarely lists: a job that supplies rollers needs
# bearings, transport and consumables; installing anything needs labour. These
# are offered as chips, never added automatically, and never a price.

_RELATED = [
    (r"conveyor|roller|idler", ["Bearings", "Transport", "Consumables"]),
    (r"\binstall|installation|fit\b|mount", ["Installation labour"]),
    (r"pump|impeller|mechanical seal", ["Mechanical seals", "Gaskets", "Installation labour"]),
    (r"weld|fabricat|steel|structural", ["Welding consumables", "Transport", "Surface preparation"]),
    (r"paint|coat|corrosion", ["Surface preparation", "Consumables"]),
    (r"electric|cable|motor|panel", ["Cable & glands", "Commissioning", "Installation labour"]),
    (r"crane|lift|rigging|hoist", ["Rigging crew", "Mobilisation", "Transport"]),
    (r"pipe|pipeline|flange|valve", ["Gaskets", "Fasteners", "Installation labour"]),
    (r"maintenance|service|inspection", ["Consumables", "Report & sign-off"]),
]


def suggest_related_items(text: str, existing: list[str] | None = None) -> list[str]:
    """Related items a scope implies. De-duplicated, and never anything the
    estimator already has on the quotation."""
    import re

    if not text or not text.strip():
        return []
    have = {e.strip().lower() for e in (existing or [])}
    out, seen = [], set()
    for pattern, related in _RELATED:
        if not re.search(pattern, text, re.IGNORECASE):
            continue
        for item in related:
            key = item.lower()
            if key in have or key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out[:6]
