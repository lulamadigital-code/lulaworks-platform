"""Background jobs for the quotes app — currently the AI document import, which
makes a slow multimodal call and must not run inside the web request (it would
block a gunicorn worker and hit the proxy timeout)."""

from celery import shared_task


@shared_task(ignore_result=True)
def run_import_task(ti_id, user_id):
    """Analyse + AI-recreate an uploaded document off the request path. Loads the
    import cross-tenant, then runs within its company's scope so metering / writes
    stay isolated."""
    from apps.core.context import tenant_scope
    from apps.identity.models import User

    from .models import TemplateImport
    from .template_import import run_import

    ti = TemplateImport.all_objects.filter(id=ti_id).select_related("company").first()
    if ti is None:
        return
    user = User.objects.filter(id=user_id).first()
    with tenant_scope(ti.company_id):
        run_import(ti, user)
