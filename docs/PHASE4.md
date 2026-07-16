# LulaWorks Platform — Phase 4 (Estimating & Quotation Intelligence)

Status: **complete.** Companion to PHASE1/2/3. Architecture source: prototype `docs/ESTIMATING.md` (Module 7).

Generalises the flat quote into a **structured cost estimate** and adds the **Pricing-Intelligence learning loop** — the estimating moat. LulaWorks prepares the cost build-up; the estimator reviews, adjusts, approves. Nothing auto-approves.

## ⚠️ The internal/external split (Financial Golden Rule at the document boundary)
| | `Estimate` (this app) | `Quotation` (quotes app) |
|---|---|---|
| Audience | admin / manager / estimator | the customer |
| Contains | full cost build-up, supplier costs, markup, **margin**, risk | scope + **selling price only** |
| Golden Rule | money visible to `finance.view_money` holders | derived; **never exposes cost, markup or margin** |

`generate_quotation()` derives the external quotation from an **approved** estimate, distributing the selling price across sections — cost/markup/margin are never copied. The `Quotation` model structurally has no cost fields (test-enforced).

## App: `estimating`
| Model | Role |
|---|---|
| `Estimate` | internal, **versioned**; markup/discount/contingency dials; risk score + flags; computed `direct_cost → total_cost → selling_price → margin_pct` |
| `EstimateSection` | cost category (labour / material / equipment / subcontractor / indirect) |
| `EstimateLine` | cost build-up line + **provenance** (`source`, `confidence`, `source_ref`, lead time) — the Confidence-Engine pattern |
| `EstimateActual` | Pricing-Intelligence loop: actuals captured at closeout → `variance` / `variance_pct` |

## Intelligence (services)
- **Cost engines (deterministic-first):** `propose_material_lines` prices items straight off the Procurement **price ledger** (latest supplier price + confidence; flags items with no history); `propose_labour_line` applies a **calibration factor learned from history**.
- **Risk scoring** (`recompute_risk`) — 0–100 from thin margin, low-confidence lines, long-lead items, unpriced lines; produces human-readable flags.
- **Approval gate** (`approval_required`) — margin/discount thresholds (reads `CompanySettings.approval_rules`, else defaults: margin < 15% or discount > 10% → needs `estimating.approve`).
- **Version control** (`create_revision`) — deep-copies to a new version, marks the prior **SUPERSEDED**; history is permanent, never overwritten. Same number across revisions (`unique (company, number, version)`).
- **Pricing-Intelligence loop** (`capture_actuals` → `labour_calibration` / `calibration_advice`) — estimate → actuals → variance → advice that calibrates the next estimate ("this work type historically exceeded labour estimate by 20% — consider adjusting"). **This is what makes LulaWorks estimate better the longer a tenant uses it.**

## API
`/api/v1/estimates/` (CRUD, `estimating.manage`) plus actions:
`POST …/submit/` (margin-gate routing), `POST …/approve/` (`estimating.approve`), `POST …/revise/`, `POST …/generate-quotation/` (price-only), `GET|POST …/actuals/` (capture + advice). All estimate money is **Golden-Rule gated** at the serializer.

## RBAC (seeded)
`estimating.manage`, `estimating.approve` added; new **Estimator** role template; granted to Operations Manager / Finance Manager / Estimator as appropriate.

## Testing
**13 tests** (91 total): cost build-up + price/margin derivation, material engine off the ledger (priced + missing-price flag), risk scoring, approval gate (thin vs healthy margin), version control (never overwrites), **quotation exposes price only — no cost/markup/margin** (Golden Rule at doc boundary), actuals variance + labour calibration, API number allocation + price, approval permission gating, Golden Rule (cost hidden without `finance.view_money`), tenant isolation (cross-tenant → 404). Ruff clean; container-validated end-to-end (estimate → approve → quotation → actuals → calibration advice).

## Note (a fail-closed/typing catch)
Field defaults return Python `int` before a DB round-trip, so `0/100` became a `float` and broke `Decimal` arithmetic — the price properties now coerce the percentage dials to `Decimal`. Caught by running the tests, not in production.

## Next
Phase 5 — Compliance Intelligence (safety files, permits, certificate expiry, the compliance gate before execution).
