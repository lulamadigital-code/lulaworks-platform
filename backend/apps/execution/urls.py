from rest_framework.routers import DefaultRouter

from .views_api import (
    ResourceAllocationViewSet,
    ResourceViewSet,
    TaskViewSet,
    TimesheetViewSet,
    WorkPackageViewSet,
)

router = DefaultRouter()
router.register("work-packages", WorkPackageViewSet, basename="work-package")
router.register("tasks", TaskViewSet, basename="task")
router.register("resources", ResourceViewSet, basename="resource")
router.register("resource-allocations", ResourceAllocationViewSet, basename="resource-allocation")
router.register("timesheets", TimesheetViewSet, basename="timesheet")

urlpatterns = router.urls
