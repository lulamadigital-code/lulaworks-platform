"""Document classifier — a cheap, deterministic first pass that labels an
imported document by type (quotation / customer PO / invoice / …).

Deterministic keyword scoring, no LLM: it is fast, free, explainable, and good
enough to route a document into the right extractor. An LLM classifier can slot
in behind the same `classify_document(filename, text)` signature later, for the
documents this pass leaves as OTHER or low-confidence.
"""
from __future__ import annotations

import re

from .models import ImportedDocument

DocType = ImportedDocument.DocType

# Ordered most-specific first: the first strong hit wins, so "supplier invoice"
# is checked before the generic "invoice". Each (type, patterns, weight).
_RULES: list[tuple[str, list[str]]] = [
    (DocType.CUSTOMER_PO, [r"\bpurchase order\b", r"\bp\.?o\.?\s*(no|number|#)", r"\border no\b"]),
    (DocType.SUPPLIER_QUOTE, [r"\bsupplier quot", r"\bvendor quot", r"\bquotation to\b.*\bsupplier"]),
    (DocType.SUPPLIER_INVOICE, [r"\bsupplier invoice\b", r"\bvendor invoice\b", r"\bstatement of account\b"]),
    (DocType.QUOTATION, [r"\bquotation\b", r"\bquote no\b", r"\bwe are pleased to quote\b", r"\bvalid for\b"]),
    (DocType.INVOICE, [r"\btax invoice\b", r"\binvoice no\b", r"\binvoice number\b", r"\bamount due\b"]),
    (DocType.DELIVERY_NOTE, [r"\bdelivery note\b", r"\bgoods received\b", r"\bdispatch note\b", r"\bwaybill\b"]),
    (DocType.RFQ, [r"\brequest for quot", r"\brfq\b", r"\btender\b", r"\bbill of quantities\b", r"\bscope of work\b"]),
    (DocType.JOB_REPORT, [r"\bjob report\b", r"\bsite report\b", r"\bcompletion certificate\b", r"\bwork done\b"]),
    (DocType.PRICE_LIST, [r"\bprice list\b", r"\bpricelist\b", r"\bunit price\b.*\bper\b", r"\bcatalogue\b"]),
]


def classify_document(filename: str, text: str) -> tuple[str, float]:
    """Return (doc_type, confidence 0..1). Combines filename hints and body
    keyword hits. Confidence reflects how many distinct signals agreed."""
    hay = f"{filename}\n{text}".lower()

    best_type = DocType.OTHER
    best_hits = 0
    for doc_type, patterns in _RULES:
        hits = sum(1 for p in patterns if re.search(p, hay))
        # Filename mentioning the type is a strong hint (+1).
        if doc_type.replace("_", " ") in filename.lower() or doc_type in filename.lower():
            hits += 1
        if hits > best_hits:
            best_hits, best_type = hits, doc_type

    if best_hits == 0:
        return DocType.OTHER, 0.0
    # 1 signal → 0.55, 2 → 0.75, 3+ → capped 0.9.
    confidence = min(0.9, 0.35 + 0.20 * best_hits)
    return best_type, round(confidence, 2)
