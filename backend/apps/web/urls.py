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
    path("projects/<uuid:pk>/override/", views.project_override, name="project_override"),
    path("projects/<uuid:pk>/progress-claim/", views.project_progress_claim,
         name="project_progress_claim"),
    path("compliance-items/<uuid:pk>/approve/", views.compliance_item_approve,
         name="compliance_item_approve"),
    # Quotations (view · review · edit · PDF)
    path("quotations/", views.quotations_list, name="quotations"),
    path("quotations/<uuid:pk>/", views.quotation_detail, name="quotation_detail"),
    path("quotations/<uuid:pk>/edit/", views.quotation_edit, name="quotation_edit"),
    path("quotations/<uuid:pk>/pdf/", views.quotation_pdf, name="quotation_pdf"),
    # Work (unified engine)
    path("work/", views.work_list, name="work"),
    path("work/new/", views.work_new, name="work_new"),
    path("work/<uuid:pk>/", views.work_detail, name="work_detail"),
    path("work/<uuid:pk>/start/", views.work_start, name="work_start"),
    path("work/<uuid:pk>/complete/", views.work_complete, name="work_complete"),
    # RFQ (front door)
    path("rfq/", views.rfq_list, name="rfq"),
    path("rfq/upload/", views.rfq_upload, name="rfq_upload"),
    path("rfq/<uuid:pk>/", views.rfq_detail, name="rfq_detail"),
    path("rfq/<uuid:pk>/approve/", views.rfq_approve, name="rfq_approve"),
    # Estimating
    path("estimates/", views.estimates_list, name="estimates"),
    path("estimates/<uuid:pk>/", views.estimate_detail, name="estimate_detail"),
    path("estimates/<uuid:pk>/approve/", views.estimate_approve, name="estimate_approve"),
    path("estimates/<uuid:pk>/revise/", views.estimate_revise, name="estimate_revise"),
    # Procurement
    path("suppliers/", views.suppliers_list, name="suppliers"),
    path("purchase-orders/", views.purchase_orders_list, name="purchase_orders"),
    path("purchase-orders/<uuid:pk>/", views.po_detail, name="po_detail"),
    path("purchase-orders/<uuid:pk>/approve/", views.po_approve, name="po_approve"),
    path("purchase-orders/<uuid:pk>/receive/", views.po_receive, name="po_receive"),
    # Commercial + AI
    path("commercial/", views.commercial, name="commercial"),
    path("invoices/<uuid:pk>/payment/", views.invoice_payment, name="invoice_payment"),
    path("lulama/", views.lulama, name="lulama"),
]
