from rest_framework import serializers

from apps.core.api import GoldenRuleSerializerMixin

from .models import POLine, PurchaseOrder, Supplier


class SupplierSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    # Supplier cost intelligence (banking, performance) is manager-level.
    money_fields = ("bank_account_no", "performance_score")

    class Meta:
        model = Supplier
        fields = [
            "id", "name", "registration_no", "vat_no", "categories", "payment_terms",
            "our_account_no", "bank_name", "bank_account_no", "bee_level",
            "insurance_expiry", "contact_person", "email", "phone", "preferred",
            "performance_score", "notes",
        ]
        read_only_fields = ["id", "performance_score"]


class POLineSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    money_fields = ("unit_price", "line_total")
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    outstanding = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = POLine
        fields = ["id", "position", "description", "qty", "unit", "unit_price",
                  "line_total", "outstanding"]


class PurchaseOrderSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    money_fields = ("total",)
    lines = POLineSerializer(many=True, read_only=True)
    total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = ["id", "number", "supplier", "supplier_name", "quotation", "status",
                  "delivery_address", "payment_terms", "lines", "total", "created_at"]
        read_only_fields = ["id", "number", "status", "created_at"]


class POCreateSerializer(serializers.Serializer):
    # UUID (not PrimaryKeyRelatedField) — the field's queryset would evaluate the
    # tenant-scoped manager at import time; the view resolves it scoped instead.
    supplier = serializers.UUIDField()
    quotation = serializers.UUIDField(required=False, allow_null=True)
    delivery_address = serializers.CharField(required=False, allow_blank=True)
    lines = serializers.ListField(child=serializers.DictField())
