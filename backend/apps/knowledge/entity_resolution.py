"""Entity Resolution — the keystone of the Company Knowledge Engine.

Given a name (plus whatever contact signals we have) this service finds the
existing ERP record it refers to, scores the match, and returns a verdict:

    matched   high confidence — safe to auto-link
    review    plausible — a human must confirm (never auto-merge)
    new       nothing close enough — treat as a new entity

Every historical-import and document-extraction flow routes company/person
mentions through here so we connect to existing Customers / Suppliers /
Contacts instead of silently creating duplicates (LULAWORKS AI OS §6–§8).

Design notes:
* Pure standard library (difflib) — no new dependency, works on this Mac.
* TENANT-SCOPED BY CONSTRUCTION. It reads through the ordinary `objects`
  managers, which are tenant-scoped on TenantBaseModel, so the caller MUST be
  inside `tenant_scope(company_id)` (or a request with tenant context). That is
  what guarantees Company A can never resolve to Company B's records — there is
  no cross-tenant query path here to get wrong.
* It never writes. Resolution is advice; the caller (with the user's
  permissions) decides whether to link, and high-risk links go through review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Verdict thresholds on the 0..1 confidence score.
MATCH_THRESHOLD = 0.90   # >= this: auto-link is safe
REVIEW_THRESHOLD = 0.60  # >= this (and < match): show for human confirmation

# Legal/company-form suffixes stripped before comparing names.
_LEGAL_SUFFIXES = {
    "pty", "ltd", "limited", "proprietary", "propriety", "cc", "inc",
    "incorporated", "pvt", "llc", "plc", "co", "company", "corp", "corporation",
    "holdings", "group", "sa", "rsa", "and", "the",
}
_WORD = re.compile(r"[a-z0-9]+")


# ── normalisation ───────────────────────────────────────────────────────────
def normalise_name(name: str) -> str:
    """A comparison key for a company/person name: lowercased, punctuation and
    legal suffixes removed, tokens sorted so word order doesn't matter."""
    if not name:
        return ""
    tokens = [t for t in _WORD.findall(name.lower()) if t not in _LEGAL_SUFFIXES]
    return " ".join(sorted(tokens))


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def normalise_phone(phone: str) -> str:
    """Digits only, keep the last 9 (drops country/leading-zero variance so
    +27 82 555 1234, 082 555 1234 and 27825551234 all compare equal)."""
    digits = re.sub(r"\D", "", phone or "")
    return digits[-9:] if len(digits) >= 9 else digits


