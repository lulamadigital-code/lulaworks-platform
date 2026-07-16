from django.contrib import admin

from .models import Estimate, EstimateActual, EstimateLine, EstimateSection


class EstimateLineInline(admin.TabularInline):
    model = EstimateLine
    extra = 0


class EstimateSectionInline(admin.TabularInline):
    model = EstimateSection
    extra = 0


@admin.register(Estimate)
class EstimateAdmin(admin.ModelAdmin):
    list_display = ("number", "version", "client_name", "status", "risk_score")
    list_filter = ("status", "work_type")
    search_fields = ("number", "client_name", "title")
    inlines = [EstimateSectionInline]


admin.site.register(EstimateSection)
admin.site.register(EstimateLine)
admin.site.register(EstimateActual)
