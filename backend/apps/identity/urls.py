from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views_api import (
    CompanyView,
    MembershipViewSet,
    MeView,
    PermissionViewSet,
    RoleViewSet,
)

router = DefaultRouter()
router.register("users", MembershipViewSet, basename="membership")
router.register("roles", RoleViewSet, basename="role")
router.register("permissions", PermissionViewSet, basename="permission")

urlpatterns = [
    path("me/", MeView.as_view(), name="me"),
    path("company/", CompanyView.as_view(), name="company"),
    path("", include(router.urls)),
]
