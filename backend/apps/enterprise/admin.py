from django.contrib import admin

from .models import ApiKey, AuditEvent, SSOConfig


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "company", "action", "actor_label", "summary")
    list_filter = ("action",)
    search_fields = ("summary", "actor_label", "target_id")
    readonly_fields = [f.name for f in AuditEvent._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ApiKey)
class ApiKeyAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "token_prefix", "last_used_at", "revoked_at")
    search_fields = ("name", "token_prefix")
    readonly_fields = ("key_hash", "token_prefix", "last_four", "last_used_at")


@admin.register(SSOConfig)
class SSOConfigAdmin(admin.ModelAdmin):
    list_display = ("company", "protocol", "status", "requested_at")
    list_filter = ("protocol", "status")
    search_fields = ("company__name", "saml_idp_entity_id", "oidc_issuer")
