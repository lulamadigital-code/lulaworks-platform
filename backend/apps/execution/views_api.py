from rest_framework import status
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from apps.core.api import TenantViewSet

from .models import Resource, ResourceAllocation, Task, Timesheet, WorkPackage
from .serializers import (
    AllocateSerializer,
    ResourceAllocationSerializer,
    ResourceSerializer,
    TaskSerializer,
    TimesheetSerializer,
    WorkPackageSerializer,
)
from .services import (
    AllocationError,
    allocate_resource,
    approve_timesheet,
    complete_task,
    refresh_task_status,
    start_task,
)


def _project_filtered(qs, request):
    project = request.query_params.get("project")
    return qs.filter(project_id=project) if project else qs


class WorkPackageViewSet(TenantViewSet):
    model = WorkPackage
    serializer_class = WorkPackageSerializer
    required_perms = {"create": "execution.manage", "update": "execution.manage",
                      "partial_update": "execution.manage", "destroy": "execution.manage"}

    def get_queryset(self):
        return _project_filtered(WorkPackage.objects.all(), self.request)


class TaskViewSet(TenantViewSet):
    """Tasks with LIVE computed readiness. Filter by ?project=<id>."""

    model = Task
    serializer_class = TaskSerializer
    search_fields = ["name", "status"]
    required_perms = {"create": "execution.manage", "update": "execution.manage",
                      "partial_update": "execution.manage", "destroy": "execution.manage",
                      "start": "execution.manage", "complete": "execution.manage"}

    def get_queryset(self):
        return _project_filtered(
            Task.objects.all().select_related("project", "material_po"), self.request
        )

    @action(detail=True, methods=["post"])
    def refresh(self, request, pk=None):
        """Recompute the task's readiness now (predecessors/compliance/materials)."""
        task = refresh_task_status(self.get_object())
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        """Begin the task — enforces the computed readiness gate (incl. compliance)."""
        try:
            task = start_task(self.get_object(), request.user)
        except ValueError as exc:
            return Response({"error": {"code": "not_ready", "message": str(exc)}},
                            status=status.HTTP_409_CONFLICT)
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        task = complete_task(self.get_object(), request.user,
                             actual_hours=request.data.get("actual_hours"))
        return Response(TaskSerializer(task, context={"request": request}).data)


class ResourceViewSet(TenantViewSet):
    model = Resource
    serializer_class = ResourceSerializer
    search_fields = ["name", "code", "kind"]
    required_perms = {"create": "execution.manage", "update": "execution.manage",
                      "partial_update": "execution.manage", "destroy": "execution.manage"}

    def get_queryset(self):
        return Resource.objects.all()


class ResourceAllocationViewSet(TenantViewSet):
    """Allocation refuses double-bookings and expired-credential resources unless
    forced with a reason (Module 9 §4)."""

    model = ResourceAllocation
    serializer_class = ResourceAllocationSerializer
    required_perms = {"create": "execution.manage", "destroy": "execution.manage"}

    def get_queryset(self):
        return _project_filtered(
            ResourceAllocation.objects.all().select_related("resource", "project"), self.request
        )

    def create(self, request, *args, **kwargs):
        from apps.projects.models import Project
        payload = AllocateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        resource = get_object_or_404(Resource.objects.all(), id=data["resource"])
        project = get_object_or_404(Project.objects.all(), id=data["project"])
        task = None
        if data.get("task"):
            task = get_object_or_404(Task.objects.all(), id=data["task"])
        try:
            alloc = allocate_resource(
                request.user.active_company, request.user, resource=resource, project=project,
                task=task, start_date=data["start_date"], end_date=data["end_date"],
                force=data.get("force", False), override_reason=data.get("override_reason", ""),
            )
        except AllocationError as exc:
            # 409: the assignment is invalid — surface the warnings so the planner
            # can resolve them or force with a reason.
            return Response(
                {"error": {"code": "allocation_conflict", "message": "Allocation blocked.",
                           "warnings": exc.warnings}},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(ResourceAllocationSerializer(alloc).data, status=status.HTTP_201_CREATED)


class TimesheetViewSet(TenantViewSet):
    """Timesheets feed actual labour hours (the Module 7 loop). Approval is gated."""

    model = Timesheet
    serializer_class = TimesheetSerializer
    required_perms = {"approve": "timesheet.approve"}

    def get_queryset(self):
        qs = Timesheet.objects.all().select_related("resource", "task")
        task = self.request.query_params.get("task")
        return qs.filter(task_id=task) if task else qs

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        ts = approve_timesheet(self.get_object(), request.user)
        return Response(TimesheetSerializer(ts, context={"request": request}).data)
