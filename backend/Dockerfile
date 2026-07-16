# LulaWorks API — container image (shared by API, Celery worker, Celery beat).
# One image, many commands (12-factor): compose/ECS run different commands off it.
FROM python:3.13-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

WORKDIR /app

# psycopg2-binary + Pillow ship manylinux wheels, so no build toolchain needed.
# libpq5 is the runtime Postgres client lib (safety for psycopg).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# Non-root runtime user (security). Pre-create writable dirs and own /app so the
# unprivileged user can run collectstatic (→ staticfiles) and write media.
RUN useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser . .
RUN mkdir -p /app/staticfiles /app/media \
    && chown -R appuser:appuser /app \
    && chmod +x entrypoint.sh

USER appuser
EXPOSE 8000

# Migrations/collectstatic are NOT run at build time (no secrets/DB available).
# entrypoint waits for the DB, then execs the service command (below / compose).
ENTRYPOINT ["./entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60"]
