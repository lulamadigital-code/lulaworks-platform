from django.apps import AppConfig


class QuotesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.quotes"
    label = "quotes"

    def ready(self):
        # Make quotation / invoice / delivery-note PDFs attachable by the email
        # platform (registers builders keyed by attachment kind).
        from .email import register_builders
        register_builders()
