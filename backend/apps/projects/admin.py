from django.contrib import admin

from .models import Project


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("number", "client_name", "work_type", "status", "awarded_at")
    list_filter = ("status", "work_type")
    search_fields = ("number", "client_name", "title")
