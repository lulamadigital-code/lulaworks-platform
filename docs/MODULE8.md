# Module 8 — Work Management Engine

The operational heart of LulaWorks. **Everything is Work.** Work enters through
many doors, but every piece of it flows through one engine — one hierarchy, one
lifecycle, one team model, one dependency model, one comment/file/notification
surface. A two-person electrical shop and a mine shutdown use the same primitives.

This is deliberately **not** a generic task board. The concepts that make it a
contractor tool are the compliance gate, the typed real-world dependencies, and
progress that is derived from what the crew ticked off rather than typed in.

## Hierarchy

```
Company → Workspace → Work ─┬─ Standalone Work
                            └─ Project → Phase → Task → Subtask → Checklist item
```

Every company gets a default **Workspace** ("General") created on demand, so a
small business never has to think about the concept while an enterprise
contractor can segment thousands of concurrent work items.

## Work origins (8)

`rfq` · `manual` · `project` · `customer_request` · `recurring` · `internal` ·
`breakdown` · `preventative`

Origin is the **only** real difference between jobs. It is recorded on every work
item and drives reporting, but never a separate code path.

## Lifecycle

`draft → ready → assigned → accepted → in_progress → waiting → blocked →
quality_check → client_signoff → completed → closed` (plus `cancelled`).

- **Computed, not clicked.** `compute_task_readiness()` derives `ready` vs
  `blocked` from typed dependencies + the project compliance gate + material
  delivery. `blocked_reason` always names the real cause.
- **The engine advises, it never overrules.** States a human deliberately chose
  (`waiting`, `quality_check`, `client_signoff`, `accepted`) and terminal states
  are left alone by `refresh_task_status()`.
- Companies customise labels/colours via `StatusDefinition`; the canonical keys
  above stay as what the services reason about.

## Typed dependencies

Classic scheduling links (`fs`, `ss`, `ff`, `sf`) sit alongside the waits a
contractor actually hits: `blocked_until`, `waiting_approval`,
`waiting_delivery`, `waiting_compliance`. Each renders as plain English —
*"waiting for delivery on Supplier delivery"* — so "why is this blocked?" always
has a real answer. `link_tasks()` refuses circular dependencies.

## Team model

Work is never limited to one assignee. Four roles via `Assignment`:

| Role | Can |
|---|---|
| **Owner** (one) | Accountable for the work |
| **Execution team** | Perform, update progress, upload, complete |
| **Watcher** | Notified only — cannot modify (`can_modify()` enforces this) |
| **Approver** | Sign off completion, compliance, commercial milestones |

## Progress roll-up

Checklist → subtask → task → phase → project. `rollup_progress()` derives the
percentage from ticked items; a subtask auto-completes when its checklist is
done. **Nobody types a percentage.**

## Views

One dataset, five lenses — `?view=` changes presentation, never the data
(`_filtered_work()` is the single filter pipeline):

**List** · **Board** (kanban, 6 lanes) · **Table** · **Calendar** (by due date) ·
**Workload** (who is carrying what, plus unassigned open work)

Timeline/Gantt and Map are deliberately deferred — see *Not built yet*.

## Automations

`AutomationRule` = trigger + conditions + action, configured per company, never
hard-coded. Actions are narrow by design: notify owner/approvers/watchers,
recompute successor readiness, set status. **Automations move information — they
never approve, award, send or pay.** That is the platform's human-approval
boundary, unchanged here.

## Notifications

One `Notification` row per person per event, fanned out by `notify_team()` to
everyone attached to the work (watchers included). The actor is never notified of
their own action. Unread count surfaces as the **Inbox** badge in the sidebar.

## Permissions (granular, on top of RBAC)

`work.create` · `work.edit` · `work.delete` · `work.assign` · `work.approve` ·
`work.close` · `work.files`

`execution.manage` is the **umbrella** that implies all of them, so existing
roles keep working (`has_work_perm()`). Sign-off and closure are held to a higher
permission than an ordinary edit (`_TRANSITION_PERM` in the web views).

## Routes

| Route | What |
|---|---|
| `/work/?view=…` | The five views, shared filters |
| `/work/new/` | The universal New Work wizard (5 steps, every origin) |
| `/work/<id>/` | Per-work dashboard — progress, blockers, hierarchy, team, dependencies, conversation, files, compliance |
| `/work/<id>/status/` | Lifecycle transition |
| `/work/<id>/{subtasks,checklist,comments,files,team,link}/` | Action endpoints |
| `/work/<id>/checklist/<item>/toggle/` | Tick — rolls progress up |
| `/notifications/` | Inbox |
| `/projects/<id>/phases/` | Add / seed default phases |

