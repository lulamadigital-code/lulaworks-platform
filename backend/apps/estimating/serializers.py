from rest_framework import serializers

from apps.core.api import GoldenRuleSerializerMixin

from .models import Estimate, EstimateActual, EstimateLine, EstimateSection


class EstimateLineSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    money_fields = ("unit_cost", "line_cost")
    line_cost = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = EstimateLine
        fields = ["id", "position", "description", "qty", "unit", "unit_cost", "line_cost",
                  "source", "confidence", "source_ref", "lead_time_days"]


class EstimateSectionSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    money_fields = ("subtotal",)
    lines = EstimateLineSerializer(many=True, read_only=True)
    subtotal = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = EstimateSection
        fields = ["id", "category", "name", "position", "lines", "subtotal"]


class EstimateSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    """The Estimate is INTERNAL — its entire cost build-up, markup, margin and
    price are money fields, stripped for users without `finance.view_money`."""

    money_fields = (
        "direct_cost", "contingency_amount", "total_cost",
        "markup_pct", "discount_pct", "selling_price", "margin_amount", "margin_pct",
    )
    sections = EstimateSectionSerializer(many=True, read_only=True)
    direct_cost = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    contingency_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    total_cost = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    selling_price = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    margin_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    margin_pct = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)

    class Meta:
        model = Estimate
        fields = [
            "id", "number", "version", "title", "client_name", "work_type", "status",
            "quotation", "parent", "contingency_pct", "markup_pct", "discount_pct",
            "risk_score", "risk_flags", "revision_reason", "notes", "sections",
            "direct_cost", "contingency_amount", "total_cost", "selling_price",
            "margin_amount", "margin_pct", "created_at",
        ]
        read_only_fields = ["id", "number", "version", "status", "risk_score", "risk_flags",
                            "quotation", "parent", "created_at"]


class EstimateCreateSerializer(serializers.Serializer):
    client_name = serializers.CharField()
    title = serializers.CharField(required=False, allow_blank=True)
    work_type = serializers.CharField(required=False, allow_blank=True)
    markup_pct = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    sections = serializers.ListField(child=serializers.DictField(), required=False)


class ActualsSerializer(serializers.Serializer):
    actuals = serializers.ListField(child=serializers.DictField())


class EstimateActualSerializer(serializers.ModelSerializer):
    variance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    variance_pct = serializers.DecimalField(max_digits=6, decimal_places=2, read_only=True)

    class Meta:
        model = EstimateActual
        fields = ["id", "category", "estimated_cost", "actual_cost", "variance",
                  "variance_pct", "source", "captured_at"]
