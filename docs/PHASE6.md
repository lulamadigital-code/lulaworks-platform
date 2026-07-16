# LulaWorks Platform — Phase 6 (Project Execution & Operations)

Status: **complete.** Companion to PHASE1–5. Architecture source: prototype `docs/PROJECT_EXECUTION.md` (Module 9).

The operational core. Everything on site (people, equipment, materials, progress, actuals) lives in LulaWorks. **The insight that sets the architecture:** a task isn't "done/not-done" — its *readiness* is **computed from real-world dependencies** (predecessors, materials, compliance), exactly as Module 8 computes project readiness. Execution hangs off the `projects.Project` aggregate root created on award.

## App: `execution`
| Model | Role |
|---|---|
| `WorkPackage` | WBS — self-referential tree per project (unlimited depth) |
| `Task` | status, priority, **predecessors** (M2M), `blocks_on_compliance`, `material_po`, estimated/actual hours, progress |
| `Resource` | employee/equipment/vehicle/subcontractor + **compliance profile** (medical/induction/inspection expiry) + cost rate |
| `ResourceAllocation` | resource ↔ project/task over a date range; carries `override_reason` when forced |
| `Timesheet` | clock time → **actual labour hours/cost**; supervisor-approved |

## Computed task readiness (the core insight)
`compute_task_readiness` returns `(status, blocked_reason)` — a task is **READY** only when its **predecessors are complete**, the **compliance gate is open** (if required), and its **materials are delivered** (the linked PO's outstanding qty is 0). The reason is *computed*, never guessed: *"materials not delivered (PO-2026-00001)"*, *"project not compliance-ready"*, *"waiting on predecessor: Strip pump"*. Exposed **live** on the API (`readiness` field) so it's always current — the same way project readiness is live. `start_task` enforces it (the hard execution gate); completing a task recomputes successors + project progress.

## Compliance-aware resource allocation (the differentiator)
`allocate_resource` refuses (409) on a **double-booking** (*"Crane already allocated to PRJ-… [dates]"*) **or an expired credential** (*"medical expired 2026-01-01"*) — pulling the resource's compliance profile. A manager may `force=True` with an `override_reason` (audited event). The system won't silently mobilise someone who legally can't be on that mine.

## The actuals loop — closes Module 7 (the moat)
This module is the *source* of the actuals that calibrate future estimates. `capture_project_actuals` aggregates **labour** (from approved timesheets) and **material** (from supplier invoices on the project's POs, Procurement §9) and pushes them into the project's approved `Estimate` via `estimating.capture_actuals` → variance → **calibration advice** for the next estimate. Proven end-to-end: 120h logged → R54 000 actual labour vs R45 000 estimate → *"this work type historically exceeded labour by 20% — consider adjusting."*

## Project health + report split (Golden Rule)
- `project_health` — live composite (progress · compliance [reuses Module 8] · safety · quality, + **budget** which is **Golden-Rule gated** — omitted without `finance.view_money`).
- `daily_progress_report` — **customer** version shows progress + safety only; **internal** adds costs, labour hours, and blocked-task issues. Same Financial-Golden-Rule document split as Estimate/Quotation.

## API
`/api/v1/` — `tasks/` (+ `refresh`/`start`/`complete`), `work-packages/`, `resources/`, `resource-allocations/` (409 on conflict/compliance), `timesheets/` (+ `approve`). Project-level ops on `projects/{id}/`: `health/`, `progress-report/?audience=`, `capture-actuals/`. RBAC: `execution.manage`, `timesheet.approve` (seeded; granted to Operations Manager + Supervisor).

## Testing
**12 tests** (112 total): readiness reflects the compliance gate, predecessor blocking, material-PO blocking, start enforced by readiness, double-booking refused, expired-medical refused-then-forced, **actuals feed the estimate variance**, customer report hides cost/issues, health composite (budget omitted without finance perm), task-create permission gating, allocation-conflict 409, tenant isolation. Ruff clean; container-validated end-to-end (award → compliance-ready → material-gated task → GRN unblocks → start/complete → capture actuals → +20% calibration advice → health 96% → report split).

## Honestly deferred (noted, not hidden)
Full Operations Command Center aggregation, site diary, variations→budget auto-update, inspections workflow, equipment-utilisation actuals, and the mobile worker UI are scoped for later — the architectural spine (computed readiness, compliance-aware assignment, the actuals loop, health, report split) is built and tested.

## Next
Phase 7 — Finance (job costing, invoicing lifecycle, payments, the money dashboards) — then Phase 8, the AI Platform (Lulama orchestrator + agents) over everything built.
