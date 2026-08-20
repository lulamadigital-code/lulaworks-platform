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


@shared_task(ignore_result=True)
def generate_import_siblings_task(template_id, user_id):
    """After an imported template is approved, create the matching templates for the
    other two document types (adapting the approved HTML) — off the request path."""
    from apps.core.context import tenant_scope
    from apps.identity.models import User

    from .models import DocumentTemplate
    from .template_import import create_siblings_from_template

    tpl = (DocumentTemplate.all_objects.select_related("company", "current_version")
           .filter(id=template_id).first())
    if tpl is None:
        return
    user = User.objects.filter(id=user_id).first()
    with tenant_scope(tpl.company_id):
        create_siblings_from_template(tpl, user)
