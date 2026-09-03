"""Customer Purchase Orders — the bridge between Sales and Operations.

A customer PO is a first-class object here (not just a file on a quotation):
capture it (upload → AI extraction, or type it), match it to the quotation it
confirms, then convert it into a job. Distinct from supplier POs (procurement).
"""
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST


def _can_see(user):
    return any(user.has_perm_code(p) for p in
               ("quotes.create", "quotes.approve", "quotes.download", "projects.view"))


def _can_edit(user):
    return user.has_perm_code("quotes.create")


def _decimal_or_none(raw):
    from decimal import Decimal, InvalidOperation
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw).replace(",", "").strip())
    except (InvalidOperation, TypeError):
        return None


def _date_or_none(raw):
    try:
        return date.fromisoformat((raw or "").strip()) if raw else None
    except ValueError:
        return None


@login_required
def customer_pos(request):
    """Workspace: status summary + recent POs + Add."""
    if not _can_see(request.user):
        messages.error(request, "You don't have access to purchase orders.")
        return redirect("web:dashboard")
    from apps.projects.models import Project
    from apps.quotes.models import CustomerPurchaseOrder

    pos = list(CustomerPurchaseOrder.objects.select_related(
        "quotation", "quotation__customer").all())
    job_quote_ids = set(Project.objects.filter(quotation__isnull=False)
                        .values_list("quotation_id", flat=True))

    def bucket(po):
        if po.status in ("cancelled", "complete"):
            return "closed"
        if not po.is_matched:
            return "unmatched"
        if po.quotation_id in job_quote_ids:
            return "converted"
        return "matched"

    counts = {"unmatched": 0, "matched": 0, "converted": 0, "closed": 0}
    rows = []
    for po in pos:
        b = bucket(po)
        counts[b] += 1
        rows.append({"po": po, "bucket": b})
    return render(request, "web/customer_pos.html", {
        "rows": rows[:50], "counts": counts, "total": len(pos),
        "can_edit": _can_edit(request.user)})


@login_required
def customer_po_add(request):
    """Add a PO — upload (auto-extract) or type it. Creates the PO then opens it
    (where the workspace suggests the quotation to match)."""
    if not _can_edit(request.user):
        messages.error(request, "You don't have permission to add purchase orders.")
        return redirect("web:customer_pos")
    from apps.core.uploads import validate_upload
    from apps.knowledge.document_intelligence import (extract_po_fields,
                                                      extract_text_from_upload)
    from apps.quotes.models import CustomerPurchaseOrder

    if request.method == "POST":
        f = request.FILES.get("document")
        data = {k: (request.POST.get(k) or "").strip()
                for k in ("po_number", "client_name", "site", "value", "po_date", "notes")}
        if f:
            try:
                validate_upload(f)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
                return redirect("web:customer_po_add")
            fields = extract_po_fields(extract_text_from_upload(f),
                                       company=request.user.active_company,
                                       user=request.user, use_ai=True)
            f.seek(0)
            for key in ("po_number", "value", "po_date"):
                if not data.get(key) and fields.get(key):
                    data[key] = str(fields[key])
            if not data.get("client_name") and fields.get("client"):
                data["client_name"] = str(fields["client"])
        if not any([data["po_number"], data["value"], data["client_name"], f]):
            messages.error(request, "Add a PO number, or upload the PO document.")
            return redirect("web:customer_po_add")
        po = CustomerPurchaseOrder.objects.create(
            company=request.user.active_company,
            po_number=data["po_number"] or f"PO-{date.today():%Y%m%d}-{CustomerPurchaseOrder.objects.count() + 1:04d}",
            client_name=data["client_name"], site=data["site"],
            value=_decimal_or_none(data["value"]) or 0,
            po_date=_date_or_none(data["po_date"]), notes=data["notes"][:255],
            document=f or None, created_by=request.user, updated_by=request.user)
        from apps.core.audit import audit
        audit(request, "customer_po.added", entity=po)
        messages.success(request, f"PO {po.po_number} captured — match it to a quotation below.")
        return redirect("web:customer_po_detail", pk=po.pk)

    # GET — recent quotations for the manual picker
    from apps.quotes.models import Quotation
    quotes = list(Quotation.objects.select_related("customer").all()[:50])
    return render(request, "web/customer_po_form.html", {"quotes": quotes})


@login_required
@require_POST
def customer_po_extract(request):
    """Stateless: read an uploaded PO and return its fields as JSON (auto-fills
    the Add form). Saves nothing."""
    if not _can_edit(request.user):
        return JsonResponse({}, status=403)
    from apps.knowledge.document_intelligence import (extract_po_fields,
                                                      extract_text_from_upload)
    f = request.FILES.get("document")
    if not f:
        return JsonResponse({}, status=400)
    fields = extract_po_fields(extract_text_from_upload(f),
                               company=request.user.active_company,
                               user=request.user, use_ai=True)
    return JsonResponse(fields)


