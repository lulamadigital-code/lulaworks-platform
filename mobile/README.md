# LulaWorks — Mobile Client (Flutter)

A cross-platform (Android / iOS / web) client for the LulaWorks Contractor
Operating System. It talks to the Django REST API over JWT and surfaces the core
operating loop: sign in → projects → the **compliance readiness gate** → **Lulama**,
the AI Operations Director.

Flutter 3.19+ / Dart 3.3+ · Material 3 · deps: `http`, `shared_preferences`.

## What's here
| Screen | Backend it drives |
|---|---|
| **Login** | `POST /api/v1/auth/token/` (JWT). Server origin is editable so a device can point at a LAN IP or a deployed environment. |
| **Projects** | `GET /projects/` — status chips (pending compliance / ready / in execution). |
| **Project detail** | `GET /projects/{id}/readiness/` + `/compliance-items/` — the **Work Readiness gate** (per-category %, overall %, blocking items) and the compliance checklist. |
| **Lulama** | `POST /ai/interactions/ask/` — one consolidated draft: agent cards (summary + confidence), proposed actions flagged *needs approval* (governance), and agents withheld by your permissions (the security model). |

The client is **RBAC-graceful**: money endpoints that return `403` (Golden Rule)
degrade quietly; the app only shows what the signed-in user is allowed to see —
the backend remains the source of truth for tenancy and permissions.

## Architecture
- `lib/api/api_client.dart` — JWT bearer, one-shot refresh on `401`, error-envelope
  parsing, and **explicit UTF-8 decoding** of response bytes (the `http` package
  otherwise falls back to Latin-1 when a response has no charset, mangling `—` etc.).
- `lib/api/config.dart` — per-platform default origin (Android emulator `10.0.2.2`,
  iOS/web `localhost`), overridable at login.
- `lib/models.dart` — lightweight view models (money fields may be absent).
- `lib/screens/*` — login, projects, project detail, Lulama.

## Run it against a local backend
```bash
# 1) Backend up (from repo root) — see ../docs/DEPLOYMENT.md
docker compose up -d
docker compose exec api python manage.py seed_platform      # roles, plans, prompts
# create a company + user, then:

# 2) The client
cd mobile
flutter pub get
flutter run -d chrome        # or an Android/iOS device/emulator
```
On the login screen set **Server** to your backend origin:
- Flutter web / iOS simulator -> `http://localhost:8000`
- Android emulator -> `http://10.0.2.2:8000`
- Real device -> `http://<your-lan-ip>:8000`

> CORS: the backend allows `http://localhost:3000` by default; add your web origin
> to `CORS_ALLOWED_ORIGINS` (`.env.docker`) if you serve the web build elsewhere.

## Verify
```bash
flutter analyze      # clean
flutter test         # boots to the sign-in screen (smoke test)
flutter build web    # compiles
```

## Scope
This is a focused client over the core loop, not every one of the platform's
16 backend apps. Procurement, estimating, execution tasks, finance dashboards and
the AI decision/approve action are all reachable via the same `ApiClient` and are
natural next screens.
