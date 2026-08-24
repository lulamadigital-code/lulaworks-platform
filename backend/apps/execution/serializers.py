from rest_framework import serializers

from apps.core.api import GoldenRuleSerializerMixin

from .models import (
    ChecklistItem,
    Notification,
    Resource,
    ResourceAllocation,
    Subtask,
    Task,
    TaskReport,
    TaskReportItem,
    TaskResourceAllocation,
    Timesheet,
    WorkPackage,
)
from .services import compute_task_readiness


class ChecklistItemSerializer(serializers.ModelSerializer):
    """A tickable step the person on site checks off. is_done is the only field
    the field app writes; done_by/done_at are stamped server-side."""

    done_by_name = serializers.CharField(source="done_by.get_full_name", read_only=True)

    class Meta:
        model = ChecklistItem
        fields = ["id", "task", "subtask", "label", "is_done", "position",
                  "done_by", "done_by_name", "done_at"]
        read_only_fields = ["id", "task", "subtask", "label", "position",
                            "done_by", "done_by_name", "done_at"]


class SubtaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subtask
        fields = ["id", "task", "name", "is_done", "position", "due_date"]
        read_only_fields = ["id", "task", "name", "position", "due_date"]


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "title", "body", "url", "is_read", "created_at"]
        read_only_fields = fields


class WorkPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = WorkPackage
        fields = ["id", "project", "parent", "name", "position"]
        read_only_fields = ["id"]


class TaskSerializer(serializers.ModelSerializer):
    """`readiness` is the LIVE computed gate (predecessors + compliance + materials),
    always current — mirroring how project readiness is computed live."""

    readiness = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = ["id", "workspace", "project", "phase", "parent", "origin",
                  "work_package", "name", "description", "priority", "status",
                  "risk_level", "labels", "site", "department", "client_name",
                  "is_billable", "predecessors", "blocks_on_compliance", "material_po",
                  "assignee", "planned_start", "planned_end", "due_date",
                  "started_at", "completed_at", "estimated_hours", "actual_hours",
                  "progress_pct", "blocked_reason", "readiness"]
        # `predecessors` routes through TaskDependency (typed links) — it is set via
        # the dependency endpoints/services, never by writing the list directly.
        read_only_fields = ["id", "status", "blocked_reason", "actual_hours",
                            "predecessors", "started_at", "completed_at"]

    def get_readiness(self, obj):
        status, reason = compute_task_readiness(obj)
        return {"status": status, "blocked_reason": reason}


class ResourceSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    # hourly_rate is a labour cost — withheld from users without finance.view_money.
    money_fields = ("hourly_rate",)

    class Meta:
        model = Resource
        fields = ["id", "kind", "name", "code", "hourly_rate", "medical_expiry",
                  "induction_expiry", "inspection_expiry", "is_active"]
        read_only_fields = ["id"]


class ResourceAllocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ResourceAllocation
        fields = ["id", "resource", "project", "task", "start_date", "end_date",
                  "notes", "override_reason"]
        read_only_fields = ["id", "override_reason"]


class AllocateSerializer(serializers.Serializer):
    resource = serializers.UUIDField()
    project = serializers.UUIDField()
    task = serializers.UUIDField(required=False)
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    force = serializers.BooleanField(required=False, default=False)
    override_reason = serializers.CharField(required=False, allow_blank=True)


class TimesheetSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    money_fields = ("labour_cost",)
    labour_cost = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = Timesheet
        fields = ["id", "task", "resource", "date", "hours", "overtime_hours",
                  "approved", "approved_by", "notes", "labour_cost"]
        read_only_fields = ["id", "approved", "approved_by", "labour_cost"]


# ── Work Execution System — the field record ─────────────────────────────────

