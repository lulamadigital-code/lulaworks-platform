from django.contrib import admin

from .models import (
    Assignment,
    Attachment,
    AutomationRule,
    ChecklistItem,
    Comment,
    Notification,
    Phase,
    Resource,
    ResourceAllocation,
    StatusDefinition,
    Subtask,
    Task,
    TaskDependency,
    Timesheet,
    WorkPackage,
    Workspace,
)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "origin", "status", "priority", "progress_pct")
    list_filter = ("status", "priority", "origin", "risk_level")
    search_fields = ("name",)


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "code", "hourly_rate", "is_active")
    list_filter = ("kind", "is_active")
    search_fields = ("name", "code")


admin.site.register(WorkPackage)
admin.site.register(ResourceAllocation)
admin.site.register(Timesheet)

admin.site.register(Workspace)
admin.site.register(Phase)
admin.site.register(Assignment)
admin.site.register(Subtask)
admin.site.register(ChecklistItem)
admin.site.register(TaskDependency)
admin.site.register(Comment)
admin.site.register(Attachment)
admin.site.register(StatusDefinition)
admin.site.register(AutomationRule)
admin.site.register(Notification)
