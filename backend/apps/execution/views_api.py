from decimal import Decimal

from django.utils import timezone

from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.core.api import TenantViewSet
from apps.core.middleware import set_tenant_from_request
from apps.core.uploads import validate_upload

from .models import (
    Attachment,
    AttendanceEvent,
    ChecklistItem,
    Notification,
    Resource,
    ResourceAllocation,
    Assignment,
    Subtask,
    Task,
    TaskMessage,
    TaskReport,
    TaskReportComment,
    TaskThreadRead,
    TaskResourceAllocation,
    Timesheet,
    WorkPackage,
)
from .serializers import (
    AllocateResourceSerializer,
    AllocateSerializer,
    AttendanceEventSerializer,
    ChecklistItemSerializer,
    TaskMessageSerializer,
    CreateTaskReportSerializer,
    NotificationSerializer,
    ResourceAllocationSerializer,
    ResourceSerializer,
    SubtaskSerializer,
    TaskReportCommentSerializer,
    TaskReportSerializer,
    TaskResourceAllocationSerializer,
    TaskSerializer,
    TimesheetSerializer,
    WorkPackageSerializer,
)
from .services import (
    AllocationError,
    add_report_comment,
    allocate_resource,
    approve_report,
    approve_timesheet,
    can_review_reports,
    complete_task,
    mark_notifications_read,
    notify,
    notify_team,
    pause_task,
    refresh_task_status,
    resume_task,
    return_report,
    start_task,
    unread_count,
)
from .work_execution import (
    add_report_item,
    allocate_task_resource,
    attendance_summary,
    broadcast_task_message,
    can_access_task_chat,
    create_task_report,
    learn_supplier_from_receipt,
    post_system_message,
    reconcile_allocation,
    task_operational_dashboard,
)


