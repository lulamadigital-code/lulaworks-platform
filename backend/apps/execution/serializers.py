from rest_framework import serializers

from apps.core.api import GoldenRuleSerializerMixin

from .models import Resource, ResourceAllocation, Task, Timesheet, WorkPackage
from .services import compute_task_readiness


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


class ResourceSerializer(serializers.ModelSerializer):
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
