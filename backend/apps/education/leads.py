"""Lead capture & scoring for the Education Engine.

Progressive, never forced: every guide, tool and template is fully usable
without giving an email. When a visitor *chooses* to opt in (save a result, get
the growth kit), we create a lead and start scoring their engagement. The score
tells sales/CRM who is warming up — the goal is timely help, not spam.
"""

from django.db.models import Sum

from .models import EducationLead, LeadEvent

#: Points per action (from the brief). Higher = closer to a serious buyer.
LEAD_SCORES = {
    "content_read": 1,
    "template_used": 2,
    "tool_used": 3,
    "opt_in": 3,
    "account_created": 5,
    "customer_created": 5,
    "quotation_created": 10,
    "rfq_created": 10,
    "job_created": 15,
    "invoice_created": 15,
    "employee_invited": 20,
    "multi_module": 20,
}

_PROFILE_FIELDS = ("name", "company", "industry", "company_size", "role",
                   "phone", "challenge")


def capture_lead(*, email, event="opt_in", request=None, detail="", **profile):
    """Create or update a lead by email, record a scored event, and return the
    lead (or None if no usable email). Only fills profile fields that are given
    and currently empty — we never overwrite better data with blanks. Safe to
    call from anywhere; failures never raise into the request."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return None
    try:
        lead, created = EducationLead.objects.get_or_create(
            email=email, defaults={"first_source": detail[:160]})
        changed = False
        for f in _PROFILE_FIELDS:
            val = (profile.get(f) or "").strip()
            if val and not getattr(lead, f):
                setattr(lead, f, val)
                changed = True

        points = LEAD_SCORES.get(event, 1)
        LeadEvent.objects.create(lead=lead, event=event, points=points,
                                 detail=detail[:160])
        lead.score = (lead.events.aggregate(s=Sum("points"))["s"] or 0)
        if event == "account_created":
            lead.has_account = True
            changed = True
        lead.save()

        try:
            from apps.analytics.services import track
            track("lead_created" if created else "lead_event", request=request,
                  module="education", feature=event, source="education",
                  metadata={"detail": detail, "score": lead.score})
        except Exception:               # noqa: BLE001 - analytics is best-effort
            pass
        return lead
    except Exception:                   # noqa: BLE001 - capture must never 500 a page
        return None


def score_signup(email, request=None):
    """When someone creates a LulaWorks account, credit the matching lead — this
    is what closes the content → signup loop and makes it measurable. Only scores
    an existing lead (does not create one from a signup)."""
    email = (email or "").strip().lower()
    if not email:
        return
    if EducationLead.objects.filter(email=email).exists():
        capture_lead(email=email, event="account_created", request=request,
                     detail="trial")
