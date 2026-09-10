from rest_framework.routers import DefaultRouter

from .views_api import ImportBatchViewSet

router = DefaultRouter()
router.register("import-batches", ImportBatchViewSet, basename="import-batch")

urlpatterns = router.urls
