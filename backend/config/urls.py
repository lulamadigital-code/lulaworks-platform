from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.marketing import views as marketing_views
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
    # SEO endpoints for the public site.
    path("robots.txt", marketing_views.robots_txt, name="robots_txt"),
    path("sitemap.xml", marketing_views.sitemap_xml, name="sitemap_xml"),
    # Payments (checkout return/cancel + provider webhooks).
    path("", include("apps.payments.urls")),
    # Manager web (session-auth, server-rendered HTML + HTMX). Mounted before the
    # public site so its named routes (login/, dashboard/, projects/, …) resolve.
    path("", include("apps.web.urls")),
    # Public marketing website — owns "/" (home) and the public content pages.
    path("", include("apps.marketing.urls")),
]

# Uploaded media (avatars, work files) in DEVELOPMENT only. In production these
# live in S3 behind the CDN and are never served by Django.
if settings.DEBUG:
    from django.conf.urls.static import static
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
