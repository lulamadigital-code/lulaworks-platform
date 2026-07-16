from django.contrib import admin

from .models import ComplianceItem, ComplianceOverride, ComplianceRequirement


@admin.register(ComplianceRequirement)
class ComplianceRequirementAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "category", "source", "is_mandatory", "is_active")
    list_filter = ("category", "source", "is_mandatory", "is_active")
    search_fields = ("code", "name")


@admin.register(ComplianceItem)
class ComplianceItemAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "category", "status", "is_mandatory", "expiry")
    list_filter = ("category", "status", "is_mandatory")
    search_fields = ("name",)


admin.site.register(ComplianceOverride)
