"""Gunicorn production configuration for the LulaWorks web container.

Every value is env-overridable so the same image tunes itself to the host it
lands on (a small DigitalOcean droplet today, a larger AWS task tomorrow)
without a rebuild. Defaults are sensible for a 1–2 vCPU node.
"""

import multiprocessing
import os

_cpus = multiprocessing.cpu_count()

# ── Socket ───────────────────────────────────────────────────────────────────
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# ── Worker processes ─────────────────────────────────────────────────────────
# Threaded workers (gthread) give good throughput for IO-bound Django views
# (DB + template rendering) at a modest memory cost. Heavy/long jobs belong on
# Celery, never in a web worker — so the pool stays lean.
workers = int(os.getenv("GUNICORN_WORKERS", (2 * _cpus) + 1))
worker_class = os.getenv("GUNICORN_WORKER_CLASS", "gthread")
threads = int(os.getenv("GUNICORN_THREADS", "4"))

# ── Timeouts ─────────────────────────────────────────────────────────────────
timeout = int(os.getenv("GUNICORN_TIMEOUT", "60"))          # worker hard limit
graceful_timeout = int(os.getenv("GUNICORN_GRACEFUL_TIMEOUT", "30"))
keepalive = int(os.getenv("GUNICORN_KEEPALIVE", "5"))       # match nginx upstream

# ── Recycle workers to cap memory growth (leak insurance) ────────────────────
max_requests = int(os.getenv("GUNICORN_MAX_REQUESTS", "1000"))
max_requests_jitter = int(os.getenv("GUNICORN_MAX_REQUESTS_JITTER", "100"))

# Heartbeat file on a tmpfs (/dev/shm) avoids slow-disk stalls that make
# gunicorn kill healthy workers. The prod compose mounts /dev/shm for this.
worker_tmp_dir = "/dev/shm"

# ── Logging → stdout/stderr (collected by the container runtime) ─────────────
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
# Log the real client IP that nginx forwards, plus response time (%(D)s, µs).
access_log_format = (
    '%({x-forwarded-for}i)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s '
    '"%(f)s" "%(a)s" %(D)s'
)

# Trust X-Forwarded-* only from the nginx reverse proxy in front of us.
forwarded_allow_ips = os.getenv("GUNICORN_FORWARDED_ALLOW_IPS", "*")
