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
from difflib import SequenceMatcher

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
# Label dictionary → semantic role. Order matters: INTERNAL wins first so
# "Prepared by: Ronny <ronny@us.com>" is never read as a customer contact.
_LABEL_ROLES: list[tuple[str, str]] = [
    (r"prepared\s*by|compiled\s*by|issued\s*by|drawn\s*up\s*by|quotation\s*prepared\s*by|"
     r"invoice\s*prepared\s*by|sales\s*rep\w*|account\s*manager|estimator|our\s*ref|"
     r"from\s*us|authorised\s*by|signed\s*by", "internal"),
    (r"supplier|vendor|seller|quoted\s*by|supplied\s*by", "supplier"),
    (r"customer|client|bill\s*to|billed\s*to|sold\s*to|ship\s*to|account\s*name|"
     r"buyer|purchaser|ordered\s*by|requested\s*by", "customer"),
    (r"contact\s*person|contact|attention|attn|for\s*attention|project\s*manager|"
     r"site\s*contact|procurement\s*contact|accounts\s*contact", "contact"),
]
_LABEL_RE = [(re.compile(rf"(?im)^\s*(?:{pat})\s*[:\-]\s*(.+)$"), role)
             for pat, role in _LABEL_ROLES]

# Document direction: on a document WE issue (our quote/invoice/delivery), a
# "from/quoted by" company is US (internal); on a SUPPLIER's document, a
# "bill to/customer" is US (internal). Used to suppress mis-rolled entities.
_OUR_DOCS = {ImportedDocument.DocType.QUOTATION, ImportedDocument.DocType.INVOICE,
             ImportedDocument.DocType.DELIVERY_NOTE}
_SUPPLIER_DOCS = {ImportedDocument.DocType.SUPPLIER_QUOTE,
                  ImportedDocument.DocType.SUPPLIER_INVOICE}
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
        if text.strip():
            # We have content — safe to (re)stage idempotently: clear THIS doc's
            # prior entities, then re-extract.
            doc.entities.all().delete()
            for cand in _extract_entities(text, doc_type=doc.doc_type, company=doc.company,
                                          user=doc.created_by, use_ai=True):
                _stage(doc.batch, doc, cand)
            # High-confidence matches auto-link to existing records (historical
            # customers/suppliers connect without a click); NEW/uncertain wait in
            # the queue → COMPLETED only if nothing is pending, else NEEDS_REVIEW.
            pending = _auto_apply(doc, doc.created_by)
            doc.status = (ImportedDocument.Status.NEEDS_REVIEW if pending
                          else ImportedDocument.Status.COMPLETED)
            doc.failed = False
            doc.completed_at = timezone.now()
        elif doc.entities.exists():
            # No text extracted this run (e.g. unreadable file on a retry) but we
            # already have entities — NEVER destroy good data; keep what we had.
            doc.status = ImportedDocument.Status.COMPLETED
            doc.failed = False
            doc.completed_at = timezone.now()
        else:
            # No text and nothing extracted — a genuine failure the user can retry.
            doc.status = ImportedDocument.Status.FAILED
            doc.failed = True
            doc.processing_error = "No readable text could be extracted from this document."
        doc.save(update_fields=["status", "failed", "completed_at", "processing_error",
                                "updated_at"])
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
# "Western Platinum (Pty) Ltd QUOTATION" → "Western Platinum (Pty) Ltd". Includes
# common OCR/typo variants (qoutation, quatation, delivary…).
_DOCTYPE_TAIL = re.compile(
    r"\s*\b(tax\s*invoice|invoice|q[ou]+tation|qu[ao]tation|quotation|quote|statement|"
    r"deliv[ae]ry\s*note|dispatch\s*note|waybill|purchase\s*order|credit\s*note|"
    r"order|rfq|bill\s*to|sold\s*to|ship\s*to)\b.*$", re.I)
# Trailing SITE / operation descriptors — a shaft/plant/mine is a site OF the
# customer, not a separate customer: "Sibanye Stillwater K4 Shaft" → "Sibanye Stillwater".
_SITE_TAIL = re.compile(
    r"\s+(?:no\.?\s*\d+\s*)?(?:[a-z]?\d+\s*)?\b(shaft|plant|mine|colliery|section|"
    r"smelter|refinery|concentrator|mill|operations?|project|site|works)\b.*$", re.I)


def _clean_company_name(name: str) -> str:
    n = (name or "").strip(" \t:-|,.")
    n = _DOCTYPE_TAIL.sub("", n).strip(" \t:-|,.")
    n = _SITE_TAIL.sub("", n).strip(" \t:-|,.")
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


_FUZZY_MERGE = 0.86   # conservative auto-merge for OCR/near-duplicate company names


def _match_token(kind, name, email) -> str:
    """A spaceless normalised token for fuzzy comparison of company names."""
    if kind == StagedEntity.Kind.CONTACT:
        return er.normalise_email(email) or er.normalise_name(name).replace(" ", "")
    return er.normalise_name(_clean_company_name(name)).replace(" ", "")


