from django.contrib import admin

from .models import (
    AuditLog,
    CompanySettings,
    FeatureFlagDefinition,
    FeatureFlagOverride,
    NumberingRule,
)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "entity_type", "company", "user", "created_at")
    list_filter = ("action", "company")
    readonly_fields = [f.name for f in AuditLog._meta.fields]  # immutable

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register([CompanySettings, FeatureFlagDefinition, FeatureFlagOverride, NumberingRule])
