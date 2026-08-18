"""LulaWorks product & website analytics — a single, standardized event stream.

Privacy is built in: events carry METADATA, never business content. A company
only ever appears in its own tenant analytics; the platform owner sees the whole
picture. Events are append-only and cheap to write.
"""
import uuid

from django.conf import settings
from django.db import models


class AnalyticsEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_name = models.CharField(max_length=64, db_index=True)
    event_version = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # Identity — anonymous for website visitors, resolved once signed in.
    session_id = models.CharField(max_length=40, blank=True, db_index=True)
    anonymous_id = models.CharField(max_length=40, blank=True, db_index=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                             null=True, blank=True, related_name="+")
    company = models.ForeignKey("identity.Company", on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="+", db_index=True)

    # Context
    path = models.CharField(max_length=300, blank=True)      # page / screen
    module = models.CharField(max_length=40, blank=True, db_index=True)
    feature = models.CharField(max_length=64, blank=True)
    source = models.CharField(max_length=40, blank=True)     # direct|organic|social|referral|campaign
    device = models.CharField(max_length=16, blank=True)     # desktop|mobile|tablet
    browser = models.CharField(max_length=24, blank=True)

    # Safe, non-sensitive properties only (scrubbed on write).
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event_name", "created_at"]),
            models.Index(fields=["company", "created_at"]),
        ]

    def __str__(self):
        return f"{self.event_name} @ {self.created_at:%Y-%m-%d %H:%M}"