## Data migration

`0003` adds the engine models and replaces the plain `predecessors` M2M with the
typed `TaskDependency` **through** model. Django cannot add `through=` in place,
so existing links are copied across first (`carry_dependencies`) before the old
table is dropped — losing dependencies would silently unblock real work.
`0004` maps the old vocabularies onto the new lifecycle (`planned→draft`,
`on_hold→waiting`, `awaiting_inspection→quality_check`, `normal→medium`), gives
every company a default workspace, and seeds the default status set.

## Tests

`apps/execution/tests.py::WorkEngineTests` — 13 tests covering: standalone work
is not compliance-gated, all 8 origins flow through one engine, checklist
roll-up, subtask auto-completion, typed dependency phrasing, start-to-start
semantics, circular-dependency refusal, the team/watcher boundary, actor-excluded
notification fan-out, lifecycle transitions not being overruled, automations
notifying approvers without approving, **project work still honouring the hard
compliance gate**, phase seeding idempotence, and portfolio reporting.

Suite: **175 tests, all passing** in the container.

## Not built yet (deliberate)

Timeline/Gantt and Map views; drag-and-drop on the board (the lanes are
server-rendered, no JS); S3 file storage (files use Django's `FileField` — the
`storage` app is where S3 lands); email/push/SMS delivery (the `Notification`
row is the substrate, only in-app is wired); voice notes, video capture, GPS and
client signatures; offline sync; recurring-schedule generation (the `recurring`
and `preventative` origins exist, the scheduler does not).


---

# LulaAI work decomposition

`apps/ai_platform/decomposition.py` — describe a job, get a plan to review.

## Grounded before generated

The grounding order is the whole design, and it is deliberately not
"ask the model":

1. **The company's own completed work.** `_similar_completed_work()` matches the
   job title against past `completed`/`closed` tasks (tenant-scoped, so a company
   can only ever learn from itself). `_checklist_from_history()` keeps the steps
   that recur across **two or more** past jobs — an item used once is noise, one
   used repeatedly is how this crew actually works. `_hours_from_history()` takes
   the **median actual hours**, which beats any estimate.
2. **The contractor pattern library** (`WORK_PATTERNS`) — gearbox, pump, conveyor,
   electrical, inspection, shutdown, welding. The fallback for work the company
   has no history of, not an attempt to encode every job on earth.
3. **A generic skeleton** at low confidence, so the answer is never a blank page.

Only then, if a provider is configured and the user holds `ai.generate`, an LLM
may **add** up to four steps and three risks and write a short briefing. It never
replaces grounded content, and every failure path (no key, no credits, bad JSON,
provider down) leaves the deterministic draft standing.

Every draft carries `source`, `confidence` and `grounded_in`, and the review
screen shows them — an estimate a person is about to accept is never a black box.

## The approval boundary, applied to planning

- `propose_decomposition()` **performs no writes at all**. Ask as often as you like.
- `apply_decomposition()` creates **only** the indexes a human ticked. Passing no
  selection creates nothing — refusing to guess is the point.
- The apply view recomputes the draft server-side (it is deterministic) and
  trusts the client for nothing except *which items were approved*.
- Compliance, resources and risks are advisory display only. LulaAI never creates
  or approves a compliance record; that stays with a person, per `governance.py`.
- Both the proposal and the acceptance are written to `AIInteraction`
  (`draft` → `approved`), so every AI-influenced plan is traceable afterwards.

| Route | View |
|---|---|
| `/work/<id>/decompose/` | proposal + provenance + per-item tick boxes (`ai.generate`) |
| `/work/<id>/decompose/apply/` | creates only the ticked items (`work.edit` + `ai.generate`) |

## Tests

`apps/ai_platform/test_decomposition.py` — 12 tests: propose writes nothing; an
empty selection creates nothing; only ticked indexes are created; hours are set
only when asked; the library matches work type; unmatched work degrades to
generic at low confidence; **own history outranks the library and supplies the
estimate**; **history never leaks across tenants**; provider failure keeps the
grounded draft; LLM suggestions are appended, never substituted; enrichment
requires `ai.generate`; and a proposal stays `draft` until applied.

Suite: **187 tests, all passing** in the container.
