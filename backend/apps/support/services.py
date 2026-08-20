"""Support Center services — the single write path for tickets and messages.

Numbering is global (LW-#####). Notifications go through the centralized email
service; every state change is written to the AuditLog. Platform reads/writes
happen inside `system_scope()` in the views, since support spans all tenants.
"""
from django.db import transaction
from django.utils import timezone

from .models import (
    OPEN_STATUSES, SupportAttachment, SupportMessage, SupportTicket, TicketStatus,
)

TICKET_SEQ_START = 10480


class SupportError(Exception):
    """Raised for support-workflow problems (bad transition, missing data)."""


def _next_number():
    """Allocate the next global ticket number (LW-#####). Locked so concurrent
    creates never collide."""
    with transaction.atomic():
        last = (SupportTicket.all_objects.select_for_update()
                .order_by("-created_at").values_list("number", flat=True).first())
        n = TICKET_SEQ_START
        if last and last.startswith("LW-"):
            try:
                n = int(last[3:])
            except ValueError:
                n = TICKET_SEQ_START
        return f"LW-{n + 1}"


def _audit(company, user, action, ticket, ip=None):
    try:
        from apps.administration.services import record_audit
        record_audit(company=company, user=user, action=action, entity=ticket, ip=ip)
    except Exception:                                          # noqa: BLE001
        pass


@transaction.atomic
def create_ticket(*, company, user, subject, category, priority, description,
                  related_module="", related_ref="", error_reference="",
                  error_context=None, ip=None):
    subject = (subject or "").strip()
    description = (description or "").strip()
    if not subject:
        raise SupportError("A subject is required.")
    if not description:
        raise SupportError("Please describe what happened.")

    ticket = SupportTicket(
        company=company, number=_next_number(), subject=subject[:200],
        category=category or "other", priority=priority or "normal",
        status=TicketStatus.OPEN, description=description,
        related_module=(related_module or "")[:40], related_ref=(related_ref or "")[:120],
        error_reference=(error_reference or "")[:24], error_context=error_context or {},
        created_by=user)
    ticket.save()
    # The opening message mirrors the description so the thread reads top to bottom.
    SupportMessage.objects.create(company=company, ticket=ticket, sender=user,
                                  body=description, from_support=False)
    _audit(company, user, "support.ticket.created", ticket, ip)
    _notify_support_new(ticket)
    return ticket


@transaction.atomic
def add_message(*, ticket, sender, body, is_internal=False, from_support=False,
                files=None, ip=None):
    body = (body or "").strip()
    if not body and not files:
        raise SupportError("Write a message or attach a file.")

    msg = SupportMessage.objects.create(
        company_id=ticket.company_id, ticket=ticket, sender=sender,
        body=body, is_internal=is_internal, from_support=from_support)
    for f in files or []:
        SupportAttachment.objects.create(
            company_id=ticket.company_id, ticket=ticket, message=msg,
            file=f, name=getattr(f, "name", "file")[:200], size=getattr(f, "size", 0) or 0)

    now = timezone.now()
    ticket.last_activity_at = now
    fields = ["last_activity_at", "updated_at"]
    # First support reply records the SLA "first response" milestone.
    if from_support and not is_internal and ticket.first_response_at is None:
        ticket.first_response_at = now
        fields.append("first_response_at")
    # A support reply moves an Open ticket into progress; a customer reply on a
    # "waiting" ticket hands it back to support.
    if from_support and ticket.status == TicketStatus.OPEN:
        ticket.status = TicketStatus.IN_PROGRESS
        fields.append("status")
    elif not from_support and ticket.status == TicketStatus.WAITING_CUSTOMER:
        ticket.status = TicketStatus.IN_PROGRESS
        fields.append("status")
    ticket.save(update_fields=fields)

    _audit(ticket.company_id, sender, "support.note.added" if is_internal
           else "support.reply.sent", ticket, ip)
    if not is_internal:
        _notify_reply(ticket, from_support=from_support)
    return msg


