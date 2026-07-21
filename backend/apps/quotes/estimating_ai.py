"""LulaAI estimating — help price the job without pricing it for you.

Same rule as everywhere else: the machine proposes, a human ticks, only then
does it exist. That matters more here than almost anywhere, because an invented
line goes to a customer with a price on it and becomes a contract.

Two separate things live here, and they are different in kind:

* `suggest_lines()` — what might be MISSING. Grounded first in the company's own
  past quotations for similar work (their real rates, their real crews), then a
  contractor pattern library, then optionally a model. A firm that has quoted
  thirty gearbox jobs knows more about gearbox jobs than any LLM does.

* `pricing_review()` — what looks WRONG. Entirely deterministic: thin margin,
  uncosted lines, markup far from your own norm, categories a job of this type
  almost always has. No model, no credits, no waiting — and reproducible, which
  matters when someone asks why a quote was flagged.
"""

import json
import logging
import re
from decimal import Decimal
from statistics import median

from django.db import transaction

from apps.ai_platform.gateway import InsufficientCreditsError, run_metered
from apps.ai_platform.providers import configured_provider_names, get_provider

from .models import LineCategory, QuotationLine, QuotationStatus

logger = logging.getLogger(__name__)

TWO = Decimal("0.01")

#: Below this, a job is usually not worth doing once overheads are counted.
THIN_MARGIN_PCT = Decimal("15")
#: A markup this far from your own median is worth a second look either way.
MARKUP_DEVIATION_PCT = Decimal("20")

#: Categories a job of each type almost always contains. Absence is a question,
#: not an error — some jobs really are labour only.
EXPECTED_CATEGORIES = {
    "mechanical_repair": ["labour", "material", "consumable"],
    "electrical_repair": ["labour", "material"],
    "installation": ["labour", "material", "equipment"],
    "shutdown": ["labour", "material", "equipment", "management"],
    "fabrication": ["labour", "material", "consumable"],
    "construction": ["labour", "material", "equipment", "transport"],
    "maintenance": ["labour", "consumable"],
    "supply": ["material", "transport"],
    "plant_hire": ["equipment", "transport"],
    "labour_hire": ["labour"],
    "transport": ["transport"],
}

_STOPWORDS = {"the", "and", "for", "with", "from", "a", "an", "of", "to", "on",
              "at", "in", "is", "it", "be", "by", "or", "new", "old", "job",
              "work", "supply", "install", "replace", "repair", "service"}


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9\-]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


# ── Grounding: the company's own quoting history ─────────────────────────────

def _similar_quotations(quote, *, limit=8):
    """Past quotations for comparable work, most similar first.

    Only ones that went out — a draft nobody sent is not evidence of anything.
    """
    from .models import Quotation

    tokens = _tokens(f"{quote.title} {quote.scope_of_work}")
    if not tokens:
        return []
    candidates = (Quotation.objects
                  .exclude(pk=quote.pk)
                  .filter(status__in=[QuotationStatus.ISSUED, QuotationStatus.SENT,
                                      QuotationStatus.ACCEPTED, QuotationStatus.AWARDED,
                                      QuotationStatus.REJECTED, QuotationStatus.LOST])
                  .prefetch_related("lines")[:300])
    scored = []
    for other in candidates:
        overlap = tokens & _tokens(f"{other.title} {other.scope_of_work}")
        if overlap:
            # Same quotation type is a strong signal; count it double.
            weight = len(overlap) + (2 if other.quotation_type_id ==
                                     quote.quotation_type_id else 0)
            scored.append((weight, other))
    scored.sort(key=lambda row: -row[0])
    return [q for _, q in scored[:limit]]


def company_markup_median(company, category=None):
    """What this company actually marks up, by category. Their norm, not a
    textbook one — the only sensible baseline for calling something an outlier."""
    lines = QuotationLine.objects.filter(markup_pct__gt=0)
    if category:
        lines = lines.filter(category=category)
    values = [float(line.markup_pct) for line in lines[:500]]
    return Decimal(str(round(median(values), 2))) if values else None


