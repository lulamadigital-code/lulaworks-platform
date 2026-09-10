"""API serializers for Customers & Contacts.

The mobile client (and any future API consumer) reads/writes customers through
these. Business logic — code generation, department seeding, overview numbers —
stays in services.py; these serializers only shape data in and out.
"""
from rest_framework import serializers

from .models import Customer, CustomerContact, Lead


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


class LeadSerializer(serializers.ModelSerializer):
    """A sales lead (pre-customer). Writes go through crm.create_lead in the view
    so codes/defaults match the web."""
    status_display = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = Lead
        fields = ["id", "company_name", "contact_name", "job_title", "email",
                  "telephone", "mobile", "industry", "customer_type", "city",
                  "country", "source", "status", "status_display", "estimated_value",
                  "currency", "notes", "converted_customer", "converted_at",
                  "lost_reason", "created_at", "updated_at"]
        read_only_fields = ["id", "status_display", "converted_customer",
                            "converted_at", "lost_reason", "created_at", "updated_at"]
