from rest_framework import serializers

from apps.core.api import GoldenRuleSerializerMixin

from .models import Quotation, QuotationLine


class QuotationLineSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    money_fields = ("unit_price", "line_total")
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = QuotationLine
        fields = ["id", "position", "description", "qty", "unit", "unit_price", "line_total"]


class QuotationSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    """Whole quotation is commercial — money fields (totals, line prices) are
    stripped for users without `finance.view_money` (Golden Rule)."""

    money_fields = ("subtotal", "vat_amount", "total")
    lines = QuotationLineSerializer(many=True, read_only=True)
    subtotal = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    vat_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Quotation
        fields = [
            "id", "number", "title", "client_name", "site", "status", "vat_rate",
            "validity_date", "notes", "lines", "subtotal", "vat_amount", "total",
            "created_at",
        ]
        read_only_fields = ["id", "number", "created_at"]


class QuotationCreateSerializer(serializers.Serializer):
    client_name = serializers.CharField()
    title = serializers.CharField(required=False, allow_blank=True)
    site = serializers.CharField(required=False, allow_blank=True)
    lines = serializers.ListField(child=serializers.DictField(), required=False)
