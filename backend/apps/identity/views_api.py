"""Identity/company management API (DATA_MODEL §4-6).

Company/User/Membership/Role are platform tables (not tenant-auto-scoped), so
these views scope explicitly to the requesting user's active company. A user
only ever sees their own company and its members.
"""

from django.db.models import Q
from rest_framework import mixins, status, viewsets
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import HasPermission

from .models import Membership, Permission, Role, User
from .serializers import (
    CompanySerializer,
    InviteSerializer,
    MembershipSerializer,
    PermissionSerializer,
    RoleSerializer,
)


class MeView(APIView):
    """Current user, active company, resolved role + permissions."""

    def get(self, request):
        user = request.user
        membership = user.active_membership()
        perms = []
        if membership and membership.role_id:
            perms = list(membership.role.permissions.values_list("codename", flat=True))
        return Response({
            "user": {
                "id": str(user.id), "email": user.email,
                "first_name": user.first_name, "last_name": user.last_name,
                "full_name": user.get_full_name(), "mobile": user.mobile,
            },
            "active_company": CompanySerializer(user.active_company).data
            if user.active_company_id else None,
            "role": membership.role.name if membership and membership.role_id else None,
            "permissions": perms,
        })


class CompanyView(RetrieveUpdateAPIView):
    """Retrieve/update the requesting user's active company."""

    serializer_class = CompanySerializer
    permission_classes = [HasPermission]
    required_perm = None  # read for any member; write guarded below

    def get_object(self):
        return self.request.user.active_company

    def update(self, request, *args, **kwargs):
        if not request.user.has_perm_code("company.manage"):
            return Response(
                {"error": {"code": "forbidden", "message": "Need company.manage."}},
                status=status.HTTP_403_FORBIDDEN,
            )
        return super().update(request, *args, **kwargs)


class MembershipViewSet(viewsets.ModelViewSet):
    """Team members of the active company. Create = invite by email + role."""

    serializer_class = MembershipSerializer
    permission_classes = [HasPermission]
    required_perms = {
        "create": "users.invite",
        "update": "users.invite",
        "partial_update": "users.invite",
        "destroy": "users.invite",
    }

    def get_queryset(self):
        company = self.request.user.active_company
        return (
            Membership.objects.filter(company=company, is_deleted=False)
            .select_related("user", "role")
            .order_by("user__email")
        )

    def create(self, request, *args, **kwargs):
        company = request.user.active_company
        invite = InviteSerializer(data=request.data)
        invite.is_valid(raise_exception=True)
        data = invite.validated_data
        user, _ = User.objects.get_or_create(
            email=data["email"],
            defaults={
                "first_name": data.get("first_name", ""),
                "last_name": data.get("last_name", ""),
                "active_company": company,
            },
        )
        if Membership.objects.filter(user=user, company=company).exists():
            return Response(
                {"error": {"code": "conflict", "message": "Already a member."}},
                status=status.HTTP_409_CONFLICT,
            )
        membership = Membership.objects.create(
            user=user, company=company, role=data["role"],
            job_title=data.get("job_title", ""), invited_by=request.user,
        )
        return Response(MembershipSerializer(membership).data, status=status.HTTP_201_CREATED)


class RoleViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    """Roles available to the active company: its own + platform templates."""

    serializer_class = RoleSerializer

    def get_queryset(self):
        company = self.request.user.active_company
        return Role.objects.filter(
            Q(company=company) | Q(company__isnull=True), is_deleted=False
        ).prefetch_related("permissions").order_by("name")


class PermissionViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The permission catalogue (read-only)."""

    serializer_class = PermissionSerializer
    queryset = Permission.objects.filter(is_deleted=False).order_by("module", "codename")
