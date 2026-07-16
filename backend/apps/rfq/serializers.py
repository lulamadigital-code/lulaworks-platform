from rest_framework import serializers

from .models import ExtractedField, RFQDocument, RFQLineItem


class ExtractedFieldSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExtractedField
        fields = ["id", "key", "value", "approved_value", "confidence", "method",
                  "source_text", "review_status"]
        read_only_fields = ["id", "key", "value", "confidence", "method", "source_text"]


class RFQLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = RFQLineItem
        fields = ["id", "position", "description", "qty", "unit", "unit_price", "confidence"]
        read_only_fields = ["id", "confidence"]


class RFQDocumentSerializer(serializers.ModelSerializer):
    fields = ExtractedFieldSerializer(many=True, read_only=True)
    lines = RFQLineItemSerializer(many=True, read_only=True)
    quotation_number = serializers.CharField(
        source="quotation.number", read_only=True, default=None
    )

    class Meta:
        model = RFQDocument
        fields = ["id", "original_name", "doc_class", "status", "warnings",
                  "fields", "lines", "quotation", "quotation_number", "created_at"]
        read_only_fields = fields


class ApproveSerializer(serializers.Serializer):
    client_name = serializers.CharField()
