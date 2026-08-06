"""Task-based provider routing — the orchestration layer decides which model.

The whole point of the AI platform: no module cares which vendor answers. It
asks for a TASK ("extract this document", "reason about this project", "write
this summary") and the router picks the best-suited provider, with a fallback
chain, from those that are configured and enabled.

Default routing follows the strengths the market has settled on — Gemini is
strong and cheap on document extraction and vision; Claude leads on careful
reasoning; OpenAI is a reliable generalist for generation. These are DEFAULTS,
overridable per deployment via settings.AI_TASK_ROUTES, and (Phase 2) tunable by
an admin without code. Adding a provider is a one-line entry here plus its
adapter — no business module changes, ever.
"""

from django.conf import settings

from .providers import ai_configured


class TaskType:
    """The kinds of work modules ask the AI to do. Every concrete AI feature
    maps to one of these so routing stays small and predictable."""

    EXTRACTION = "extraction"        # documents → structured JSON
    REASONING = "reasoning"          # analysis, risk, compliance judgement
    GENERATION = "generation"        # drafting: emails, suggestions, replies
    SUMMARY = "summary"              # condense jobs / quotations / notes
    IMAGE = "image"                  # image / scan understanding
    CLASSIFICATION = "classification"  # what kind of document is this
    MATCHING = "matching"            # product / supplier / duplicate matching
    CHAT = "chat"                    # the LulaAI assistant


#: Preferred → fallback provider chain per task. First configured+enabled wins;
#: the rest are the failover order. A deployment can override any of these via
#: settings.AI_TASK_ROUTES (same shape).
DEFAULT_TASK_ROUTES = {
    TaskType.EXTRACTION:     ["gemini", "claude", "openai"],
    TaskType.IMAGE:          ["gemini", "openai", "claude"],
    TaskType.CLASSIFICATION: ["gemini", "openai", "claude"],
    TaskType.REASONING:      ["claude", "openai", "gemini"],
    TaskType.MATCHING:       ["claude", "openai", "gemini"],
    TaskType.GENERATION:     ["openai", "claude", "gemini"],
    TaskType.SUMMARY:        ["openai", "claude", "gemini"],
    TaskType.CHAT:           ["claude", "openai", "gemini"],
}

#: Every named AI feature in the platform, mapped to its task category — so a
#: caller can pass its feature name and still route correctly, and so the admin
#: console can show which model serves which feature.
FEATURE_TASKS = {
    "quotation_scope_extraction": TaskType.EXTRACTION,
    "purchase_order_extraction": TaskType.EXTRACTION,
    "supplier_invoice_extraction": TaskType.EXTRACTION,
    "receipt_extraction": TaskType.EXTRACTION,
    "delivery_note_extraction": TaskType.EXTRACTION,
    "rfq_extraction": TaskType.EXTRACTION,
    "drawing_notes_extraction": TaskType.EXTRACTION,
    "document_classification": TaskType.CLASSIFICATION,
    "quotation_suggestions": TaskType.GENERATION,
    "item_suggestions": TaskType.GENERATION,
    "product_matching": TaskType.MATCHING,
    "supplier_matching": TaskType.MATCHING,
    "duplicate_detection": TaskType.MATCHING,
    "document_summary": TaskType.SUMMARY,
    "task_summary": TaskType.SUMMARY,
    "job_summary": TaskType.SUMMARY,
    "risk_detection": TaskType.REASONING,
    "compliance_review": TaskType.REASONING,
    "email_drafting": TaskType.GENERATION,
    "customer_reply": TaskType.GENERATION,
    "note_summary": TaskType.SUMMARY,
    "search_assist": TaskType.REASONING,
    "chat_assistant": TaskType.CHAT,
    "image_understanding": TaskType.IMAGE,
}

#: Static tie-break priority when a task doesn't name a provider (or for the
#: "then every other configured provider" tail). Lower = tried first.
PROVIDER_PRIORITY = {"gemini": 0, "claude": 1, "openai": 2}
ALL_PROVIDERS = ["gemini", "claude", "openai"]


def _routes() -> dict:
    """The active routing table: defaults overlaid with any deployment override
    in settings.AI_TASK_ROUTES."""
    override = getattr(settings, "AI_TASK_ROUTES", None) or {}
    table = dict(DEFAULT_TASK_ROUTES)
    for task, chain in override.items():
        if isinstance(chain, (list, tuple)) and chain:
            table[task] = list(chain)
    return table


def task_for_feature(feature: str) -> str:
    """Resolve a feature name (or a raw task) to a task category."""
    if feature in DEFAULT_TASK_ROUTES:
        return feature
    return FEATURE_TASKS.get(feature, TaskType.REASONING)


def provider_available(name: str) -> bool:
    """A provider can serve a request only if it's enabled and has a key.

    Phase 1: 'enabled' == has a key (env-configured). Phase 2 lets an admin
    disable a configured provider; this is the single seam that check plugs
    into, so nothing else changes when it lands.
    """
    return provider_enabled(name) and ai_configured(name)


def provider_enabled(name: str) -> bool:
    """Whether an admin has left this provider switched on. Reads the
    AIProviderSetting an admin controls (default on), then a deployment-level
    settings.AI_DISABLED_PROVIDERS kill-switch as a final override."""
    disabled = set(getattr(settings, "AI_DISABLED_PROVIDERS", []) or [])
    if name in disabled:
        return False
    try:
        from .provider_admin import is_enabled
        return is_enabled(name)
    except Exception:                       # DB not ready (e.g. during migrate)
        return True


def _priority(name: str) -> int:
    """Tie-break order, admin-tunable via AIProviderSetting; static default else."""
    try:
        from .provider_admin import priority as admin_priority
        return admin_priority(name)
    except Exception:
        return PROVIDER_PRIORITY.get(name, 99)


def route(task_or_feature: str) -> list[str]:
    """The ordered provider chain to try for a task: its preferred order first,
    then any other available provider as a last-ditch fallback — all filtered to
    providers that are enabled and configured. Empty when nothing is available
    (callers then keep their deterministic result)."""
    task = task_for_feature(task_or_feature)
    preferred = _routes().get(task, ALL_PROVIDERS)

    chain, seen = [], set()
    for name in preferred:
        if name not in seen and provider_available(name):
            chain.append(name)
            seen.add(name)
    # Tail: any remaining available provider, by admin-tunable priority — so a
    # task is never stranded just because its named providers are all down.
    for name in sorted(ALL_PROVIDERS, key=_priority):
        if name not in seen and provider_available(name):
            chain.append(name)
            seen.add(name)
    return chain
