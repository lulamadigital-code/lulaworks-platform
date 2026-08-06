"""Base settings for the LulaWorks production platform.

Source of truth: docs/ Modules 1-13 + IMPLEMENTATION_READINESS.md.
Split settings: base (shared) + dev/prod. Select via DJANGO_SETTINGS_MODULE.
"""

from datetime import timedelta
from pathlib import Path

from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("ALLOWED_HOSTS", default="localhost,127.0.0.1").split(",")

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]
THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "simple_history",
]
LOCAL_APPS = [
    "apps.core",
    "apps.identity",
    "apps.administration",
    "apps.billing",
    "apps.storage",
    "apps.ai_platform",
    "apps.knowledge",
    "apps.quotes",
    "apps.rfq",
    "apps.procurement",
    "apps.estimating",
    "apps.projects",
    "apps.customers",
    "apps.compliance",
    "apps.execution",
    "apps.finance",
    "apps.web",
    "apps.marketing",
    "apps.payments",
    "apps.tax",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serves static files from inside the container (no separate web
    # server needed) — container-first. In prod, static/media go to S3/CDN.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    # i18n-ready: resolves the active locale per request so languages can be added
    # later (V1 is English-only) without re-architecting. Must sit after Session
    # and before Common.
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "simple_history.middleware.HistoryRequestMiddleware",
    # Admin-created accounts must replace their temporary password before they
    # can use anything (manager web only; the JWT API is untouched).
    "apps.web.middleware.ForcePasswordChangeMiddleware",
    # Ambient tenant resolution — sets TenantContext from the JWT (DATA_MODEL §1).
    "apps.core.middleware.TenantMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.web.context.nav_flags",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("DB_NAME", default="lulaworks_platform"),
        "USER": config("DB_USER", default="postgres"),
        "PASSWORD": config("DB_PASSWORD"),
        "HOST": config("DB_HOST", default="localhost"),
        "PORT": config("DB_PORT", default="5432"),
        # Connection pooling: hold a connection open for CONN_MAX_AGE seconds
        # instead of reconnecting per request (0 = old behaviour). CONN_HEALTH_CHECKS
        # discards a pooled connection that died between requests so a reused
        # connection never serves an error.
        "CONN_MAX_AGE": config("DB_CONN_MAX_AGE", default=60, cast=int),
        "CONN_HEALTH_CHECKS": True,
        "OPTIONS": {"connect_timeout": config("DB_CONNECT_TIMEOUT", default=10, cast=int)},
    }
}

AUTH_USER_MODEL = "identity.User"

# Session-authenticated manager web (server-rendered HTML + HTMX). Separate from
# the JWT API used by the Flutter field app.
LOGIN_URL = "web:login"
LOGIN_REDIRECT_URL = "web:dashboard"
LOGOUT_REDIRECT_URL = "web:login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- DRF / API (IMPLEMENTATION_READINESS §7) ---
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.DefaultPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.core.api.exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "auth": "20/min",
        "default": "1000/hour",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "LulaWorks API",
    "DESCRIPTION": "AI-powered Contractor Operating System — API v1",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

# --- AI providers (AI_PLATFORM §2). Deterministic-first; AI is fallback/premium.
# Keys come from the environment (Secrets Manager in prod); empty = not
# configured, and the platform runs fully on the deterministic path. ---
AI_PROVIDER = config("AI_PROVIDER", default="claude")
ANTHROPIC_API_KEY = config("ANTHROPIC_API_KEY", default="")
ANTHROPIC_MODEL = config("ANTHROPIC_MODEL", default="claude-sonnet-5")
OPENAI_API_KEY = config("OPENAI_API_KEY", default="")
GEMINI_API_KEY = config("GEMINI_API_KEY", default="")
GEMINI_MODEL = config("GEMINI_MODEL", default="gemini-2.5-flash")
# 0 disables "thinking" — those tokens share the output budget and can
# starve the answer on long extraction prompts.
GEMINI_THINKING_BUDGET = config("GEMINI_THINKING_BUDGET", default="0", cast=str)
AI_CREDITS_PER_EXTRACTION = config("AI_CREDITS_PER_EXTRACTION", default="1", cast=str)

# --- Celery / Redis (async boundary; DATA_MODEL §14) ---
REDIS_URL = config("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = config("CELERY_EAGER", default=False, cast=bool)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

CORS_ALLOWED_ORIGINS = config(
    "CORS_ALLOWED_ORIGINS", default="http://localhost:3000"
).split(",")

# Behind the nginx reverse proxy the browser talks HTTPS to a real domain, so
# Django must trust that origin for CSRF (POSTs from the manager web / admin).
# Comma-separated, scheme-qualified, e.g. "https://app.lulaworks.co.za".
CSRF_TRUSTED_ORIGINS = [
    o for o in config("CSRF_TRUSTED_ORIGINS", default="").split(",") if o
]

# Internationalisation. LulaWorks is a global platform: English-only in V1, but
# i18n is switched on and the language list is here so more can be added without
# re-architecting. Dates/numbers are stored ISO/UTC and displayed locale-aware.
LANGUAGE_CODE = config("LANGUAGE_CODE", default="en")
LANGUAGES = [
    ("en", "English"),
    # Future: ("fr", "Français"), ("es", "Español"), ("pt", "Português"), …
]
# Default platform timezone; per-company timezone lives on Company.timezone.
TIME_ZONE = config("TIME_ZONE", default="UTC")
USE_I18N = True
USE_TZ = True

# Default pricing/display currency for the platform. Individual plans carry their
# own currency (Plan.currency); tenants carry Company.currency. Multi-currency
# ready — V1 defaults to ZAR.
DEFAULT_CURRENCY = config("DEFAULT_CURRENCY", default="ZAR")

# --- Payments (provider-agnostic gateway abstraction; apps.payments) ---
# 'mock' is the safe offline default (no real charge). Set 'stripe' + the keys
# below to charge for real. Add PayFast/Paystack/etc. as new gateways later
# without touching subscription logic.
PAYMENT_GATEWAY = config("PAYMENT_GATEWAY", default="mock")
STRIPE_SECRET_KEY = config("STRIPE_SECRET_KEY", default="")
STRIPE_PUBLISHABLE_KEY = config("STRIPE_PUBLISHABLE_KEY", default="")
STRIPE_WEBHOOK_SECRET = config("STRIPE_WEBHOOK_SECRET", default="")
# Paystack (Africa-focused alternative provider). Webhook auth is HMAC-SHA512
# with the secret key, so no separate webhook secret is needed.
PAYSTACK_SECRET_KEY = config("PAYSTACK_SECRET_KEY", default="")
PAYSTACK_PUBLIC_KEY = config("PAYSTACK_PUBLIC_KEY", default="")

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Container-first: WhiteNoise for static; media → S3 in production (DATA_MODEL §10).
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Structured logging: human-readable text in dev, single-line JSON in prod
# (LOG_FORMAT=json) so logs drop straight into CloudWatch / Loki / an ELK stack
# with no reparsing. Everything still goes to stdout/stderr — the container
# runtime (Docker, later ECS) owns collection, never a file inside the container.
LOG_FORMAT = config("LOG_FORMAT", default="plain")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "format": "%(levelname)s %(asctime)s %(name)s %(module)s %(process)d %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if LOG_FORMAT == "json" else "verbose",
        }
    },
    "root": {"handlers": ["console"], "level": config("LOG_LEVEL", default="INFO")},
}
