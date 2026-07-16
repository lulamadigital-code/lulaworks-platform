from rest_framework import serializers

from .models import Project


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = ["id", "number", "quotation", "title", "client_name", "site", "mine",
                  "work_type", "status", "awarded_at", "created_at"]
        read_only_fields = fields


class AwardSerializer(serializers.Serializer):
    quotation = serializers.UUIDField()
    work_type = serializers.CharField(required=False, allow_blank=True)
    mine = serializers.CharField(required=False, allow_blank=True)
    site = serializers.CharField(required=False, allow_blank=True)


class OverrideSerializer(serializers.Serializer):
    reason = serializers.CharField()
    requirement = serializers.UUIDField(required=False)
