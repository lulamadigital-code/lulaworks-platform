from django.contrib import admin

from .models import Plan, Subscription


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "price", "max_users", "monthly_ai_credits", "is_active")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("company", "plan", "status", "seats", "current_period_end")
    list_filter = ("status", "plan")


from .models import EnterpriseAgreement  # noqa: E402


@admin.register(EnterpriseAgreement)
class EnterpriseAgreementAdmin(admin.ModelAdmin):
    list_display = ("company", "version", "status", "provisioning_status",
                    "accepted_at", "finalized_at")
    list_filter = ("status", "provisioning_status")
    search_fields = ("company__name", "contract_ref", "accepted_by_email")
    readonly_fields = [f.name for f in EnterpriseAgreement._meta.fields]

    def has_add_permission(self, request):
        return False


from .models import EnterpriseDocument  # noqa: E402


@admin.register(EnterpriseDocument)
class EnterpriseDocumentAdmin(admin.ModelAdmin):
    list_display = ("company", "kind", "name", "created_at")
    list_filter = ("kind",)
    search_fields = ("company__name", "name")
