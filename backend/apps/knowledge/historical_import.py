"""Historical Business Import — the "Bring Your Business History" pipeline.

    ingest:   upload → extract text → classify → extract entities → resolve
              against the ERP → STAGE (nothing written yet)
    review:   a person confirms each staged entity
    commit:   link to the matched record, or create a new one — the ONLY step
              that writes to the ERP, and only on human confirmation

This closes the loop end-to-end while keeping the guardrail the AI-OS brief
insists on: imported data never enters the ERP silently. Entity extraction here
is deterministic (labels + emails); a stronger LLM extractor can replace
`_extract_entities` behind the same signature without touching the pipeline.
"""
from __future__ import annotations

import re

from django.core.files.base import ContentFile

from . import entity_resolution as er
from .classifier import classify_document
from .document_intelligence import extract_text_from_upload
from .models import ImportBatch, ImportedDocument, StagedEntity

_TEXT_CAP = 20000          # store at most this many chars of extracted text
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_REF = re.compile(r"(?:reg(?:istration)?\.?\s*(?:no\.?|number)?|vat\s*(?:no\.?|number)?)"
                  r"\s*[:\-]?\s*([0-9][0-9/ \-]{5,})", re.I)
_COMPANY_LABELS = {
    StagedEntity.Kind.CUSTOMER: r"(?:customer|client|bill\s*to|sold\s*to|ship\s*to)",
    StagedEntity.Kind.SUPPLIER: r"(?:supplier|vendor|seller|from)",
}


# ── batch lifecycle ───────────────────────────────────────────────────────────
def create_batch(user, *, label="") -> ImportBatch:
    return ImportBatch.objects.create(label=label, created_by=user,
                                      status=ImportBatch.Status.PROCESSING)


def ingest(batch: ImportBatch, filename: str, data: bytes, user) -> ImportedDocument:
    """Process one document into the batch: extract text, classify, extract &
    resolve entities, and stage them. Never raises on a bad file — a document we
    can't read becomes a `failed` row so the batch still completes."""
    try:
        text = extract_text_from_upload(ContentFile(data, name=filename)) or ""
    except Exception:                                # noqa: BLE001
        text = ""
    doc_type, confidence = classify_document(filename, text)
    doc = ImportedDocument.objects.create(
        batch=batch, filename=filename, doc_type=doc_type,
        doc_type_confidence=confidence, text=text[:_TEXT_CAP],
        text_chars=len(text), failed=(text == ""))

    # Deterministic-first, then AI enriches (metered; a document upload is a
    # deliberate action). With no provider configured this is regex-only.
    for cand in _extract_entities(text, company=batch.company, user=user, use_ai=True):
        _stage(batch, doc, cand)

    batch.document_count = batch.documents.count()
    batch.entity_count = batch.entities.count()
    batch.status = ImportBatch.Status.REVIEW
    batch.save(update_fields=["document_count", "entity_count", "status", "updated_at"])
    return doc


def _stage(batch, doc, cand: dict) -> StagedEntity:
    kind = cand["kind"]
    if kind == StagedEntity.Kind.CONTACT:
        res = er.resolve_contact(cand["raw_name"], email=cand.get("email", ""),
                                 phone=cand.get("phone", ""))
    else:
        res = er.resolve_company(
            cand["raw_name"],
            kind="supplier" if kind == StagedEntity.Kind.SUPPLIER else "customer",
            email=cand.get("email", ""), phone=cand.get("phone", ""),
            reg_no=cand.get("reference", ""))
    best = res.best
    return StagedEntity.objects.create(
        batch=batch, document=doc, kind=kind, raw_name=cand["raw_name"][:255],
        email=cand.get("email", "")[:255], phone=cand.get("phone", "")[:64],
        reference=cand.get("reference", "")[:64],
        verdict=res.verdict, confidence=(best.score if best else 0.0),
        match_id=(best.id if best else ""), match_label=(best.label if best else ""))


