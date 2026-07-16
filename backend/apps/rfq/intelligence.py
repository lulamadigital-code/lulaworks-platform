"""Document Intelligence orchestration (RFQ_INTELLIGENCE §1, §4-5; AI_PLATFORM).

Deterministic-first: the parser runs for free. AI is the fallback for gaps
(missing critical fields, no line items, low confidence) — metered through the
gateway, only when a provider is configured. Every AI-sourced field is marked
and confidence-scored, and flows through the same human review + approval.
"""

import json
import re
from decimal import Decimal, InvalidOperation

from apps.ai_platform.gateway import run_metered
from apps.ai_platform.models import PromptTemplate
from apps.ai_platform.providers import ai_configured, get_provider

from .extraction import ExtractedLine, ExtractedValue

CRITICAL_FIELDS = ("po_number",)
DEFAULT_PROMPT = (
    "Extract the following from this RFQ/purchase-order text as strict JSON with "
    'keys "fields" (object of key -> {value, confidence 0-1}) and "lines" (array of '
    '{description, qty, unit, unit_price}). Keys to find: po_number, order_date, '
    "client, site, contact, scope, work_type. Text:\n\n{text}"
)


def _has_gaps(extraction) -> bool:
    if any(k not in extraction.fields for k in CRITICAL_FIELDS):
        return True
    if not extraction.lines:
        return True
    return False


def _prompt_text() -> str:
    tmpl = PromptTemplate.objects.filter(agent="rfq_extraction", is_active=True).first()
    return tmpl.content if tmpl else DEFAULT_PROMPT


def _dec(v):
    try:
        return Decimal(str(v))
    except (InvalidOperation, TypeError):
        return None


def _parse_ai(text: str):
    """Parse the model's JSON (tolerating ```json fences)."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}, []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}, []
    return data.get("fields", {}) or {}, data.get("lines", []) or []


def enrich_with_ai(company, user, extraction, *, provider=None, force=False):
    """Fill gaps in a deterministic extraction using AI (metered). Deterministic
    values always win; AI only *adds* missing fields/lines. `provider`/`force`
    are for dependency injection in tests; production passes neither and the
    call is a no-op unless a provider is configured and gaps exist."""
    if provider is None:
        if not ai_configured():
            return extraction
        provider = get_provider()
    if not force and not _has_gaps(extraction):
        return extraction

    prompt = _prompt_text().replace("{text}", extraction.text[:12000])
    resp = run_metered(company, user, provider, prompt, agent="rfq_extraction")
    ai_fields, ai_lines = _parse_ai(resp.text)
    method = f"ai_{resp.provider}"

    for key, payload in ai_fields.items():
        if key in extraction.fields:
            continue  # deterministic wins
        value = payload.get("value") if isinstance(payload, dict) else payload
        conf = float(payload.get("confidence", 0.6)) if isinstance(payload, dict) else 0.6
        if value:
            extraction.fields[key] = ExtractedValue(str(value), conf, method=method)

    if not extraction.lines and ai_lines:
        for ln in ai_lines:
            extraction.lines.append(
                ExtractedLine(
                    description=str(ln.get("description", "")),
                    qty=_dec(ln.get("qty", 1)) or Decimal("1"),
                    unit=str(ln.get("unit", "each")),
                    unit_price=_dec(ln.get("unit_price")),
                )
            )
    return extraction
