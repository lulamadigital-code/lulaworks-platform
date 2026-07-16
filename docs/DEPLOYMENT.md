# LulaWorks — Container-First Deployment

**Core architectural requirement:** LulaWorks is a **container-first** application. All application services run as Docker containers, configured **only** through environment variables, with **no persistent data inside containers**. The same setup runs locally (docker compose) and in production (Amazon ECS) **without code changes**.

---

## 1. Principles

- **12-factor config:** every setting comes from environment variables (`python-decouple` reads `os.environ`). No config baked into images; no secrets in the repo or images (`.env*` are gitignored and `.dockerignore`d).
- **Stateless app containers:** API/worker/beat hold no state. All persistent data lives in **external services**: PostgreSQL, Redis, S3, SES. Uploaded files go to **S3** (never the container filesystem); static files are served by **WhiteNoise** from the image.
- **One image, many commands:** a single image runs the API, the Celery worker, and Celery beat — they differ only by their start command. This guarantees they never drift apart.
- **External backing services in production:** Postgres → **RDS**, Redis → **ElastiCache**, files → **S3**, email → **SES**, secrets → **Secrets Manager**. These are *not* application containers.

## 2. Services

| Service | Command | Prod backing service |
|---|---|---|
| **api** | `gunicorn config.wsgi:application` | ECS Fargate task (behind ALB) |
| **worker** | `celery -A config worker` | ECS Fargate task |
| **beat** | `celery -A config beat` | ECS Fargate task (single) |
| db *(dev only)* | postgres container | **Amazon RDS PostgreSQL** |
| redis *(dev only)* | redis container | **Amazon ElastiCache** |

In production the `db` and `redis` containers are **not deployed** — the app points at RDS/ElastiCache via env vars.

## 3. Docker architecture

- **`backend/Dockerfile`** — `python:3.13-slim`, installs `requirements.txt`, runs as a **non-root** user, exposes 8000. **No `manage.py` at build time** (no secrets/DB available during build). `ENTRYPOINT` waits for the DB, then execs the service command; default `CMD` is gunicorn.
- **`backend/entrypoint.sh`** — waits for Postgres (`DB_HOST:DB_PORT`) up to 60s, then `exec "$@"`. Migrations/`collectstatic` are **explicit**, not implicit on every start (so parallel ECS tasks don't race).
- **`backend/.dockerignore`** — excludes `.env*`, caches, `staticfiles/`, `media/`, `.git/`.

## 4. Local development (docker compose)

```bash
cp .env.docker.example .env.docker      # then set a real SECRET_KEY
docker compose build
docker compose up
# API → http://localhost:8000/health/   ·   Swagger → /api/v1/docs/
```
- `docker-compose.yml` runs **db, redis, api, worker, beat**. `db`/`redis` are dev-only, with **named volumes** (`postgres-data`, `redis-data`) so data survives restarts — data lives in volumes, **never in app containers**.
- The **api** service command runs `migrate` + `collectstatic` + `gunicorn` for convenience in dev.
- Config comes from `.env.docker` (service hostnames `db`/`redis`, not localhost).

One-off commands:
```bash
docker compose run --rm api python manage.py createsuperuser
docker compose run --rm api python manage.py test
```

## 5. Production deployment workflow (Amazon ECS)

1. **Build & push** the image to ECR (CI): `docker build -t <ecr>/lulaworks-api backend/ && docker push …` (one image for all three services).
2. **Provision external services** (Terraform, `infra/`): RDS PostgreSQL, ElastiCache Redis, S3 buckets, SES, Secrets Manager, ALB, ECS cluster.
3. **Inject config** from **Secrets Manager** as task-definition environment/secrets (`SECRET_KEY`, `DB_*`, `REDIS_URL`, AWS creds via IAM task role). `DJANGO_SETTINGS_MODULE=config.settings.prod`.
4. **Run migrations as a one-off ECS task** (not in the service start command) before/at release: `python manage.py migrate --noinput`.
5. **collectstatic** to S3 (or bake to the image with WhiteNoise) at release.
6. **Deploy three ECS services** from the same image, differing by command:
   - api: `gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 3` (behind ALB, `/health/` healthcheck)
   - worker: `celery -A config worker -l info`
   - beat: `celery -A config beat -l info`
7. Logs → **CloudWatch** (app logs to stdout — already configured).

Scaling: api/worker scale horizontally (stateless). Postgres/Redis scale as managed services. No code changes between dev and prod — only environment.

## 6. Environment variables (contract)

| Var | Purpose | Dev (compose) | Prod (Secrets Manager) |
|---|---|---|---|
| `SECRET_KEY` | Django secret | in `.env.docker` | secret |
| `DEBUG` | debug mode | `True` | `False` |
| `DJANGO_SETTINGS_MODULE` | settings | `config.settings.dev` | `config.settings.prod` |
| `DB_NAME/USER/PASSWORD/HOST/PORT` | Postgres | `db:5432` | RDS endpoint |
| `REDIS_URL` | broker + cache | `redis://redis:6379/0` | ElastiCache endpoint |
| `CORS_ALLOWED_ORIGINS` | web/app origins | localhost | app domains |
| `AWS_*` (later) | S3/SES | — | IAM task role |

## 7. What changed in the code for container-first

- **WhiteNoise** added (middleware + `STORAGES.staticfiles`) so the API serves its own static files inside a container — no separate web server.
- Settings already env-only (`python-decouple`); `os.environ` (compose/ECS) takes precedence over any `.env` file, so the same code runs in every environment.
- Logging already writes to **stdout** (CloudWatch-ready).
- `config.settings.prod` hardens security (SSL redirect, HSTS, secure cookies, proxy SSL header) for the ALB/ECS edge.
- No business logic changed.

> **Validated (2026-07-16):** `docker compose build && up` runs the full stack — **api, worker, beat, db, redis all healthy**. `/health/` returns ok, JWT is issued over HTTP through gunicorn, the OpenAPI schema + Swagger (`/api/v1/docs/`) serve 200, and the **14-test suite passes inside the container**. Local dev publishes db→`5433` and redis→`6380` on the host to avoid clashing with a locally-running Postgres/Redis (internal ports are unchanged).
