"""WhatsApp campaign delivery (Phase 3) — Meta Cloud API, per-tenant number.

Marketing WhatsApp always sends from the COMPANY's own connected number (never a
shared Lulaworks one). The single live Meta HTTP call is isolated in
``_post_message`` so recipient resolution, gating and record-keeping are testable
without hitting Meta.

Production marketing (a first message to a contact) requires a Meta-APPROVED
message TEMPLATE — set ``Campaign.wa_template_name``. A blank template sends
``content`` as free text, which Meta only accepts inside a 24h customer-care
window (fine for tests / replies).
"""
import re

from django.conf import settings

from .email import render_content
from .models import CampaignSend, CampaignStatus, WhatsAppConnection

GRAPH = "https://graph.facebook.com"


def get_connection(company):
    return (getattr(company, "whatsapp_connection", None)
            or WhatsAppConnection.objects.filter(company=company).first())


def _first_name(name):
    name = (name or "").strip()
    return name.split(" ")[0] if name else "there"


def _wa_number(raw):
    """E.164 digits Meta expects — no '+', spaces or punctuation."""
    return re.sub(r"\D", "", str(raw or ""))


def resolve_wa_recipients(segment):
    """A de-duplicated list of WhatsApp recipients for a segment. Leads use their
    mobile/telephone; customers use their number, else a contact's whatsapp/
    mobile. Numbers without digits are dropped."""
    if segment is None:
        return []
    from .services import segment_queryset
    seen, out = set(), []
    if segment.audience == "customers":
        for c in segment_queryset(segment).prefetch_related("contacts"):
            phone = getattr(c, "mobile", "") or getattr(c, "telephone", "")
            name = c.name
            if not _wa_number(phone):
                contact = (c.contacts.filter(is_primary=True).first()
                           or c.contacts.first())
                if contact:
                    phone = (getattr(contact, "whatsapp", "")
                             or getattr(contact, "mobile", "")
                             or getattr(contact, "telephone", ""))
                    name = contact.full_name or name
            key = _wa_number(phone)
            if key and key not in seen:
                seen.add(key)
                out.append({"phone": phone, "name": name, "lead": None, "customer": c,
                            "first_name": _first_name(name)})
    else:
        for lead in segment_queryset(segment):
            phone = lead.mobile or lead.telephone
            key = _wa_number(phone)
            if key and key not in seen:
                seen.add(key)
                name = lead.display_contact
                out.append({"phone": phone, "name": name, "lead": lead, "customer": None,
                            "first_name": _first_name(name)})
    return out


def _post_message(conn, to_number, *, text=None, template=None, params=None):
    """Talk to Meta once. Returns the message id, or raises. Isolated so tests
    can monkeypatch it (no live call in the suite)."""
    import requests
    version = getattr(settings, "WHATSAPP_API_VERSION", "v21.0")
    url = f"{GRAPH}/{version}/{conn.phone_number_id}/messages"
    if template:
        payload = {"messaging_product": "whatsapp", "to": to_number, "type": "template",
                   "template": {"name": template, "language": {"code": "en"}}}
        if params:
            payload["template"]["components"] = [{
                "type": "body",
                "parameters": [{"type": "text", "text": str(p)} for p in params]}]
    else:
        payload = {"messaging_product": "whatsapp", "to": to_number, "type": "text",
                   "text": {"body": text or ""}}
    resp = requests.post(url, json=payload, timeout=20,
                         headers={"Authorization": f"Bearer {conn.access_token}"})
    if resp.status_code >= 300:
        raise RuntimeError(f"WhatsApp API {resp.status_code}: {resp.text[:300]}")
    return (resp.json().get("messages") or [{}])[0].get("id", "")


def _send_one(conn, campaign, recipient, company):
    if campaign.wa_template_name:
        return _post_message(conn, _wa_number(recipient["phone"]),
                             template=campaign.wa_template_name,
                             params=[recipient["first_name"]])
    text = render_content(campaign.content, recipient, company)
    return _post_message(conn, _wa_number(recipient["phone"]), text=text)


def send_test_wa(campaign, user, to_phone):
    company = campaign.company
    conn = get_connection(company)
    if not (conn and conn.is_connected):
        raise ValueError("WhatsApp isn't connected for this company yet.")
    recipient = {"first_name": _first_name(user.get_full_name()), "name": "",
                 "phone": to_phone, "company_name": getattr(company, "name", "")}
    return _send_one(conn, campaign, recipient, company)


def send_whatsapp_campaign(campaign, user):
    company = campaign.company
    conn = get_connection(company)
    if not (conn and conn.is_connected):
        raise ValueError("Connect this company's WhatsApp number before sending.")
    if not campaign.segment:
        raise ValueError("Add an audience segment first.")
    recipients = resolve_wa_recipients(campaign.segment)
    sent = failed = 0
    for r in recipients:
        cs, created = CampaignSend.objects.get_or_create(
            company=company, campaign=campaign, phone=r["phone"], email="",
            defaults={"channel": "whatsapp", "name": r["name"], "lead": r["lead"],
                      "customer": r["customer"], "created_by": user, "updated_by": user})
        if not created and cs.status == CampaignSend.Status.SENT:
            continue
        cs.channel = "whatsapp"
        try:
            cs.wa_message_id = _send_one(conn, campaign, r, company)
            cs.status = CampaignSend.Status.SENT
            sent += 1
        except Exception:                                      # noqa: BLE001
            cs.status = CampaignSend.Status.FAILED
            failed += 1
        cs.save(update_fields=["channel", "wa_message_id", "status", "updated_at"])
    campaign.sent = campaign.sends.filter(status=CampaignSend.Status.SENT).count()
    campaign.failed = campaign.sends.filter(status=CampaignSend.Status.FAILED).count()
    campaign.status = CampaignStatus.COMPLETED
    campaign.updated_by = user
    campaign.save(update_fields=["sent", "failed", "status", "updated_by", "updated_at"])
    return {"sent": sent, "failed": failed, "recipients": len(recipients)}
