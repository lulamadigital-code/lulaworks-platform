"""Identity/company management API (DATA_MODEL §4-6).

Company/User/Membership/Role are platform tables (not tenant-auto-scoped), so
these views scope explicitly to the requesting user's active company. A user
only ever sees their own company and its members.
"""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Q
from rest_framework import mixins, status, viewsets
from rest_framework.generics import RetrieveUpdateAPIView
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import HasPermission
from apps.core.uploads import validate_upload

from .models import Membership, Permission, Role, User
from .serializers import (
    CompanySerializer,
    InviteSerializer,
    MembershipSerializer,
    PermissionSerializer,
    RoleSerializer,
)


def _me_payload(request):
    """The shared /me/ shape — user (incl. avatar + phone), job title, active
    company, resolved role and permission codenames."""
    user = request.user
    membership = user.active_membership()
    perms = []
    if membership and membership.role_id:
        perms = list(membership.role.permissions.values_list("codename", flat=True))
    avatar = None
    if getattr(user, "avatar", None):
        try:
            avatar = request.build_absolute_uri(user.avatar.url)
        except Exception:  # noqa: BLE001
            avatar = None
    return {
        "user": {
            "id": str(user.id), "email": user.email,
            "first_name": user.first_name, "last_name": user.last_name,
            "full_name": user.get_full_name(),
            "mobile": user.mobile,
            "avatar": avatar,
        },
        "job_title": membership.job_title if membership else "",
        "active_company": CompanySerializer(user.active_company).data
        if user.active_company_id else None,
        "role": membership.role.name if membership and membership.role_id else None,
        "permissions": perms,
    }


class MeView(APIView):
    """Current user + company + role/permissions (GET), and edit the user's own
    personal details (PATCH). Personal profile only — company data is edited via
    /company/ by users with company.manage."""

    def get(self, request):
        return Response(_me_payload(request))

    def patch(self, request):
        user = request.user
        for field in ("first_name", "last_name", "mobile"):
            if field in request.data:
                setattr(user, field, request.data[field] or "")
        user.save(update_fields=["first_name", "last_name", "mobile"])
        membership = user.active_membership()
        if membership and "job_title" in request.data:
            membership.job_title = request.data["job_title"] or ""
            membership.save(update_fields=["job_title"])
        return Response(_me_payload(request))


class MeAvatarView(APIView):
    """Upload or remove the signed-in user's profile photo."""

    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        upload = request.FILES.get("file")
        if not upload:
            return Response({"error": {"code": "no_file", "message": "No image uploaded."}},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_upload(upload, allowed={"png", "jpg", "jpeg", "webp"}, max_mb=5)
        except ValidationError as exc:
            return Response({"error": {"code": "invalid_file", "message": " ".join(exc.messages)}},
                            status=status.HTTP_400_BAD_REQUEST)
        request.user.avatar = upload
        request.user.save(update_fields=["avatar"])
        return Response(_me_payload(request))

    def delete(self, request):
        request.user.avatar = None
        request.user.save(update_fields=["avatar"])
        return Response(_me_payload(request))


class MeChangePasswordView(APIView):
    """Change the signed-in user's own password (current password required)."""

    def post(self, request):
        user = request.user
        old = request.data.get("old_password") or ""
        new = request.data.get("new_password") or ""
        if not user.check_password(old):
            return Response({"error": {"code": "invalid", "message": "Current password is incorrect."}},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_password(new, user=user)
        except ValidationError as exc:
            return Response({"error": {"code": "invalid", "message": " ".join(exc.messages)}},
                            status=status.HTTP_400_BAD_REQUEST)
        user.set_password(new)
        user.save(update_fields=["password"])
        return Response({"ok": True})


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


class CompanySetupView(APIView):
    """Progressive-setup state for the active company (§21) — overall %, per
    section, and which actions are allowed. Web + Flutter render from this, so
    both show identical state. Read-only; any member may read."""

    def get(self, request):
        from apps.identity.company_setup import status as setup_status
        company = request.user.active_company
        if company is None:
            return Response({"error": {"code": "no_company",
                             "message": "No active company."}}, status=400)
        data = setup_status(company)
        # employees can't fix company settings — tell the UI who can (§24)
        data["can_edit"] = request.user.has_perm_code("company.manage")
        return Response(data)


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
