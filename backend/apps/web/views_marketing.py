"""Marketing module (web) — Phase 1.

Overview, Campaigns, Segments and Lead-source performance, all reading the CRM.
Gated by ``customers.manage`` (the same permission that owns the CRM records
Marketing operates on). Channel sending (email/WhatsApp) arrives in later phases.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.campaigns.models import (
    AUDIENCE_CHOICES,
    Campaign,
    CampaignChannel,
    CampaignStatus,
    Segment,
)
from apps.campaigns.services import (
    lead_source_performance,
    marketing_overview,
    segment_count,
    segment_queryset,
)
from apps.customers.models import (
    LEAD_SOURCES,
    CustomerStatus,
    CustomerType,
    Lead,
)


def _can_market(user) -> bool:
    return user.has_perm_code("customers.manage")


def _guard(request):
    if not _can_market(request.user):
        messages.error(request, "You do not have permission to manage marketing.")
        return redirect("web:dashboard")
    return None


def _parse_iso_dt(raw):
    from django.utils.dateparse import parse_datetime
    if not raw:
        return None
    dt = parse_datetime(raw)
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _cost_or_zero(raw):
    from decimal import Decimal, InvalidOperation
    raw = (raw or "").strip().replace(",", "").replace("R", "").replace(" ", "")
    if not raw:
        return Decimal("0")
    try:
        v = Decimal(raw)
        return v if v > 0 else Decimal("0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


# ── Overview ──────────────────────────────────────────────────────────────────

@login_required
def marketing_home(request):
    if (r := _guard(request)):
        return r
    ctx = marketing_overview()
    ctx["recent_campaigns"] = Campaign.objects.all()[:6]
    return render(request, "web/marketing/overview.html", ctx)


# ── Campaigns ──────────────────────────────────────────────────────────────────

@login_required
def campaigns_list(request):
    if (r := _guard(request)):
        return r
    status = (request.GET.get("status") or "").strip()
    qs = Campaign.objects.select_related("segment").all()
    if status:
        qs = qs.filter(status=status)
    return render(request, "web/marketing/campaigns.html", {
        "campaigns": qs,
        "status": status,
        "statuses": CampaignStatus.choices,
        "channels": CampaignChannel.choices,
    })


@login_required
def campaign_new(request):
    if (r := _guard(request)):
        return r
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if not name:
            messages.error(request, "Give the campaign a name.")
            return redirect("web:marketing_campaigns")
        segment = None
        if request.POST.get("segment"):
            segment = Segment.objects.filter(pk=request.POST["segment"]).first()
        Campaign.objects.create(
            company=request.user.active_company,
            name=name,
            objective=(request.POST.get("objective") or "").strip(),
            channel=request.POST.get("channel") or CampaignChannel.EMAIL,
            segment=segment,
            subject=(request.POST.get("subject") or "").strip(),
            content=(request.POST.get("content") or "").strip(),
            wa_template_name=(request.POST.get("wa_template_name") or "").strip(),
            cost=_cost_or_zero(request.POST.get("cost")),
            scheduled_at=_parse_iso_dt(request.POST.get("scheduled_at")),
            sender=request.user,
            status=CampaignStatus.DRAFT,
            created_by=request.user, updated_by=request.user,
        )
        messages.success(request, f"Campaign “{name}” created as a draft.")
        return redirect("web:marketing_campaigns")
    return render(request, "web/marketing/campaign_form.html", {
        "segments": Segment.objects.all(),
        "channels": CampaignChannel.choices,
    })


@login_required
def campaign_detail(request, pk):
    if (r := _guard(request)):
        return r
    campaign = get_object_or_404(Campaign.objects.select_related("segment", "sender"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        # Phase 1: channel sending isn't wired yet, so status moves are manual
        # markers a person sets — never an automatic send.
        transitions = {
            "schedule": CampaignStatus.SCHEDULED,
            "run": CampaignStatus.RUNNING,
            "complete": CampaignStatus.COMPLETED,
            "cancel": CampaignStatus.CANCELLED,
            "reopen": CampaignStatus.DRAFT,
        }
        if action in transitions:
            campaign.status = transitions[action]
            campaign.updated_by = request.user
            campaign.save(update_fields=["status", "updated_by", "updated_at"])
            messages.success(request, f"Campaign marked {campaign.get_status_display()}.")
        elif action == "delete":
            campaign.delete()
            messages.success(request, "Campaign deleted.")
            return redirect("web:marketing_campaigns")
        elif action == "send_test":
            if campaign.channel == "whatsapp":
                from apps.campaigns import whatsapp as wa
                to = (request.POST.get("test_phone") or "").strip()
                if not to:
                    messages.error(request, "Enter a WhatsApp number to send the test to.")
                else:
                    try:
                        wa.send_test_wa(campaign, request.user, to)
                        messages.success(request, f"Test WhatsApp sent to {to}.")
                    except Exception as exc:                   # noqa: BLE001
                        messages.error(request, f"Could not send the test: {exc}")
            else:
                from apps.campaigns import email as cmail
                to = (request.POST.get("test_email") or request.user.email or "").strip()
                if not to:
                    messages.error(request, "Enter an email address to send the test to.")
                else:
                    try:
                        cmail.send_test(campaign, request.user, to)
                        messages.success(request, f"Test email sent to {to}.")
                    except Exception as exc:                   # noqa: BLE001
                        messages.error(request, f"Could not send the test: {exc}")
        elif action == "send":
            try:
                if campaign.channel == "whatsapp":
                    from apps.campaigns import whatsapp as wa
                    r = wa.send_whatsapp_campaign(campaign, request.user)
                    messages.success(
                        request, f"WhatsApp campaign sent — {r['sent']} sent"
                        + (f", {r['failed']} failed" if r["failed"] else "")
                        + f" of {r['recipients']} recipients.")
                else:
                    from apps.campaigns import email as cmail
                    base = request.build_absolute_uri("/").rstrip("/")
                    r = cmail.send_campaign(campaign, request.user, base_url=base)
                    messages.success(
                        request, f"Campaign sent — {r['sent']} queued"
                        + (f", {r['skipped']} skipped (unsubscribed)" if r["skipped"] else "")
                        + f" of {r['recipients']} recipients.")
            except ValueError as exc:
                messages.error(request, str(exc))
            except Exception as exc:                           # noqa: BLE001
                messages.error(request, f"Send failed: {exc}")
        return redirect("web:marketing_campaign_detail", pk=campaign.id)

    audience_count = segment_count(campaign.segment) if campaign.segment else None
    recipient_count = None
    wa_connected = False
    if campaign.segment:
        if campaign.channel == "email":
            from apps.campaigns.email import resolve_recipients
            recipient_count = len(resolve_recipients(campaign.segment))
        elif campaign.channel == "whatsapp":
            from apps.campaigns.whatsapp import get_connection, resolve_wa_recipients
            recipient_count = len(resolve_wa_recipients(campaign.segment))
            conn = get_connection(request.user.active_company)
            wa_connected = bool(conn and conn.is_connected)
    return render(request, "web/marketing/campaign_detail.html", {
        "campaign": campaign,
        "audience_count": audience_count,
        "recipient_count": recipient_count,
        "wa_connected": wa_connected,
    })


@login_required
def whatsapp_connect(request):
    """Connect (or update) the company's own WhatsApp Business number. Owner/admin
    only — this is the tenant's number, never a shared Lulaworks one."""
    from apps.campaigns.models import WhatsAppConnection
    company = request.user.active_company
    if not request.user.has_perm_code("company.manage"):
        messages.error(request, "Only a company admin can manage the WhatsApp connection.")
        return redirect("web:marketing")
    conn, _ = WhatsAppConnection.objects.get_or_create(
        company=company, defaults={"created_by": request.user, "updated_by": request.user})
    if request.method == "POST":
        action = request.POST.get("action", "save")
        if action == "disconnect":
            conn.is_active = False
            conn.access_token = ""
            conn.save(update_fields=["is_active", "access_token", "updated_at"])
            messages.success(request, "WhatsApp disconnected.")
            return redirect("web:marketing_whatsapp")
        conn.phone_number_id = (request.POST.get("phone_number_id") or "").strip()
        conn.waba_id = (request.POST.get("waba_id") or "").strip()
        conn.display_number = (request.POST.get("display_number") or "").strip()
        token = (request.POST.get("access_token") or "").strip()
        if token and token != "********":          # keep existing if left masked
            conn.access_token = token
        conn.is_active = bool(conn.phone_number_id and conn.access_token)
        conn.updated_by = request.user
        conn.save()
        messages.success(request, "WhatsApp connection saved."
                         if conn.is_active else "Saved — add a phone number id and token to activate.")
        return redirect("web:marketing_whatsapp")
    from apps.campaigns.whatsapp import embedded_signup_configured
    return render(request, "web/marketing/whatsapp.html", {
        "conn": conn,
        "has_token": bool(conn.access_token),
        "api_version": getattr(settings, "WHATSAPP_API_VERSION", "v21.0"),
        "embedded_configured": embedded_signup_configured(),
        "meta_app_id": getattr(settings, "META_APP_ID", ""),
        "wa_config_id": getattr(settings, "WHATSAPP_CONFIG_ID", ""),
    })


