"""Tenant-side Support Center — Help & Support in the app. A company user reports
a problem to LulaWorks Support and follows the conversation. Everything is
tenant-scoped: a company only ever sees its own tickets."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from apps.support import services as support
from apps.support.models import (
    OPEN_STATUSES, SupportTicket, TicketCategory, TicketPriority, TicketStatus,
)


def _client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return xff.split(",")[0].strip() if xff else request.META.get("REMOTE_ADDR")


def _can_see_company(user):
    """Owners/administrators can see the whole company's tickets; everyone else
    sees their own."""
    return user.has_perm_code("company.manage")


@login_required
def support_home(request):
    """My Tickets — the signed-in user's own support tickets."""
    mine = list(SupportTicket.objects.filter(created_by=request.user))
    return render(request, "web/support/list.html", {
        "nav_section": "support", "tickets": mine, "scope": "mine",
        "can_company": _can_see_company(request.user),
        "open_count": sum(1 for t in mine if t.status in OPEN_STATUSES),
    })


@login_required
def support_company(request):
    """Company Tickets — every ticket raised by anyone in the company (admins)."""
    if not _can_see_company(request.user):
        messages.error(request, "You can only see your own support tickets.")
        return redirect("web:support_home")
    tickets = list(SupportTicket.objects.all().select_related("created_by"))
    return render(request, "web/support/list.html", {
        "nav_section": "support", "tickets": tickets, "scope": "company",
        "can_company": True,
        "open_count": sum(1 for t in tickets if t.status in OPEN_STATUSES),
    })


@login_required
def support_create(request):
    if request.method == "POST":
        try:
            # If the ticket came from an error page, attach the safe technical
            # context captured by the 500 handler (module, request id, version…).
            err_ref = request.POST.get("error_reference", "").strip()
            err_ctx = {}
            if err_ref:
                from apps.support.models import ErrorEvent
                ev = ErrorEvent.objects.filter(reference=err_ref).first()
                if ev:
                    err_ctx = ev.safe_context()
            # Carry the pre-ticket AI/KB assist so the customer needn't repeat it.
            assist_summary = request.POST.get("assist_summary", "").strip()
            if assist_summary:
                err_ctx["AI assist"] = assist_summary[:800]
            ticket = support.create_ticket(
                company=request.user.active_company, user=request.user,
                subject=request.POST.get("subject", ""),
                category=request.POST.get("category", "other"),
                priority=request.POST.get("priority", "normal"),
                description=request.POST.get("description", ""),
                related_module=request.POST.get("related_module", ""),
                related_ref=request.POST.get("related_ref", ""),
                error_reference=err_ref, error_context=err_ctx,
                ip=_client_ip(request))
            for f in request.FILES.getlist("attachments"):
                support.add_message(ticket=ticket, sender=request.user, body="",
                                    files=[f])
            messages.success(request, f"Ticket {ticket.number} created — we'll be in touch.")
            return redirect("web:support_detail", pk=ticket.id)
        except support.SupportError as exc:
            messages.error(request, str(exc))
        except Exception as exc:                               # noqa: BLE001
            messages.error(request, f"Could not create the ticket: {exc}")
    ref = request.GET.get("ref", "").strip()
    # LulaAI pre-ticket assist: search the KB (and, if available, add a grounded
    # AI suggestion) before the user commits to a ticket.
    ask = request.GET.get("ask", "").strip()
    assist = support.assist(request.user.active_company, request.user, ask) if ask else None
    return render(request, "web/support/create.html", {
        "nav_section": "support",
        "categories": TicketCategory.choices, "priorities": TicketPriority.choices,
        "ask": ask, "assist": assist,
        "prefill": {"subject": request.GET.get("subject", "") or ask,
                    "description": ask,
                    # Errors default to the Technical Problem category.
                    "category": request.GET.get("category", "") or ("technical" if ref else ""),
                    "error_reference": ref},
    })


@login_required
def support_kb(request):
    """Knowledge Base — browse & search LulaWorks help articles."""
    q = request.GET.get("q", "").strip()
    if q:
        articles = support.search_kb(q, limit=30)
    else:
        from apps.core.context import system_scope
        from apps.support.models import KBArticle
        with system_scope():
            articles = list(KBArticle.objects.filter(is_published=True))
    return render(request, "web/support/kb.html", {
        "nav_section": "support", "articles": articles, "q": q})


@login_required
def support_kb_article(request, slug):
    from apps.core.context import system_scope
    from apps.support.models import KBArticle
    with system_scope():
        article = KBArticle.objects.filter(slug=slug, is_published=True).first()
        if article is None:
            messages.error(request, "Article not found.")
            return redirect("web:support_kb")
        KBArticle.objects.filter(pk=article.pk).update(views=article.views + 1)
    return render(request, "web/support/kb_article.html", {
        "nav_section": "support", "article": article})


@login_required
def support_detail(request, pk):
    ticket = SupportTicket.objects.filter(pk=pk).select_related("created_by", "assigned_agent").first()
    if ticket is None:
        messages.error(request, "Ticket not found.")
        return redirect("web:support_home")
    # An employee may only open their own ticket unless they can see company-wide.
    if ticket.created_by_id != request.user.id and not _can_see_company(request.user):
        messages.error(request, "You don't have access to that ticket.")
        return redirect("web:support_home")

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "reply":
                support.add_message(ticket=ticket, sender=request.user,
                                    body=request.POST.get("body", ""),
                                    from_support=False,
                                    files=request.FILES.getlist("attachments"),
                                    ip=_client_ip(request))
                messages.success(request, "Reply sent.")
            elif action == "reopen":
                support.set_status(ticket=ticket, actor=request.user,
                                   status=TicketStatus.OPEN, ip=_client_ip(request))
                messages.success(request, "Ticket reopened.")
            elif action == "close":
                support.set_status(ticket=ticket, actor=request.user,
                                   status=TicketStatus.CLOSED, ip=_client_ip(request))
                messages.success(request, "Ticket closed. Thanks!")
        except support.SupportError as exc:
            messages.error(request, str(exc))
        return redirect("web:support_detail", pk=pk)

    # Customers never see internal support notes.
    thread = list(ticket.messages.filter(is_internal=False)
                  .select_related("sender").prefetch_related("attachments"))
    return render(request, "web/support/detail.html", {
        "nav_section": "support", "ticket": ticket, "thread": thread,
        "can_reopen": ticket.status in {TicketStatus.RESOLVED, TicketStatus.CLOSED},
    })
