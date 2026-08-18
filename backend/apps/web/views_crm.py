"""CRM web surface — the relationship layer, server-rendered.

This is the "CRM" the user sees. It sits on the customers app (whose internal
Django label stays `customers` so no FK churn) and drives the pre-sale flow the
platform was missing: Lead → Opportunity → Customer → Quotation → Job.

Every write goes through apps.customers.services so the business rules live in
one place; these views are thin — parse the request, call the service, redirect
with a message. Tenancy is ambient (TenantMiddleware binds the tenant from the
signed-in user), so `Model.objects.all()` is already scoped to the company.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.customers import services as crm
from apps.customers.models import (
    Activity,
    CustomerType,
    Interaction,
    LEAD_SOURCES,
    Lead,
    Opportunity,
    OpportunityStage,
    OPEN_OPPORTUNITY_STAGES,
)


def _can_manage(user) -> bool:
    """CRM writes reuse the same gate as customer/project management, so a sales
    user who can create work can also work the pipeline."""
    return user.has_perm_code("projects.create")


def _parse_dt(value):
    """Accept a browser <input type=datetime-local> value, else None."""
    if not value:
        return None
    parsed = timezone.datetime.fromisoformat(value) if "T" in value else None
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def _decimal_or_none(value):
    from decimal import Decimal, InvalidOperation
    value = (value or "").replace(",", "").strip()
    if not value:
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


# ── Hub ───────────────────────────────────────────────────────────────────────

@login_required
def crm_hub(request):
    """The CRM landing page: the pipeline at a glance, this user's open
    activities, the newest leads, and the headline report numbers."""
    company = request.user.active_company
    reports = crm.crm_reports(company)
    my_activities = crm.open_activities(company, assigned_to=request.user, limit=12)
    recent_leads = list(Lead.objects.filter(status__in=[
        Lead.Status.NEW, Lead.Status.CONTACTED, Lead.Status.QUALIFIED])[:8])

    # People we work with — the human contacts at our clients. Key people
    # (primary first) plus a headcount so the page shows real relationships.
    people = []
    people_total = 0
    try:
        from apps.customers.models import CustomerContact
        active = CustomerContact.objects.filter(status=CustomerContact.Status.ACTIVE)
        people_total = active.count()
        people = list(active.select_related("customer")
                      .order_by("-is_primary", "full_name")[:12])
    except Exception:
        pass

    return render(request, "web/crm/hub.html", {
        "reports": reports,
        "pipeline": reports["pipeline"],
        "my_activities": my_activities,
        "recent_leads": recent_leads,
        "people": people,
        "people_total": people_total,
        "can_manage": _can_manage(request.user),
    })


# ── Leads ─────────────────────────────────────────────────────────────────────

@login_required
def leads_list(request):
    status = request.GET.get("status", "")
    leads = Lead.objects.all()
    if status:
        leads = leads.filter(status=status)
    return render(request, "web/crm/leads.html", {
        "leads": list(leads[:200]),
        "status": status,
        "statuses": Lead.Status.choices,
        "sources": LEAD_SOURCES,
        "types": CustomerType.choices,
        "can_manage": _can_manage(request.user),
    })


@login_required
@require_POST
def lead_create(request):
    if not _can_manage(request.user):
        messages.error(request, "You do not have permission to add leads.")
        return redirect("web:crm_leads")
    try:
        lead = crm.create_lead(
            request.user.active_company, request.user,
            company_name=request.POST.get("company_name", ""),
            contact_name=request.POST.get("contact_name", "").strip(),
            job_title=request.POST.get("job_title", "").strip(),
            email=request.POST.get("email", "").strip(),
            telephone=request.POST.get("telephone", "").strip(),
            mobile=request.POST.get("mobile", "").strip(),
            industry=request.POST.get("industry", "").strip(),
            customer_type=request.POST.get("customer_type", "").strip(),
            city=request.POST.get("city", "").strip(),
            source=request.POST.get("source", "").strip(),
            estimated_value=_decimal_or_none(request.POST.get("estimated_value")),
            assigned_to=request.user,
        )
    except crm.CRMError as exc:
        messages.error(request, str(exc))
        return redirect("web:crm_leads")
    messages.success(request, f"Lead “{lead.company_name}” captured.")
    return redirect("web:crm_lead_detail", pk=lead.id)


@login_required
def lead_detail(request, pk):
    lead = get_object_or_404(Lead.objects.all(), pk=pk)
    return render(request, "web/crm/lead_detail.html", {
        "lead": lead,
        "activities": list(lead.activities.all()[:20]),
        "interactions": list(lead.interactions.all()[:20]),
        "notes": list(lead.crm_notes.all()[:20]),
        "can_manage": _can_manage(request.user),
    })


@login_required
@require_POST
def lead_convert(request, pk):
    lead = get_object_or_404(Lead.objects.all(), pk=pk)
    if not _can_manage(request.user):
        messages.error(request, "You do not have permission to convert leads.")
        return redirect("web:crm_lead_detail", pk=pk)
    customer = crm.convert_lead(
        lead, request.user,
        create_opportunity=bool(request.POST.get("create_opportunity")),
        opportunity_title=request.POST.get("opportunity_title", "").strip(),
    )
    messages.success(request, f"“{lead.company_name}” is now a customer.")
    from apps.analytics.services import track
    track("customer_created", request=request, module="crm", feature="convert_lead")
    return redirect("web:customer_detail", pk=customer.id)


@login_required
@require_POST
def lead_lost(request, pk):
    lead = get_object_or_404(Lead.objects.all(), pk=pk)
    if not _can_manage(request.user):
        messages.error(request, "You do not have permission to update leads.")
        return redirect("web:crm_lead_detail", pk=pk)
    crm.mark_lead_lost(lead, request.user, reason=request.POST.get("reason", ""))
    messages.success(request, "Lead marked as lost.")
    return redirect("web:crm_lead_detail", pk=pk)


# ── Opportunities (the pipeline) ──────────────────────────────────────────────

@login_required
def pipeline(request):
    """The deal board — one column per open stage, plus won/lost tallies."""
    open_opps = list(Opportunity.objects.filter(stage__in=OPEN_OPPORTUNITY_STAGES)
                     .select_related("customer"))
    columns = []
    for stage in OPEN_OPPORTUNITY_STAGES:
        cards = [o for o in open_opps if o.stage == stage]
        columns.append({
            "stage": stage,
            "label": OpportunityStage(stage).label,
            "cards": cards,
            "value": sum((o.estimated_value or 0) for o in cards),
        })
    return render(request, "web/crm/pipeline.html", {
        "columns": columns,
        "summary": crm.pipeline_summary(request.user.active_company),
        "won": list(Opportunity.objects.filter(stage=OpportunityStage.WON)
                    .select_related("customer")[:10]),
        "can_manage": _can_manage(request.user),
    })


@login_required
@require_POST
def opportunity_create(request):
    from apps.customers.models import Customer

    if not _can_manage(request.user):
        messages.error(request, "You do not have permission to add opportunities.")
        return redirect("web:crm_pipeline")
    customer = get_object_or_404(Customer.objects.all(),
                                 pk=request.POST.get("customer"))
    try:
        opp = crm.create_opportunity_for(
            customer, request.user,
            title=request.POST.get("title", ""),
            description=request.POST.get("description", "").strip(),
            estimated_value=_decimal_or_none(request.POST.get("estimated_value")),
            currency=request.POST.get("currency", "").strip() or customer.currency,
            source=request.POST.get("source", "").strip(),
            assigned_to=request.user,
        )
    except crm.CRMError as exc:
        messages.error(request, str(exc))
        return redirect("web:customer_detail", pk=customer.id)
    messages.success(request, f"Opportunity “{opp.title}” opened.")
    from apps.analytics.services import track
    track("opportunity_created", request=request, module="crm", feature="opportunity",
          metadata={"stage": opp.stage})
    return redirect("web:crm_opportunity_detail", pk=opp.id)


@login_required
def opportunity_detail(request, pk):
    opp = get_object_or_404(Opportunity.objects.select_related("customer", "lead"),
                            pk=pk)
    return render(request, "web/crm/opportunity_detail.html", {
        "opp": opp,
        "stages": OpportunityStage.choices,
        "activities": list(opp.activities.all()[:20]),
        "interactions": list(opp.interactions.all()[:20]),
        "notes": list(opp.crm_notes.all()[:20]),
        "can_manage": _can_manage(request.user),
    })


@login_required
@require_POST
def opportunity_stage(request, pk):
    opp = get_object_or_404(Opportunity.objects.all(), pk=pk)
    if not _can_manage(request.user):
        messages.error(request, "You do not have permission to update the pipeline.")
        return redirect("web:crm_opportunity_detail", pk=pk)
    stage = request.POST.get("stage", "")
    try:
        if stage == OpportunityStage.WON:
            crm.win_opportunity(opp, request.user)
        elif stage == OpportunityStage.LOST:
            crm.lose_opportunity(opp, request.user,
                                 reason=request.POST.get("reason", ""))
        else:
            crm.set_opportunity_stage(opp, request.user, stage)
    except crm.CRMError as exc:
        messages.error(request, str(exc))
        return redirect("web:crm_opportunity_detail", pk=pk)
    messages.success(request, f"Moved to {opp.get_stage_display()}.")
    return redirect("web:crm_opportunity_detail", pk=pk)


@login_required
@require_POST
def opportunity_move(request, pk):
    """Drag-and-drop endpoint for the pipeline board: set an opportunity's stage
    and answer JSON so the card can settle in its new column without a reload.

    The board only renders the OPEN stages as columns, so a drag never lands on
    won/lost — those stay deliberate decisions made on the detail page (a win
    links a quotation, a loss records a reason). We still guard here.
    """
    opp = get_object_or_404(Opportunity.objects.all(), pk=pk)
    if not _can_manage(request.user):
        return JsonResponse({"ok": False, "error": "Not allowed."}, status=403)
    stage = request.POST.get("stage", "")
    if stage not in OPEN_OPPORTUNITY_STAGES:
        return JsonResponse(
            {"ok": False, "error": "Won/lost are set from the opportunity page."},
            status=400)
    try:
        crm.set_opportunity_stage(opp, request.user, stage)
    except crm.CRMError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "stage": opp.stage,
                         "probability": opp.probability})


# ── Activities ────────────────────────────────────────────────────────────────

@login_required
def activities(request):
    scope = request.GET.get("scope", "mine")
    company = request.user.active_company
    if scope == "all":
        items = crm.open_activities(company)
    else:
        items = crm.open_activities(company, assigned_to=request.user)
    return render(request, "web/crm/activities.html", {
        "items": items, "scope": scope,
        "can_manage": _can_manage(request.user),
    })


@login_required
@require_POST
def activity_schedule(request):
    """Schedule an activity against whatever anchor the form carries (customer,
    lead or opportunity id)."""
    if not _can_manage(request.user):
        messages.error(request, "You do not have permission to add activities.")
        return redirect("web:crm_activities")
    company = request.user.active_company
    kwargs = _anchor_kwargs(request)
    try:
        crm.schedule_activity(
            company, request.user,
            subject=request.POST.get("subject", ""),
            activity_type=request.POST.get("activity_type", Activity.Type.FOLLOW_UP),
            due_at=_parse_dt(request.POST.get("due_at")),
            detail=request.POST.get("detail", "").strip(),
            **kwargs,
        )
    except crm.CRMError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Activity scheduled.")
    return redirect(request.POST.get("next") or "web:crm_activities")


@login_required
@require_POST
def activity_complete(request, pk):
    activity = get_object_or_404(Activity.objects.all(), pk=pk)
    crm.complete_activity(activity, request.user,
                          outcome=request.POST.get("outcome", ""))
    messages.success(request, "Activity completed.")
    return redirect(request.POST.get("next") or "web:crm_activities")


# ── Communication history + notes (posted from any detail page) ───────────────

@login_required
@require_POST
def interaction_log(request):
    if not _can_manage(request.user):
        messages.error(request, "You do not have permission to log interactions.")
        return redirect(request.POST.get("next") or "web:crm_hub")
    company = request.user.active_company
    kwargs = _anchor_kwargs(request)
    try:
        crm.log_interaction(
            company, request.user,
            summary=request.POST.get("summary", ""),
            channel=request.POST.get("channel", Interaction.Channel.NOTE),
            direction=request.POST.get("direction", Interaction.Direction.OUTBOUND),
            subject=request.POST.get("subject", "").strip(),
            occurred_at=_parse_dt(request.POST.get("occurred_at")),
            **kwargs,
        )
    except crm.CRMError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Interaction logged.")
    return redirect(request.POST.get("next") or "web:crm_hub")


@login_required
@require_POST
def note_add(request):
    company = request.user.active_company
    kwargs = _anchor_kwargs(request)
    try:
        crm.add_note(company, request.user, body=request.POST.get("body", ""),
                     is_pinned=bool(request.POST.get("is_pinned")), **kwargs)
    except crm.CRMError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Note added.")
    return redirect(request.POST.get("next") or "web:crm_hub")


def _anchor_kwargs(request):
    """Resolve the customer/lead/opportunity/contact a note or interaction hangs off."""
    from apps.customers.models import Customer, CustomerContact
    kwargs = {}
    if request.POST.get("customer"):
        kwargs["customer"] = get_object_or_404(Customer.objects.all(),
                                               pk=request.POST["customer"])
    if request.POST.get("lead"):
        kwargs["lead"] = get_object_or_404(Lead.objects.all(),
                                           pk=request.POST["lead"])
    if request.POST.get("opportunity"):
        kwargs["opportunity"] = get_object_or_404(Opportunity.objects.all(),
                                                  pk=request.POST["opportunity"])
    if request.POST.get("contact"):
        contact = get_object_or_404(CustomerContact.objects.select_related("customer"),
                                    pk=request.POST["contact"])
        kwargs["contact"] = contact
        kwargs.setdefault("customer", contact.customer)
    return kwargs


# ── Search + reports ──────────────────────────────────────────────────────────

@login_required
def crm_search(request):
    q = request.GET.get("q", "")
    results = crm.crm_search(request.user.active_company, q)
    return render(request, "web/crm/search.html", {"results": results, "q": q})


@login_required
def crm_reports(request):
    return render(request, "web/crm/reports.html", {
        "reports": crm.crm_reports(request.user.active_company),
    })


@login_required
def crm_analytics(request):
    """Sales analytics — won value by salesperson / customer / industry, the
    won-vs-lost trend, deal size & sales cycle, and customer acquisition. All
    computed from existing opportunities and customers."""
    return render(request, "web/crm/analytics.html", {
        "a": crm.crm_analytics(request.user.active_company),
    })


# ── Customer Sites & Contacts management ──────────────────────────────────────

@login_required
def customer_sites(request, pk):
    """Manage a customer's operating sites — where work actually happens, and
    what it takes to get on site (GPS, access, safety)."""
    from apps.customers.models import Customer, CustomerSite
    customer = get_object_or_404(
        Customer.objects.prefetch_related("sites", "contacts", "branches"), pk=pk)

    if request.method == "POST" and _can_manage(request.user):
        site = None
        edit_id = request.POST.get("site_id")
        if edit_id:
            site = get_object_or_404(CustomerSite.objects.filter(customer=customer),
                                     pk=edit_id)
        try:
            crm.save_site(customer, request.user, site=site, data=request.POST)
        except crm.CRMError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Site saved.")
            return redirect("web:crm_customer_sites", pk=pk)

    editing = None
    if request.GET.get("edit"):
        editing = CustomerSite.objects.filter(customer=customer,
                                              pk=request.GET["edit"]).first()
    return render(request, "web/crm/customer_sites.html", {
        "customer": customer,
        "sites": list(customer.sites.select_related("parent", "site_contact")),
        "contacts": list(customer.contacts.all()),
        "editing": editing,
        "can_manage": _can_manage(request.user),
    })


@login_required
@require_POST
def customer_site_delete(request, pk, site_id):
    from apps.customers.models import Customer, CustomerSite
    customer = get_object_or_404(Customer.objects.all(), pk=pk)
    if not _can_manage(request.user):
        messages.error(request, "You do not have permission to remove sites.")
        return redirect("web:crm_customer_sites", pk=pk)
    site = get_object_or_404(CustomerSite.objects.filter(customer=customer), pk=site_id)
    crm.delete_site(site, request.user)
    messages.success(request, "Site removed.")
    return redirect("web:crm_customer_sites", pk=pk)


@login_required
def customer_contacts(request, pk):
    """Manage a customer's people — grouped by department, with the
    responsibility coverage that drives document routing (the gaps are the point)."""
    from apps.customers.models import (
        CONTACT_ROLES, Customer, CustomerContact, RESPONSIBILITIES)
    customer = get_object_or_404(
        Customer.objects.prefetch_related("contacts", "departments"), pk=pk)

    if request.method == "POST" and _can_manage(request.user):
        contact = None
        edit_id = request.POST.get("contact_id")
        if edit_id:
            contact = get_object_or_404(
                CustomerContact.objects.filter(customer=customer), pk=edit_id)
        try:
            crm.save_contact(customer, request.user, contact=contact, data=request.POST)
        except crm.CRMError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Contact saved.")
            return redirect("web:crm_customer_contacts", pk=pk)

    contacts = list(customer.contacts.select_related("department"))
    grouped, unassigned = [], []
    for dept in customer.departments.all():
        people = [c for c in contacts if c.department_id == dept.id]
        if people:
            grouped.append({"department": dept, "contacts": people})
    unassigned = [c for c in contacts if c.department_id is None]

    editing = None
    if request.GET.get("edit"):
        editing = CustomerContact.objects.filter(
            customer=customer, pk=request.GET["edit"]).first()

    return render(request, "web/crm/customer_contacts.html", {
        "customer": customer,
        "grouped": grouped, "unassigned": unassigned,
        "matrix": crm.responsibility_matrix(customer),
        "departments": list(customer.departments.all()),
        "roles": CONTACT_ROLES,
        "responsibilities": list(RESPONSIBILITIES.items()),
        "methods": CustomerContact.Method.choices,
        "statuses": CustomerContact.Status.choices,
        "editing": editing,
        "can_manage": _can_manage(request.user),
    })


@login_required
@require_POST
def customer_contact_status(request, pk, contact_id):
    from apps.customers.models import Customer, CustomerContact
    customer = get_object_or_404(Customer.objects.all(), pk=pk)
    if not _can_manage(request.user):
        messages.error(request, "You do not have permission to change contacts.")
        return redirect("web:crm_customer_contacts", pk=pk)
    contact = get_object_or_404(
        CustomerContact.objects.filter(customer=customer), pk=contact_id)
    status = request.POST.get("status", CustomerContact.Status.ACTIVE)
    crm.set_contact_status(contact, request.user, status=status)
    messages.success(request, f"{contact.full_name} updated.")
    return redirect("web:crm_customer_contacts", pk=pk)