@login_required
def whatsapp_embedded_finish(request):
    """Receive the Embedded Signup result (auth code + phone_number_id/waba_id)
    from the browser, exchange it for the company's token, and store the
    connection. JSON in/out. Owner/admin only."""
    from django.http import JsonResponse
    if request.method != "POST":
        return JsonResponse({"error": "POST only"}, status=405)
    if not request.user.has_perm_code("company.manage"):
        return JsonResponse({"error": "forbidden"}, status=403)
    import json

    from apps.campaigns.whatsapp import connect_via_embedded
    try:
        data = json.loads(request.body or "{}")
    except ValueError:
        return JsonResponse({"error": "bad json"}, status=400)
    code = (data.get("code") or "").strip()
    phone_number_id = (data.get("phone_number_id") or "").strip()
    if not (code and phone_number_id):
        return JsonResponse({"error": "Missing code or phone number — please retry."},
                            status=400)
    try:
        connect_via_embedded(
            request.user.active_company, request.user, code=code,
            phone_number_id=phone_number_id, waba_id=(data.get("waba_id") or "").strip(),
            display_number=(data.get("display_number") or "").strip())
    except Exception as exc:                                   # noqa: BLE001
        return JsonResponse({"error": str(exc)}, status=400)
    return JsonResponse({"ok": True})


