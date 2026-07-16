from django.contrib import admin

from .models import Company, Membership, Permission, Role, User


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "subscription_status", "is_active", "created_at")
    search_fields = ("name", "registration_no", "vat_no")


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "first_name", "last_name", "active_company", "is_staff", "is_active")
    search_fields = ("email", "first_name", "last_name")
    list_filter = ("is_staff", "is_active", "active_company")


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "is_system")
    filter_horizontal = ("permissions",)


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ("codename", "module", "label")
    search_fields = ("codename",)
    list_filter = ("module",)


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "company", "role", "status")
    list_filter = ("company", "status")
