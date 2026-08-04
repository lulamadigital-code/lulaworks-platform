# LulaWorks — Deployment & Infrastructure

Production infrastructure for the LulaWorks platform. Phase 1 targets a single
**DigitalOcean** droplet with Docker Compose; Phase 2 lifts the *same images* to
**AWS** with only configuration changes. This document is the operator's source
of truth — you should be able to deploy, back up, and recover without reading
application code.

> Local development uses `docker-compose.yml` (db, redis, api, worker, beat on
> named volumes). Production uses `docker-compose.prod.yml` (this document).

---

## 1. Container architecture

One responsibility per container. In production, **only nginx faces the
internet** — everything else talks over the private Docker network.

| Service   | Image                   | Responsibility                                             | Host port |
|-----------|-------------------------|------------------------------------------------------------|-----------|
| `nginx`   | `nginx:1.27-alpine`     | TLS termination, reverse proxy, static/media, gzip, rate limiting, security headers | 80, 443 |
| `web`     | `lulaworks/api:prod`    | Django + Gunicorn only (API, auth, business logic)         | — (internal) |
| `worker`  | `lulaworks/api:prod`    | Celery worker — background jobs (AI extraction, PDFs, exports, email, notifications) | — |
| `beat`    | `lulaworks/api:prod`    | Celery beat — scheduled jobs (reminders, cleanups, reports) | — |
| `db`      | `postgres:16-alpine`    | PostgreSQL (persistent volume)                             | — (internal) |
| `redis`   | `redis:7-alpine`        | Celery broker + Django cache (append-only persistence)     | — (internal) |
| `backup`  | `lulaworks/backup` (`postgres:16-alpine` + aws-cli) | Nightly `pg_dump` → `backups` volume + off-box copy to Spaces/S3 | — |
| `certbot` | `certbot/certbot`       | Automatic TLS certificate renewal (12h loop)               | — |
| `flower`  | `mher/flower:2.0`       | *(optional, `--profile monitoring`)* Celery dashboard      | 127.0.0.1:5555 |
| `pgadmin` | `dpage/pgadmin4`        | *(optional, `--profile debug`)* DB console                  | 127.0.0.1:5050 |

The `web`, `worker`, and `beat` services are the **same image** running
different commands (12-factor). Background work never runs inside `web`.

### Data flow

```
Internet ──HTTPS──▶ nginx ──HTTP──▶ web (gunicorn) ──▶ db  (PostgreSQL)
                     │                    │           └──▶ redis (cache)
                     ├─ /static/ (volume) │
                     └─ /media/  (volume) worker/beat ──▶ redis (broker) ──▶ db
                                          backup ──nightly pg_dump──▶ backups volume
```

---

## 2. Startup process

The `web` container's entrypoint waits for PostgreSQL to accept connections,
then the service command runs migrations, collects static files into the shared
`static-data` volume (which nginx serves), and starts Gunicorn:

```
migrate --noinput  →  collectstatic --noinput  →  gunicorn -c gunicorn.conf.py
```

Compose dependency ordering guarantees: `db`/`redis` become **healthy** →
`web` starts and becomes **healthy** → `nginx` starts. Every service has a
healthcheck (§6), and `restart: unless-stopped` restarts anything that dies.

> Single web instance runs migrations on start (fine for one node). If you scale
> `web` to multiple replicas, move `migrate`/`collectstatic` into a one-off
> release task so replicas don't race.

---

## 3. Environment variables

Configuration is entirely environment-driven. Templates live in the repo; the
real files (`.env.docker`, `.env.prod`) are **gitignored** and never committed.

| File                  | Purpose                          | Committed? |
|-----------------------|----------------------------------|------------|
| `.env.docker.example` | Local dev template               | ✅ (template) |
| `.env.prod.example`   | Production template              | ✅ (template) |
| `.env.docker`         | Local dev secrets                | ❌ gitignored |
| `.env.prod`           | Production secrets (on server)   | ❌ gitignored |

Key variables (see `.env.prod.example` for the full list):