def _ref(value: str) -> str:
    """A registration/VAT number reduced to comparable form."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def _name_ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# ── results ─────────────────────────────────────────────────────────────────
@dataclass
class Candidate:
    id: str
    label: str
    score: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label,
                "score": round(self.score, 3), "reasons": self.reasons}


@dataclass
class Resolution:
    verdict: str                     # "matched" | "review" | "new"
    best: Candidate | None
    candidates: list[Candidate]
    query: dict

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "best": self.best.as_dict() if self.best else None,
            "candidates": [c.as_dict() for c in self.candidates],
            "query": self.query,
        }


def _verdict(score: float) -> str:
    if score >= MATCH_THRESHOLD:
        return "matched"
    if score >= REVIEW_THRESHOLD:
        return "review"
    return "new"


def _rank(scored: list[Candidate], query: dict, limit: int) -> Resolution:
    scored.sort(key=lambda c: c.score, reverse=True)
    top = [c for c in scored if c.score >= REVIEW_THRESHOLD][:limit]
    best = scored[0] if scored else None
    if not best or best.score < REVIEW_THRESHOLD:
        return Resolution("new", None, [], query)
    return Resolution(_verdict(best.score), best, top or [best], query)


def _score_signals(*, want_name, want_email, want_phone, want_ref,
                    cand_name, cand_email, cand_phone, cand_refs) -> tuple[float, list[str]]:
    """Combine the available signals into one confidence score + human reasons.
    The strongest single identifier dominates; a fuzzy name adds corroboration."""
    reasons: list[str] = []
    score = 0.0

    if want_email and cand_email and want_email == cand_email:
        score = max(score, 0.97)
        reasons.append("same email")
    if want_ref and cand_refs and want_ref in cand_refs:
        score = max(score, 0.95)
        reasons.append("same registration/VAT")
    if want_phone and cand_phone and want_phone == cand_phone:
        score = max(score, 0.86)
        reasons.append("same phone")

    if want_name and cand_name:
        if want_name == cand_name:
            score = max(score, 0.92)
            reasons.append("same name")
        else:
            ratio = _name_ratio(want_name, cand_name)
            if ratio >= 0.6:
                score = max(score, 0.55 + 0.4 * ratio)  # 0.6→0.79 … 1.0→0.95
                reasons.append(f"similar name ({int(ratio * 100)}%)")
    # A strong identifier PLUS a similar name nudges into auto-match territory.
    if len(reasons) >= 2 and score < MATCH_THRESHOLD and score >= 0.82:
        score = MATCH_THRESHOLD
    return score, reasons


# ── public API ──────────────────────────────────────────────────────────────
def resolve_company(name, *, kind="customer", email="", phone="",
                    reg_no="", vat_no="", limit=5) -> Resolution:
    """Resolve a company mention to an existing Customer (kind='customer') or
    Supplier (kind='supplier') in the CURRENT tenant. Caller must hold tenant
    context. Read-only."""
    from apps.customers.models import Customer
    from apps.procurement.models import Supplier

    want_name = normalise_name(name)
    want_email = normalise_email(email)
    want_phone = normalise_phone(phone)
    want_ref = _ref(reg_no) or _ref(vat_no)
    query = {"name": name, "kind": kind, "email": want_email,
             "phone": want_phone, "ref": want_ref}
    if not any([want_name, want_email, want_phone, want_ref]):
        return Resolution("new", None, [], query)

    scored: list[Candidate] = []
    if kind == "supplier":
        rows = Supplier.objects.all().only(
            "id", "name", "email", "phone", "registration_no", "vat_no")
        for s in rows:
            score, reasons = _score_signals(
                want_name=want_name, want_email=want_email,
                want_phone=want_phone, want_ref=want_ref,
                cand_name=normalise_name(s.name),
                cand_email=normalise_email(s.email),
                cand_phone=normalise_phone(s.phone),
                cand_refs={_ref(s.registration_no), _ref(s.vat_no)} - {""})
            if score:
                scored.append(Candidate(str(s.id), s.name, score, reasons))
    else:
        rows = Customer.objects.all().only(
            "id", "name", "trading_name", "email", "telephone", "mobile",
            "registration_no", "vat_no")
        for c in rows:
            # A customer may be known by its registered OR trading name.
            name_score = max(
                _pair_name(want_name, c.name),
                _pair_name(want_name, c.trading_name))
            score, reasons = _score_signals(
                want_name=want_name, want_email=want_email,
                want_phone=want_phone, want_ref=want_ref,
                cand_name=name_score[0],
                cand_email=normalise_email(c.email),
                cand_phone=normalise_phone(c.mobile) or normalise_phone(c.telephone),
                cand_refs={_ref(c.registration_no), _ref(c.vat_no)} - {""})
            if score:
                scored.append(Candidate(str(c.id), c.display_name, score, reasons))
    return _rank(scored, query, limit)


def _pair_name(want_name: str, candidate_raw: str) -> tuple[str, float]:
    """Return the normalised candidate name that best matches `want_name` and
    its ratio — used so trading_name can win over registered name."""
    cand = normalise_name(candidate_raw)
    return (cand, _name_ratio(want_name, cand) if cand else 0.0)


def resolve_contact(full_name, *, company_id=None, email="", phone="", limit=5) -> Resolution:
    """Resolve a person mention to an existing CustomerContact in the current
    tenant, optionally narrowed to one customer. Read-only."""
    from apps.customers.models import CustomerContact

    want_name = normalise_name(full_name)
    want_email = normalise_email(email)
    want_phone = normalise_phone(phone)
    query = {"full_name": full_name, "company_id": str(company_id) if company_id else None,
             "email": want_email, "phone": want_phone}
    if not any([want_name, want_email, want_phone]):
        return Resolution("new", None, [], query)

    rows = CustomerContact.objects.all().only(
        "id", "full_name", "email", "mobile", "telephone", "customer_id", "job_title")
    if company_id:
        rows = rows.filter(customer_id=company_id)

    scored: list[Candidate] = []
    for p in rows:
        score, reasons = _score_signals(
            want_name=want_name, want_email=want_email,
            want_phone=want_phone, want_ref="",
            cand_name=normalise_name(p.full_name),
            cand_email=normalise_email(p.email),
            cand_phone=normalise_phone(p.mobile) or normalise_phone(p.telephone),
            cand_refs=set())
        if score:
            label = p.full_name + (f" ({p.job_title})" if p.job_title else "")
            scored.append(Candidate(str(p.id), label, score, reasons))
    return _rank(scored, query, limit)
