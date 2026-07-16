# LulaWorks Platform — Phase 1 (Platform Foundation)

Status: **in progress.** Fresh production build (`lulaworks-platform`), container-first, per the Master Implementation Directive. Companion: `docs/DEPLOYMENT.md`; architecture source-of-truth in the prototype repo's `docs/` (Modules 1–13).

## Architecture summary
Django + DRF modular monolith; bounded-context apps under `backend/apps/`, communicating via service interfaces + a domain event bus (transactional outbox). Ambient multi-tenancy, JWT auth, granular RBAC, and the Financial Golden Rule are cross-cutting in `apps.core`. Container-first: one image runs api/worker/beat; Postgres/Redis/S3/SES are external.

## Apps & responsibilities
| App | Delivers |
|---|---|
| `core` | UUID/audit/soft-delete base models; ambient `TenantContext` + fail-closed `TenantManager`; `TenantViewSet` + Golden-Rule serializer mixin; `DomainEvent` outbox + event bus; pagination/error-envelope |
| `identity` | `Company` (tenant); email-login `User`; `Membership` (multi-company); `Permission` + `Role` engine (no `is_admin`); JWT |
| `administration` | immutable `AuditLog`; `CompanySettings`; configurable `NumberingRule` + atomic `NumberSequence`; `FeatureFlag` (defn/override + resolution); `NotificationPreference` |
| `billing` | `Plan` (configurable data product) + `Subscription`; entitlement engine (allow/warn/block graceful degradation) |
| `storage` | `StorageFile` (metadata; S3 blobs in prod) + quota enforcement |
| `ai_platform` | `AICreditLedger` (append-only) + `AIUsageLog`; AI Gateway (provider interface + metered `run_metered`, fails closed). Concrete LLM adapters: Phase 8 |

## Database design (standards, DATA_MODEL §2)
Every business entity: **UUID pk · company (tenant) · created/updated at+by · soft-delete**. Append-only logs (`AuditLog`, `AIUsageLog`, `DomainEvent`) are separate, immutable, partition-ready. `company_id` leads composite indexes.

## Business rules enforced
- **Tenant isolation (fail-closed):** no tenant in context → query raises; cross-tenant object access 404s. Verified by tests.
- **RBAC:** access via `user.has_perm_code("<module>.<action>")`; superuser bypasses; **no `is_admin`**.
- **Financial Golden Rule:** money fields stripped from serializers without `finance.view_money`.
- **Graceful degradation:** limits inform + offer upgrade, never hard-fail.
- **AI metering:** every AI call debits the credit ledger; fails closed with no credits.

## API
`/api/v1/` (versioned). Auth: `POST /api/v1/auth/token/` (+ `/refresh/`, `/logout/`). OpenAPI at `/api/v1/schema/`; Swagger at `/api/v1/docs/`. Base `TenantViewSet` auto-scopes and re-checks ownership.

## Testing summary
**33 tests pass** (local + in-container): tenant isolation & soft-delete, RBAC, JWT, health, numbering, feature flags, audit, entitlement, storage quota, AI credit ledger. Ruff clean. CI (`.github/workflows/ci.yml`) runs ruff + migration-check + tests on Postgres/Redis services, and builds the image.

## Deployment
Container-first — see `docs/DEPLOYMENT.md`. Validated: `docker compose up` → api/worker/beat/db/redis healthy, JWT over HTTP, tests pass in-container.

## Known limitations / next in Phase 1
- Concrete AI provider adapters (Claude/OpenAI/Gemini) — Phase 8.
- pgvector (Project-DNA/Knowledge embeddings) deferred — not needed until Phase 2.
- Identity/company management **REST endpoints** (viewsets) not yet exposed — only auth + admin so far.
- JWT carries the user; active company resolved from `User.active_company` (company-switch claim is a later refinement).
- Then: the thin RFQ→quote vertical slice (proves the stack end-to-end) before Phase 2.

## Future improvements (tracked)
Partition the append-only tables at scale; SSO auth adapters; S3/SES wiring in prod settings; mypy in CI; per-tenant rate-limit tuning.