| Variable                | Meaning                                                        |
|-------------------------|----------------------------------------------------------------|
| `SECRET_KEY`            | Django secret — `python -c "import secrets;print(secrets.token_urlsafe(64))"` |
| `DEBUG`                 | Always `False` in production                                   |
| `ALLOWED_HOSTS`         | Your domain(s) + droplet IP, comma-separated                   |
| `CSRF_TRUSTED_ORIGINS`  | `https://your-domain` — required for admin/manager POSTs over HTTPS |
| `SECURE_SSL_REDIRECT`   | `False` for the first HTTP-only boot; `True` once TLS is live  |
| `DB_*`                  | PostgreSQL name / user / password / host (`db`) / port         |
| `REDIS_URL`             | `redis://redis:6379/0`                                         |
| `CELERY_CONCURRENCY`    | Worker process count (default 4)                               |
| `LOG_LEVEL` / `LOG_FORMAT` | `INFO` / `json` (structured logs in prod)                  |
| `GEMINI_API_KEY` etc.   | AI provider keys — **secrets**, only in `.env.prod`            |
| `BACKUP_RETENTION_DAYS` | Days of nightly dumps to keep (default 14)                     |

**AI is optional and deterministic-first:** with no key set, Lulama and every
agent run fully on grounded data — free, no LLM. Set `GEMINI_API_KEY` /
`ANTHROPIC_API_KEY` (in `.env.prod`, Secrets Manager on AWS) to enable metered
live enrichment; the gateway falls back to the deterministic result on any
failure.

---

## 4. Deploying to DigitalOcean (Phase 1)

**Prerequisites:** a droplet (Ubuntu 22.04+, ≥ 2 GB RAM) with Docker Engine and
the Compose plugin installed, and a domain's A-record pointed at the droplet IP.

```bash
# 1. Get the code onto the droplet
git clone <repo-url> lulaworks && cd lulaworks

# 2. Create the production env file from the template and fill in real secrets
cp .env.prod.example .env.prod
nano .env.prod            # SECRET_KEY, DB_PASSWORD, ALLOWED_HOSTS, domain, AI keys…
#   keep SECURE_SSL_REDIRECT=False for this first (HTTP) boot

# 3. Build and start the whole stack
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

# 4. Create the first admin user
docker compose -f docker-compose.prod.yml --env-file .env.prod \
    exec web python manage.py createsuperuser

# 5. Verify
curl http://<droplet-ip>/health/      # → {"status": "ok", ...}
docker compose -f docker-compose.prod.yml --env-file .env.prod ps
```

The app is now live over HTTP. Add HTTPS next.

### Enabling HTTPS

nginx already serves the ACME HTTP-01 challenge from the `certbot-webroot`
volume. Issue a certificate, then switch nginx to the TLS server block:

```bash
# 1. Issue the certificate (webroot matches the mounted volume)
docker run --rm \
  -v lulaworks_letsencrypt:/etc/letsencrypt \
  -v lulaworks_certbot-webroot:/var/www/certbot \
  certbot/certbot certonly --webroot -w /var/www/certbot \
  -d app.your-domain.co.za --agree-tos -m you@your-domain.co.za --no-eff-email

# 2. Turn on the HTTPS server block
cd infra/nginx/conf.d
mv default.conf default.conf.disabled
mv ssl.conf.example ssl.conf
sed -i 's/DOMAIN/app.your-domain.co.za/g' ssl.conf

# 3. Enforce HTTPS in Django and reload
sed -i 's/^SECURE_SSL_REDIRECT=.*/SECURE_SSL_REDIRECT=True/' ../../../.env.prod
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d web
docker compose -f docker-compose.prod.yml --env-file .env.prod exec nginx nginx -s reload
```

**Renewal is automatic.** The `certbot` service wakes every 12 h and renews any
cert near expiry via the same webroot; nginx reloads on its own 6 h loop to pick
up the new cert. Nothing to schedule — it just works once the first cert exists.

