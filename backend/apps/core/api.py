"""Shared API layer: base viewset (tenant-safe), Golden-Rule serializer mixin,
pagination, and a consistent error envelope (IMPLEMENTATION_READINESS §7)."""

from rest_framework import viewsets
from rest_framework.views import exception_handler as drf_exception_handler

from .middleware import set_tenant_from_request
from .permissions import HasPermission


def exception_handler(exc, context):
    """Consistent error envelope: {"error": {"code", "message", "detail"}}."""
    response = drf_exception_handler(exc, context)
    if response is None:
        return None
    detail = response.data
    message = detail.get("detail") if isinstance(detail, dict) else detail
    response.data = {
        "error": {
            "code": getattr(exc, "default_code", "error"),
            "message": str(message) if message else "Request failed.",
            "detail": detail,
        }
    }
    return response


class TenantViewSet(viewsets.ModelViewSet):
    """Base for all tenant-scoped API resources.

    - Binds the ambient tenant after DRF authentication (defence in depth).
    - `get_queryset()` returns `model.objects` which is already tenant-scoped.
    - Object lookups therefore 404 across tenants (never leak, never 403-reveal).
    """

    model = None
    permission_classes = [HasPermission]  # RBAC via required_perms/required_perm

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        set_tenant_from_request(request)

    def get_queryset(self):
        assert self.model is not None, "TenantViewSet subclasses must set `model`."
        return self.model.objects.all()

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(updated_by=self.request.user)


class GoldenRuleSerializerMixin:
    """Financial fields render only for users with `finance.view_money`
    (BRIEF §9). List them in `money_fields`. Default-deny."""

    money_fields: tuple = ()

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        user = getattr(request, "user", None)
        can_view = bool(user and getattr(user, "is_authenticated", False)
                        and user.has_perm_code("finance.view_money"))
        if not can_view:
            for field in self.money_fields:
                data.pop(field, None)
        return data


class HealthView:
    """Lightweight liveness/readiness — wired as a function view in urls."""
