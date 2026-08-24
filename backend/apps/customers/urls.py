from rest_framework.routers import DefaultRouter

from .views_api import CustomerContactViewSet, CustomerViewSet

router = DefaultRouter()
router.register("customers", CustomerViewSet, basename="customer")
router.register("customer-contacts", CustomerContactViewSet, basename="customer-contact")

urlpatterns = router.urls