### Updating a running deployment

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build web worker beat
# migrations + collectstatic run automatically as web starts
```

### Monitoring (optional)

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod --profile monitoring up -d
ssh -L 5555:127.0.0.1:5555 user@droplet   # then open http://localhost:5555 (Flower)
```

---

## 5. Backups

The `backup` service runs a `pg_dump` on boot and every `BACKUP_INTERVAL_SECONDS`
(default 24 h), gzips it to the `backups-data` volume, and prunes local dumps
older than `BACKUP_RETENTION_DAYS` (default 14).

### Off-box copy (DigitalOcean Spaces) — recommended

The local volume lives on the droplet, so it dies with the droplet. **DigitalOcean
Spaces** is DigitalOcean's own object storage; it speaks the S3 API, which is why
the settings are named `S3_*` (same variables would point at Amazon S3 in Phase 2
— that is the *only* difference). You stay entirely on DigitalOcean.

**One-time setup in the DigitalOcean console:**

1. **Create a Space.** Left menu → **Spaces Object Storage** → **Create a Spaces
   Bucket**. Pick a region near your droplet (e.g. Frankfurt = `fra1`), give it a
   name (e.g. `lulaworks-prod-backups`), and set file listing to **Restrict**
   (private). Note the region and the endpoint shown, e.g.
   `https://fra1.digitaloceanspaces.com`.
2. **Create an access key.** Left menu → **API** → **Spaces Keys** →
   **Generate New Key**. Copy the **Key** and **Secret** now — the secret is
   shown only once.
3. **(Optional) retention.** In the Space → **Settings** → lifecycle rule to
   expire objects after e.g. 30–90 days, so old dumps clean themselves up.

**Then fill these three-ish values in `.env.prod` on the droplet** (region name
appears twice — in the endpoint host and in `AWS_DEFAULT_REGION`):

```env
S3_BUCKET=lulaworks-prod-backups                  # the Space name from step 1
S3_PREFIX=lulaworks-backups                        # a folder inside the Space
S3_ENDPOINT=https://fra1.digitaloceanspaces.com    # your Space's region endpoint
AWS_ACCESS_KEY_ID=<Spaces Key from step 2>
AWS_SECRET_ACCESS_KEY=<Spaces Secret from step 2>
AWS_DEFAULT_REGION=fra1                             # the region code (matches endpoint)
```

Restart the backup service to apply, then force one and confirm it uploaded:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d backup
docker compose -f docker-compose.prod.yml --env-file .env.prod \
    exec backup sh /usr/local/bin/backup.sh        # look for "off-box copy done"
```

Leave `S3_BUCKET` empty to keep backups local only. Off-box upload failures log a
warning but never fail the local backup, so the app is never blocked on Spaces.

```bash
# List backups
docker compose -f docker-compose.prod.yml --env-file .env.prod exec backup ls -lh /backups

# Force one now
docker compose -f docker-compose.prod.yml --env-file .env.prod exec backup sh /usr/local/bin/backup.sh

# Copy a dump off the server (do this to somewhere durable — Spaces/S3 — regularly!)
docker compose -f docker-compose.prod.yml --env-file .env.prod cp \
    backup:/backups/<dump>.sql.gz ./
```

> ⚠️ The backup volume lives on the same droplet. For real disaster recovery,
> copy dumps off-box (DigitalOcean Spaces / S3) on a schedule.

### Restore

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod run --rm \
  -e RESTORE_FILE=/backups/lulaworks_..._YYYYMMDD_HHMMSS.sql.gz \
  backup sh /usr/local/bin/restore.sh

# then, if the dump predates the current code:
docker compose -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py migrate
```

Dumps are taken with `--clean --if-exists`, so a restore safely drops and
recreates each object.

---

## 6. Health checks

Every service reports health; the orchestrator restarts unhealthy containers.

| Service | Check |
|---------|-------|
| `web`   | `curl -f http://localhost:8000/health/` |
| `nginx` | `wget --spider http://localhost/health/` |
| `db`    | `pg_isready` |
| `redis` | `redis-cli ping` |
| `worker`| `celery -A config inspect ping` |

