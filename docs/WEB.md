# LulaWorks — Manager Web (Django + HTMX)

Status: **built & verified.** The office/manager surface of the platform.

## Why a second frontend
Two very different users, each served by the tool that fits:
- **Field workers** (on site, mobile) → the **Flutter** app (`mobile/`), over the JWT API.
- **Managers/admin** (data-heavy dashboards) → **server-rendered Django HTML + HTMX** (`backend/apps/web/`).

DOM-native HTML is the better fit for the manager surface: fast first paint, real tables, keyboard/copy-paste, accessibility — where a canvas app fights you. Both frontends call the **exact same services**, so readiness, health and profitability have one source of truth.

## App: `apps/web` (no models)
Session-authenticated (email/password via Django's `ModelBackend`), separate from the JWT API. The ambient `TenantMiddleware` already binds the tenant from `request.user` for session requests, so `Project.objects.all()` is tenant-scoped in views with no extra wiring.

| Route | View |
|---|---|
| `/login/`, `/logout/` | session auth |
| `/` | **Operations dashboard** — portfolio tiles, compliance attention list, and (finance only) the commercial panel |
| `/projects/`, `/projects/<id>/` | projects table + **project detail** (Work Readiness gate, health, and finance-only profitability + forecast + budget-vs-actual) |
| `/projects/<id>/readiness/` | HTMX partial — live re-render of the gate card |
| `/estimates/`, `/estimates/<id>/` | **estimates** list + detail (cost sections/lines, margin, risk — finance-gated); **approve** action (`estimating.approve`) |
| `/suppliers/`, `/purchase-orders/`, `/purchase-orders/<id>/` | **procurement** — suppliers by performance, POs, PO detail with **3-way match** panel + **approve** (`po.approve`) |
| `/commercial/` | **commercial** — portfolio money, aging buckets, retention, loss-making, invoices (`finance.view_money`) |
| `/work/?view=…` | **Work Management Engine** (Module 8) — one dataset, five lenses: List · Board · Table · Calendar · Workload |
| `/work/new/` | **Universal New Work wizard** — every origin (RFQ, manual, project, customer request, recurring, internal, breakdown, preventative) through one front door |
| `/work/<id>/` | **Per-work dashboard** — progress, computed blockers, subtasks/checklist, team by role, typed dependencies, conversation, files, compliance |
| `/notifications/` | **Inbox** — in-app notifications, unread badge in the sidebar |
| `/lulama/` | **LulaAI** — ask → one consolidated draft (grounded agent cards + confidence + governance proposals) (`ai.generate`) |

See [MODULE8.md](MODULE8.md) for the engine itself.

## Guarantees (reused, not re-implemented)
- **Financial Golden Rule** — the commercial/profitability blocks are computed and rendered only for users with `finance.view_money`. Test-enforced (`GoldenRuleTests`): an ops user gets the operational view; a finance user additionally sees the money.
- **Tenant isolation** — a project from another company `404`s (the fail-closed manager). Test-enforced.
- **One source of truth** — views call `recompute_readiness`, `project_health`, `profitability`, `profit_forecast`, `budget_vs_actual`, `commercial_dashboard` — the same services the API and Flutter app use.

## Scrolling & small screens
- `scroll-behavior: smooth` with a `prefers-reduced-motion` opt-out.
- Scrollbars are styled thin-but-visible, so a long page *looks* scrollable.
- `overscroll-behavior: contain` on the Kanban board, the nav strip and the
  filter bar — a sideways-scrolling pane must never swallow the page's vertical
  scroll.
- **Back to top** on long pages: a plain `#top` anchor (works with JS disabled);
  `web/app.js` only fades it in past 400px on pages long enough to warrant it.
  It deliberately avoids `requestAnimationFrame`, which is throttled in
  background/headless tabs and would leave the control stuck hidden.
- **Under 820px the sidebar used to be `display:none`, leaving phones with no
  navigation at all.** It is now a sticky, horizontally-scrollable top strip.

## Frontend deps
- **htmx** is **vendored** (`apps/web/static/web/htmx.min.js`, served by WhiteNoise) — no runtime CDN, consistent with container-first / CSP.
- Styling is a small hand-written CSS design system embedded in `base.html` — zero external requests.

## Testing
**5 tests** (148 total): auth gate (unauth → login), dashboard renders, **Golden Rule money hidden from non-finance user** (dashboard + detail), cross-tenant `404`, HTMX readiness partial renders. `manage.py check` clean; ruff clean; container-validated.

## Verified live (2026-07-17)
Driven in-browser against the running backend (same origin, `:8000`): session login → **Operations dashboard** (1 project, commercial panel R63 525 revenue / 14.99% margin, "needs attention" PRJ-2026-00001 · 50% · 2 blocking) → **project detail** (Work Readiness gate 50% with Refresh, health 59%, profitability + forecast + budget-vs-actual table, compliance checklist). Real DOM, real tables — the manager experience the split was chosen for.

## Run
```bash
docker compose up -d
docker compose exec api python manage.py collectstatic --noinput   # htmx via WhiteNoise
# open http://localhost:8000/login/
```