def consolidate_batch(batch: ImportBatch) -> int:
    """Merge already-staged duplicates in a batch into one row per real entity.
    Two phases: (1) exact key (cleaned+normalised name / email), then (2) a
    conservative FUZZY pass for companies so OCR variants like "Sibanye Stillwater
    K4 Shaft" and "Slbanye Stlllwatu" collapse together. Cleans names, sums
    mentions, keeps any resolved match. Returns the number of rows merged away."""
    from collections import defaultdict

    removed = 0
    # First, drop internal/company-owner emails that slipped through as contacts —
    # the owner's own address must never appear as a customer contact.
    internal_emails, internal_domains = _internal_identity(batch.company)
    for e in StagedEntity.objects.filter(batch=batch, kind=StagedEntity.Kind.CONTACT):
        if e.email and _is_internal_email(e.email, internal_emails, internal_domains):
            e.delete()
            removed += 1

    by_kind: dict = defaultdict(list)
    for e in StagedEntity.objects.filter(batch=batch):
        by_kind[e.kind].append(e)

    for kind, items in by_kind.items():
        # Phase 1 — exact dedup-key groups.
        groups: dict = defaultdict(list)
        for e in items:
            name = e.raw_name if kind == StagedEntity.Kind.CONTACT else _clean_company_name(e.raw_name)
            groups[_dedup_key(kind, name, e.email)].append(e)
        clusters = [list(g) for g in groups.values()]

        # Phase 2 — fuzzy-merge company clusters by token similarity.
        if kind != StagedEntity.Kind.CONTACT:
            merged: list = []            # [(token, [entities])]
            for g in clusters:
                token = _match_token(kind, g[0].raw_name, g[0].email)
                placed = False
                for m in merged:
                    if token and m[0] and SequenceMatcher(None, token, m[0]).ratio() >= _FUZZY_MERGE:
                        m[1].extend(g)
                        placed = True
                        break
                if not placed:
                    merged.append((token, g))
            clusters = [g for _t, g in merged]

        for g in clusters:
            removed += _collapse_cluster(g, kind)

    batch.entity_count = batch.entities.count()
    batch.save(update_fields=["entity_count", "updated_at"])
    return removed


def _collapse_cluster(ents: list, kind) -> int:
    """Keep one canonical entity for a cluster; sum mentions; soft-delete the rest."""
    # Canonical: prefer a resolved/committed row, then a real (spaced) name, then
    # the most-mentioned, then the longest.
    ents.sort(key=lambda x: (x.review_status == StagedEntity.Review.PENDING,
                             0 if " " in x.raw_name.strip() else 1,
                             -(x.mentions or 1), -len(x.raw_name)))
    canon = ents[0]
    total = sum(e.mentions or 1 for e in ents)
    if kind != StagedEntity.Kind.CONTACT:
        canon.raw_name = (_clean_company_name(canon.raw_name) or canon.raw_name)[:255]
    canon.dedup_key = _dedup_key(kind, canon.raw_name, canon.email)
    canon.mentions = total
    if not canon.email:
        for e in ents:
            if e.email:
                canon.email = e.email
                break
    canon.save(update_fields=["dedup_key", "mentions", "raw_name", "email", "updated_at"])
    removed = 0
    for dup in ents[1:]:
        dup.delete()
        removed += 1
    return removed


def commit_all(batch: ImportBatch, user, *, kind) -> dict:
    """Approve every pending entity of one kind in one action: matched rows link
    to the existing record, the rest are created in the ERP. Customers/suppliers
    only — contacts need a parent customer, so they're confirmed after. This is
    the bulk 'add these to my CRM' step. Requires customers.manage."""
    if not user.has_perm_code("customers.manage"):
        raise CommitError("You don't have permission to add these to your CRM.")
    if kind == StagedEntity.Kind.CONTACT:
        raise CommitError("Create the customers first, then add contacts to them.")
    created = linked = failed = 0
    pending = list(StagedEntity.objects.filter(
        batch=batch, kind=kind, review_status=StagedEntity.Review.PENDING))
    for e in pending:
        try:
            decision = "link" if (e.verdict == StagedEntity.Verdict.MATCHED and e.match_id) else "create"
            commit_entity(e, user, decision=decision)
            if decision == "link":
                linked += 1
            else:
                created += 1
        except CommitError:
            failed += 1
    return {"created": created, "linked": linked, "failed": failed}


def merge_entities(batch: ImportBatch, primary_id, other_ids, user) -> int:
    """Human-driven merge: fold `other_ids` into `primary_id` (same kind). For the
    cases only a person knows — e.g. a trading name and its registered company, or
    OCR variants the fuzzy pass didn't catch. Requires customers.manage."""
    if not user.has_perm_code("customers.manage"):
        raise CommitError("You don't have permission to merge entities.")
    primary = StagedEntity.objects.filter(batch=batch, pk=primary_id).first()
    if primary is None:
        raise CommitError("Couldn't find the entity to merge into.")
    merged = 0
    for oid in other_ids:
        if str(oid) == str(primary_id):
            continue
        other = StagedEntity.objects.filter(batch=batch, pk=oid, kind=primary.kind).first()
        if other is None:
            continue
        primary.mentions = (primary.mentions or 1) + (other.mentions or 1)
        if not primary.email and other.email:
            primary.email = other.email
        other.delete()
        merged += 1
    if merged:
        primary.save(update_fields=["mentions", "email", "updated_at"])
    return merged


