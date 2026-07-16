"""Storage engine (DATA_MODEL §10). File metadata in Postgres; blobs in S3
(prod). Quota enforced before upload."""

from django.conf import settings
from django.db import models

from apps.core.models import TenantBaseModel


class StorageFile(TenantBaseModel):
    module = models.CharField(max_length=40, blank=True)
    project_id = models.UUIDField(null=True, blank=True)
    document_type = models.CharField(max_length=40, blank=True)
    original_name = models.CharField(max_length=255)
    storage_path = models.CharField(max_length=512)  # S3 key
    version = models.PositiveIntegerField(default=1)
    checksum = models.CharField(max_length=64, blank=True)  # sha256
    file_size = models.BigIntegerField(default=0)
    mime_type = models.CharField(max_length=120, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    virus_scan_status = models.CharField(max_length=16, default="pending")
    ocr_status = models.CharField(max_length=16, default="none")
    ai_processed_status = models.CharField(max_length=16, default="none")
    retention_policy = models.CharField(max_length=32, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "module"])]

    def __str__(self):
        return self.original_name
