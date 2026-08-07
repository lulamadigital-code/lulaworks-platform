from django.contrib import admin

from .models import EmailLog


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ("to_email", "template", "category", "status", "created_at")
    list_filter = ("status", "category", "template")
    search_fields = ("to_email", "subject")
    readonly_fields = ("html_body", "text_body", "created_at", "sent_at")
