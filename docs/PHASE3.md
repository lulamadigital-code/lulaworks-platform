# LulaWorks Platform — Phase 3 (Procurement Intelligence)

Status: **complete.** Companion to PHASE1/PHASE2. Architecture source: prototype `docs/PROCUREMENT.md` (Module 6).

Turns awarded/quoted work into intelligent procurement: source the right materials from the right suppliers at the right price, with full traceability and controls.

## The chain
```
Supplier → Supplier RFQ (outbound) → Supplier Quote (in) → [feeds price ledger] →
Purchase Order (outbound, approval-gated) → GRN (goods received) →
Supplier Invoice → 3-way match → variances flagged before payment
```

## ⚠️ The two-PO distinction (kept, by design)
`PurchaseOrder` here is **outbound** (us → supplier), a *separate* entity from the inbound client award. They are never one model.

## App: `procurement`
| Model | Role |
|---|---|
| `Supplier` | categories, payment terms, banking (encrypted in prod), BEE, insurance expiry, **performance_score**, preferred |
| `SupplierPrice` | append-only **price-history ledger** → estimation + anomaly detection |
| `SupplierRFQ` / `SupplierRFQLine` | outbound request to a supplier |
| `SupplierQuote` / `SupplierQuoteLine` | supplier's response; confirming feeds the price ledger |
| `PurchaseOrder` / `POLine` | outbound order; lifecycle draft→pending→approved→sent→…→completed; money Golden-Rule gated |
| `GRN` / `GRNLine` | goods received (partial deliveries; qty received vs ordered) |
| `SupplierInvoice` | actual cost + the invoice leg of the 3-way match |

## Intelligence (services)
- **Price anomaly** (`price_anomaly`) — flags a quote deviating ≥25% from the item's historical average ("this bearing is 52% above your average").
- **Supplier performance** (`recompute_performance`) — weighted 0–100 from RFQ responsiveness (30), delivery completeness (40) and quality (30); updated as procurement events land.
- **3-way match** (`three_way_match`) — reconciles PO ↔ GRN ↔ supplier invoice; returns quantity and price variances; matched only when all agree.

## API
`/api/v1/suppliers/` (CRUD, `procurement.manage`), `/api/v1/purchase-orders/` (create `procurement.manage`; `POST …/approve/` gated by `po.approve`; `GET …/match/` = 3-way match). Supplier costs and PO money are **Golden-Rule gated** at the serializer.

## RBAC (seeded)
`procurement.manage`, `po.approve` added; granted to Operations Manager / Procurement Officer / Finance Manager as appropriate.

## Testing
**78 tests** (8 new): price-anomaly vs history, performance scoring, 3-way match (matched + quantity/price variance), PO numbering + total, approval permission gating, tenant isolation (cross-tenant PO → 404), Golden Rule (PO money hidden without `finance.view_money`). Ruff clean; container-validated.

## Note (fail-closed tenancy caught a real bug)
A DRF `PrimaryKeyRelatedField(queryset=Supplier.objects.all())` evaluated the tenant-scoped manager at import time (no tenant context) → the manager correctly raised. Fixed by accepting a UUID and resolving it scoped in the view — which also enforces cross-tenant 404.

## Next
Phase 4 — Estimating (cost engines, quotation builder, Pricing-Intelligence estimate-vs-actual loop), reusing the price ledger built here.