# ── entity extraction (deterministic first pass) ──────────────────────────────
def _extract_entities(text: str, *, company=None, user=None, use_ai=False) -> list[dict]:
    if not text:
        return []
    found: list[dict] = []
    seen: set[tuple] = set()

    def add(kind, raw_name, *, email="", phone="", reference=""):
        name = (raw_name or "").strip(" \t:-|,.")
        if not name or not re.search(r"[A-Za-z]", name) or len(name) < 2:
            return
        key = (kind, er.normalise_name(name) or (email or "").lower())
        if key in seen:
            return
        seen.add(key)
        found.append({"kind": kind, "raw_name": name[:120], "email": email,
                      "phone": phone, "reference": reference})

    ref_m = _REF.search(text)
    reference = re.sub(r"\s", "", ref_m.group(1)) if ref_m else ""

    for kind, label in _COMPANY_LABELS.items():
        for m in re.finditer(rf"(?im)^\s*{label}\s*[:\-]\s*(.+)$", text):
            name = m.group(1).split("  ")[0]
            add(kind, name, reference=reference if kind == StagedEntity.Kind.CUSTOMER else "")

    for m in _EMAIL.finditer(text):
        email = m.group(0)
        local = email.split("@")[0]
        guessed = re.sub(r"[._\-]+", " ", local).title()
        add(StagedEntity.Kind.CONTACT, guessed, email=email)

    # AI enrichment — only ADDS what the regex missed; dedup via `add`/`seen`.
    if use_ai and company is not None and user is not None:
        from .document_intelligence import ai_extract_entities
        for e in ai_extract_entities(text, company=company, user=user):
            add(e["kind"], e["raw_name"], email=e.get("email", ""),
                phone=e.get("phone", ""), reference=e.get("reference", ""))

    return found


# ── review + commit (the only writes) ─────────────────────────────────────────
class CommitError(Exception):
    pass


def batch_summary(batch: ImportBatch) -> dict:
    """The counts that drive the onboarding screen (§22) — documents by type and
    entities by kind/verdict. Only real, staged rows are counted."""
    docs = list(batch.documents.all())
    ents = list(batch.entities.all())
    by_type: dict[str, int] = {}
    for d in docs:
        by_type[d.doc_type] = by_type.get(d.doc_type, 0) + 1

    def count(pred):
        return sum(1 for e in ents if pred(e))

    return {
        "batch_id": str(batch.pk),
        "status": batch.status,
        "documents": len(docs),
        "documents_by_type": by_type,
        "entities": len(ents),
        "customers": count(lambda e: e.kind == StagedEntity.Kind.CUSTOMER),
        "suppliers": count(lambda e: e.kind == StagedEntity.Kind.SUPPLIER),
        "contacts": count(lambda e: e.kind == StagedEntity.Kind.CONTACT),
        "auto_matched": count(lambda e: e.verdict == StagedEntity.Verdict.MATCHED),
        "need_review": count(lambda e: e.verdict == StagedEntity.Verdict.REVIEW),
        "new": count(lambda e: e.verdict == StagedEntity.Verdict.NEW),
        "committed": count(lambda e: e.review_status in
                           (StagedEntity.Review.LINKED, StagedEntity.Review.CREATED)),
    }


def commit_entity(staged: StagedEntity, user, *, decision, customer_id=None) -> dict:
    """Apply a human decision to a staged entity. This is the ONLY ERP write.

    decision "link"   → attach to the matched existing record (needs match_id)
    decision "create" → create a new Customer/Supplier/Contact from the mention
    decision "reject" → discard the mention

    Requires customers.manage. A contact 'create' needs customer_id (a person
    must belong to a customer)."""
    if not user.has_perm_code("customers.manage"):
        raise CommitError("You don't have permission to import company knowledge.")

    if decision == "reject":
        staged.review_status = StagedEntity.Review.REJECTED
        staged.save(update_fields=["review_status", "updated_at"])
        return {"ok": True, "review_status": staged.review_status}

    if decision == "link":
        if not staged.match_id:
            raise CommitError("No existing record to link to.")
        staged.resolved_id = staged.match_id
        staged.review_status = StagedEntity.Review.LINKED
        staged.save(update_fields=["resolved_id", "review_status", "updated_at"])
        prices = _capture_supplier_prices(staged, user)
        return {"ok": True, "review_status": staged.review_status,
                "resolved_id": staged.resolved_id, "prices_recorded": prices}

    if decision == "create":
        resolved_id = _create_record(staged, user, customer_id=customer_id)
        staged.resolved_id = str(resolved_id)
        staged.review_status = StagedEntity.Review.CREATED
        staged.save(update_fields=["resolved_id", "review_status", "updated_at"])
        prices = _capture_supplier_prices(staged, user)
        return {"ok": True, "review_status": staged.review_status,
                "resolved_id": staged.resolved_id, "prices_recorded": prices}

    raise CommitError(f"Unknown decision '{decision}'.")


