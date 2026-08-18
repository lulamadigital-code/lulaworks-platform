from django.contrib import admin

from .models import AnalyticsEvent


@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = ("event_name", "module", "company", "user", "source", "device", "created_at")
    list_filter = ("event_name", "module", "source", "device")
    search_fields = ("event_name", "path", "session_id", "anonymous_id")
    readonly_fields = [f.name for f in AnalyticsEvent._meta.fields]