def message_dict(m):
    """Serialise one message for the live-chat feed (both the customer and the
    technician poll this shape)."""
    if m.from_support:
        who = "LulaWorks Support"
    elif m.sender:
        who = m.sender.get_full_name() or m.sender.email
    else:
        who = "Customer"
    return {
        "id": str(m.id),
        "body": m.body,
        "from_support": bool(m.from_support),
        "is_internal": bool(m.is_internal),
        "who": who,
        "when": m.created_at.strftime("%d %b %H:%M") if m.created_at else "",
        "attachments": [{"name": a.name, "url": a.file.url}
                        for a in m.attachments.all()],
    }


@transaction.atomic
def set_status(*, ticket, actor, status, from_support=False, ip=None):
    if status not in TicketStatus.values:
        raise SupportError("Unknown status.")
    now = timezone.now()
    ticket.status = status
    fields = ["status", "last_activity_at", "updated_at"]
    ticket.last_activity_at = now
    if status == TicketStatus.RESOLVED and ticket.resolved_at is None:
        ticket.resolved_at = now
        fields.append("resolved_at")
    if status == TicketStatus.CLOSED and ticket.closed_at is None:
        ticket.closed_at = now
        fields.append("closed_at")
    if status in OPEN_STATUSES:            # reopened → clear resolution stamps
        ticket.resolved_at = None
        ticket.closed_at = None
        fields += ["resolved_at", "closed_at"]
    ticket.save(update_fields=list(set(fields)))
    _audit(ticket.company_id, actor, f"support.status.{status}", ticket, ip)
    _notify_status(ticket)
    return ticket


@transaction.atomic
def assign(*, ticket, agent, actor=None, ip=None):
    ticket.assigned_agent = agent
    ticket.last_activity_at = timezone.now()
    ticket.save(update_fields=["assigned_agent", "last_activity_at", "updated_at"])
    _audit(ticket.company_id, actor, "support.assigned", ticket, ip)
    return ticket


@transaction.atomic
def set_priority(*, ticket, priority, actor=None, ip=None):
    from .models import TicketPriority
    if priority not in TicketPriority.values:
        raise SupportError("Unknown priority.")
    ticket.priority = priority
    ticket.save(update_fields=["priority", "updated_at"])
    _audit(ticket.company_id, actor, f"support.priority.{priority}", ticket, ip)
    return ticket


# ── Notifications (email; in-app/WhatsApp are later phases) ──────────────────
def _company_name(ticket):
    return getattr(getattr(ticket, "company", None), "name", "your company")


def _send(to, subject, heading, body, cta_url="", company=None):
    if not to:
        return
    try:
        from apps.notifications.models import EmailCategory
        from apps.notifications.service import send_email
        ctx = {"heading": heading, "body": body}
        if cta_url:
            ctx.update(cta_url=cta_url, cta_label="View ticket")
        send_email(to=to, subject=subject, template="generic", company=company,
                   category=EmailCategory.ACCOUNT, context=ctx)
    except Exception:                                          # noqa: BLE001
        pass


def _ticket_url(ticket):
    from django.conf import settings
    base = getattr(settings, "SITE_URL", "").rstrip("/")
    return f"{base}/support/{ticket.id}/" if base else ""


def _push_mobile(user, text, company=None):
    """Best-effort WhatsApp (preferred) or SMS to a user who has opted in and has
    a mobile number. Silent when the channel isn't configured."""
    try:
        from apps.notifications.sms import (
            send_sms, sms_allowed, sms_configured, whatsapp_configured,
        )
        if not (user and sms_allowed(user)):
            return
        if whatsapp_configured():
            send_sms(to=user.mobile, body=text, company=company, channel="whatsapp")
        elif sms_configured():
            send_sms(to=user.mobile, body=text, company=company)
    except Exception:                                          # noqa: BLE001
        pass


def _notify_support_new(ticket):
    """Tell the platform support desk a new ticket arrived."""
    from django.conf import settings
    to = getattr(settings, "SUPPORT_INBOX_EMAIL", "") or getattr(settings, "DEFAULT_FROM_EMAIL", "")
    _send(to, f"[{ticket.number}] {ticket.subject}",
          f"New support ticket · {ticket.number}",
          f"{_company_name(ticket)} raised a {ticket.get_priority_display()} "
          f"{ticket.get_category_display()} ticket.", company=None)


