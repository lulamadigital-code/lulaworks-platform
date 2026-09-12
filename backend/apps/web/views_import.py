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

    # POST = upload one or more documents. We only hash, de-dupe, store and queue
    # here — extraction runs in the worker, so the request returns immediately and
    # can never hit a gateway timeout.
    if request.method == "POST":
        files = request.FILES.getlist("documents")
        if not files:
            messages.error(request, "Choose at least one document to upload.")
            return redirect("web:import_batch", pk=pk)
        from apps.knowledge.models import ImportedDocument
        from apps.knowledge.tasks import process_import_document
        queued = dupes = 0
        for f in files:
            try:
                doc = imp.queue_document(batch, f.name, f.read(), request.user)
            except Exception:                            # noqa: BLE001
                continue
            if doc.status == ImportedDocument.Status.DUPLICATE:
                dupes += 1
            else:
                queued += 1
                process_import_document.delay(str(doc.id))
        msg = f"{queued} document{'s' if queued != 1 else ''} uploaded — processing in the "
        msg += "background. You can leave this page and come back."
        if dupes:
            msg += f" {dupes} duplicate{'s' if dupes != 1 else ''} skipped."
        messages.success(request, msg)
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
def import_consolidate(request, pk):
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    batch = get_object_or_404(ImportBatch.objects.all(), pk=pk)
    removed = imp.consolidate_batch(batch)
    messages.success(request, f"Merged {removed} duplicate mention{'s' if removed != 1 else ''} — "
                              "each customer, supplier and person now appears once.")
    return redirect("web:import_batch", pk=pk)


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
def import_document_retry(request, pk, did):
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    batch = get_object_or_404(ImportBatch.objects.all(), pk=pk)
    from apps.knowledge.models import ImportedDocument
    from apps.knowledge.tasks import process_import_document
    doc = get_object_or_404(batch.documents, pk=did)
    if doc.status == ImportedDocument.Status.DUPLICATE:
        messages.info(request, "That document is a duplicate — nothing to retry.")
        return redirect("web:import_batch", pk=pk)
    doc.status = ImportedDocument.Status.QUEUED
    doc.failed = False
    doc.save(update_fields=["status", "failed", "updated_at"])
    process_import_document.delay(str(doc.id))
    messages.success(request, f"Retrying “{doc.filename}”.")
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
