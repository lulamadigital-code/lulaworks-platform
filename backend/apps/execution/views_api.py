from decimal import Decimal

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.core.api import TenantViewSet
from apps.core.uploads import validate_upload

from .models import (
    Attachment,
    Resource,
    ResourceAllocation,
    Task,
    TaskReport,
    TaskResourceAllocation,
    Timesheet,
    WorkPackage,
)
from .serializers import (
    AllocateResourceSerializer,
    AllocateSerializer,
    CreateTaskReportSerializer,
    ResourceAllocationSerializer,
    ResourceSerializer,
    TaskReportSerializer,
    TaskResourceAllocationSerializer,
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
from .work_execution import (
    add_report_item,
    allocate_task_resource,
    create_task_report,
    learn_supplier_from_receipt,
    reconcile_allocation,
    task_operational_dashboard,
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

    @action(detail=True, methods=["get"])
    def operational(self, request, pk=None):
        """The task's operational hub, computed server-side: who's on it, what's
        outstanding, money allocated/spent/remaining, materials, documents,
        latest GPS, map points and the full timeline."""
        data = task_operational_dashboard(self.get_object())
        return Response(_serialize_dashboard(data, request))


def _serialize_dashboard(data, request) -> dict:
    task = data["task"]
    fin = data["financials"]
    return {
        "task": {"id": str(task.id), "name": task.name, "status": task.status},
        "progress_pct": data["progress_pct"],
        "team": {role: [u.get_full_name() or u.email for u in users]
                 for role, users in data["team"].items()},
        "outstanding": data["outstanding"],
        "financials": {
            "allocated": str(fin["allocated"]),
            "spent": str(fin["spent"]),
            "remaining": str(fin["remaining"]),
            "over_budget": fin["over_budget"],
            "materials_total": str(fin["materials_total"]),
            "materials_count": fin["materials_count"],
            "allocations": TaskResourceAllocationSerializer(
                fin["allocations"], many=True).data,
        },
        "documents": data["documents"],
        "reports": TaskReportSerializer(
            data["reports"], many=True, context={"request": request}).data,
        "flagged_count": len(data["flagged_reports"]),
        "latest_gps": (
            {"lat": float(data["latest_gps"].latitude),
             "lng": float(data["latest_gps"].longitude),
             "when": data["latest_gps"].reported_at.isoformat()}
            if data["latest_gps"] else None),
        "map_points": [
            {**p, "when": p["when"].isoformat()} for p in data["map_points"]],
        "timeline": [
            {"when": e["when"].isoformat(), "kind": e["kind"],
             "label": e["label"], "detail": e["detail"]}
            for e in data["timeline"]],
    }


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


class TaskReportViewSet(TenantViewSet):
    """Field reports on a task — fuel, material, time/attendance, progress, each
    GPS-stamped. Filter by ?task=<id>. This is the Flutter app's write surface."""

    model = TaskReport
    serializer_class = TaskReportSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    required_perms = {"create": "execution.manage", "update": "execution.manage",
                      "partial_update": "execution.manage", "destroy": "execution.manage",
                      "photo": "execution.manage", "extract_invoice": "execution.manage"}

    def get_queryset(self):
        qs = TaskReport.objects.all().select_related("employee", "allocation") \
            .prefetch_related("items")
        task = self.request.query_params.get("task")
        return qs.filter(task_id=task) if task else qs

    def create(self, request, *args, **kwargs):
        payload = CreateTaskReportSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        task = get_object_or_404(Task.objects.all(), id=data["task"])
        allocation = None
        if data.get("allocation"):
            allocation = get_object_or_404(
                TaskResourceAllocation.objects.all(), id=data["allocation"])

        report = create_task_report(
            task, request.user, kind=data.get("kind", "progress"),
            title=data["title"], event=data.get("event", ""),
            notes=data.get("notes", ""), reported_at=data.get("reported_at"),
            latitude=data.get("latitude"), longitude=data.get("longitude"),
            gps_accuracy_m=data.get("gps_accuracy_m"),
            supplier=data.get("supplier", ""),
            invoice_number=data.get("invoice_number", ""),
            document_date=data.get("document_date"),
            amount=data.get("amount", 0), vat_amount=data.get("vat_amount", 0),
            currency=data.get("currency") or "ZAR", allocation=allocation,
        )
        for item in data.get("items", []):
            add_report_item(report, description=item["description"],
                            quantity=item.get("quantity", 1), unit=item.get("unit", ""),
                            unit_price=item.get("unit_price", 0),
                            line_total=item.get("line_total"), user=request.user)
        # A confirmed material receipt teaches the Suppliers database: match/add
        # the seller and record its prices, so next time we know where we buy this.
        learn_supplier_from_receipt(report, request.user)
        report.refresh_from_db()
        return Response(TaskReportSerializer(report, context={"request": request}).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def photo(self, request, pk=None):
        """Attach a receipt/invoice/site photo to a report (multipart)."""
        report = self.get_object()
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"error": {"code": "no_file", "message": "No file uploaded."}},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_upload(upload)
        except ValueError as exc:
            return Response({"error": {"code": "invalid_file", "message": str(exc)}},
                            status=status.HTTP_400_BAD_REQUEST)
        att = Attachment.objects.create(
            company=report.company, task=report.task, report=report, file=upload,
            original_name=upload.name, content_type=getattr(upload, "content_type", ""),
            size_bytes=upload.size, kind=request.data.get("kind", "photo"),
            created_by=request.user, updated_by=request.user,
        )
        return Response({"id": str(att.id), "original_name": att.original_name},
                        status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def extract_invoice(self, request):
        """Read a supplier invoice into fields the user reviews before saving a
        material report — nothing is persisted here (human confirms first)."""
        from apps.knowledge.document_intelligence import (
            extract_po_fields,
            extract_text_from_upload,
        )
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"error": {"code": "no_file", "message": "No file uploaded."}},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            validate_upload(upload)
        except ValueError as exc:
            return Response({"error": {"code": "invalid_file", "message": str(exc)}},
                            status=status.HTTP_400_BAD_REQUEST)
        text = extract_text_from_upload(upload)
        fields = extract_po_fields(text, company=request.user.active_company,
                                   user=request.user, use_ai=True)
        return Response({
            "supplier": fields.get("contact", ""),
            "invoice_number": fields.get("po_number", ""),
            "document_date": fields.get("po_date", ""),
            "amount": fields.get("value", ""),
            "currency": "ZAR",
            "items": fields.get("lines", []),
        })


class TaskResourceAllocationViewSet(TenantViewSet):
    """Money/logistics set aside for a task before work starts. Filter by ?task=."""

    model = TaskResourceAllocation
    serializer_class = TaskResourceAllocationSerializer
    required_perms = {"create": "execution.manage", "update": "execution.manage",
                      "partial_update": "execution.manage", "destroy": "execution.manage",
                      "reconcile": "execution.manage"}

    def get_queryset(self):
        qs = TaskResourceAllocation.objects.all()
        task = self.request.query_params.get("task")
        return qs.filter(task_id=task) if task else qs

    def create(self, request, *args, **kwargs):
        payload = AllocateResourceSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        task = get_object_or_404(Task.objects.all(), id=data["task"])
        alloc = allocate_task_resource(
            task, request.user, kind=data["kind"],
            amount_allocated=data.get("amount_allocated", 0),
            label=data.get("label", ""), notes=data.get("notes", ""))
        return Response(TaskResourceAllocationSerializer(alloc).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def reconcile(self, request, pk=None):
        alloc = reconcile_allocation(self.get_object())
        return Response(TaskResourceAllocationSerializer(alloc).data)
