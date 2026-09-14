"""Storage housekeeping for imported documents.

A soft-delete (the default) keeps the row and its file — recoverable. Only a
HARD delete (e.g. purging a whole import batch) fires Django's post_delete, and
that's when we both remove the stored file from disk and give the bytes back to
the company's storage quota. Registering this receiver also stops Django from
"fast-deleting" ImportedDocument rows in bulk, so the signal runs for every row
in a cascaded batch delete.
"""
import logging

from django.db.models.signals import post_delete
from django.dispatch import receiver

from .models import ImportedDocument

log = logging.getLogger("apps.knowledge")


@receiver(post_delete, sender=ImportedDocument)
def _reclaim_document_storage(sender, instance, **kwargs):
    f = instance.file
    if not f or not f.name:
        return
    size = 0
    try:
        size = f.size
    except Exception:                         # noqa: BLE001 — file may already be gone
        size = 0
    # Delete the physical file (Django never does this on its own).
    try:
        f.storage.delete(f.name)
    except Exception:                         # noqa: BLE001
        log.warning("Could not delete import file %s on hard delete", f.name)
    # Give the bytes back to the plan quota.
    if size:
        try:
            from apps.identity.models import Company
            from apps.storage.services import release_storage
            company = Company.all_objects.filter(pk=instance.company_id).first()
            if company is not None:
                release_storage(company, size)
        except Exception:                     # noqa: BLE001
            log.warning("Could not release storage for company %s", instance.company_id)