def _notify_reply(ticket, *, from_support):
    if from_support:
        creator = getattr(ticket, "created_by", None)
        company = getattr(ticket, "company", None)
        _send(getattr(creator, "email", ""),
              f"LulaWorks Support Ticket {ticket.number} updated",
              f"Support replied to {ticket.number}",
              "LulaWorks Support has replied to your ticket.",
              cta_url=_ticket_url(ticket), company=company)
        _push_mobile(creator, f"LulaWorks Support replied to ticket {ticket.number}. "
                              f"View: {_ticket_url(ticket)}", company=company)


def _notify_status(ticket):
    creator = getattr(ticket, "created_by", None)
    company = getattr(ticket, "company", None)
    _send(getattr(creator, "email", ""),
          f"LulaWorks Support Ticket {ticket.number} — {ticket.get_status_display()}",
          f"{ticket.number} is now {ticket.get_status_display()}",
          f"Your support ticket status changed to {ticket.get_status_display()}.",
          cta_url=_ticket_url(ticket), company=company)
    _push_mobile(creator, f"LulaWorks ticket {ticket.number} is now "
                          f"{ticket.get_status_display()}.", company=company)


# ── Knowledge Base search + grounded AI assist ───────────────────────────────
_STOP = {"the", "a", "an", "is", "are", "to", "of", "and", "or", "i", "my", "me",
         "in", "on", "it", "cant", "can't", "cannot", "not", "how", "do", "does",
         "with", "for", "why", "when", "this", "that", "was", "were", "have"}


def _tokens(text):
    import re
    return [w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in _STOP and len(w) > 1]


def search_kb(query, *, limit=4):
    """Deterministic keyword search over published KB articles. Ranks by matches
    in title/tags (weighted) then summary/body. Returns a list of KBArticle."""
    from .models import KBArticle
    from apps.core.context import system_scope

    terms = set(_tokens(query))
    with system_scope():
        articles = list(KBArticle.objects.filter(is_published=True))
    if not terms:
        return articles[:limit]
    scored = []
    for a in articles:
        title = set(_tokens(a.title)) | set(_tokens(a.tags))
        body = set(_tokens(a.summary)) | set(_tokens(a.body))
        score = 3 * len(terms & title) + len(terms & body)
        if score:
            scored.append((score, a))
    scored.sort(key=lambda t: -t[0])
    return [a for _, a in scored[:limit]]


def assist(company, user, query):
    """Pre-ticket help: find relevant KB articles, and — if AI is available and
    the tenant has credits — add a concise suggestion GROUNDED ONLY in those
    articles. Never claims the problem is resolved. Always degrades gracefully to
    the deterministic KB matches. Returns {articles, ai_answer, used_ai}."""
    articles = search_kb(query, limit=4)
    result = {"articles": articles, "ai_answer": "", "used_ai": False, "query": query}
    if not articles:
        return result
    try:
        from apps.ai_platform.gateway import run_task
        excerpts = "\n\n".join(
            f"[{i+1}] {a.title}\n{a.summary or a.body[:400]}" for i, a in enumerate(articles))
        prompt = (
            "You are the LulaWorks Support Assistant. Answer the user's question "
            "using ONLY the knowledge base excerpts below. If they do not clearly "
            "answer it, say you're not certain and suggest opening a support ticket. "
            "Never say the problem is solved — the user will confirm. Reply in at "
            "most 3 short sentences, plain and friendly.\n\n"
            f"KNOWLEDGE BASE:\n{excerpts}\n\nUSER QUESTION: {query}\n\nAnswer:")
        resp = run_task(company, user, task="support_assist", prompt=prompt,
                        prompt_name="support_assist")
        text = (getattr(resp, "text", "") or "").strip()
        if text:
            result["ai_answer"] = text
            result["used_ai"] = True
    except Exception:                                          # noqa: BLE001
        pass  # deterministic KB matches are the guaranteed fallback
    return result
