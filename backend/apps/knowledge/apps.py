from django.apps import AppConfig


class KnowledgeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.knowledge"
    label = "knowledge"

    def ready(self):
        from . import signals  # noqa: F401 — register post_delete storage housekeeping