# ── Public tracking endpoints (no login) ──────────────────────────────────────

def marketing_unsubscribe(request, pk):
    """Public one-click unsubscribe from a campaign's link — suppresses the
    recipient's email for that company's marketing (transactional is untouched).

    Public + no tenant context, so look the send up cross-tenant by its
    unguessable id (all_objects) and then act inside its own tenant scope."""
    from django.http import HttpResponse

    from apps.campaigns.email import suppress
    from apps.campaigns.models import CampaignSend
    from apps.core.context import tenant_scope
    cs = CampaignSend.all_objects.filter(pk=pk).first()
    if cs:
        with tenant_scope(cs.company_id):
            suppress(cs.company, cs.email, reason="unsubscribe link")
    return HttpResponse(
        "<html><body style='font-family:system-ui;text-align:center;padding:60px;'>"
        "<h2>You've been unsubscribed</h2>"
        "<p>You won't receive further marketing emails from this sender. "
        "Account and billing emails are not affected.</p></body></html>")


# A 1×1 transparent GIF (43 bytes).
_PIXEL_GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!"
              b"\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00"
              b"\x00\x02\x01D\x00;")


def marketing_pixel(request, pk):
    """Open-tracking pixel — marks the CampaignSend opened, returns a 1×1 GIF."""
    from django.http import HttpResponse

    from apps.campaigns.email import mark_opened
    from apps.campaigns.models import CampaignSend
    from apps.core.context import tenant_scope
    cs = CampaignSend.all_objects.filter(pk=pk).first()
    if cs:
        try:
            with tenant_scope(cs.company_id):
                mark_opened(cs)
        except Exception:                                      # noqa: BLE001
            pass
    resp = HttpResponse(_PIXEL_GIF, content_type="image/gif")
    resp["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return resp


# ── Segments ───────────────────────────────────────────────────────────────────

@login_required
def segments_list(request):
    if (r := _guard(request)):
        return r
    segments = list(Segment.objects.all())
    rows = [{"segment": s, "count": segment_count(s)} for s in segments]
    return render(request, "web/marketing/segments.html", {"rows": rows})


@login_required
def segment_new(request):
    if (r := _guard(request)):
        return r
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        if not name:
            messages.error(request, "Give the segment a name.")
            return redirect("web:marketing_segments")
        audience = request.POST.get("audience") or "leads"
        Segment.objects.create(
            company=request.user.active_company,
            name=name,
            description=(request.POST.get("description") or "").strip(),
            audience=audience,
            criteria=_criteria_from_post(request.POST, audience),
            created_by=request.user, updated_by=request.user,
        )
        messages.success(request, f"Segment “{name}” saved.")
        return redirect("web:marketing_segments")
    return render(request, "web/marketing/segment_form.html", _segment_form_ctx())


@login_required
def segment_preview(request):
    """Live count for the segment being built (used by the builder)."""
    if not _can_market(request.user):
        from django.http import JsonResponse
        return JsonResponse({"count": 0})
    audience = request.GET.get("audience") or "leads"
    dummy = Segment(company=request.user.active_company, audience=audience,
                    criteria=_criteria_from_post(request.GET, audience))
    from django.http import JsonResponse
    return JsonResponse({"count": segment_queryset(dummy).count()})


@login_required
def segment_delete(request, pk):
    if (r := _guard(request)):
        return r
    if request.method == "POST":
        seg = get_object_or_404(Segment.objects.all(), pk=pk)
        seg.delete()
        messages.success(request, "Segment deleted.")
    return redirect("web:marketing_segments")


def _criteria_from_post(data, audience) -> dict:
    """Build the criteria dict from posted filter fields (blank ones dropped).

    The builder renders both audiences' inputs in one form, so the customer
    inputs are ``cust_``-prefixed to avoid clashing with the lead inputs. We map
    them back to the canonical criteria keys here."""
    crit = {}
    if audience == "customers":
        mapping = {"cust_status": "status", "customer_type": "customer_type",
                   "cust_industry": "industry", "cust_country": "country",
                   "cust_city": "city", "no_activity_days": "no_activity_days"}
    else:
        if data.get("uncontacted"):
            crit["uncontacted"] = True
        mapping = {"status": "status", "source": "source", "industry": "industry",
                   "country": "country", "city": "city",
                   "no_contact_days": "no_contact_days"}
    for src, key in mapping.items():
        v = (data.get(src) or "").strip()
        if v:
            crit[key] = v
    return crit


def _segment_form_ctx() -> dict:
    return {
        "audiences": AUDIENCE_CHOICES,
        "sources": LEAD_SOURCES,
        "lead_statuses": Lead.Status.choices,
        "customer_statuses": CustomerStatus.choices,
        "customer_types": CustomerType.choices,
    }


# ── Lead sources ───────────────────────────────────────────────────────────────

@login_required
def lead_sources(request):
    if (r := _guard(request)):
        return r
    rows = lead_source_performance()
    return render(request, "web/marketing/lead_sources.html", {
        "rows": rows,
        "total_leads": sum(r["leads"] for r in rows),
        "total_opps": sum(r["opportunities"] for r in rows),
        "total_won": sum(r["won"] for r in rows),
    })


# ── Analytics & ROI ────────────────────────────────────────────────────────────

@login_required
def marketing_analytics(request):
    if (r := _guard(request)):
        return r
    from apps.campaigns.analytics import marketing_analytics as analytics
    ctx = analytics()
    ctx["source_revenue"] = sum((row["revenue"] for row in ctx["sources"]), 0)
    return render(request, "web/marketing/analytics.html", ctx)
