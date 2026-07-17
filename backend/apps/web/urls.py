from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("projects/", views.projects_list, name="projects"),
    path("projects/<uuid:pk>/", views.project_detail, name="project_detail"),
    path("projects/<uuid:pk>/readiness/", views.readiness_partial, name="readiness_partial"),
    # Estimating
    path("estimates/", views.estimates_list, name="estimates"),
    path("estimates/<uuid:pk>/", views.estimate_detail, name="estimate_detail"),
    path("estimates/<uuid:pk>/approve/", views.estimate_approve, name="estimate_approve"),
    # Procurement
    path("suppliers/", views.suppliers_list, name="suppliers"),
    path("purchase-orders/", views.purchase_orders_list, name="purchase_orders"),
    path("purchase-orders/<uuid:pk>/", views.po_detail, name="po_detail"),
    path("purchase-orders/<uuid:pk>/approve/", views.po_approve, name="po_approve"),
    # Commercial + AI
    path("commercial/", views.commercial, name="commercial"),
    path("lulama/", views.lulama, name="lulama"),
]
