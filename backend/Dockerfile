# LulaWorks API — container image (shared by API, Celery worker, Celery beat).
# One image, many commands (12-factor): compose/ECS run different commands off it.
#
# Multi-stage: a throwaway "builder" compiles all Python deps into an isolated
# virtualenv; the final "runtime" stage copies only that venv, so the pip cache,
# build metadata and any transient build tools never reach the shipped image.

# ── Stage 1: build the virtualenv ────────────────────────────────────────────
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# All wheels here are manylinux (psycopg2-binary, Pillow, reportlab, …) so no
# compiler toolchain is needed — keeps the builder small and fast.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# ── Stage 2: lean runtime ────────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.prod

WORKDIR /app

# Runtime OS deps. libpq5 (Postgres client lib for psycopg2), curl (health
# probes), tesseract-ocr + poppler-utils (document OCR / PDF rasterisation), and
# the Pango/cairo/gdk-pixbuf stack + base fonts that WeasyPrint needs to render
# the HTML-engine document templates to PDF.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libpq5 curl tesseract-ocr poppler-utils \
       libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf-2.0-0 \
       libcairo2 libffi8 shared-mime-info fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# The ready-built virtualenv from the builder stage (no pip cache, no toolchain).
COPY --from=builder /opt/venv /opt/venv

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
CMD ["gunicorn", "config.wsgi:application", "-c", "gunicorn.conf.py"]
