"""Read helpers for the Education Engine — published-content queries and the
in-app 'Learn → Apply' prompt lookup. Kept tiny and defensive so the marketing
site and the in-app hooks never break when content is missing."""

from .models import ContentStatus, LearningPath, Resource


def published_resources():
    return Resource.objects.filter(status=ContentStatus.PUBLISHED)


def published_paths():
    return LearningPath.objects.filter(status=ContentStatus.PUBLISHED)


def featured_resources(limit=3):
    return list(published_resources().filter(is_featured=True)[:limit])


def prompt_for(feature: str):
    """The best published resource to surface next to a given Lulaworks feature
    (e.g. 'quotations'), or None. Drives the in-app Learn → Apply banners."""
    if not feature:
        return None
    return (published_resources()
            .filter(related_features__contains=[feature])
            .order_by("-is_featured", "-published_at")
            .first())