@login_required
def customer_po_detail(request, pk):
    if not _can_see(request.user):
        messages.error(request, "You don't have access to purchase orders.")
        return redirect("web:dashboard")
    from apps.projects.models import Project
    from apps.quotes.models import CustomerPurchaseOrder
    from apps.quotes.services import po_variance, suggest_quotations_for_po

    po = get_object_or_404(CustomerPurchaseOrder.objects.select_related(
        "quotation", "quotation__customer"), pk=pk)
    suggestions, job, search = [], None, (request.GET.get("q") or "").strip()
    variance = po_variance(po) if po.is_matched else None
    if not po.is_matched:
        suggestions = suggest_quotations_for_po(
            request.user.active_company, po_number=po.po_number,
            client_name=po.client_name, value=po.value)
        if search:
            from apps.quotes.models import Quotation
            from django.db.models import Q
            search_results = list(Quotation.objects.select_related("customer").filter(
                Q(number__icontains=search) | Q(client_name__icontains=search)
                | Q(title__icontains=search))[:8])
        else:
            search_results = []
    else:
        search_results = []
        job = Project.objects.filter(quotation=po.quotation).first()
    return render(request, "web/customer_po_detail.html", {
        "po": po, "suggestions": suggestions, "job": job, "variance": variance,
        "search": search, "search_results": search_results,
        "can_edit": _can_edit(request.user),
        "statuses": CustomerPurchaseOrder.Status.choices})


@login_required
@require_POST
def customer_po_link(request, pk):
    """Link (or relink) the PO to a quotation."""
    if not _can_edit(request.user):
        messages.error(request, "You don't have permission.")
        return redirect("web:customer_po_detail", pk=pk)
    from apps.quotes.models import CustomerPurchaseOrder, Quotation
    po = get_object_or_404(CustomerPurchaseOrder.objects.all(), pk=pk)
    quote = Quotation.objects.filter(pk=request.POST.get("quotation")).first()
    if quote is None:
        messages.error(request, "Choose a quotation to link.")
        return redirect("web:customer_po_detail", pk=pk)
    po.quotation = quote
    po.status = CustomerPurchaseOrder.Status.ACKNOWLEDGED
    po.updated_by = request.user
    po.save(update_fields=["quotation", "status", "updated_by", "updated_at"])
    from apps.core.audit import audit
    audit(request, "customer_po.matched", entity=po)
    messages.success(request, f"PO {po.po_number} linked to {quote.number}.")
    return redirect("web:customer_po_detail", pk=pk)


@login_required
@require_POST
def customer_po_create_job(request, pk):
    """Convert the matched PO into operational work (project · phases · tasks)."""
    if not request.user.has_perm_code("projects.create"):
        messages.error(request, "You don't have permission to create jobs.")
        return redirect("web:customer_po_detail", pk=pk)
    from apps.quotes.models import CustomerPurchaseOrder
    from apps.quotes.services import QuotationError, initiate_work_from_quotation
    po = get_object_or_404(CustomerPurchaseOrder.objects.select_related("quotation"), pk=pk)
    if not po.is_matched:
        messages.error(request, "Match the PO to a quotation first.")
        return redirect("web:customer_po_detail", pk=pk)
    try:
        project = initiate_work_from_quotation(po.quotation, request.user)
    except QuotationError as exc:
        messages.error(request, str(exc))
        return redirect("web:customer_po_detail", pk=pk)
    po.status = CustomerPurchaseOrder.Status.IN_PROGRESS
    po.save(update_fields=["status", "updated_at"])
    from apps.core.audit import audit
    audit(request, "work.initiated", entity=project)
    messages.success(request, f"Job {project.number} created from PO {po.po_number}.")
    return redirect("web:project_detail", pk=project.id)


@login_required
@require_POST
def customer_po_status(request, pk):
    if not _can_edit(request.user):
        messages.error(request, "You don't have permission.")
        return redirect("web:customer_po_detail", pk=pk)
    from apps.quotes.models import CustomerPurchaseOrder
    po = get_object_or_404(CustomerPurchaseOrder.objects.all(), pk=pk)
    new = request.POST.get("status")
    if new in dict(CustomerPurchaseOrder.Status.choices):
        po.status = new
        po.save(update_fields=["status", "updated_at"])
        messages.success(request, "Status updated.")
    return redirect("web:customer_po_detail", pk=pk)
