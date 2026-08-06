from django.contrib import admin

from .models import TaxJurisdiction


@admin.register(TaxJurisdiction)
class TaxJurisdictionAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "tax_name", "rate", "prices_include_tax", "reverse_charge_region")
    list_filter = ("tax_name", "reverse_charge_region", "prices_include_tax")
    search_fields = ("name", "code")
