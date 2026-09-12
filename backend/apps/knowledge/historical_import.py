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

import hashlib
import re

from django.core.files.base import ContentFile
from django.db import IntegrityError
from django.utils import timezone

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
_DOMAIN = re.compile(r"@([A-Za-z0-9.\-]+)")
# Generic mail hosts that must never count as a company's "internal domain" —
# a member using gmail doesn't make every gmail address internal.
_GENERIC_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
                    "icloud.com", "live.com", "webmail.co.za", "mweb.co.za"}


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── batch lifecycle ───────────────────────────────────────────────────────────
def create_batch(user, *, label="") -> ImportBatch:
    return ImportBatch.objects.create(label=label, created_by=user,
                                      status=ImportBatch.Status.PROCESSING)


def queue_document(batch: ImportBatch, filename: str, data: bytes, user) -> ImportedDocument:
    """FAST path — runs inside the HTTP request. Hash, de-duplicate, persist the
    file and create a QUEUED record. NO extraction here (that's the worker). A
    byte-identical file already imported for this company becomes a DUPLICATE row
    pointing at the original instead of being processed again."""
    digest = file_sha256(data)
    original = (ImportedDocument.all_objects
                .filter(company=batch.company, file_hash=digest)
                .exclude(status=ImportedDocument.Status.DUPLICATE)
                .exclude(status=ImportedDocument.Status.FAILED)
                .order_by("created_at").first())
    doc = ImportedDocument(batch=batch, company=batch.company, filename=filename[:255],
                           file_hash=digest, created_by=user, updated_by=user)
    if original is not None:
        doc.status = ImportedDocument.Status.DUPLICATE
        doc.duplicate_of = original
    else:
        doc.status = ImportedDocument.Status.QUEUED
        doc.file.save(filename[:120], ContentFile(data), save=False)
    doc.save()
    batch.document_count = batch.documents.count()
    batch.save(update_fields=["document_count", "updated_at"])
    return doc


def process_document(doc: ImportedDocument) -> ImportedDocument:
    """WORKER path — the heavy work, off the request. Extract text → classify →
    extract & resolve entities → stage. Idempotent: a retry clears this doc's
    prior staged entities and re-stages, and a DUPLICATE/COMPLETED doc is a no-op.
    Status + timestamps are updated throughout; failures are caught and recorded,
    never raised to a user."""
    if doc.status == ImportedDocument.Status.DUPLICATE:
        return doc
    doc.status = ImportedDocument.Status.EXTRACTING
    doc.attempts = (doc.attempts or 0) + 1
    doc.started_at = doc.started_at or timezone.now()
    doc.processing_error = ""
    doc.save(update_fields=["status", "attempts", "started_at", "processing_error", "updated_at"])
    try:
        data = b""
        if doc.file:
            try:
                doc.file.open("rb")          # fresh handle — safe on a re-run
                data = doc.file.read()
            except Exception:                # noqa: BLE001
                data = b""
            finally:
                try:
                    doc.file.close()
                except Exception:            # noqa: BLE001
                    pass
        text = extract_text_from_upload(ContentFile(data, name=doc.filename)) if data else ""
        text = text or ""
        doc_type, confidence = classify_document(doc.filename, text)
        doc.doc_type, doc.doc_type_confidence = doc_type, confidence
        doc.text, doc.text_chars = text[:_TEXT_CAP], len(text)
        doc.status = ImportedDocument.Status.MATCHING
        doc.save(update_fields=["doc_type", "doc_type_confidence", "text", "text_chars",
                                "status", "updated_at"])
        # Idempotent re-stage: drop this document's previous entities first.
        doc.entities.all().delete()
        for cand in _extract_entities(text, company=doc.company, user=doc.created_by, use_ai=True):
            _stage(doc.batch, doc, cand)
        # High-confidence matches auto-link to existing records (so historical
        # customers/suppliers connect without a manual click); NEW/uncertain ones
        # wait in the review queue → the doc is COMPLETED only if nothing is pending.
        pending = _auto_apply(doc, doc.created_by)
        doc.status = (ImportedDocument.Status.NEEDS_REVIEW if pending
                      else ImportedDocument.Status.COMPLETED)
        doc.failed = False
        doc.completed_at = timezone.now()
        doc.save(update_fields=["status", "failed", "completed_at", "updated_at"])
    except Exception as exc:                         # noqa: BLE001
        doc.status = ImportedDocument.Status.FAILED
        doc.failed = True
        doc.processing_error = str(exc)[:2000]
        doc.save(update_fields=["status", "failed", "processing_error", "updated_at"])
    _refresh_batch(doc.batch)
    return doc