def _capture_supplier_prices(staged: StagedEntity, user) -> int:
    """When a supplier from a supplier quote/invoice is confirmed, turn the
    document's line items into price history (§11) — the bridge from Historical
    Import into the price ledger. Only real, priced lines are recorded."""
    if staged.kind != StagedEntity.Kind.SUPPLIER or not staged.resolved_id:
        return 0
    doc = staged.document
    price_docs = (ImportedDocument.DocType.SUPPLIER_QUOTE,
                  ImportedDocument.DocType.SUPPLIER_INVOICE)
    if doc is None or doc.doc_type not in price_docs or not doc.text:
        return 0
    from apps.procurement.models import Supplier
    from apps.procurement.services import record_prices

    supplier = Supplier.objects.filter(pk=staged.resolved_id).first()
    if supplier is None:
        return 0
    items = _extract_priced_lines(doc.text, company=staged.company, user=user, use_ai=True)
    if not items:
        return 0
    return record_prices(staged.company, supplier, items, user=user)


# A line with a clear trailing money amount: optional "qty unit", a description
# (must contain letters), then a currency amount with cents. The cents requirement
# keeps us from mistaking a bare quantity for a price — we only record real prices.
_PRICED_LINE = re.compile(
    r"^\s*(?:(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z]{1,6})?\s+)?"
    r"(?P<desc>.*?[A-Za-z].*?)\s+"
    r"(?:R|ZAR)?\s*(?P<price>\d[\d ,]*\.\d{2})\s*$")


def _extract_priced_lines(text: str, *, company=None, user=None, use_ai=False) -> list[dict]:
    """Pull priced lines from a supplier document. Deterministic first (lines with
    an explicit amount + cents), then AI adds priced lines a regex missed. Never
    an invented price."""
    out: list[dict] = []
    seen: set[str] = set()

    def add(desc, unit, price):
        desc = (desc or "").strip(" \t:-|")
        key = desc.lower()
        if len(desc) < 3 or key in seen:
            return
        seen.add(key)
        out.append({"description": desc, "unit": unit or "each", "unit_price": price})

    for raw in (text or "").splitlines():
        m = _PRICED_LINE.match(raw)
        if not m:
            continue
        price = m.group("price").replace(" ", "").replace(",", "")
        add(m.group("desc"), m.group("unit"), price)

    if use_ai and company is not None and user is not None:
        from .document_intelligence import ai_extract_prices
        for ln in ai_extract_prices(text, company=company, user=user):
            add(ln["description"], ln.get("unit"), ln["unit_price"])

    return out


def _create_record(staged: StagedEntity, user, *, customer_id=None):
    from apps.customers.models import Customer, CustomerContact
    from apps.procurement.models import Supplier

    if staged.kind == StagedEntity.Kind.SUPPLIER:
        s = Supplier.objects.create(name=staged.raw_name, email=staged.email,
                                    phone=staged.phone, created_by=user)
        return s.pk
    if staged.kind == StagedEntity.Kind.CONTACT:
        if not customer_id:
            raise CommitError("A contact must be attached to a customer — "
                              "choose the customer to create this person under.")
        c = CustomerContact.objects.create(
            customer_id=customer_id, full_name=staged.raw_name,
            email=staged.email, mobile=staged.phone, created_by=user)
        return c.pk
    # default: customer
    cust = Customer.objects.create(name=staged.raw_name, email=staged.email,
                                   telephone=staged.phone, created_by=user)
    return cust.pk
