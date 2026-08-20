"""LulaWorks Customer Education & Growth Engine — content models.

This is a *platform-owned* library (not tenant-scoped): LulaWorks staff author
guides, lessons, templates, calculators and learning paths that are the same for
every visitor and tenant. It is inbound-growth content, so it is deliberately
independent of the ERP/CRM data model and safe to grow on its own. See the
`lulaworks_education_engine` memory for the strategy and roadmap.
"""

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class ResourceKind(models.TextChoices):
    ARTICLE = "article", "Article"
    GUIDE = "guide", "Guide"
    LESSON = "lesson", "Lesson"
    COURSE = "course", "Course"
    TEMPLATE = "template", "Template"
    CALCULATOR = "calculator", "Calculator"
    CHECKLIST = "checklist", "Checklist"
    VIDEO = "video", "Video"


class Difficulty(models.TextChoices):
    BEGINNER = "beginner", "Beginner"
    INTERMEDIATE = "intermediate", "Intermediate"
    ADVANCED = "advanced", "Advanced"


class ContentStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    REVIEW = "review", "In review"
    PUBLISHED = "published", "Published"
    ARCHIVED = "archived", "Archived"


def _unique_slug(model, value, *, pk=None):
    base = slugify(value)[:230] or "item"
    slug, n = base, 2
    qs = model.objects.all()
    if pk:
        qs = qs.exclude(pk=pk)
    while qs.filter(slug=slug).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


class ResourceCategory(models.Model):
    """A top-level area of the Learning Centre (e.g. Quoting, Procurement,
    Profitability). Groups resources and drives the index page."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140, unique=True, blank=True)
    description = models.CharField(max_length=255, blank=True)
    icon = models.CharField(max_length=8, blank=True)          # a single emoji
    order = models.PositiveSmallIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name_plural = "Resource categories"

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(ResourceCategory, self.name, pk=self.pk)
        super().save(*args, **kwargs)


class Resource(models.Model):
    """One piece of educational content — an article, guide, template,
    calculator, checklist or video. Every resource answers a real business
    problem and connects to the LulaWorks feature that automates it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=16, choices=ResourceKind.choices,
                            default=ResourceKind.ARTICLE)
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    summary = models.CharField(max_length=300, blank=True)
    body = models.TextField(blank=True)                        # Markdown/HTML

    category = models.ForeignKey(ResourceCategory, on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name="resources")
    industry = models.CharField(max_length=120, blank=True)    # "" = all
    company_size = models.CharField(max_length=40, blank=True)  # "" = all
    difficulty = models.CharField(max_length=16, choices=Difficulty.choices,
                                  default=Difficulty.BEGINNER)
    read_minutes = models.PositiveSmallIntegerField(default=4)

    status = models.CharField(max_length=12, choices=ContentStatus.choices,
                              default=ContentStatus.DRAFT)
    is_featured = models.BooleanField(default=False)

    #: Feature keys this content connects to (e.g. ["quotations","crm"]) — drives
    #: the in-app "Learn → Apply" prompts and the content→product CTAs.
    related_features = models.JSONField(default=list, blank=True)
    cta_label = models.CharField(max_length=80, blank=True)
    cta_url = models.CharField(max_length=300, blank=True)      # named url or path

    seo_title = models.CharField(max_length=200, blank=True)
    seo_description = models.CharField(max_length=300, blank=True)
    featured_image_url = models.URLField(blank=True)

    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                               null=True, blank=True, related_name="+")
    published_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["status", "kind"]),
            models.Index(fields=["status", "is_featured"]),
        ]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Resource, self.title, pk=self.pk)
        if self.status == ContentStatus.PUBLISHED and self.published_at is None:
            self.published_at = timezone.now()
        super().save(*args, **kwargs)

    @property
    def is_published(self) -> bool:
        return self.status == ContentStatus.PUBLISHED


class LearningPath(models.Model):
    """A guided sequence of lessons — e.g. 'Start Your Contractor Business' or
    'Win More Jobs'. Walks a user from a business goal through the LulaWorks
    workflow that achieves it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    summary = models.CharField(max_length=300, blank=True)
    industry = models.CharField(max_length=120, blank=True)
    icon = models.CharField(max_length=8, blank=True)
    status = models.CharField(max_length=12, choices=ContentStatus.choices,
                              default=ContentStatus.DRAFT)
    order = models.PositiveSmallIntegerField(default=100)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order", "title"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(LearningPath, self.title, pk=self.pk)
        super().save(*args, **kwargs)

    @property
    def is_published(self) -> bool:
        return self.status == ContentStatus.PUBLISHED


class EducationLead(models.Model):
    """An inbound lead captured through the Education Engine — someone who read a
    guide, used a tool or grabbed a template and opted in. Pre-account and
    pre-tenant, so it lives here (not in the tenant-scoped CRM). Carries a running
    engagement `score`; sales/CRM pick up the hot ones. Progressive: the content
    is always fully usable WITHOUT giving an email — capture is opt-in only."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=150, blank=True)
    company = models.CharField(max_length=200, blank=True)
    industry = models.CharField(max_length=120, blank=True)
    company_size = models.CharField(max_length=40, blank=True)
    role = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=40, blank=True)
    challenge = models.TextField(blank=True)               # main business challenge
    first_source = models.CharField(max_length=160, blank=True)  # slug they arrived on
    score = models.PositiveIntegerField(default=0)
    has_account = models.BooleanField(default=False)       # converted to a signup
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-score", "-updated_at"]

    def __str__(self):
        return self.email


class LeadEvent(models.Model):
    """One scored action by a lead — the audit trail behind the running score."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    lead = models.ForeignKey(EducationLead, on_delete=models.CASCADE,
                             related_name="events")
    event = models.CharField(max_length=40)
    points = models.PositiveSmallIntegerField(default=0)
    detail = models.CharField(max_length=160, blank=True)   # e.g. the slug
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.event} (+{self.points}) — {self.lead_id}"


class LearningPathStep(models.Model):
    """One step in a learning path. Either points at a Resource, or stands alone
    with its own title/description (for steps that map to a product action)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    path = models.ForeignKey(LearningPath, on_delete=models.CASCADE,
                             related_name="steps")
    order = models.PositiveSmallIntegerField(default=0)
    resource = models.ForeignKey(Resource, on_delete=models.SET_NULL, null=True,
                                 blank=True, related_name="+")
    title = models.CharField(max_length=200, blank=True)
    description = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["path", "order"]

    def __str__(self):
        return self.title or (self.resource.title if self.resource else "Step")

    @property
    def label(self) -> str:
        return self.title or (self.resource.title if self.resource else "Step")
