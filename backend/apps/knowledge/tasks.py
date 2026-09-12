"""Background jobs for Historical Import — document processing off the request
path, so uploads return instantly and nothing can hit an Nginx/Gunicorn timeout."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def process_import_document(self, doc_id):
    """Process one uploaded historical document in the worker, within its own
    tenant scope. Idempotent (see historical_import.process_document)."""
    from apps.core.context import tenant_scope
    from .historical_import import process_document
    from .models import ImportedDocument

    doc = (ImportedDocument.all_objects
           .filter(id=doc_id).select_related("batch", "created_by").first())
    if doc is None:
        return {"doc": str(doc_id), "status": "missing"}
    try:
        with tenant_scope(doc.company_id):
            process_document(doc)
        return {"doc": str(doc_id), "status": doc.status}
    except Exception as exc:                          # noqa: BLE001
        logger.warning("process_import_document %s failed: %s", doc_id, exc)
        return {"doc": str(doc_id), "status": "error"}
