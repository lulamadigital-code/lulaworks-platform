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
import hashlib
import hmac
import logging
import re

from django.conf import settings
from django.utils import timezone

from apps.core.context import tenant_scope

from .email import render_content
from .models import CampaignSend, CampaignStatus, WhatsAppConnection

GRAPH = "https://graph.facebook.com"
logger = logging.getLogger(__name__)


def get_connection(company):
    return (getattr(company, "whatsapp_connection", None)
            or WhatsAppConnection.objects.filter(company=company).first())


def embedded_signup_configured() -> bool:
    """True when the Lulaworks Meta app is set up for one-click Embedded Signup."""
    return bool(getattr(settings, "META_APP_ID", "")
                and getattr(settings, "WHATSAPP_CONFIG_ID", ""))


def exchange_code_for_token(code) -> str:
    """Swap the Embedded Signup authorization code for a business access token via
    the Lulaworks Meta app. Isolated so tests can monkeypatch it."""
    import requests
    version = getattr(settings, "WHATSAPP_API_VERSION", "v21.0")
    resp = requests.get(
        f"{GRAPH}/{version}/oauth/access_token", timeout=20,
        params={"client_id": getattr(settings, "META_APP_ID", ""),
                "client_secret": getattr(settings, "META_APP_SECRET", ""),
                "code": code})
    if resp.status_code >= 300:
        raise RuntimeError(f"Token exchange failed {resp.status_code}: {resp.text[:200]}")
    return resp.json().get("access_token", "")


def connect_via_embedded(company, user, *, code, phone_number_id, waba_id="",
                         display_number=""):
    """Finish Embedded Signup: exchange the code for a token and store the
    company's own WhatsApp connection."""
    token = exchange_code_for_token(code)
    if not token:
        raise ValueError("Could not obtain an access token from Meta.")
    conn, _ = WhatsAppConnection.objects.get_or_create(
        company=company, defaults={"created_by": user, "updated_by": user})
    conn.phone_number_id = phone_number_id
    conn.waba_id = waba_id or ""
    conn.display_number = display_number or ""
    conn.access_token = token
    conn.is_active = bool(phone_number_id and token)
    conn.updated_by = user
    conn.save()
    return conn


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


# ── Delivery webhook (Meta → us) ──────────────────────────────────────────────
#
# Meta pushes status updates (sent → delivered → read, or failed) and inbound
# replies to a public URL. We verify it's really Meta (a shared token on GET, an
# HMAC-SHA256 signature on POST), then update the matching CampaignSend so the
# analytics show real engagement — the WhatsApp analogue of email open tracking.

def verify_webhook_token(mode, token) -> bool:
    """The GET handshake Meta does when you register the webhook."""
    expected = getattr(settings, "WHATSAPP_WEBHOOK_VERIFY_TOKEN", "")
    return bool(mode == "subscribe" and token and expected and token == expected)


def verify_signature(raw_body: bytes, signature_header: str) -> bool:
    """True if the POST body was signed with our Meta app secret (X-Hub-Signature-256)."""
    secret = getattr(settings, "META_APP_SECRET", "")
    if not (secret and signature_header):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw_body or b"",
                                    hashlib.sha256).hexdigest()
    try:
        return hmac.compare_digest(expected, signature_header)
    except Exception:                                          # noqa: BLE001
        return False


def process_webhook(payload: dict):
    """Walk a WhatsApp webhook payload: apply message statuses + inbound replies."""
    for entry in payload.get("entry", []) or []:
        for change in entry.get("changes", []) or []:
            value = change.get("value", {}) or {}
            for st in value.get("statuses", []) or []:
                try:
                    _apply_status(st)
                except Exception:                              # noqa: BLE001
                    logger.exception("wa status apply failed")
            for msg in value.get("messages", []) or []:
                try:
                    _apply_inbound(value, msg)
                except Exception:                              # noqa: BLE001
                    logger.exception("wa inbound apply failed")


def _apply_status(st: dict):
    mid = st.get("id")
    status = st.get("status")
    if not mid:
        return
    cs = CampaignSend.all_objects.filter(wa_message_id=mid, channel="whatsapp").first()
    if not cs:
        return
    with tenant_scope(cs.company_id):
        fields = []
        if status == "delivered" and not cs.delivered:
            cs.delivered = True
            fields.append("delivered")
        elif status == "read":
            if not cs.delivered:
                cs.delivered = True
                fields.append("delivered")
            if not cs.opened:                                  # opened == "read"
                cs.opened = True
                cs.opened_at = timezone.now()
                fields += ["opened", "opened_at"]
        elif status == "failed" and cs.status != CampaignSend.Status.FAILED:
            cs.status = CampaignSend.Status.FAILED
            fields.append("status")
        if fields:
            fields.append("updated_at")
            cs.save(update_fields=fields)
            _recount(cs.campaign_id, cs.company_id)


def _apply_inbound(value: dict, msg: dict):
    """A contact replied. Mark the most recent campaign send to that number as
    replied (scoped to the company that owns the receiving phone number)."""
    frm = _wa_number(msg.get("from", ""))
    if not frm:
        return
    phone_number_id = (value.get("metadata", {}) or {}).get("phone_number_id", "")
    conn = (WhatsAppConnection.all_objects.filter(phone_number_id=phone_number_id).first()
            if phone_number_id else None)
    qs = CampaignSend.all_objects.filter(channel="whatsapp")
    if conn:
        qs = qs.filter(company_id=conn.company_id)
    # Match the recipient by normalized number (phone stored as entered).
    for cs in qs.order_by("-created_at")[:500]:
        if _wa_number(cs.phone) == frm:
            if not cs.replied:
                with tenant_scope(cs.company_id):
                    cs.replied = True
                    cs.save(update_fields=["replied", "updated_at"])
                    _recount(cs.campaign_id, cs.company_id)
            return


def _recount(campaign_id, company_id):
    """Recompute a WhatsApp campaign's delivered/read/replied/failed from its sends."""
    from .models import Campaign
    with tenant_scope(company_id):
        camp = Campaign.objects.filter(pk=campaign_id).first()
        if not camp:
            return
        sends = camp.sends.all()
        camp.delivered = sends.filter(delivered=True).count()
        camp.opened = sends.filter(opened=True).count()
        camp.replied = sends.filter(replied=True).count()
        camp.failed = sends.filter(status=CampaignSend.Status.FAILED).count()
        camp.save(update_fields=["delivered", "opened", "replied", "failed", "updated_at"])
