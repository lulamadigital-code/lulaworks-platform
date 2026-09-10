"""API serializers for Customers & Contacts.

The mobile client (and any future API consumer) reads/writes customers through
these. Business logic — code generation, department seeding, overview numbers —
stays in services.py; these serializers only shape data in and out.
"""
from rest_framework import serializers

from .models import Activity, Customer, CustomerContact, Lead, Opportunity


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
    customer_name = serializers.SerializerMethodField()

    class Meta:
        model = CustomerContact
        fields = [
            "id", "customer", "customer_name", "department", "full_name", "job_title",
            "email", "telephone", "mobile", "whatsapp", "extension",
            "preferred_method", "status", "roles", "responsibilities",
            "is_primary", "notes", "reach",
        ]
        read_only_fields = ["id", "reach", "customer_name"]

    def get_customer_name(self, obj):
        c = getattr(obj, "customer", None)
        if not c:
            return ""
        return getattr(c, "display_name", None) or str(c)


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


class OpportunitySerializer(serializers.ModelSerializer):
    """A deal in the pipeline. Stage changes go through the view's `stage` action
    so WON/LOST run the proper close logic."""
    stage_display = serializers.CharField(source="get_stage_display", read_only=True)
    customer_name = serializers.CharField(source="customer.display_name", read_only=True)

    class Meta:
        model = Opportunity
        fields = ["id", "customer", "customer_name", "title", "reference",
                  "description", "stage", "stage_display", "estimated_value",
                  "currency", "probability", "expected_close_date", "source",
                  "quotation", "closed_at", "lost_reason", "created_at", "updated_at"]
        read_only_fields = ["id", "stage_display", "customer_name", "closed_at",
                            "lost_reason", "created_at", "updated_at"]


class ActivitySerializer(serializers.ModelSerializer):
    """A CRM to-do (call/meeting/follow-up) attached to a customer/lead/deal."""
    type_display = serializers.CharField(source="get_activity_type_display", read_only=True)
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    customer_name = serializers.SerializerMethodField()

    class Meta:
        model = Activity
        fields = ["id", "activity_type", "type_display", "subject", "detail",
                  "customer", "customer_name", "lead", "opportunity", "due_at",
                  "status", "status_display", "completed_at", "outcome", "created_at"]
        read_only_fields = ["id", "type_display", "status_display", "customer_name",
                            "completed_at", "created_at"]

    def get_customer_name(self, obj):
        return obj.customer.display_name if obj.customer_id else ""