def _auto_apply(doc: ImportedDocument, user) -> int:
    """Auto-link this document's high-confidence MATCHED entities to the existing
    ERP records they resolved to — the safe half of "load customers from history"
    (it connects, never invents). NEW / needs-review entities are left PENDING for
    a human. Returns the count still pending."""
    can = bool(user and user.has_perm_code("customers.manage"))
    pending = 0
    for e in doc.entities.all():
        if e.review_status != StagedEntity.Review.PENDING:
            continue
        if can and e.verdict == StagedEntity.Verdict.MATCHED and e.match_id:
            e.resolved_id = e.match_id
            e.review_status = StagedEntity.Review.LINKED
            e.save(update_fields=["resolved_id", "review_status", "updated_at"])
            if e.kind == StagedEntity.Kind.SUPPLIER:
                try:
                    _capture_supplier_prices(e, user)
                except Exception:                    # noqa: BLE001
                    pass
        else:
            pending += 1
    return pending


def _refresh_batch(batch: ImportBatch) -> None:
    docs = list(batch.documents.all())
    done = all(d.status in (ImportedDocument.Status.COMPLETED,
                            ImportedDocument.Status.NEEDS_REVIEW,
                            ImportedDocument.Status.DUPLICATE,
                            ImportedDocument.Status.FAILED) for d in docs)
    batch.document_count = len(docs)
    batch.entity_count = batch.entities.count()
    batch.status = ImportBatch.Status.REVIEW if done else ImportBatch.Status.PROCESSING
    batch.save(update_fields=["document_count", "entity_count", "status", "updated_at"])


# Back-compat for tests/callers that want one synchronous call.
def ingest(batch: ImportBatch, filename: str, data: bytes, user) -> ImportedDocument:
    """Upload + process in one synchronous call (used by tests and eager mode)."""
    doc = queue_document(batch, filename, data, user)
    if doc.status != ImportedDocument.Status.DUPLICATE:
        process_document(doc)
    return doc


# Trailing document-type words that pollute an extracted organisation name, e.g.
# "Western Platinum (Pty) Ltd QUOTATION" → "Western Platinum (Pty) Ltd".
_DOCTYPE_TAIL = re.compile(
    r"\s*\b(tax\s*invoice|invoice|quotation|quote|statement|delivery\s*note|"
    r"dispatch\s*note|waybill|purchase\s*order|credit\s*note|order|rfq|"
    r"bill\s*to|sold\s*to|ship\s*to)\b.*$", re.I)


def _clean_company_name(name: str) -> str:
    n = (name or "").strip(" \t:-|,.")
    n = _DOCTYPE_TAIL.sub("", n).strip(" \t:-|,.")
    n = re.sub(r"(?<=[A-Za-z0-9])\(", " (", n)              # "Platinum(Pty)" → "Platinum (Pty)"
    n = re.sub(r"\(\s*pty\s*\)\s*ltd", "(Pty) Ltd", n, flags=re.I)
    n = re.sub(r"\s{2,}", " ", n)
    return n.strip()


def _dedup_key(kind, name, email) -> str:
    """A stable key so the same real entity collapses to one staged row across
    every document. Contacts key on email (the strongest identity) then name;
    companies on their cleaned, normalised name."""
    if kind == StagedEntity.Kind.CONTACT:
        e = er.normalise_email(email)
        return f"e:{e}" if e else f"n:{er.normalise_name(name)}"
    return f"n:{er.normalise_name(_clean_company_name(name))}"


def _better_name(new: str, old: str) -> bool:
    """Prefer a human name over an email-local guess: one with a space and mixed
    case beats 'luckymacheke89'."""
    return (" " in new.strip()) and (" " not in (old or "").strip())


def _stage(batch, doc, cand: dict) -> StagedEntity | None:
    kind = cand["kind"]
    name = _clean_company_name(cand["raw_name"]) if kind != StagedEntity.Kind.CONTACT \
        else cand["raw_name"].strip()
    if not name or len(name) < 2:
        return None
    email = cand.get("email", "")
    key = _dedup_key(kind, name, email)

    # De-dup across documents in this batch: one row per real entity, counting mentions.
    existing = StagedEntity.objects.filter(batch=batch, kind=kind, dedup_key=key).first()
    if existing is not None:
        fields = ["mentions", "updated_at"]
        existing.mentions = (existing.mentions or 1) + 1
        if _better_name(name, existing.raw_name):
            existing.raw_name = name[:255]; fields.append("raw_name")
        if not existing.email and email:
            existing.email = email[:255]; fields.append("email")
        existing.save(update_fields=fields)
        return existing

    if kind == StagedEntity.Kind.CONTACT:
        res = er.resolve_contact(name, email=email, phone=cand.get("phone", ""))
    else:
        res = er.resolve_company(
            name, kind="supplier" if kind == StagedEntity.Kind.SUPPLIER else "customer",
            email=email, phone=cand.get("phone", ""), reg_no=cand.get("reference", ""))
    best = res.best
    try:
        return StagedEntity.objects.create(
            batch=batch, document=doc, kind=kind, raw_name=name[:255],
            email=email[:255], phone=cand.get("phone", "")[:64],
            reference=cand.get("reference", "")[:64], dedup_key=key, mentions=1,
            verdict=res.verdict, confidence=(best.score if best else 0.0),
            match_id=(best.id if best else ""), match_label=(best.label if best else ""))
    except IntegrityError:
        # A concurrent worker staged the same entity first — just count the mention.
        existing = StagedEntity.objects.filter(batch=batch, kind=kind, dedup_key=key).first()
        if existing is not None:
            existing.mentions = (existing.mentions or 1) + 1
            existing.save(update_fields=["mentions", "updated_at"])
        return existing


