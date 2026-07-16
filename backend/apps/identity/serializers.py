from rest_framework import serializers

from .models import Company, Membership, Permission, Role, User


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "codename", "module", "label", "description"]


class RoleSerializer(serializers.ModelSerializer):
    permissions = serializers.SlugRelatedField(
        slug_field="codename", many=True, read_only=True
    )

    class Meta:
        model = Role
        fields = ["id", "name", "is_system", "permissions"]


class UserBasicSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name", read_only=True)

    class Meta:
        model = User
        fields = ["id", "email", "first_name", "last_name", "full_name", "mobile"]


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = [
            "id", "name", "trading_name", "registration_no", "vat_no", "industry",
            "company_size", "country", "province", "city", "timezone", "currency",
            "brand_primary", "brand_secondary",
            "subscription_status", "ai_credit_balance",
            "storage_quota_bytes", "storage_used_bytes", "max_users",
        ]
        read_only_fields = [
            "id", "subscription_status", "ai_credit_balance",
            "storage_quota_bytes", "storage_used_bytes", "max_users",
        ]


class MembershipSerializer(serializers.ModelSerializer):
    user = UserBasicSerializer(read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True)

    class Meta:
        model = Membership
        fields = [
            "id", "user", "role", "role_name", "job_title", "department",
            "employee_number", "status", "joined_at",
        ]
        read_only_fields = ["id", "user", "joined_at"]


class InviteSerializer(serializers.Serializer):
    """Invite a user to the active company by email + role."""

    email = serializers.EmailField()
    first_name = serializers.CharField(required=False, allow_blank=True)
    last_name = serializers.CharField(required=False, allow_blank=True)
    role = serializers.PrimaryKeyRelatedField(queryset=Role.objects.all())
    job_title = serializers.CharField(required=False, allow_blank=True)


class MeSerializer(serializers.Serializer):
    user = UserBasicSerializer()
    active_company = CompanySerializer()
    role = serializers.CharField(allow_null=True)
    permissions = serializers.ListField(child=serializers.CharField())
