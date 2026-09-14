from django.apps import AppConfig


class ReferenceConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.reference"
    label = "reference"
    verbose_name = "International reference data"