def _project_filtered(qs, request):
    project = request.query_params.get("project")
    return qs.filter(project_id=project) if project else qs


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The signed-in user's in-app notifications. `unread` gives the badge count;
    `mark-read` clears them (all, or a given list of ids)."""

    serializer_class = NotificationSerializer

    def initial(self, request, *args, **kwargs):
        # Notification is tenant-scoped; a plain GenericViewSet must set the
        # tenant itself (TenantViewSet does this for its subclasses).
        super().initial(request, *args, **kwargs)
        set_tenant_from_request(request)

    def get_queryset(self):
        return (Notification.objects.filter(user=self.request.user)
                .order_by("-created_at"))

    @action(detail=False, methods=["get"])
    def unread(self, request):
        return Response({"count": unread_count(request.user)})

    @action(detail=False, methods=["post"], url_path="mark-read")
    def mark_read(self, request):
        mark_notifications_read(request.user, ids=request.data.get("ids"))
        return Response({"count": unread_count(request.user)})


class ChecklistItemViewSet(TenantViewSet):
    """The steps a field worker ticks off on a task. Read + toggle only (no
    create/delete via the app — labels come from the task template); the app only
    flips is_done. Ticking is field work, so work.edit is enough. ?task=<id>."""

    http_method_names = ["get", "patch", "head", "options"]
    model = ChecklistItem
    serializer_class = ChecklistItemSerializer
    required_perms = {"partial_update": ("work.edit", "execution.manage")}

    def get_queryset(self):
        qs = ChecklistItem.objects.all()
        task = self.request.query_params.get("task")
        return qs.filter(task_id=task) if task else qs

    def perform_update(self, serializer):
        from django.utils import timezone
        done = serializer.validated_data.get("is_done")
        if done is True:
            serializer.save(done_by=self.request.user, done_at=timezone.now())
        elif done is False:
            serializer.save(done_by=None, done_at=None)
        else:
            serializer.save()


class SubtaskViewSet(TenantViewSet):
    """Coarser tickable steps on a task (a checklist may hang off one). Same
    field-work gate as checklist items. Read + toggle only. ?task=<id>."""

    http_method_names = ["get", "patch", "head", "options"]
    model = Subtask
    serializer_class = SubtaskSerializer
    required_perms = {"partial_update": ("work.edit", "execution.manage")}

    def get_queryset(self):
        qs = Subtask.objects.all()
        task = self.request.query_params.get("task")
        return qs.filter(task_id=task) if task else qs


class AttendanceEventViewSet(TenantViewSet):
    """Time & attendance — clock in/out, breaks, site arrival/departure.

    Every worker records their OWN events (perform_create forces user=self), so
    no special permission is needed to clock in. A correction request lands as
    status=pending; only a manager (timesheet.approve or execution.manage) can
    approve/reject it — a worker can never silently rewrite their record.
    Filter by ?date=YYYY-MM-DD, ?user=<id> (managers), ?pending=1 (review queue).
    """

    http_method_names = ["get", "post", "patch", "head", "options"]
    model = AttendanceEvent
    serializer_class = AttendanceEventSerializer
    # create is open (self only); reviewing a correction is a manager action.
    required_perms = {"partial_update": ("timesheet.approve", "execution.manage")}

    def _is_manager(self):
        u = self.request.user
        return u.has_perm_code("timesheet.approve") or u.has_perm_code("execution.manage")

    def get_queryset(self):
        qs = AttendanceEvent.objects.all().select_related("user")
        params = self.request.query_params
        # Managers reviewing a specific event (approve/reject) may reach anyone's.
        if self.action in ("retrieve", "update", "partial_update") and self._is_manager():
            return qs
        if params.get("pending") in ("1", "true", "yes"):
            # The manager review queue — everyone's pending corrections.
            if not self._is_manager():
                return qs.none()
            qs = qs.filter(status=AttendanceEvent.Status.PENDING)
        elif params.get("user") and self._is_manager():
            qs = qs.filter(user_id=params["user"])
        else:
            qs = qs.filter(user=self.request.user)   # default: my own record
        date = params.get("date")
        if date:
            qs = qs.filter(occurred_at__date=date)
        return qs

    def perform_create(self, serializer):
        is_correction = serializer.validated_data.pop("is_correction", False)
        serializer.save(
            user=self.request.user,
            created_by=self.request.user,
            updated_by=self.request.user,
            status=(AttendanceEvent.Status.PENDING if is_correction
                    else AttendanceEvent.Status.RECORDED),
        )

    def perform_update(self, serializer):
        # Managers approve/reject corrections; stamp the reviewer.
        serializer.save(reviewed_by=self.request.user,
                        updated_by=self.request.user)

    @action(detail=False, methods=["get"])
    def today(self, request):
        """This user's events for today (or ?date=) + a computed summary:
        current state, since when, and seconds actually worked (breaks removed)."""
        from django.utils import timezone
        day = request.query_params.get("date") or timezone.localdate().isoformat()
        events = list(AttendanceEvent.objects.filter(
            user=request.user, occurred_at__date=day,
            status__in=[AttendanceEvent.Status.RECORDED,
                        AttendanceEvent.Status.APPROVED],
        ).order_by("occurred_at"))
        return Response({
            "date": day,
            "summary": attendance_summary(events),
            "events": AttendanceEventSerializer(events, many=True).data,
        })


class TaskMessageViewSet(TenantViewSet):
    """A task's chat. Read + post only. Access is per-object: a participant
    (assigned to the task) or a manager (execution.manage). The backend is the
    security boundary — a client can never read another task's (or company's)
    conversation. Filter by ?task=<id> (required for list).
    """

    http_method_names = ["get", "post", "head", "options"]
    model = TaskMessage
    serializer_class = TaskMessageSerializer
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_queryset(self):
        qs = TaskMessage.objects.all().select_related("author", "task")
        task_id = self.request.query_params.get("task")
        if not task_id:
            return qs.none()
        task = get_object_or_404(Task.objects.all(), id=task_id)
        if not can_access_task_chat(self.request.user, task):
            return qs.none()
        return qs.filter(task_id=task_id)

    def create(self, request, *args, **kwargs):
        task = get_object_or_404(Task.objects.all(), id=request.data.get("task"))
        if not can_access_task_chat(request.user, task):
            return Response(
                {"error": {"code": "forbidden",
                           "message": "You're not a participant on this task."}},
                status=status.HTTP_403_FORBIDDEN)
        body = (request.data.get("body") or "").strip()
        image = request.FILES.get("image")
        if not body and not image:
            return Response(
                {"error": {"code": "empty", "message": "Say something first."}},
                status=status.HTTP_400_BAD_REQUEST)
        if image:
            validate_upload(image)
        msg = TaskMessage.objects.create(
            task=task, company=task.company, author=request.user,
            kind=TaskMessage.Kind.IMAGE if image else TaskMessage.Kind.TEXT,
            body=body, image=image,
            created_by=request.user, updated_by=request.user)
        # Tell the other people on the task (never the sender).
        notify_team(task, verb="task_message",
                    title=f"New message on {task.name}",
                    body=(body or "Photo")[:120], actor=request.user)
        data = TaskMessageSerializer(msg, context={"request": request}).data
        # Push to anyone watching the thread in realtime (best-effort).
        broadcast_task_message(task.id, data)
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"])
    def inbox(self, request):
        """The signed-in user's task chats in one place — the tasks they're a
        participant on OR have posted in — each with its latest message and an
        unread count (messages newer than they last opened, excluding own)."""
        me = request.user
        part = Assignment.objects.filter(user=me).values_list("task_id", flat=True)
        authored = TaskMessage.objects.filter(author=me).values_list("task_id", flat=True)
        task_ids = set(part) | set(authored)
        if not task_ids:
            return Response({"threads": []})
        reads = {r.task_id: r.last_read_at
                 for r in TaskThreadRead.objects.filter(user=me, task_id__in=task_ids)}
        threads = []
        for task in Task.objects.filter(id__in=task_ids):
            last = task.messages.select_related("author").order_by("-created_at").first()
            if last is None:
                continue
            lr = reads.get(task.id)
            unread_qs = task.messages.exclude(author=me)
            if lr is not None:
                unread_qs = unread_qs.filter(created_at__gt=lr)
            threads.append({
                "task_id": str(task.id),
                "task_name": task.name,
                "unread": unread_qs.count(),
                "last_message": {
                    "body": last.body,
                    "kind": last.kind,
                    "is_system": last.kind == TaskMessage.Kind.SYSTEM,
                    "author_name": (last.author.get_full_name() if last.author else ""),
                    "created_at": last.created_at.isoformat(),
                },
            })
        threads.sort(key=lambda t: t["last_message"]["created_at"], reverse=True)
        return Response({"threads": threads})

    @action(detail=False, methods=["post"])
    def mark_read(self, request):
        """Mark a task's chat read up to now for this user (clears its unread)."""
        from django.utils import timezone
        task = get_object_or_404(Task.objects.all(), id=request.data.get("task"))
        if not can_access_task_chat(request.user, task):
            return Response(
                {"error": {"code": "forbidden", "message": "Not a participant."}},
                status=status.HTTP_403_FORBIDDEN)
        TaskThreadRead.objects.update_or_create(
            user=request.user, task=task,
            defaults={"last_read_at": timezone.now(), "company": task.company})
        return Response({"ok": True})


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
    # Creating / editing / deleting a task is management (execution.manage).
    # Progressing a task you're working on — start / complete — is field work,
    # so a groundfloor worker (work.edit) can do it too.
    required_perms = {"create": "execution.manage", "update": "execution.manage",
                      "partial_update": "execution.manage", "destroy": "execution.manage",
                      "start": ("work.edit", "execution.manage"),
                      "complete": ("work.edit", "execution.manage"),
                      "pause": ("work.edit", "execution.manage"),
                      "resume": ("work.edit", "execution.manage")}

    def get_queryset(self):
        qs = _project_filtered(
            Task.objects.all().select_related("project", "material_po"), self.request
        )
        # ?mine=1 → only tasks the requesting user is assigned to (the field
        # worker's "My tasks"). Assignment is the user↔task link (Task.assignee is
        # a Resource, not a user).
        if self.request.query_params.get("mine") in ("1", "true", "yes"):
            qs = qs.filter(assignments__user=self.request.user).distinct()
        return qs

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
        post_system_message(task,
                            f"{request.user.get_full_name() or 'A worker'} started the task.")
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        try:
            task = complete_task(self.get_object(), request.user,
                                 actual_hours=request.data.get("actual_hours"))
        except ValueError as exc:
            # The completion gate refused — required evidence is missing (§45).
            return Response({"error": {"code": "incomplete", "message": str(exc)}},
                            status=status.HTTP_409_CONFLICT)
        post_system_message(task,
                            f"{request.user.get_full_name() or 'A worker'} completed the task.")
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def pause(self, request, pk=None):
        try:
            task = pause_task(self.get_object(), request.user,
                              reason=request.data.get("reason", ""))
        except ValueError as exc:
            return Response({"error": {"code": "conflict", "message": str(exc)}},
                            status=status.HTTP_409_CONFLICT)
        post_system_message(task,
                            f"{request.user.get_full_name() or 'A worker'} paused the task.")
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def resume(self, request, pk=None):
        try:
            task = resume_task(self.get_object(), request.user)
        except ValueError as exc:
            return Response({"error": {"code": "conflict", "message": str(exc)}},
                            status=status.HTTP_409_CONFLICT)
        post_system_message(task,
                            f"{request.user.get_full_name() or 'A worker'} resumed the task.")
        return Response(TaskSerializer(task, context={"request": request}).data)

    @action(detail=True, methods=["get"])
    def operational(self, request, pk=None):
        """The task's operational hub, computed server-side: who's on it, what's
        outstanding, money allocated/spent/remaining, materials, documents,
        latest GPS, map points and the full timeline."""
        data = task_operational_dashboard(self.get_object(), request.user)
        return Response(_serialize_dashboard(data, request))


