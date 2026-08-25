from rest_framework import serializers

from .models import SupportMessage, SupportTicket


class SupportMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.get_full_name", read_only=True)

    class Meta:
        model = SupportMessage
        fields = ["id", "body", "from_support", "sender_name", "created_at"]
        read_only_fields = fields


class SupportTicketSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(source="get_category_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    message_count = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = ["id", "number", "subject", "category", "category_display",
                  "priority", "status", "status_display", "created_at",
                  "last_activity_at", "message_count"]
        read_only_fields = fields

    def get_message_count(self, obj):
        # Only customer-visible messages (never internal support notes).
        return obj.messages.filter(is_internal=False).count()


class SupportTicketDetailSerializer(SupportTicketSerializer):
    messages = serializers.SerializerMethodField()

    class Meta(SupportTicketSerializer.Meta):
        fields = SupportTicketSerializer.Meta.fields + ["description", "messages"]
        read_only_fields = fields

    def get_messages(self, obj):
        # The customer never sees internal support notes.
        rows = obj.messages.filter(is_internal=False).select_related("sender")
        return SupportMessageSerializer(rows, many=True).data
