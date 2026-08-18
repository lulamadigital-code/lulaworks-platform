from django.contrib import admin

from .models import SupportAttachment, SupportMessage, SupportTicket


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ("number", "subject", "company", "status", "priority", "category",
                    "assigned_agent", "created_at")
    list_filter = ("status", "priority", "category")
    search_fields = ("number", "subject", "description")
    readonly_fields = ("number",)


admin.site.register(SupportMessage)
admin.site.register(SupportAttachment)
