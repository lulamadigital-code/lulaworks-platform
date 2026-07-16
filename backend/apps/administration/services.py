"""Administration services: audit recording, atomic numbering, feature-flag
resolution."""

from django.db import transaction
from django.utils import timezone

from .models import (
    AuditLog,
    FeatureFlagDefinition,
    FeatureFlagOverride,
    NumberingRule,
    NumberSequence,
)


def record_audit(*, company=None, user=None, action, entity=None, before=None, after=None, ip=None):
    entity_type = entity.__class__.__name__ if entity is not None else ""
    entity_id = getattr(entity, "id", None)
    return AuditLog.objects.create(
        company=company, user=user, action=action, entity_type=entity_type,
        entity_id=entity_id, before=before, after=after, ip_address=ip,
    )


@transaction.atomic
def next_number(company, doc_type: str) -> str:
    """Allocate the next document number for a company/doc_type using its
    NumberingRule format. The sequence row is locked (no collisions)."""
    rule = (
        NumberingRule.objects.filter(company=company, doc_type=doc_type).first()
        or NumberingRule(company=company, doc_type=doc_type, prefix=doc_type[:3].upper())
    )
    year = timezone.localdate().year
    seq_year = year if rule.reset_yearly else 0
    seq, _ = NumberSequence.objects.select_for_update().get_or_create(
        company=company, doc_type=doc_type, year=seq_year
    )
    seq.last_seq += 1
    seq.save(update_fields=["last_seq"])
    return rule.fmt.format(
        prefix=rule.prefix, yyyy=year, yy=year % 100, seq=seq.last_seq
    )


def feature_enabled(company, key: str) -> bool:
    """Resolve a feature flag: company override → definition default → False
    (DATA_MODEL §8)."""
    override = FeatureFlagOverride.objects.filter(company=company, key=key).first()
    if override is not None:
        return override.enabled
    definition = FeatureFlagDefinition.objects.filter(key=key).first()
    return bool(definition and definition.default_enabled)
