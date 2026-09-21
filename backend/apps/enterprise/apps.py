from django.apps import AppConfig


class EnterpriseConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.enterprise"
    verbose_name = "Enterprise Governance"

    def ready(self):
        # Register audit signal handlers (login/logout trail).
        from . import signals  # noqa: F401
