"""Central audit trail.

Writes an immutable ``administration.AuditLog`` row for consequential actions —
approvals, downloads, PO uploads, document generation — capturing who, which
company, what, and from where. This is separate from the per-quotation domain
events (which drive the timeline): the audit log is the append-only record for
production diagnostics and compliance, never edited or deleted.
"""


def client_ip(request):
    """The caller's IP, honouring a single proxy hop via X-Forwarded-For."""
    if request is None:
        return None
    fwd = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR") or None


def audit(request, action, *, entity=None, entity_type="", entity_id=None,
          before=None, after=None, company_id=None):
    """Record one audited action. Never raises — an audit failure must not break
    the user's request; it is logged and swallowed."""
    from apps.administration.models import AuditLog
    try:
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            user = None
        if company_id is None:
            company_id = getattr(user, "company_id", None) if user else None
        if entity is not None:
            entity_type = entity_type or entity.__class__.__name__
            entity_id = entity_id or getattr(entity, "id", None)
        AuditLog.objects.create(
            company_id=company_id, user=user, action=action,
            entity_type=entity_type, entity_id=entity_id,
            before=before, after=after, ip_address=client_ip(request))
    except Exception:      # noqa: BLE001 — auditing must never break the request
        import logging
        logging.getLogger(__name__).exception("audit write failed: %s", action)
