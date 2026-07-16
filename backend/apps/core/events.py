"""Domain event bus (DATA_MODEL §13). Publishing writes a DomainEvent row in
the caller's transaction (transactional outbox); a relay dispatches to
subscribers. Idempotent consumers key on event_id."""

from .models import DomainEvent


def publish(event_type: str, *, company=None, subject=None, payload=None, actor=None) -> DomainEvent:
    subject_type = subject.__class__.__name__ if subject is not None else ""
    subject_id = getattr(subject, "id", None)
    return DomainEvent.objects.create(
        company=company,
        type=event_type,
        subject_type=subject_type,
        subject_id=subject_id,
        payload=payload or {},
        actor=actor,
    )
