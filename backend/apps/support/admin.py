from django.contrib import admin

from .models import ErrorEvent, SupportAttachment, SupportMessage, SupportTicket


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("number", "subject", "company", "status", "priority", "category",
                    "assigned_agent", "created_at")
    list_filter = ("status", "priority", "category")
    search_fields = ("number", "subject", "description")
    readonly_fields = ("number",)


admin.site.register(SupportMessage)
admin.site.register(SupportAttachment)


@admin.register(ErrorEvent)
class ErrorEventAdmin(admin.ModelAdmin):
    list_display = ("reference", "view_name", "exception_type", "company", "created_at")
    search_fields = ("reference", "request_id", "path")
    readonly_fields = [f.name for f in ErrorEvent._meta.fields]
