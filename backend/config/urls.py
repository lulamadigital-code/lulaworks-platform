from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import (
    TokenBlacklistView,
    TokenObtainPairView,
    TokenRefreshView,
)


def health(_request):
    """Liveness/readiness probe (used by container healthchecks + ECS)."""
    return JsonResponse({"status": "ok", "service": "lulaworks-api"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    # Auth (JWT)
    path("api/v1/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/v1/auth/logout/", TokenBlacklistView.as_view(), name="token_blacklist"),
    # Identity / company management
    path("api/v1/", include("apps.identity.urls")),
    # Quotations (the pre-award lifecycle root)
    path("api/v1/", include("apps.quotes.urls")),
    # RFQ Intelligence
    path("api/v1/", include("apps.rfq.urls")),
    # Procurement
    path("api/v1/", include("apps.procurement.urls")),
    # Estimating & Quotation Intelligence
    path("api/v1/", include("apps.estimating.urls")),
    # Projects (execution aggregate root)
    path("api/v1/", include("apps.projects.urls")),
    # Compliance Intelligence (the readiness gate)
    path("api/v1/", include("apps.compliance.urls")),
    # Project Execution & Operations
    path("api/v1/", include("apps.execution.urls")),
    # Finance, Commercial & Payments
    path("api/v1/", include("apps.finance.urls")),
    # AI Platform — Lulama orchestrator + agents
    path("api/v1/", include("apps.ai_platform.urls")),
    # OpenAPI
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    # Manager web (session-auth, server-rendered HTML + HTMX) — mounted last so
    # admin / api / health take precedence.
    path("", include("apps.web.urls")),
]