def _serialize_dashboard(data, request) -> dict:
    task = data["task"]
    fin = data["financials"]
    # Golden Rule: null money fields (the app renders them as "—") and expose no
    # allocations when the viewer may not see money. `financials` is None here.
    financials = None if fin is None else {
        "allocated": str(fin["allocated"]),
        "spent": str(fin["spent"]),
        "remaining": str(fin["remaining"]),
        "over_budget": fin["over_budget"],
        "materials_total": str(fin["materials_total"]),
        "materials_count": fin["materials_count"],
        "allocations": TaskResourceAllocationSerializer(
            fin["allocations"], many=True, context={"request": request}).data,
    }
    return {
        "task": {
            "id": str(task.id), "name": task.name, "status": task.status,
            "description": task.description, "priority": task.priority,
            "client_name": task.client_name, "site": task.site,
            "due_date": task.due_date.isoformat() if task.due_date else None,
            "site_latitude": str(task.site_latitude) if task.site_latitude is not None else None,
            "site_longitude": str(task.site_longitude) if task.site_longitude is not None else None,
        },
        "progress_pct": data["progress_pct"],
        "team": {role: [u.get_full_name() or u.email for u in users]
                 for role, users in data["team"].items()},
        "outstanding": data["outstanding"],
        "checklist": ChecklistItemSerializer(data.get("checklist", []), many=True).data,
        "subtasks": SubtaskSerializer(data.get("subtasks", []), many=True).data,
        "completion": data.get("completion"),
        "can_view_money": data.get("can_view_money", False),
        "financials": financials,
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
    # Logging hours is field work; approving them is a gated, separate step.
    # Without this, create/update/destroy fell through to "authenticated is
    # enough" — any member could delete a timesheet.
    required_perms = {"create": ("work.edit", "execution.manage"),
                      "update": ("work.edit", "execution.manage"),
                      "partial_update": ("work.edit", "execution.manage"),
                      "destroy": "execution.manage",
                      "approve": "timesheet.approve"}

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
    # Filing a field report IS the field worker's job — accept the granular field
    # permission (work.edit) as well as the management umbrella. Deleting a
    # report (destroying captured evidence) stays a management action.
    required_perms = {
        "create": ("work.edit", "execution.manage"),
        "update": ("work.edit", "execution.manage"),
        "partial_update": ("work.edit", "execution.manage"),
        "photo": ("work.edit", "execution.manage"),
        "extract_invoice": ("work.edit", "execution.manage"),
        "destroy": "execution.manage",
    }

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

    # ── Review workflow (§16 / §36) — logic lives in services (shared w/ web) ──
    def _can_review(self, user):
        return can_review_reports(user)

    def _out(self, report, request):
        return Response(
            TaskReportSerializer(report, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        """Manager sign-off on a field report."""
        if not self._can_review(request.user):
            return Response({"error": {"code": "forbidden",
                             "message": "You can't review reports."}},
                            status=status.HTTP_403_FORBIDDEN)
        return self._out(approve_report(self.get_object(), request.user), request)

    @action(detail=True, methods=["post"], url_path="return")
    def return_report(self, request, pk=None):
        """Return a report for correction, with a required comment (§36)."""
        if not self._can_review(request.user):
            return Response({"error": {"code": "forbidden",
                             "message": "You can't review reports."}},
                            status=status.HTTP_403_FORBIDDEN)
        body = (request.data.get("comment") or "").strip()
        if not body:
            return Response({"error": {"code": "empty",
                             "message": "Say what needs fixing."}},
                            status=status.HTTP_400_BAD_REQUEST)
        return self._out(return_report(self.get_object(), request.user, body), request)

    @action(detail=True, methods=["post"])
    def resubmit(self, request, pk=None):
        """The author fixes a returned report and resubmits it (§36)."""
        report = self.get_object()
        is_author = report.employee_id == request.user.id
        if not (is_author or self._can_review(request.user)):
            return Response({"error": {"code": "forbidden",
                             "message": "Only the author can resubmit this report."}},
                            status=status.HTTP_403_FORBIDDEN)
        note = (request.data.get("comment") or "").strip()
        if note:
            TaskReportComment.objects.create(report=report, company=report.company,
                                             author=request.user, body=note)
        report.status = TaskReport.ReviewStatus.SUBMITTED
        report.save(update_fields=["status", "updated_at"])
        if report.reviewed_by_id and report.reviewed_by_id != request.user.id:
            notify(report.reviewed_by, task=report.task, verb="report_resubmitted",
                   title=f"Report resubmitted: {report.title}")
        return self._out(report, request)

    @action(detail=True, methods=["post"])
    def comment(self, request, pk=None):
        """Add a message to a report's review thread (author or a reviewer)."""
        report = self.get_object()
        is_author = report.employee_id == request.user.id
        if not (is_author or self._can_review(request.user)):
            return Response({"error": {"code": "forbidden",
                             "message": "You can't comment on this report."}},
                            status=status.HTTP_403_FORBIDDEN)
        body = (request.data.get("body") or "").strip()
        if not body:
            return Response({"error": {"code": "empty", "message": "Say something."}},
                            status=status.HTTP_400_BAD_REQUEST)
        add_report_comment(report, request.user, body)
        return self._out(report, request)


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
        return Response(
            TaskResourceAllocationSerializer(alloc, context={"request": request}).data,
            status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def reconcile(self, request, pk=None):
        alloc = reconcile_allocation(self.get_object())
        return Response(
            TaskResourceAllocationSerializer(alloc, context={"request": request}).data)
