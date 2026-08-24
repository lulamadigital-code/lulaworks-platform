from rest_framework.routers import DefaultRouter

from .views_api import (
    ChecklistItemViewSet,
    NotificationViewSet,
    ResourceAllocationViewSet,
    ResourceViewSet,
    SubtaskViewSet,
    TaskReportViewSet,
    TaskResourceAllocationViewSet,
    TaskViewSet,
    TimesheetViewSet,
    WorkPackageViewSet,
)

router = DefaultRouter()
router.register("work-packages", WorkPackageViewSet, basename="work-package")
router.register("tasks", TaskViewSet, basename="task")
router.register("notifications", NotificationViewSet, basename="notification")
router.register("resources", ResourceViewSet, basename="resource")
router.register("resource-allocations", ResourceAllocationViewSet, basename="resource-allocation")
router.register("task-reports", TaskReportViewSet, basename="task-report")
router.register("task-allocations", TaskResourceAllocationViewSet, basename="task-allocation")
router.register("timesheets", TimesheetViewSet, basename="timesheet")
router.register("checklist-items", ChecklistItemViewSet, basename="checklist-item")
router.register("subtasks", SubtaskViewSet, basename="subtask")

urlpatterns = router.urls
