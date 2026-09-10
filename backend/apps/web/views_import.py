"""Bring Your Business History — the historical-import console flow.

A contractor uploads its existing documents (old quotes, POs, invoices,
supplier quotes…); we classify them, extract the companies and people, resolve
them against the ERP, and present the matches for confirmation. Nothing enters
the ERP until the user confirms — the views call apps.knowledge.historical_import,
where the guardrails live.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.knowledge import historical_import as imp
from apps.knowledge import job_reconstruction as jr
from apps.knowledge.models import HistoricalJob, ImportBatch, StagedEntity


def _can(user):
    return user.has_perm_code("customers.manage")


@login_required
def import_centre(request):
    """List import batches + start a new one."""
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    batches = list(ImportBatch.objects.all().order_by("-created_at")[:50])
    return render(request, "web/import_centre.html", {"batches": batches})


@login_required
@require_POST
def import_start(request):
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    batch = imp.create_batch(request.user, label=(request.POST.get("label") or "").strip())
    return redirect("web:import_batch", pk=batch.pk)


@login_required
def import_batch(request, pk):
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    batch = get_object_or_404(ImportBatch.objects.all(), pk=pk)

    # POST = upload one or more documents into this batch.
    if request.method == "POST":
        files = request.FILES.getlist("documents")
        if not files:
            messages.error(request, "Choose at least one document to upload.")
            return redirect("web:import_batch", pk=pk)
        ok = 0
        for f in files:
            try:
                imp.ingest(batch, f.name, f.read(), request.user)
                ok += 1
            except Exception:                            # noqa: BLE001
                pass
        messages.success(request, f"Processed {ok} document{'s' if ok != 1 else ''}. "
                                  "Review the matches below.")
        return redirect("web:import_batch", pk=pk)

    summary = imp.batch_summary(batch)
    documents = list(batch.documents.all().order_by("-created_at"))
    entities = list(batch.entities.all().order_by("review_status", "kind", "-confidence"))
    # Split into what needs a human vs. what's settled, so the queue is obvious.
    pending = [e for e in entities if e.review_status == StagedEntity.Review.PENDING]
    done = [e for e in entities if e.review_status != StagedEntity.Review.PENDING]
    jobs = list(batch.jobs.exclude(status=HistoricalJob.Status.DISMISSED)
                .order_by("status", "-confidence"))
    return render(request, "web/import_batch.html", {
        "batch": batch, "summary": summary, "documents": documents,
        "pending": pending, "done": done, "jobs": jobs})


@login_required
@require_POST
def import_reconstruct(request, pk):
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    batch = get_object_or_404(ImportBatch.objects.all(), pk=pk)
    jobs = jr.reconstruct_jobs(batch, request.user)
    messages.success(request, f"Found {len(jobs)} possible job{'s' if len(jobs) != 1 else ''} "
                              "from your documents. Confirm the ones that are right.")
    return redirect("web:import_batch", pk=pk)


@login_required
@require_POST
def import_job(request, pk, jid):
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    batch = get_object_or_404(ImportBatch.objects.all(), pk=pk)
    job = get_object_or_404(batch.jobs, pk=jid)
    try:
        jr.confirm_job(job, request.user, decision=(request.POST.get("decision") or "").strip())
        messages.success(request, f"{job.title} — {job.get_status_display().lower()}.")
    except (PermissionError, ValueError) as exc:
        messages.error(request, str(exc))
    return redirect("web:import_batch", pk=pk)


@login_required
@require_POST
def import_commit(request, pk, eid):
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    batch = get_object_or_404(ImportBatch.objects.all(), pk=pk)
    staged = get_object_or_404(batch.entities, pk=eid)
    decision = (request.POST.get("decision") or "").strip()
    customer_id = request.POST.get("customer_id") or None
    try:
        imp.commit_entity(staged, request.user, decision=decision, customer_id=customer_id)
        verb = {"link": "Linked", "create": "Created", "reject": "Skipped"}.get(decision, "Updated")
        messages.success(request, f"{verb} {staged.raw_name}.")
    except imp.CommitError as exc:
        messages.error(request, str(exc))
    return redirect("web:import_batch", pk=pk)