def suggest_lines(quote, user=None, *, use_ai=None) -> dict:
    """Lines this quotation may be missing. PERFORMS NO WRITES."""
    existing = {line.description.strip().lower() for line in quote.lines.all()}
    candidates, seen = [], set()

    history = _similar_quotations(quote)
    for other in history:
        for line in other.lines.all():
            key = line.description.strip().lower()
            if not key or key in existing or key in seen:
                continue
            seen.add(key)
            candidates.append({
                "description": line.description,
                "category": line.category,
                "qty": line.qty,
                "unit": line.unit,
                "unit_cost": line.unit_cost,
                "markup_pct": line.markup_pct,
                "source": f"you quoted this on {other.number}",
                "confidence": 0.8,
            })

    ai_note = ""
    if use_ai is not False and user is not None and user.has_perm_code("ai.generate"):
        added, ai_note = _ai_lines(quote, user, existing | seen)
        candidates.extend(added)

    return {
        "candidates": candidates[:40],
        "grounded_in": [f"{len(history)} similar quotation(s) of your own"]
                       if history else [],
        "ai_note": ai_note,
        "requires_approval": True,
        "executed_by_ai": False,
    }


def _ai_lines(quote, user, already) -> tuple[list, str]:
    """A model may ADD items the history did not cover. Prices are never taken
    from it — only descriptions and units. A made-up rate in a quotation is a
    made-up contract."""
    lines = "\n".join(f"- {line.description}" for line in quote.lines.all())
    prompt = (
        "You are helping a South African contractor check a quotation for "
        "missing items.\n"
        f"JOB: {quote.title}\nSCOPE: {quote.scope_of_work or '(none given)'}\n"
        f"ALREADY QUOTED:\n{lines or '(nothing yet)'}\n\n"
        "List work items commonly needed for this job that are missing above. "
        "Do NOT invent prices, part numbers or rates. Return strict JSON only: "
        '{"lines": [{"description": "...", "category": "labour|material|'
        'consumable|equipment|transport|other", "unit": "each"}]}'
    )
    for provider_name in configured_provider_names():
        try:
            provider = get_provider(provider_name)
            resp = run_metered(company=quote.company, user=user, provider=provider,
                               prompt=prompt, agent="quote_estimating", json_mode=True)
            payload = json.loads(resp.text[resp.text.find("{"):resp.text.rfind("}") + 1])
        except InsufficientCreditsError:
            return [], "AI suggestions skipped — no AI credits."
        except Exception as exc:  # noqa: BLE001 - history-based suggestions still stand
            logger.warning("Quote estimating via %s failed (%s).", provider_name, exc)
            continue
        else:
            out = []
            for row in payload.get("lines", [])[:15]:
                description = str(row.get("description", "")).strip()
                if not description or description.lower() in already:
                    continue
                already.add(description.lower())
                out.append({
                    "description": description[:500],
                    "category": str(row.get("category", "other"))[:16],
                    "qty": Decimal("1"),
                    "unit": str(row.get("unit") or "each")[:32],
                    # Deliberately no cost: the estimator prices it.
                    "unit_cost": Decimal("0"),
                    "markup_pct": Decimal("0"),
                    "source": f"{resp.provider} — needs pricing",
                    "confidence": 0.5,
                })
            return out, f"{len(out)} suggestion(s) from {resp.provider}, unpriced."
    return [], ""


@transaction.atomic
def apply_suggestions(quote, user, suggestion, indexes) -> int:
    """Create ONLY the ticked candidates."""
    from .services import guard_editable
    guard_editable(quote)

    wanted = {int(i) for i in indexes}
    position = quote.lines.count()
    created = 0
    for index, row in enumerate(suggestion["candidates"]):
        if index not in wanted:
            continue
        position += 1
        QuotationLine.objects.create(
            company=quote.company, quotation=quote, position=position,
            description=row["description"], category=row["category"],
            qty=row["qty"], unit=row["unit"], unit_cost=row["unit_cost"],
            markup_pct=row["markup_pct"], ai_suggested=True,
            created_by=user, updated_by=user,
        )
        created += 1
    return created