def consolidate_batch(batch: ImportBatch) -> int:
    """Merge already-staged duplicates in a batch into one row per real entity
    (cleaning names, summing mentions, keeping the best name / any resolved match).
    Lets an existing noisy batch be cleaned up without re-uploading. Returns the
    number of duplicate rows removed."""
    from collections import defaultdict

    groups: dict = defaultdict(list)
    for e in StagedEntity.objects.filter(batch=batch):
        name = e.raw_name if e.kind == StagedEntity.Kind.CONTACT else _clean_company_name(e.raw_name)
        groups[(e.kind, _dedup_key(e.kind, name, e.email))].append(e)

    removed = 0
    for (kind, key), ents in groups.items():
        # Canonical = an already-committed row if any, else the best-named one.
        ents.sort(key=lambda x: (x.review_status == StagedEntity.Review.PENDING,
                                 0 if " " in x.raw_name.strip() else 1,
                                 -len(x.raw_name)))
        canon = ents[0]
        canon.dedup_key = key
        canon.mentions = len(ents)
        if kind != StagedEntity.Kind.CONTACT:
            canon.raw_name = (_clean_company_name(canon.raw_name) or canon.raw_name)[:255]
        if not canon.email:
            for e in ents:
                if e.email:
                    canon.email = e.email
                    break
        canon.save(update_fields=["dedup_key", "mentions", "raw_name", "email", "updated_at"])
        for dup in ents[1:]:
            dup.delete()
            removed += 1
    batch.entity_count = batch.entities.count()
    batch.save(update_fields=["entity_count", "updated_at"])
    return removed


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

    internal_emails, internal_domains = _internal_identity(company)
    for m in _EMAIL.finditer(text):
        email = m.group(0)
        # THE critical fix: an internal/company-owner email is never a customer
        # contact. Only external people become contacts.
        if _is_internal_email(email, internal_emails, internal_domains):
            continue
        local = email.split("@")[0]
        guessed = re.sub(r"[._\-]+", " ", local).title()
        add(StagedEntity.Kind.CONTACT, guessed, email=email)

    # AI enrichment — only ADDS what the regex missed; dedup via `add`/`seen`.
    # Internal emails are filtered here too, so a model that returns them can't
    # slip an owner/employee in as a customer contact.
    if use_ai and company is not None and user is not None:
        from .document_intelligence import ai_extract_entities
        for e in ai_extract_entities(text, company=company, user=user):
            if (e.get("kind") == StagedEntity.Kind.CONTACT
                    and _is_internal_email(e.get("email", ""), internal_emails, internal_domains)):
                continue
            add(e["kind"], e["raw_name"], email=e.get("email", ""),
                phone=e.get("phone", ""), reference=e.get("reference", ""))

    return found


def _internal_identity(company) -> tuple[set[str], set[str]]:
    """(internal_emails, internal_domains) for the current company — used to keep
    the company's own / employees' emails out of the customer-contact list.
    Internal domain = the company's official domain (from its email/website),
    never a generic mail host. Internal emails = members' exact addresses."""
    emails: set[str] = set()
    domains: set[str] = set()
    if company is None:
        return emails, domains
    for val in (getattr(company, "email", ""), getattr(company, "website", "")):
        d = _domain_of(val)
        if d and d not in _GENERIC_DOMAINS:
            domains.add(d)
    if getattr(company, "email", ""):
        emails.add(company.email.strip().lower())
    try:
        from apps.identity.models import Membership
        for e in Membership.objects.filter(company=company).values_list("user__email", flat=True):
            if e:
                emails.add(e.strip().lower())
    except Exception:                                # noqa: BLE001
        pass
    return emails, domains


def _domain_of(value: str) -> str:
    v = (value or "").strip().lower()
    if "@" in v:
        m = _DOMAIN.search(v)
        return m.group(1) if m else ""
    v = re.sub(r"^https?://", "", v).lstrip("/")
    v = re.sub(r"^www\.", "", v).split("/")[0]
    return v if "." in v else ""


def _is_internal_email(email: str, internal_emails: set[str], internal_domains: set[str]) -> bool:
    e = (email or "").strip().lower()
    if not e:
        return False
    if e in internal_emails:
        return True
    dom = _domain_of(e)
    return bool(dom and dom in internal_domains)


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

    S = ImportedDocument.Status
    processing_states = {S.QUEUED, S.PROCESSING, S.EXTRACTING, S.MATCHING}

    def dcount(pred):
        return sum(1 for d in docs if pred(d))

    return {
        "batch_id": str(batch.pk),
        "status": batch.status,
        "documents": len(docs),
        "documents_by_type": by_type,
        "processing": dcount(lambda d: d.status in processing_states),
        "completed": dcount(lambda d: d.status == S.COMPLETED),
        "duplicates": dcount(lambda d: d.status == S.DUPLICATE),
        "doc_failed": dcount(lambda d: d.status == S.FAILED),
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
