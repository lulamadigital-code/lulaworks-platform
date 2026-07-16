from rest_framework import serializers

from .models import AIInteraction


class AIInteractionSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIInteraction
        fields = ["id", "request_text", "agent", "prompt_version", "provider", "result",
                  "confidence", "approval_status", "entity_type", "entity_id",
                  "decided_at", "created_at"]
        read_only_fields = fields


class AskSerializer(serializers.Serializer):
    request = serializers.CharField()
    project = serializers.UUIDField(required=False)
    quotation = serializers.UUIDField(required=False)
    # None = auto (enrich when a provider is configured); explicit true/false overrides.
    enrich = serializers.BooleanField(required=False, allow_null=True, default=None)


class DecisionSerializer(serializers.Serializer):
    approved = serializers.BooleanField()
