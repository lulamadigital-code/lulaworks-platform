"""Email campaign delivery (Phase 2).

Send a campaign to its segment over the notifications pipe — per-recipient
tracking (:class:`CampaignSend`), unsubscribe suppression and open tracking.
Everything here is MARKETING category; transactional email is never touched, and
a suppressed (unsubscribed) recipient is always skipped.
"""
import re

from django.urls import reverse
from django.utils import timezone

from apps.notifications.models import EmailCategory, EmailStatus
from apps.notifications.service import send_email

from .models import CampaignSend, CampaignStatus, EmailSuppression
from .services import segment_queryset

_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _first_name(name):
    name = (name or "").strip()
    return name.split(" ")[0] if name else "there"


def resolve_recipients(segment):
    """A de-duplicated list of email recipients for a segment.

    Leads use their own email; customers use the customer email, else the primary
    contact's. Blank emails are dropped."""
    if segment is None:
        return []
    seen, out = set(), []
    if segment.audience == "customers":
        for c in segment_queryset(segment).prefetch_related("contacts"):
            email = (getattr(c, "email", "") or "").strip().lower()
            name = c.name
            if not email:
                contact = (c.contacts.filter(is_primary=True).first()
                           or c.contacts.first())
                if contact and contact.email:
                    email, name = contact.email.strip().lower(), (contact.full_name or name)
            if email and email not in seen:
                seen.add(email)
                out.append({"email": email, "name": name, "lead": None, "customer": c,
                            "first_name": _first_name(name)})
    else:
        for lead in segment_queryset(segment):
            email = (lead.email or "").strip().lower()
            if email and email not in seen:
                seen.add(email)
                name = lead.display_contact
                out.append({"email": email, "name": name, "lead": lead, "customer": None,
                            "first_name": _first_name(name)})
    return out


def render_content(content, recipient, company):
    """Fill {{first_name}} / {{name}} / {{company_name}} placeholders."""
    values = {
        "first_name": recipient.get("first_name") or "there",
        "name": recipient.get("name") or "",
        "company_name": getattr(company, "name", "") or "",
    }
    return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), m.group(0))),
                            content or "")


def is_suppressed(company, email) -> bool:
    return EmailSuppression.objects.filter(company=company,
                                           email__iexact=(email or "").strip()).exists()


def suppress(company, email, reason="unsubscribe", user=None):
    obj, _ = EmailSuppression.objects.get_or_create(
        company=company, email=(email or "").strip().lower(),
        defaults={"reason": reason, "created_by": user, "updated_by": user})
    return obj


def send_test(campaign, user, to_email):
    """Send one preview to `to_email` — no CampaignSend, ignores suppression."""
    company = campaign.company
    recipient = {"first_name": _first_name(user.get_full_name()), "name": "",
                 "company_name": getattr(company, "name", "")}
    body = render_content(campaign.content, recipient, company)
    return send_email(
        to=to_email, subject=f"[TEST] {campaign.subject or campaign.name}",
        template="marketing",
        context={"body": body, "company_name": getattr(company, "name", ""),
                 "unsubscribe_url": "", "tracking_pixel": ""},
        company=company, category=EmailCategory.MARKETING, sent_by=user,
        related=campaign, now=True)


def send_campaign(campaign, user, base_url=""):
    """Send the campaign to its segment. Skips suppressed recipients, records a
    CampaignSend per person, links the EmailLog, and updates the totals."""
    if not campaign.can_send:
        raise ValueError("This campaign can't be sent — it needs an email channel, "
                         "a segment, and a draft/scheduled status.")
    company = campaign.company
    recipients = resolve_recipients(campaign.segment)
    sent = skipped = 0
    for r in recipients:
        cs, created = CampaignSend.objects.get_or_create(
            company=company, campaign=campaign, email=r["email"],
            defaults={"name": r["name"], "lead": r["lead"], "customer": r["customer"],
                      "created_by": user, "updated_by": user})
        if not created and cs.status == CampaignSend.Status.SENT:
            continue                                   # already delivered — no re-send
        if is_suppressed(company, r["email"]):
            cs.status = CampaignSend.Status.SKIPPED
            cs.save(update_fields=["status", "updated_at"])
            skipped += 1
            continue
        body = render_content(campaign.content, r, company)
        unsub = f"{base_url}{reverse('web:marketing_unsubscribe', args=[cs.id])}" if base_url else ""
        pixel = f"{base_url}{reverse('web:marketing_pixel', args=[cs.id])}" if base_url else ""
        log = send_email(
            to=r["email"], to_name=r["name"],
            subject=campaign.subject or campaign.name, template="marketing",
            context={"body": body, "company_name": getattr(company, "name", ""),
                     "unsubscribe_url": unsub, "tracking_pixel": pixel},
            company=company, category=EmailCategory.MARKETING, sent_by=user,
            related=campaign)
        cs.email_log = log
        cs.status = CampaignSend.Status.SENT
        cs.save(update_fields=["email_log", "status", "updated_at"])
        sent += 1
    campaign.status = CampaignStatus.COMPLETED
    campaign.updated_by = user
    campaign.save(update_fields=["status", "updated_by", "updated_at"])
    refresh_metrics(campaign)
    return {"sent": sent, "skipped": skipped, "recipients": len(recipients)}


def refresh_metrics(campaign):
    """Recompute delivered/failed/opened/unsubscribed from the CampaignSends."""
    sends = list(campaign.sends.select_related("email_log").all())
    suppressed = set(EmailSuppression.objects.filter(company=campaign.company)
                     .values_list("email", flat=True))
    sent = delivered = failed = opened = unsub = 0
    for cs in sends:
        if cs.status == CampaignSend.Status.SENT:
            sent += 1
            log = cs.email_log
            if log and log.status == EmailStatus.SENT:
                delivered += 1
            elif log and log.status == EmailStatus.FAILED:
                failed += 1
        if cs.opened:
            opened += 1
        if cs.email in suppressed:
            unsub += 1
    campaign.sent = sent
    campaign.delivered = delivered
    campaign.failed = failed
    campaign.opened = opened
    campaign.unsubscribed = unsub
    campaign.save(update_fields=["sent", "delivered", "failed", "opened",
                                 "unsubscribed", "updated_at"])
    return campaign


def mark_opened(campaign_send):
    if not campaign_send.opened:
        campaign_send.opened = True
        campaign_send.opened_at = timezone.now()
        campaign_send.save(update_fields=["opened", "opened_at", "updated_at"])
        Campaign_ = campaign_send.campaign
        Campaign_.opened = Campaign_.sends.filter(opened=True).count()
        Campaign_.save(update_fields=["opened", "updated_at"])
