from rest_framework import serializers

from apps.core.api import GoldenRuleSerializerMixin

from .models import (
    BudgetLine,
    CostCode,
    CostEntry,
    Invoice,
    InvoiceLine,
    ProjectBudget,
    Variation,
)


class CostCodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostCode
        fields = ["id", "code", "name", "category", "parent"]
        read_only_fields = ["id"]


class BudgetLineSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    money_fields = ("amount",)

    class Meta:
        model = BudgetLine
        fields = ["id", "category", "amount"]


class ProjectBudgetSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    money_fields = ("revenue", "total_cost_budget")
    lines = BudgetLineSerializer(many=True, read_only=True)
    total_cost_budget = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = ProjectBudget
        fields = ["id", "project", "source_estimate", "revenue", "expected_margin_pct",
                  "total_cost_budget", "lines"]
        read_only_fields = fields


class CostEntrySerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    money_fields = ("amount",)

    class Meta:
        model = CostEntry
        fields = ["id", "project", "cost_code", "work_package", "category", "description",
                  "amount", "source", "source_ref", "date"]
        read_only_fields = ["id"]


class InvoiceLineSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    money_fields = ("unit_price", "line_total")
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = InvoiceLine
        fields = ["id", "position", "description", "qty", "unit_price", "line_total", "cost_code"]


class InvoiceSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    money_fields = ("subtotal", "vat_amount", "retention_amount", "total", "paid", "outstanding")
    lines = InvoiceLineSerializer(many=True, read_only=True)
    subtotal = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    vat_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    retention_amount = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    paid = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    outstanding = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Invoice
        fields = ["id", "project", "number", "client_name", "status", "is_progress_claim",
                  "percent_complete", "issue_date", "due_date", "vat_rate", "retention_pct",
                  "retention_released", "notes", "lines", "subtotal", "vat_amount",
                  "retention_amount", "total", "paid", "outstanding", "created_at"]
        read_only_fields = ["id", "number", "status", "created_at"]


class InvoiceCreateSerializer(serializers.Serializer):
    project = serializers.UUIDField()
    client_name = serializers.CharField(required=False, allow_blank=True)
    retention_pct = serializers.DecimalField(max_digits=5, decimal_places=2, required=False)
    due_date = serializers.DateField(required=False)
    lines = serializers.ListField(child=serializers.DictField(), required=False)


class PaymentSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    date = serializers.DateField(required=False)
    method = serializers.CharField(required=False, allow_blank=True)
    reference = serializers.CharField(required=False, allow_blank=True)
    is_retention = serializers.BooleanField(required=False, default=False)


class VariationSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    money_fields = ("estimated_cost", "revenue_impact")

    class Meta:
        model = Variation
        fields = ["id", "project", "number", "description", "reason", "kind", "category",
                  "estimated_cost", "revenue_impact", "status", "created_at"]
        read_only_fields = ["id", "number", "status", "created_at"]
