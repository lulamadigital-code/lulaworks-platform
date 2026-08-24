"""API serializers for Customers & Contacts.

The mobile client (and any future API consumer) reads/writes customers through
these. Business logic — code generation, department seeding, overview numbers —
stays in services.py; these serializers only shape data in and out.
"""
from rest_framework import serializers

from .models import Customer, CustomerContact


class CustomerListSerializer(serializers.ModelSerializer):
    """Lean shape for the list screen."""

    class Meta:
        model = Customer
        fields = [
            "id", "code", "name", "trading_name", "customer_type", "status",
            "city", "province", "telephone", "mobile", "email",
        ]
        read_only_fields = fields


class CustomerSerializer(serializers.ModelSerializer):
    """Full customer for detail + create/update. `code` is system-generated."""

    class Meta:
        model = Customer
        fields = [
            "id", "code", "name", "trading_name", "customer_type",
            "registration_no", "vat_no", "tax_no", "industry",
            "country", "province", "city", "physical_address", "postal_address",
            "postal_code", "telephone", "mobile", "email",
            "payment_terms_note", "currency", "status", "notes",
        ]
        read_only_fields = ["id", "code"]


class CustomerContactSerializer(serializers.ModelSerializer):
    reach = serializers.CharField(read_only=True)

    class Meta:
        model = CustomerContact
        fields = [
            "id", "customer", "department", "full_name", "job_title",
            "email", "telephone", "mobile", "whatsapp", "extension",
            "preferred_method", "status", "roles", "responsibilities",
            "is_primary", "notes", "reach",
        ]
        read_only_fields = ["id", "reach"]
