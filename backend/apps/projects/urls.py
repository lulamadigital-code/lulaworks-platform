from rest_framework.routers import DefaultRouter

from .views_api import ProjectViewSet

router = DefaultRouter()
router.register("projects", ProjectViewSet, basename="project")

urlpatterns = router.urls
