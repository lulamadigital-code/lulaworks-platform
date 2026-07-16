from django.contrib import admin

from .models import Resource, ResourceAllocation, Task, Timesheet, WorkPackage


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "status", "priority", "progress_pct")
    list_filter = ("status", "priority")
    search_fields = ("name",)


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "code", "hourly_rate", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("name", "code")


admin.site.register(WorkPackage)
admin.site.register(ResourceAllocation)
admin.site.register(Timesheet)