# ── entity extraction (deterministic first pass) ──────────────────────────────
def _role_of_line(line: str):
    """(role, value) for a labelled line, else (None, None). Role is one of
    internal / supplier / customer / contact."""
    for rx, role in _LABEL_RE:
        m = rx.match(line)
        if m:
            return role, m.group(1).strip()
    return None, None


def _split_value(value: str) -> tuple[str, str]:
    """Split a labelled value into (name, email) — name is what remains once the
    email and any phone-like run are removed."""
    m = _EMAIL.search(value)
    email = m.group(0) if m else ""
    name = value.replace(email, " ") if email else value
    name = re.sub(r"[+()]?\d[\d\s\-]{6,}\d", " ", name)   # drop a phone-ish run
    name = re.sub(r"\s{2,}", " ", name).strip(" \t:-|,.")
    return name, email


def _extract_entities(text: str, *, doc_type=None, company=None, user=None, use_ai=False) -> list[dict]:
    if not text:
        return []
    found: list[dict] = []
    seen: set[tuple] = set()
    internal_emails, internal_domains = _internal_identity(company)
    # Internal names/emails gathered from INTERNAL-labelled lines (prepared by…),
    # so they're excluded as contacts even without a known company domain.
    internal_seen_emails: set[str] = set()

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

    # Pass 1 — labelled lines carry a semantic role (this is the direction-aware
    # part: "Prepared by" = internal, "Bill To/Customer" = customer, etc.).
    for line in text.splitlines():
        role, value = _role_of_line(line)
        if not role or not value:
            continue
        name, email = _split_value(value)
        # Document direction: the counterparty on our/supplier docs can be US.
        if role == "supplier" and doc_type in _OUR_DOCS:
            role = "internal"               # "from/quoted by" on our own doc = us
        elif role == "customer" and doc_type in _SUPPLIER_DOCS:
            role = "internal"               # "bill to" on a supplier's doc = us
        if role == "internal":
            if email:
                internal_seen_emails.add(email.strip().lower())
            continue                         # never stage internal people/companies
        if role == "supplier":
            add(StagedEntity.Kind.SUPPLIER, name, email=email)
        elif role == "customer":
            add(StagedEntity.Kind.CUSTOMER, name, email=email, reference=reference)
        elif role == "contact":
            low = email.strip().lower()
            if email and (low in internal_seen_emails
                          or _is_internal_email(email, internal_emails, internal_domains)):
                continue
            add(StagedEntity.Kind.CONTACT, name or email.split("@")[0], email=email)

    # Pass 2 — unlabelled emails become contacts only if clearly external.
    for m in _EMAIL.finditer(text):
        email = m.group(0)
        low = email.strip().lower()
        if low in internal_seen_emails or _is_internal_email(email, internal_emails, internal_domains):
            continue
        guessed = re.sub(r"[._\-]+", " ", email.split("@")[0]).title()
        add(StagedEntity.Kind.CONTACT, guessed, email=email)

    # AI enrichment — AI returns a kind/role already; internal emails still filtered.
    if use_ai and company is not None and user is not None:
        from .document_intelligence import ai_extract_entities
        for e in ai_extract_entities(text, company=company, user=user):
            em = e.get("email", "")
            if (e.get("kind") == StagedEntity.Kind.CONTACT and em
                    and (em.strip().lower() in internal_seen_emails
                         or _is_internal_email(em, internal_emails, internal_domains))):
                continue
            add(e["kind"], e["raw_name"], email=em,
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
        staged.delete()   # remove it from the queue entirely (soft-delete, recoverable)
        return {"ok": True, "review_status": StagedEntity.Review.REJECTED}

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

    company = staged.company
    if staged.kind == StagedEntity.Kind.SUPPLIER:
        # (company, name) is unique — reuse an existing supplier of that name.
        s, created = Supplier.objects.get_or_create(
            company=company, name=staged.raw_name,
            defaults={"email": staged.email, "phone": staged.phone,
                      "created_by": user, "updated_by": user})
        return s.pk
    if staged.kind == StagedEntity.Kind.CONTACT:
        if not customer_id:
            raise CommitError("A contact must be attached to a customer — "
                              "choose the customer to create this person under.")
        c = CustomerContact.objects.create(
            company=company, customer_id=customer_id, full_name=staged.raw_name,
            email=staged.email, mobile=staged.phone, created_by=user, updated_by=user)
        return c.pk
    # default: customer — via the service so the customer code is generated.
    from apps.customers.services import create_customer
    cust = create_customer(company, user, name=staged.raw_name,
                           email=staged.email, telephone=staged.phone)
    return cust.pk
