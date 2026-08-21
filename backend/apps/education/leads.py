"""Lead capture & scoring for the Education Engine.

Progressive, never forced: every guide, tool and template is fully usable
without giving an email. When a visitor *chooses* to opt in (save a result, get
the growth kit), we create a lead and start scoring their engagement. The score
tells sales/CRM who is warming up — the goal is timely help, not spam.
"""

from django.core import signing
from django.db.models import Sum
from django.utils import timezone

from .models import EducationLead, LeadEvent

_UNSUB_SALT = "education.unsubscribe"


def unsubscribe_token(lead) -> str:
    """A tamper-proof token for a one-click unsubscribe link (no login needed)."""
    return signing.dumps(str(lead.id), salt=_UNSUB_SALT)


def lead_from_token(token):
    """Resolve an unsubscribe token back to its lead, or None if invalid."""
    try:
        lead_id = signing.loads(token, salt=_UNSUB_SALT, max_age=None)
    except signing.BadSignature:
        return None
    return EducationLead.objects.filter(id=lead_id).first()

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

#: A lead is pushed into the sales CRM once it shows real intent — either it
#: crosses this engagement score, or it created an account. Below this, it stays
#: in the Education lead list only (so the CRM pipeline isn't full of one-page
#: visitors).
CRM_SYNC_THRESHOLD = 5


def crm_company():
    """The one tenant marked to receive Education leads (the LulaWorks sales
    team's own company), or None. When None, the CRM bridge is a safe no-op."""
    from apps.identity.models import Company
    return Company.objects.filter(
        receives_education_leads=True, is_active=True).first()


def sync_lead_to_crm(ed_lead):
    """Mirror a hot Education lead into the sales company's CRM pipeline as a
    Lead (source 'LulaWorks Academy'), matched by email so it never duplicates.
    Fully fail-safe: any problem (no sales company, scoping, etc.) is swallowed."""
    try:
        company = crm_company()
        if company is None:
            return None
        from apps.core.context import tenant_scope
        from apps.customers.models import Lead as CrmLead
        from apps.customers.services import create_lead
        note = f"From LulaWorks Academy · engagement score {ed_lead.score}."
        if ed_lead.first_source:
            note += f" First touch: {ed_lead.first_source}."
        if ed_lead.challenge:
            note += f" Challenge: {ed_lead.challenge}"
        with tenant_scope(company.id):
            existing = (CrmLead.objects.filter(email__iexact=ed_lead.email).first()
                        if ed_lead.email else None)
            if existing:
                if not existing.notes:          # don't clobber a rep's working notes
                    existing.notes = note
                    existing.save(update_fields=["notes", "updated_at"])
                return existing
            return create_lead(
                company, None,
                company_name=(ed_lead.company or ed_lead.name or ed_lead.email),
                contact_name=ed_lead.name, email=ed_lead.email,
                telephone=ed_lead.phone, industry=ed_lead.industry,
                source="LulaWorks Academy", notes=note)
    except Exception:                           # noqa: BLE001 - bridge is best-effort
        return None


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

        # First time we see this email → welcome them (they gave it via the form).
        if created and lead.subscribed and lead.email and lead.welcomed_at is None:
            send_welcome_email(lead, request=request)

        # Hand hot leads to the sales CRM (idempotent; no-op if unconfigured).
        if lead.score >= CRM_SYNC_THRESHOLD or event == "account_created":
            sync_lead_to_crm(lead)
        return lead
    except Exception:                   # noqa: BLE001 - capture must never 500 a page
        return None


def _abs_url(request, path):
    """Absolute URL for an email link — from the request when we have one, else
    settings.SITE_URL (email clients need absolute URLs)."""
    if request is not None:
        return request.build_absolute_uri(path)
    from django.conf import settings
    site = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    return f"{site}{path}" if site else path


def send_welcome_email(lead, request=None):
    """Branded welcome email with the growth-kit links and a real unsubscribe
    link — sent once, over the configured SMTP backend. Fail-safe."""
    try:
        from django.urls import reverse

        from apps.notifications.models import EmailCategory
        from apps.notifications.service import send_email

        # White wordmark for the teal email header (absolute URL for email
        # clients). A coloured logo would vanish on the coloured header. Isolated
        # so a static-manifest hiccup can't block the whole email.
        try:
            from django.templatetags.static import static
            logo_url = _abs_url(request, static("web/logo-white.png"))
        except Exception:               # noqa: BLE001
            logo_url = ""

        token = unsubscribe_token(lead)
        ctx = {
            "logo_url": logo_url,
            "heading": "Welcome to the LulaWorks Academy",
            "name": (lead.name or "").split(" ")[0],
            "learn_url": _abs_url(request, reverse("marketing:learn")),
            "tools_url": _abs_url(request, reverse("marketing:tools")),
            "templates_url": _abs_url(request, reverse("marketing:templates")),
            "unsubscribe_url": _abs_url(request, reverse("marketing:unsubscribe",
                                                         args=[token])),
            "preheader": "Free guides, tools and templates for a more profitable business.",
        }
        send_email(to=lead.email, to_name=lead.name,
                   subject="Welcome to the LulaWorks Academy",
                   template="academy_welcome", category=EmailCategory.MARKETING,
                   context=ctx, related=lead)
        lead.welcomed_at = timezone.now()
        lead.save(update_fields=["welcomed_at", "updated_at"])
    except Exception:                   # noqa: BLE001 - email is best-effort
        pass


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