# ── Pricing review: deterministic, free, reproducible ────────────────────────

def pricing_review(quote) -> dict:
    """What looks wrong with this quotation's pricing.

    No model involved. Every finding names the specific line or number behind
    it, so the estimator can agree or disagree on the evidence rather than
    being told a score.
    """
    findings = []
    lines = list(quote.lines.all())

    if not lines:
        return {"findings": [], "checked": 0, "ok": False,
                "summary": "Nothing to review — the quotation has no lines."}

    # 1 · Lines nobody costed. Margin is unknown, and unknown reads as fine.
    uncosted = [line for line in lines if not line.has_cost]
    if uncosted:
        findings.append({
            "severity": "high",
            "title": f"{len(uncosted)} line(s) have no cost",
            "detail": "Margin cannot be computed, so this quotation looks more "
                      "profitable than it may be.",
            "items": [line.description for line in uncosted],
        })

    # 2 · Thin overall margin.
    if quote.margin_pct is not None and quote.margin_pct < THIN_MARGIN_PCT:
        findings.append({
            "severity": "high",
            "title": f"Margin is {quote.margin_pct}%",
            "detail": f"Below {THIN_MARGIN_PCT}%, most contracting work does not "
                      "cover its overheads once management time is counted.",
            "items": [],
        })

    # 3 · Lines priced below cost — usually a typo, occasionally deliberate.
    below_cost = [line for line in lines
                  if line.has_cost and line.line_total < line.total_cost]
    if below_cost:
        findings.append({
            "severity": "high",
            "title": f"{len(below_cost)} line(s) priced below cost",
            "detail": "Selling for less than it costs you. Deliberate loss "
                      "leaders happen; typos happen more often.",
            "items": [f"{line.description} — costs R{line.total_cost}, "
                      f"sells R{line.line_total}" for line in below_cost],
        })

    # 4 · Markup far from this company's own norm.
    outliers = []
    for line in lines:
        if not line.markup_pct:
            continue
        norm = company_markup_median(quote.company, line.category)
        if norm and abs(line.markup_pct - norm) > MARKUP_DEVIATION_PCT:
            direction = "above" if line.markup_pct > norm else "below"
            outliers.append(f"{line.description} — {line.markup_pct}% is "
                            f"{direction} your usual {norm}% for "
                            f"{line.get_category_display().lower()}")
    if outliers:
        findings.append({
            "severity": "medium",
            "title": f"{len(outliers)} markup(s) unlike your usual",
            "detail": "Compared against your own median for that category, not "
                      "an industry figure.",
            "items": outliers,
        })

    # 5 · Categories a job of this type usually has.
    if quote.quotation_type_id:
        expected = EXPECTED_CATEGORIES.get(quote.quotation_type.key, [])
        present = {line.category for line in lines}
        missing = [LineCategory(c).label for c in expected if c not in present]
        if missing:
            findings.append({
                "severity": "low",
                "title": f"No {', '.join(missing).lower()} priced",
                "detail": f"A {quote.quotation_type.label.lower()} job usually "
                          "includes these. Sometimes it genuinely does not.",
                "items": [],
            })

    # 6 · Expiring or missing validity.
    if not quote.validity_date:
        findings.append({
            "severity": "low",
            "title": "No validity date",
            "detail": "Without one the price is open-ended, and material costs "
                      "move.",
            "items": [],
        })

    return {
        "findings": findings,
        "checked": len(lines),
        "ok": not findings,
        "summary": ("Nothing flagged." if not findings else
                    f"{len(findings)} thing(s) worth checking before this goes out."),
    }
