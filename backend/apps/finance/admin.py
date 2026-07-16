from django.contrib import admin

from .models import (
    BudgetLine,
    CostCode,
    CostEntry,
    Invoice,
    InvoiceLine,
    Payment,
    ProjectBudget,
    Variation,
)


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("number", "project", "status", "is_progress_claim", "retention_pct")
    list_filter = ("status", "is_progress_claim")
    search_fields = ("number", "client_name")


@admin.register(ProjectBudget)
class ProjectBudgetAdmin(admin.ModelAdmin):
    list_display = ("project", "revenue", "expected_margin_pct")


@admin.register(Variation)
class VariationAdmin(admin.ModelAdmin):
    list_display = ("number", "project", "status", "estimated_cost", "revenue_impact")
    list_filter = ("status",)


admin.site.register(CostCode)
admin.site.register(CostEntry)
admin.site.register(BudgetLine)
admin.site.register(InvoiceLine)
admin.site.register(Payment)
