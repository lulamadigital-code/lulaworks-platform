# LulaWorks Platform — Phase 5 (Compliance Intelligence — the signature module)

Status: **complete.** Companion to PHASE1–4. Architecture source: prototype `docs/COMPLIANCE.md` (Module 8).

The mindset that sets the architecture: not *"do we have a safety file?"* but ***"can this project legally and safely start today?"*** Compliance is an **operational-readiness platform**, not a document store — a **computed gate**, re-evaluated continuously, that decides whether a project may enter execution.

## The readiness gate (the core)
```
mandatory ComplianceItems all satisfied (approved + unexpired)  OR  authorised override
        →  Work Readiness = pass  →  project may enter execution
```
Readiness is **computed on demand** — never a stale snapshot — so it is always current when queried, and reflected onto the Project status (`pending_compliance ↔ ready`).

## New app: `projects` (the execution aggregate root)
`Project` is created when a Quotation is **awarded** (`award_quotation`), fires `ProjectCreated`, and starts `pending_compliance`. This is the root that Phase 6 (Execution) will extend with tasks and field ops. The compliance gate is a **hard execution gate** on it.

## App: `compliance`
| Model | Role |
|---|---|
| `ComplianceRequirement` | per-tenant **library**; `applies_when` (work type / mine / site) drives discovery; source + confidence + mandatory + expiry horizon |
| `ComplianceItem` | a requirement instantiated against a project; status (missing→pending→submitted→approved→rejected→expired), document, valid_from/expiry; `is_satisfied` = approved & unexpired |
| `ComplianceOverride` | authorised passage past the gate — **immutable, permanently audited** (null requirement = whole-project) |

## Intelligence (services)
- **Discovery** (`discover_requirements`) — on award, auto-composes a **project-specific** checklist by matching the active library against the project; de-duplicated; each item records **source + confidence** (Confidence-Engine pattern).
- **Computed readiness** (`recompute_readiness`) — per-category %, overall %, and gate status ∈ {ready, not_ready, overridden}; lists exactly what's **blocking**. Non-mandatory items count toward % but never block.
- **Continuous validation** (`validate_expiries` + `compliance_sweep` command) — a scheduled sweep (Celery beat) flips lapsed certificates to EXPIRED and **re-blocks** their projects; reports upcoming expiries for the alert engine. Cross-tenant, each project re-evaluated in its own tenant scope.
- **Override** (`override`) — opens the gate past unmet compliance **only** with a reason, written to the immutable audit log. The gate never silently opens.

## API
- `/api/v1/projects/` — award (`projects.create`), `GET …/readiness/` (the live gate), `GET …/compliance/` (checklist), `POST …/override/` (`compliance.override`).
- `/api/v1/compliance-requirements/` (library, `compliance.manage`) · `/api/v1/compliance-items/?project=` with submit / approve / reject (approve+reject gated by `compliance.override`).

## RBAC (seeded)
`compliance.manage` added (alongside existing `compliance.override`); granted to Safety Officer + Operations Manager. `seed_compliance_library` seeds a starter SA-mining requirement set per company.

## Testing
**9 tests** (109 total): discovery composes a project-specific checklist (work-type filtering — hot-work permit in, working-at-heights out), readiness not-ready → ready, permit-missing keeps the gate closed, **expiry sweep re-blocks a ready project**, override opens the gate **and is audited**, override requires a reason, award API + readiness, override permission gating, tenant isolation (cross-tenant project → 404). Ruff clean; container-validated end-to-end (award → 0% → approve → 100% ready → hot-work permit lapses → sweep re-blocks → authorised override → ready).

## Note (fail-closed tenancy caught the cross-tenant sweep)
`validate_expiries` runs across tenants on `all_objects`, but re-evaluating each project's gate touches tenant-scoped relations — the fail-closed manager raised until each project's re-eval was wrapped in its own `tenant_scope`. (`timezone.timedelta` was also a typo for `datetime.timedelta`.) Both caught by running the tests.

## Next
Phase 6 — Project Execution (tasks, job cards, field ops, worker mobile flow) on the `projects` root — unlocked only once the compliance gate reads `ready`/`overridden`.