class TaskResourceAllocationSerializer(GoldenRuleSerializerMixin,
                                       serializers.ModelSerializer):
    # A task budget is company money. The item still lists (kind/label/status)
    # for non-money users, but the rand amounts are withheld (Golden Rule).
    money_fields = ("amount_allocated", "amount_spent", "remaining")
    remaining = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    is_over_budget = serializers.BooleanField(read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = TaskResourceAllocation
        fields = ["id", "task", "kind", "kind_display", "label", "is_monetary",
                  "amount_allocated", "amount_spent", "status", "notes",
                  "remaining", "is_over_budget", "created_at"]
        read_only_fields = ["id", "amount_spent", "remaining", "is_over_budget",
                            "kind_display", "created_at"]


class TaskReportItemSerializer(GoldenRuleSerializerMixin,
                               serializers.ModelSerializer):
    money_fields = ("unit_price", "line_total")

    class Meta:
        model = TaskReportItem
        fields = ["id", "description", "quantity", "unit", "unit_price", "line_total"]
        read_only_fields = ["id"]


class TaskReportSerializer(GoldenRuleSerializerMixin, serializers.ModelSerializer):
    """Read view of a field report, including its extracted line items.

    A report's captured spend (amount/VAT + its line items) is company money, so
    those figures are withheld from users without finance.view_money. The report
    itself — who/when/where, kind, evidence — is always visible so field crews
    and supervisors can see the work; only the rand values are gated."""

    # Nested item money is stripped by TaskReportItemSerializer's own mixin.
    money_fields = ("amount", "vat_amount")
    items = TaskReportItemSerializer(many=True, read_only=True)
    kind_display = serializers.CharField(source="get_kind_display", read_only=True)
    employee_name = serializers.CharField(source="employee.get_full_name", read_only=True)
    supplier_ref_name = serializers.CharField(source="supplier_ref.name", read_only=True)

    class Meta:
        model = TaskReport
        fields = ["id", "task", "kind", "kind_display", "title", "event",
                  "reported_at", "employee", "employee_name", "notes",
                  "latitude", "longitude", "gps_accuracy_m", "distance_m",
                  "location_flagged", "supplier", "supplier_ref", "supplier_ref_name",
                  "invoice_number", "document_date", "amount", "vat_amount", "currency",
                  "allocation", "extraction_status", "items", "created_at"]
        read_only_fields = ["id", "kind_display", "employee_name", "supplier_ref",
                            "supplier_ref_name", "distance_m", "location_flagged",
                            "extraction_status", "items", "created_at"]


class CreateTaskReportSerializer(serializers.Serializer):
    """Write payload the Flutter app posts from the field. GPS is optional (the
    device may lack a fix); the server verifies it against the task's site."""

    task = serializers.UUIDField()
    kind = serializers.ChoiceField(choices=TaskReport._meta.get_field("kind").choices,
                                   default="progress")
    title = serializers.CharField(max_length=200)
    event = serializers.CharField(max_length=80, required=False, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True)
    reported_at = serializers.DateTimeField(required=False)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False,
                                        allow_null=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False,
                                         allow_null=True)
    gps_accuracy_m = serializers.DecimalField(max_digits=8, decimal_places=1, required=False,
                                              allow_null=True)
    supplier = serializers.CharField(max_length=200, required=False, allow_blank=True)
    invoice_number = serializers.CharField(max_length=80, required=False, allow_blank=True)
    document_date = serializers.DateField(required=False, allow_null=True)
    amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    vat_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    currency = serializers.CharField(max_length=8, required=False, allow_blank=True)
    allocation = serializers.UUIDField(required=False, allow_null=True)
    items = TaskReportItemSerializer(many=True, required=False)


class AllocateResourceSerializer(serializers.Serializer):
    task = serializers.UUIDField()
    kind = serializers.ChoiceField(
        choices=TaskResourceAllocation._meta.get_field("kind").choices)
    amount_allocated = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    label = serializers.CharField(max_length=200, required=False, allow_blank=True)
    notes = serializers.CharField(max_length=255, required=False, allow_blank=True)
