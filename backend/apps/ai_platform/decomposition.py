"""LulaAI work decomposition — "describe the job, get a plan to review".

The single most valuable thing an AI can do in a contracting business is turn a
one-line job description into the structure a professional would have written:
phases, tasks, a checklist the crew can actually tick, a duration estimate, the
compliance you'll be asked for, and the risks.

THREE RULES, in priority order:

1. **Grounded before generated.** The first source of truth is the company's OWN
   completed work — the checklists their people actually used and the hours those
   jobs actually took. A contractor who has done forty gearbox swaps has better
   data about gearbox swaps than any language model. The pattern library is the
   fallback for work they haven't done yet; the LLM is the last resort and only
   ever *adds* to what grounding produced.

2. **Propose, never persist.** `propose_decomposition()` performs no writes at
   all. It returns a draft. Nothing exists until a human ticks items and calls
   `apply_decomposition()` — the human-approval boundary, applied to planning.

3. **Say where it came from.** Every proposal carries its provenance and a
   confidence figure, so the estimate a person is about to accept is never a
   black box. `source="history"` with 12 past jobs behind it deserves more trust
   than `source="library"`, and the UI shows the difference.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from statistics import median

from django.db import transaction

from . import governance
from .gateway import InsufficientCreditsError, run_metered
from .providers import NotConfiguredError, configured_provider_names, get_provider

logger = logging.getLogger(__name__)

#: Words that carry no signal when matching one job against another.
_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "onto", "a", "an", "of", "to", "on",
    "at", "in", "is", "it", "be", "by", "or", "no", "not", "new", "old", "job", "work",
    "task", "please", "urgent", "asap", "site", "plant", "unit", "no.", "number",
}


def _tokens(text: str) -> set[str]:
    """Meaningful lowercase words — the crude but predictable matcher behind
    "have we done something like this before?"."""
    words = re.findall(r"[a-z0-9\-]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


# ── The pattern library: what a competent supervisor would write ──────────────
#
# Deliberately small and contractor-specific. This is the fallback for work the
# company has no history of — not an attempt to encode every job on earth.

WORK_PATTERNS = {
    "gearbox": {
        "label": "Gearbox replacement / overhaul",
        "keywords": {"gearbox", "reducer", "drive", "coupling", "gear"},
        "hours": 8,
        "checklist": [
            "Isolate, lock out and tag out",
            "Confirm zero energy state",
            "Remove guarding and access covers",
            "Mark and disconnect coupling",
            "Support and unbolt gearbox",
            "Remove old unit and record serial",
            "Fit replacement and align to spec",
            "Torque all fasteners to specification",
            "Refill/verify lubricant level",
            "Refit guarding",
            "Remove locks, test run and record vibration",
        ],
        "compliance": ["Lock-out/tag-out permit", "Working-at-height permit (if applicable)",
                       "Lifting equipment inspection certificate"],
        "resources": ["Millwright", "Rigger", "Overhead crane or chain block"],
        "risks": ["Stored energy on drive", "Suspended load during removal",
                  "Misalignment causing repeat failure"],
    },
    "pump": {
        "label": "Pump repair / replacement",
        "keywords": {"pump", "impeller", "seal", "gland", "casing"},
        "hours": 6,
        "checklist": [
            "Isolate electrically and mechanically",
            "Drain and flush lines",
            "Lock out and tag out",
            "Disconnect suction and discharge",
            "Remove pump from base",
            "Strip, inspect and record wear",
            "Replace seals and bearings",
            "Reassemble to torque specification",
            "Re-align pump to driver",
            "Recommission and record flow and pressure",
        ],
        "compliance": ["Lock-out/tag-out permit", "Confined-space permit (if applicable)",
                       "Hazardous-substance handling"],
        "resources": ["Fitter", "Rigger"],
        "risks": ["Residual pressure in line", "Hazardous process fluid",
                  "Seal failure on recommissioning"],
    },
    "conveyor": {
        "label": "Conveyor maintenance",
        "keywords": {"conveyor", "belt", "idler", "pulley", "roller", "scraper"},
        "hours": 10,
        "checklist": [
            "Isolate and lock out drive",
            "Install belt clamps where required",
            "Barricade the work area",
            "Inspect belt, splice and tracking",
            "Replace defective idlers and rollers",
            "Check and adjust scrapers",
            "Verify take-up tension",
            "Remove tools and clamps",
            "Test run empty and check tracking",
            "Hand back and sign off",
        ],
        "compliance": ["Lock-out/tag-out permit", "Working-at-height permit",
                       "Guarding compliance check"],
        "resources": ["Millwright", "Belt technician", "Safety observer"],
        "risks": ["Belt run-back", "Entanglement at nip points",
                  "Working at height over structure"],
    },
    "electrical": {
        "label": "Electrical fault / installation",
        "keywords": {"electrical", "board", "panel", "cable", "motor", "starter",
                     "switchgear", "wiring", "db", "transformer"},
        "hours": 6,
        "checklist": [
            "Issue and sign electrical permit",
            "Isolate, lock out and prove dead",
            "Apply earths where required",
            "Verify circuit and record readings",
            "Carry out repair or installation",
            "Insulation-resistance test",
            "Continuity and polarity test",
            "Restore supply under permit",
            "Functional test and record",
            "Update the electrical certificate",
        ],
        "compliance": ["Electrical permit to work", "Certificate of Compliance (CoC)",
                       "Registered person sign-off"],
        "resources": ["Qualified electrician", "Registered person for CoC"],
        "risks": ["Arc flash", "Back-feed from a second supply",
                  "Test equipment out of calibration"],
    },
    "inspection": {
        "label": "Inspection / condition survey",
        "keywords": {"inspect", "inspection", "survey", "audit", "condition",
                     "assessment", "measure"},
        "hours": 4,
        "checklist": [
            "Confirm scope and access with client",
            "Complete site induction",
            "Photograph general arrangement",
            "Record measurements and readings",
            "Note defects with photographs",
            "Rate severity of each finding",
            "Compile findings into the report",
            "Issue report and recommendations",
        ],
        "compliance": ["Site induction", "Medical certificate of fitness"],
        "resources": ["Inspector", "Measuring and test equipment"],
        "risks": ["Access restrictions on the day", "Scope creep during survey"],
    },
    "shutdown": {
        "label": "Shutdown / turnaround",
        "keywords": {"shutdown", "turnaround", "outage", "overhaul", "stoppage"},
        "hours": 40,
        "phases": ["Planning", "Procurement", "Compliance", "Execution",
                   "Commissioning", "Closure"],
        "checklist": [
            "Freeze and agree the work list",
            "Confirm long-lead materials on site",
            "Complete permits and inductions",
            "Mobilise crews and equipment",
            "Execute the critical path first",
            "Daily progress and variance review",
            "Commission and functionally test",
            "Hand back to production",
            "Close out documentation and punch list",
        ],
        "compliance": ["Shutdown safety file", "Permit to work", "Contractor packs",
                       "Medical certificates", "Site inductions"],
        "resources": ["Shutdown coordinator", "Multi-discipline crews",
                      "Lifting equipment", "Standby maintenance"],
        "risks": ["Critical-path slip", "Scope growth from discovered defects",
                  "Late material delivery", "Permit backlog on day one"],
    },
    "welding": {
        "label": "Welding / fabrication",
        "keywords": {"weld", "welding", "fabricat", "structural", "plate", "beam", "chute"},
        "hours": 8,
        "checklist": [
            "Issue hot-work permit",
            "Clear combustibles and post fire watch",
            "Verify welder qualification for procedure",
            "Prepare and fit up joint",
            "Complete welding to procedure",
            "Visual inspection of welds",
            "NDT where specified",
            "Grind, clean and apply protective coating",
            "Maintain fire watch after completion",
        ],
        "compliance": ["Hot-work permit", "Welder qualification certificate",
                       "Fire-watch assignment"],
        "resources": ["Coded welder", "Fire watch", "Welding machine"],
        "risks": ["Fire from sparks", "Fume exposure in confined areas",
                  "Weld defects requiring rework"],
    },
}

#: Used when nothing matches — still better than a blank page.
GENERIC_PATTERN = {
    "label": "General work",
    "keywords": set(),
    "hours": 4,
    "checklist": [
        "Confirm scope and access",
        "Complete permits and inductions",
        "Prepare tools and materials",
        "Carry out the work",
        "Quality check and photograph",
        "Clean up and hand back",
        "Record time and materials",
    ],
    "compliance": ["Site induction", "Permit to work"],
    "resources": ["Technician"],
    "risks": ["Scope unclear at start"],
}


@dataclass
class Decomposition:
    """A reviewable plan. `requires_approval` and `executed_by_ai` mirror the
    governance contract — this object is a proposal, never an action."""

    work_type: str
    summary: str
    confidence: float
    source: str                      # history | library | generic
    grounded_in: list = field(default_factory=list)
    phases: list = field(default_factory=list)
    checklist: list = field(default_factory=list)
    subtasks: list = field(default_factory=list)
    compliance: list = field(default_factory=list)
    resources: list = field(default_factory=list)
    risks: list = field(default_factory=list)
    estimated_hours: float = 0.0
    duration_days: int = 1
    briefing: str = ""
    provider: str = ""
    requires_approval: bool = True
    executed_by_ai: bool = False

    def to_dict(self):
        return asdict(self)


# ── Grounding pass 1: the company's own completed work ────────────────────────

def _match_pattern(text: str):
    """Best-matching library pattern by keyword overlap."""
    tokens = _tokens(text)
    best, best_score = None, 0
    for key, pattern in WORK_PATTERNS.items():
        score = len(tokens & pattern["keywords"])
        if score > best_score:
            best, best_score = key, score
    return (best, WORK_PATTERNS[best]) if best else (None, None)


def _similar_completed_work(name, description="", *, limit=8):
    """Past work whose title overlaps this one. Tenant-scoped by the ambient
    manager, so a company can only ever learn from its own history."""
    from apps.execution.models import Task

    tokens = _tokens(f"{name} {description}")
    if not tokens:
        return []
    candidates = (Task.objects.filter(status__in=["completed", "closed"])
                  .prefetch_related("checklist_items")[:400])
    scored = []
    for task in candidates:
        overlap = tokens & _tokens(task.name)
        if overlap:
            scored.append((len(overlap), task))
    scored.sort(key=lambda row: -row[0])
    return [task for _, task in scored[:limit]]


def _checklist_from_history(tasks) -> list[str]:
    """Checklist items that recur across past jobs, most-used first. An item one
    person used once is noise; one used on four jobs is how this crew works."""
    counts: dict[str, int] = {}
    for task in tasks:
        seen = set()
        for item in task.checklist_items.all():
            label = item.label.strip()
            key = label.lower()
            if key and key not in seen:
                seen.add(key)
                counts[label] = counts.get(label, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    return [label for label, count in ranked if count >= 2] or [l for l, _ in ranked]


def _hours_from_history(tasks) -> float | None:
    """Median ACTUAL hours from past jobs — the number that beats a guess."""
    actuals = [float(t.actual_hours) for t in tasks if t.actual_hours and t.actual_hours > 0]
    return round(median(actuals), 2) if actuals else None


# ── Grounding pass 2: what the rest of the platform already knows ─────────────

def _compliance_from_project(project) -> list[str]:
    """Outstanding compliance on the parent project — real items, not guesses."""
    if project is None:
        return []
    try:
        from apps.compliance.services import recompute_readiness
        readiness = recompute_readiness(project)
    except Exception as exc:  # noqa: BLE001 - advisory only, never break the draft
        logger.warning("Decomposition compliance grounding failed: %s", exc)
        return []
    return [b.get("name", "") for b in readiness.get("blocking", []) if b.get("name")]


# ── The proposal ──────────────────────────────────────────────────────────────

def propose_decomposition(company, user, *, name, description="", origin=None,
                          project=None, enrich=None) -> Decomposition:
    """Turn a job description into a reviewable plan. PERFORMS NO WRITES.

    Grounding order: the company's own completed work → the contractor pattern
    library → a generic skeleton. An LLM, if configured and permitted, may then
    ADD suggestions on top; it never replaces what grounding produced and its
    failure never breaks the draft.
    """
    key, pattern = _match_pattern(f"{name} {description}")
    pattern = pattern or GENERIC_PATTERN
    work_type = pattern["label"]

    history = _similar_completed_work(name, description)
    history_checklist = _checklist_from_history(history) if history else []
    history_hours = _hours_from_history(history) if history else None

    if history_checklist:
        checklist = history_checklist
        source, confidence = "history", min(0.95, 0.6 + 0.05 * len(history))
        grounded_in = [f"{len(history)} similar completed job(s) in your own history"]
    elif key:
        checklist = list(pattern["checklist"])
        source, confidence = "library", 0.7
        grounded_in = [f"Standard {work_type.lower()} method"]
    else:
        checklist = list(GENERIC_PATTERN["checklist"])
        source, confidence = "generic", 0.4
        grounded_in = ["No close match — generic skeleton"]

    if history_hours is not None:
        hours = history_hours
        grounded_in.append(f"Median actual hours from past jobs: {history_hours}h")
    else:
        hours = float(pattern.get("hours", 4))
        grounded_in.append(f"Library estimate: {hours}h")

    compliance = list(pattern.get("compliance", []))
    outstanding = _compliance_from_project(project)
    if outstanding:
        compliance = outstanding + [c for c in compliance if c not in outstanding]
        grounded_in.append(f"{len(outstanding)} item(s) currently blocking the project gate")

    phases = list(pattern.get("phases", []))
    if project is not None and not phases:
        phases = ["Planning", "Execution", "Closure"]

    draft = Decomposition(
        work_type=work_type,
        summary=(f"{work_type} — {len(checklist)} checklist step(s), "
                 f"about {hours}h of work."),
        confidence=round(confidence, 2),
        source=source,
        grounded_in=grounded_in,
        phases=phases,
        checklist=checklist,
        compliance=compliance,
        resources=list(pattern.get("resources", [])),
        risks=list(pattern.get("risks", [])),
        estimated_hours=hours,
        duration_days=max(1, round(hours / 8)),
    )

    if enrich is not False:
        _maybe_enrich(company, user, draft, name=name, description=description)
    return draft


def _maybe_enrich(company, user, draft: Decomposition, *, name, description) -> None:
    """Optional live-LLM pass. It may only ADD checklist steps and risks that the
    grounded plan missed, and writes a short briefing. Any failure — no key, no
    credits, bad JSON, provider down — leaves the deterministic draft untouched.
    Mutates `draft` in place; returns nothing."""
    if not user.has_perm_code("ai.generate"):
        return
    prompt = (
        "You are helping a South African contracting supervisor plan a job.\n"
        f"JOB: {name}\nNOTES: {description or '(none)'}\n"
        f"WORK TYPE: {draft.work_type}\n"
        f"EXISTING CHECKLIST (already decided, do not repeat or reorder):\n"
        + "\n".join(f"- {c}" for c in draft.checklist)
        + "\n\nReturn STRICT JSON only, no prose, with keys: "
        '{"extra_checklist": [up to 4 steps this plan is missing], '
        '"extra_risks": [up to 3 risks], "briefing": "<=60 words for the supervisor"}. '
        "Do not invent equipment numbers, people, dates or prices."
    )
    for provider_name in configured_provider_names():
        try:
            provider = get_provider(provider_name)
            resp = run_metered(company, user, provider, prompt, agent="lulaai_decompose")
            payload = json.loads(_json_slice(resp.text))
        except InsufficientCreditsError:
            logger.info("Decomposition enrichment skipped: no AI credits.")
            return
        except Exception as exc:  # noqa: BLE001 - resilient: grounding already stands
            logger.warning("Decomposition enrichment via %s failed (%s); keeping "
                           "the grounded draft.", provider_name, exc)
            continue
        else:
            existing = {c.lower() for c in draft.checklist}
            added = [str(c).strip() for c in payload.get("extra_checklist", [])[:4]
                     if str(c).strip() and str(c).strip().lower() not in existing]
            draft.checklist.extend(added)
            draft.risks.extend(str(r).strip() for r in payload.get("extra_risks", [])[:3]
                               if str(r).strip())
            draft.briefing = str(payload.get("briefing", ""))[:400]
            draft.provider = resp.provider
            if added:
                draft.grounded_in.append(
                    f"{len(added)} step(s) suggested by {resp.provider} (review these)")
            return


def _json_slice(text: str) -> str:
    """Models like to wrap JSON in prose or fences — take the outermost object."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object in model response.")
    return text[start:end + 1]