`/health/` is exempt from the HTTPS redirect so internal HTTP probes always get
a `200`.

---

## 7. Security posture

- **Non-root:** the app image runs as `appuser` (uid 10001), never root.
- **No exposed internals:** `db` and `redis` publish **no** host ports — they are
  reachable only on the private Docker network. Only nginx binds 80/443.
- **Secrets:** live in `.env.prod` (gitignored) / a secrets manager — never in
  the image or git. `.dockerignore` keeps `.env*` out of the build context.
- **TLS:** terminated at nginx; modern protocols/ciphers; HSTS once live.
- **Security headers:** `X-Frame-Options`, `X-Content-Type-Options`,
  `Referrer-Policy`, `Permissions-Policy` — set once, no duplication.
- **Rate limiting:** auth endpoints throttled hard (20 req/min) against
  credential stuffing; API and general traffic have their own zones.
- **Request size limit:** 25 MB at nginx (plus Django's own limit).
- **Least privilege:** Flower/pgAdmin bind to `127.0.0.1` only — reach them via
  an SSH tunnel, never the public internet.

---

## 8. Performance

- **Gunicorn** (`backend/gunicorn.conf.py`): threaded workers sized to CPU,
  `max_requests` recycling to cap memory, heartbeat on `/dev/shm`. All values
  env-overridable (`GUNICORN_WORKERS`, `GUNICORN_TIMEOUT`, …).
- **DB connection pooling:** `CONN_MAX_AGE=60` with health checks — connections
  are reused across requests instead of reopened each time.
- **Static caching:** nginx serves static/media directly with long-lived
  `Cache-Control`; static is WhiteNoise-compressed at collect time.
- **Compression:** gzip on text/JSON/JS/CSS/SVG responses.
- **Resource limits:** each service has CPU/memory `limits` so one container
  can't starve the droplet (tune per droplet size).

---

## 9. Logging

All services log to stdout/stderr — the runtime owns collection (no log files
inside containers). The Django app emits **structured JSON** in production
(`LOG_FORMAT=json`), ready to ship to CloudWatch / Loki / an ELK stack. nginx
access logs include real client IP and upstream response time. Inspect with:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f web
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f nginx
```

---

## 10. AWS migration (Phase 2)

The images do not change. The single-node compose maps cleanly onto managed AWS
services:

| Phase 1 (DigitalOcean)      | Phase 2 (AWS)                                            |
|-----------------------------|---------------------------------------------------------|
| `web`/`worker`/`beat` images | Same images on **ECS Fargate** (one task def per role)  |
| `db` container               | **RDS PostgreSQL** — drop the service, point `DB_HOST` at the RDS endpoint |
| `redis` container            | **ElastiCache Redis** — drop the service, point `REDIS_URL` at it |
| `nginx` container            | **ALB** (TLS via ACM) → the `web` service; keep nginx only if you still serve static locally |
| `media` volume               | **S3** — flip the `STORAGES["default"]` backend (already isolated in settings) |
| `static` volume              | **S3 + CloudFront** (WhiteNoise → collectstatic to S3)  |
| `backup` container           | **RDS automated snapshots** + PITR                      |
| `.env.prod`                  | **Secrets Manager / SSM Parameter Store** (same keys)   |
| JSON logs to stdout          | **CloudWatch Logs** (already structured)                |

Migration-safe choices already in place: env-driven config everywhere, the
storage backend isolated behind Django's `STORAGES`, `SECURE_PROXY_SSL_HEADER`
for running behind a load balancer, and stateless app containers (all state in
db/redis/volumes). Nothing here paints the platform into a DigitalOcean corner.

---

## Quick reference

```bash
# Dev (local)
docker compose up -d --build

# Prod
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f web
docker compose -f docker-compose.prod.yml --env-file .env.prod ps
docker compose -f docker-compose.prod.yml --env-file .env.prod down       # keep data
docker compose -f docker-compose.prod.yml --env-file .env.prod down -v     # wipe volumes (danger)
```
