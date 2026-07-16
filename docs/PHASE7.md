# LulaWorks Platform — Phase 7 (Finance, Commercial & Payments)

Status: **complete.** Companion to PHASE1–6. Architecture source: prototype `docs/FINANCE.md` (Module 10).

Closes the money loop. Manages the commercial lifecycle of every project — first cost to last payment — so managers always know whether a project is making or losing money, answering one question at any moment: ***"Are we making or losing money, and why?"***

## ⚠️ The boundary (owner instruction)
This is **operational project finance** (budgets, costs, margins, claims, profitability). It is **NOT** a general ledger and does not replace Sage/Xero/QuickBooks — no chart-of-accounts, no trial balance, no tax submissions. It *integrates* with them later via `integrations`. This boundary prevents scope creep into a domain accounting packages already own.

## App: `finance`
| Model | Role |
|---|---|
| `CostCode` | hierarchical, company-configurable coding |
| `ProjectBudget` + `BudgetLine` | the baseline, auto-created from the approved Estimate on award — category cost lines + revenue + expected margin |
| `CostEntry` | **the convergence point** — actual costs from every module, coded by project (+ cost code / work package); auto-sourced entries upsert by (project, category, source) |
| `Invoice` + `InvoiceLine` + `Payment` | lifecycle draft→issued→…→paid; VAT; **retention first-class**; progress claims; POP |
| `Variation` | commercial view of the M9 variation — value + budget impact on approval |

All money is **Golden-Rule gated**.

## The money loop closing (§3-4)
Actuals **converge automatically** from the operational modules into the CostEntry ledger:
- **labour** from approved timesheets (Execution §5),
- **material** from supplier invoices on the project's POs (Procurement §9).

`create_budget_from_estimate` (auto-run on award) turns the approved estimate into the baseline; `rebuild_actuals_from_sources` pulls the live actuals; `budget_vs_actual` shows per-category variance. Estimate → Procurement → Execution → **Finance** — one coded ledger, no orphan money.

## Live Profitability Engine (§9)
`profitability` — revenue, actual cost, gross profit, margin, budget variance, live. No exporting to spreadsheets to understand performance.

## Project Profit Predictor (§10 — the early-warning moat)
`profit_forecast` continuously forecasts the **final** outcome from current trend and **explains itself, citing the data**:
> *"At 50% complete, 106% of budget consumed. Projected final cost R108 000 vs budget R50 820; projected margin −70% vs planned 20%. Largest contributors: labour (+R31 500)."* → verdict **at risk**.

Managers get time to act *before* a project turns unprofitable — visibility traditional contractor systems lack.

## Invoicing / retention / progress claims (§7-8)
Progress claims bill the delta of % complete against the contract value, with **retention held back** (first-class, essential for mining/construction). Payments update status (partially_paid → paid) and outstanding. **Sending is human-approved** — `issue_invoice` marks issued and audits it; nothing auto-sends to a customer.

## Variations (§6 — one entity, two views)
`approve_variation` (customer approval is external → human-approved) **auto-updates the budget**: revenue grows by the variation's revenue impact and a category cost line is added.

## Commercial dashboard (§11)
`commercial_dashboard` — portfolio revenue/cost/margin, **loss-making projects**, outstanding invoiced + aged buckets (current/30/60/90+), and **outstanding retention**. Gated by `finance.view_money`.

## API
`/api/v1/` — `invoices/` (+ `issue`, `record-payment`), `variations/` (+ `approve`), `cost-codes/`, `cost-entries/`, `finance/commercial-dashboard/`. Project-level (all require `finance.view_money`): `projects/{id}/` `create-budget`, `budget`, `profitability`, `profit-forecast`. RBAC: `finance.manage` (new) + reused `invoices.approve` / `finance.view_money`; granted to Finance Manager.

## Testing
**11 tests** (123 total): budget from estimate on award, actuals convergence (execution + procurement), live profitability, **profit predictor flags an overrun with contributors**, retention + payment lifecycle, progress-claim delta billing, variation budget update, commercial dashboard flags the loss-maker, profitability endpoint Golden Rule (worker 403), invoice money hidden from worker, tenant isolation. Ruff clean; container-validated end-to-end (award auto-budget → converged actuals → 15% live margin → "at risk" forecast → progress claim w/ retention → payment → variation grows budget → dashboard → worker 403).

## Note (a test-count correction)
Verified totals are cumulative and exact: Phase 4 = 91, Phase 5 = 100, Phase 6 = 112, **Phase 7 = 123**. (Earlier phase summaries mis-stated the running total by double-counting a delta; the per-phase additions were always right. This doc uses the verified 123.)

## Next
Phase 8 — the AI Platform (Lulama orchestrator + agents) over everything built: grounded, metered, deterministic-first, human-approval-gated for all external actions.