# ── Applying it — only what a human ticked ────────────────────────────────────

@transaction.atomic
def apply_decomposition(task, user, draft: Decomposition, *, checklist_indexes=None,
                        phase_indexes=None, apply_hours=False) -> dict:
    """Create ONLY the items the human selected. Passing no selection creates
    nothing — refusing to guess is the point. Returns what was created."""
    from apps.execution.services import add_checklist_item, add_phase, rollup_progress

    created = {"checklist": 0, "phases": 0, "hours_set": False}

    wanted = set(checklist_indexes or [])
    for index, label in enumerate(draft.checklist):
        if index in wanted:
            add_checklist_item(task, user, label=label)
            created["checklist"] += 1

    wanted_phases = set(phase_indexes or [])
    if task.project_id:
        existing = {p.name.lower() for p in task.project.phases.all()}
        for index, phase_name in enumerate(draft.phases):
            if index in wanted_phases and phase_name.lower() not in existing:
                add_phase(task.project, user, name=phase_name)
                created["phases"] += 1

    if apply_hours and draft.estimated_hours:
        task.estimated_hours = Decimal(str(draft.estimated_hours))
        task.updated_by = user
        task.save(update_fields=["estimated_hours", "updated_by", "updated_at"])
        created["hours_set"] = True

    if created["checklist"]:
        rollup_progress(task, user)
    return created


def record_proposal(company, user, task, draft: Decomposition, *, applied=None):
    """Audit the proposal (and any human acceptance) as an AIInteraction, so
    every AI-influenced plan is traceable after the fact."""
    from .models import AIInteraction

    from .models import ApprovalStatus

    return AIInteraction.objects.create(
        company=company, agent="lulaai_decompose",
        request_text=f"Decompose: {task.name}",
        result={
            "draft": draft.to_dict(),
            "applied": applied or {},
            "governance": governance.propose(
                "decompose_work",
                "Proposed a work breakdown for human review.",
                task=str(task.id), source=draft.source, confidence=draft.confidence,
            ),
        },
        confidence=Decimal(str(draft.confidence)),
        provider=draft.provider or "deterministic",
        entity_type="execution.Task", entity_id=task.id,
        # Applying is the human's acceptance of the draft; proposing alone is not.
        approval_status=(ApprovalStatus.APPROVED if applied else ApprovalStatus.DRAFT),
        decided_by=user if applied else None,
        created_by=user, updated_by=user,
    )
