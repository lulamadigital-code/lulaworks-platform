"""Public-website capture models — demo requests and contact messages.

Platform-level (not tenant-scoped): these are inbound leads from anonymous
visitors, before any company exists. Kept in the DB so a submission is never
lost even before email/CRM delivery is wired.
"""

from django.db import models


class DemoRequest(models.Model):
    company = models.CharField(max_length=200)
    name = models.CharField(max_length=200)
    email = models.EmailField()
    phone = models.CharField(max_length=40, blank=True)
    industry = models.CharField(max_length=120, blank=True)
    employees = models.CharField(max_length=40, blank=True)
    preferred_date = models.DateField(null=True, blank=True)
    preferred_time = models.CharField(max_length=40, blank=True)
    notes = models.TextField(blank=True)
    handled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Demo · {self.company} ({self.email})"


class ContactMessage(models.Model):
    name = models.CharField(max_length=200)
    email = models.EmailField()
    company = models.CharField(max_length=200, blank=True)
    subject = models.CharField(max_length=200, blank=True)
    message = models.TextField()
    handled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Contact · {self.name} ({self.email})"
