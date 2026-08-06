from django.contrib import admin

from .models import CheckoutIntent, GatewayEvent, PaymentCustomer


@admin.register(CheckoutIntent)
class CheckoutIntentAdmin(admin.ModelAdmin):
    list_display = ("company", "kind", "gateway", "currency", "amount", "status", "created_at")
    list_filter = ("kind", "gateway", "status")
    search_fields = ("company__name", "plan_code", "pack_code")


@admin.register(PaymentCustomer)
class PaymentCustomerAdmin(admin.ModelAdmin):
    list_display = ("company", "gateway", "external_customer_id")
    list_filter = ("gateway",)


@admin.register(GatewayEvent)
class GatewayEventAdmin(admin.ModelAdmin):
    list_display = ("gateway", "external_event_id", "kind", "processed", "created_at")
    list_filter = ("gateway", "processed")
