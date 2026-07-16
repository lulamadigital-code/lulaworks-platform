"""Compliance engine services (COMPLIANCE.md / Module 8).

Discovery (compose a project checklist from the requirement library), the
computed Work Readiness gate, continuous validation (expiry sweep), and the
authorised, audited override. Readiness is COMPUTED on demand — never a stale
snapshot — so it is always current when queried.
"""

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.administration.services import record_audit
from apps.core.context import tenant_scope
from apps.core.events import publish

from .models import (
    ComplianceItem,
    ComplianceOverride,
    ComplianceRequirement,
    ItemStatus,
)

# ── Discovery: compose the project-specific checklist (COMPLIANCE §5) ──────────

def discover_requirements(project, user=None) -> list[ComplianceItem]:
    """On project creation, auto-compose the checklist by matching active library
    requirements against the project (work type / mine / site). De-duplicated by
    requirement. Each item records its source + confidence + mandatory flag."""
    created = []
    for req in ComplianceRequirement.objects.filter(is_active=True):
        if not req.applies_to(project):
            continue
        item, was_created = ComplianceItem.objects.get_or_create(
            project=project, requirement=req,
            defaults={
                "company": project.company, "category": req.category, "name": req.name,
                "source": req.source, "confidence": req.confidence,
                "is_mandatory": req.is_mandatory, "status": ItemStatus.MISSING,
                "created_by": user, "updated_by": user,
            },
        )
        if was_created:
            created.append(item)
    publish("ComplianceDiscovered", company=project.company, subject=project, actor=user,
            payload={"project": project.number, "items": len(created)})
    return created


# ── Item lifecycle ────────────────────────────────────────────────────────────

def submit_item(item, user, *, document=None, valid_from=None, expiry=None) -> ComplianceItem:
    item.status = ItemStatus.SUBMITTED
    item.document = document
    item.valid_from = valid_from
    item.expiry = expiry
    item.updated_by = user
    item.save(update_fields=["status", "document", "valid_from", "expiry",
                             "updated_by", "updated_at"])
    return item


def approve_item(item, user, *, valid_from=None, expiry=None) -> ComplianceItem:
    """Approve a compliance item (Safety Officer / manager). Recompute follows on
    the next readiness query — the gate is always live."""
    item.status = ItemStatus.APPROVED
    if valid_from is not None:
        item.valid_from = valid_from
    if expiry is not None:
        item.expiry = expiry
    item.approved_by = user
    item.approved_at = timezone.now()
    item.updated_by = user
    item.save(update_fields=["status", "valid_from", "expiry", "approved_by",
                             "approved_at", "updated_by", "updated_at"])
    _sync_project_gate(item.project, user)
    return item


def reject_item(item, user, *, reason="") -> ComplianceItem:
    item.status = ItemStatus.REJECTED
    item.notes = reason
    item.updated_by = user
    item.save(update_fields=["status", "notes", "updated_by", "updated_at"])
    _sync_project_gate(item.project, user)
    return item


# ── The computed Work Readiness gate (COMPLIANCE §1, §9) ──────────────────────

def recompute_readiness(project) -> dict:
    """Compute readiness live: per-category %, overall %, and the gate status.
    gate_status ∈ {ready, not_ready, overridden}. Non-mandatory items count
    toward the % but never block the gate."""
    items = list(project.compliance_items.all())
    overrides = {o.requirement_id for o in project.compliance_overrides.all()}
    whole_project_override = None in overrides  # a null-requirement override

    categories: dict[str, dict] = {}
    blocking = []
    for it in items:
        cat = categories.setdefault(it.category, {"total": 0, "satisfied": 0})
        cat["total"] += 1
        if it.is_satisfied:
            cat["satisfied"] += 1
        elif it.is_mandatory and not (whole_project_override or it.requirement_id in overrides):
            blocking.append(it)

    cat_pct = {
        c: (round(v["satisfied"] / v["total"] * 100) if v["total"] else 100)
        for c, v in categories.items()
    }
    total = len(items)
    satisfied = sum(v["satisfied"] for v in categories.values())
    overall = round(satisfied / total * 100) if total else 100

    if not blocking:
        gate = "overridden" if (whole_project_override or overrides) and satisfied < total \
            else "ready"
    else:
        gate = "not_ready"

    return {
        "overall": overall, "categories": cat_pct, "gate_status": gate,
        "blocking": [{"name": b.name, "category": b.category, "status": b.status,
                      "source": b.source} for b in blocking],
        "item_count": total,
    }


def can_start(project) -> bool:
    return recompute_readiness(project)["gate_status"] in ("ready", "overridden")


def _sync_project_gate(project, user=None) -> None:
    """Reflect the computed gate onto the Project status (pending_compliance ↔ ready).
    Never downgrades a project already in execution/complete."""
    from apps.projects.models import ProjectStatus
    if project.status in (ProjectStatus.IN_EXECUTION, ProjectStatus.COMPLETE,
                          ProjectStatus.CANCELLED):
        return
    ready = can_start(project)
    new = ProjectStatus.READY if ready else ProjectStatus.PENDING_COMPLIANCE
    if new != project.status:
        project.status = new
        project.save(update_fields=["status", "updated_at"])
        if ready:
            publish("ComplianceApproved", company=project.company, subject=project,
                    actor=user, payload={"project": project.number})


# ── Override — authorised + permanently audited (COMPLIANCE §10) ──────────────

@transaction.atomic
def override(project, user, *, reason, requirement=None) -> ComplianceOverride:
    """Open the gate past unmet compliance — ONLY with a reason, permanently
    audited. The gate never silently opens."""
    if not reason:
        raise ValueError("An override requires a reason.")
    ov = ComplianceOverride.objects.create(
        company=project.company, project=project, requirement=requirement,
        authorised_by=user, reason=reason, created_by=user, updated_by=user,
    )
    record_audit(company=project.company, user=user, action="compliance.override",
                 entity=project, after={"requirement": requirement.code if requirement else "*",
                                        "reason": reason})
    _sync_project_gate(project, user)
    return ov


# ── Continuous validation: the scheduled expiry sweep (COMPLIANCE §8) ─────────

def validate_expiries(*, within_days=30) -> dict:
    """Sweep approved items: flip any past expiry to EXPIRED (re-blocking their
    project), and report items expiring within `within_days`. Called on a Celery
    beat schedule and ad hoc. Uses all_objects — runs across tenants."""
    from apps.projects.models import Project

    today = timezone.localdate()
    horizon = today + timedelta(days=within_days)

    expired_project_ids = set()
    n_expired = 0
    for it in ComplianceItem.all_objects.filter(status=ItemStatus.APPROVED, expiry__lt=today):
        it.status = ItemStatus.EXPIRED
        it.save(update_fields=["status", "updated_at"])
        expired_project_ids.add(it.project_id)
        n_expired += 1

    upcoming = list(
        ComplianceItem.all_objects.filter(
            status=ItemStatus.APPROVED, expiry__gte=today, expiry__lte=horizon
        ).values("id", "project_id", "name", "expiry")
    )

    # Re-evaluate gates for projects that lost an item — each in its own tenant scope.
    for pid in expired_project_ids:
        project = Project.all_objects.get(id=pid)
        with tenant_scope(project.company_id):
            _sync_project_gate(project)

    return {"expired": n_expired, "reblocked_projects": len(expired_project_ids),
            "upcoming": upcoming}
