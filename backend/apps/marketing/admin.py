from django.contrib import admin

from .models import ContactMessage, DemoRequest


@admin.register(DemoRequest)
class DemoRequestAdmin(admin.ModelAdmin):
    list_display = ("company", "name", "email", "industry", "preferred_date", "handled", "created_at")
    list_filter = ("handled", "industry")
    search_fields = ("company", "name", "email")


@admin.register(ContactMessage)
class ContactMessageAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "company", "subject", "handled", "created_at")
    list_filter = ("handled",)
    search_fields = ("name", "email", "company", "message")
