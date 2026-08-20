from django.contrib import admin

from .models import LearningPath, LearningPathStep, Resource, ResourceCategory


@admin.register(ResourceCategory)
class ResourceCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "order", "slug")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("order", "name")


@admin.register(Resource)
class ResourceAdmin(admin.ModelAdmin):
    list_display = ("title", "kind", "category", "status", "is_featured",
                    "difficulty", "published_at")
    list_filter = ("status", "kind", "difficulty", "category", "is_featured")
    search_fields = ("title", "summary", "body", "industry")
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ("author",)
    date_hierarchy = "published_at"
    fieldsets = (
        (None, {"fields": ("kind", "title", "slug", "summary", "body")}),
        ("Classification", {"fields": ("category", "industry", "company_size",
                                       "difficulty", "read_minutes",
                                       "related_features")}),
        ("Call to action", {"fields": ("cta_label", "cta_url")}),
        ("SEO & media", {"fields": ("seo_title", "seo_description",
                                    "featured_image_url")}),
        ("Publishing", {"fields": ("status", "is_featured", "author",
                                   "published_at")}),
    )


class LearningPathStepInline(admin.TabularInline):
    model = LearningPathStep
    extra = 1
    autocomplete_fields = ("resource",)


@admin.register(LearningPath)
class LearningPathAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "order", "industry")
    list_filter = ("status", "industry")
    prepopulated_fields = {"slug": ("title",)}
    inlines = [LearningPathStepInline]
