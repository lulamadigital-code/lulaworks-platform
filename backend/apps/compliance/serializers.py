from rest_framework import serializers

from .models import ComplianceItem, ComplianceRequirement


class ComplianceRequirementSerializer(serializers.ModelSerializer):
    class Meta:
        model = ComplianceRequirement
        fields = ["id", "code", "name", "category", "source", "is_mandatory",
                  "applies_when", "default_valid_days", "confidence", "is_active"]
        read_only_fields = ["id"]


class ComplianceItemSerializer(serializers.ModelSerializer):
    is_satisfied = serializers.BooleanField(read_only=True)

    class Meta:
        model = ComplianceItem
        fields = ["id", "project", "requirement", "category", "name", "source",
                  "confidence", "is_mandatory", "status", "document", "valid_from",
                  "expiry", "approved_by", "approved_at", "is_satisfied", "notes"]
        read_only_fields = ["id", "project", "requirement", "category", "name", "source",
                            "confidence", "is_mandatory", "approved_by", "approved_at",
                            "is_satisfied"]


class ApproveSerializer(serializers.Serializer):
    valid_from = serializers.DateField(required=False)
    expiry = serializers.DateField(required=False)


class RejectSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)
