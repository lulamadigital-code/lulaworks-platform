"""Bring Your Business History — the historical-import console flow.

A contractor uploads its existing documents (old quotes, POs, invoices,
supplier quotes…); we classify them, extract the companies and people, resolve
them against the ERP, and present the matches for confirmation. Nothing enters
the ERP until the user confirms — the views call apps.knowledge.historical_import,
where the guardrails live.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.knowledge import historical_import as imp
from apps.knowledge import job_reconstruction as jr
from apps.knowledge.models import HistoricalJob, ImportBatch, StagedEntity


def _can(user):
    return user.has_perm_code("customers.manage")


def _is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"


def _bulk_done(request, pk, message, *, ok=True, status=200):
    """Return JSON for an AJAX bulk action (the page toasts + refreshes), or fall
    back to a message + redirect for a normal form post."""
    if _is_ajax(request):
        body = {"ok": ok, "message": message} if ok else {"ok": False, "error": message}
        return JsonResponse(body, status=status)
    (messages.success if ok else messages.error)(request, message)
    return redirect("web:import_batch", pk=pk)


@login_required
def import_centre(request):
    """Business History Overview — what Lulaworks learned, plus the batch list."""
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    batches = list(ImportBatch.objects.all().order_by("-created_at")[:50])
    overview = imp.history_overview(request.user.active_company)
    return render(request, "web/import_centre.html",
                  {"batches": batches, "overview": overview})


@login_required
def import_documents(request):
    """Documents explorer — every imported document across all batches, with
    search + a type filter. Click through to see what was extracted."""
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    from apps.knowledge.models import ImportedDocument
    q = (request.GET.get("q") or "").strip()
    dtype = (request.GET.get("type") or "").strip()
    docs = ImportedDocument.objects.select_related("batch").all()
    if q:
        from django.db.models import Q
        docs = docs.filter(Q(filename__icontains=q) | Q(text__icontains=q))
    if dtype:
        docs = docs.filter(doc_type=dtype)
    from django.db.models import F
    docs = list(docs.order_by(F("document_date").desc(nulls_last=True), "-created_at")[:500])
    # Group by document type so quotations, POs, invoices etc. are separated.
    order = [c[0] for c in ImportedDocument.DocType.choices]
    labels = dict(ImportedDocument.DocType.choices)
    buckets: dict = {}
    for d in docs:
        buckets.setdefault(d.doc_type, []).append(d)
    groups = [{"type": t, "label": labels.get(t, t), "docs": buckets[t]}
              for t in order if t in buckets]
    return render(request, "web/import_documents.html", {
        "groups": groups, "total": len(docs), "q": q, "dtype": dtype,
        "types": ImportedDocument.DocType.choices})


@login_required
def import_document(request, pk):
    """One imported document — its classification, status, and the entities
    Lulaworks extracted from it (the evidence)."""
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    from apps.knowledge.models import ImportedDocument
    doc = get_object_or_404(ImportedDocument.objects.select_related("batch"), pk=pk)
    # Decide how to preview the original file (if we kept it).
    file_kind = ""
    if doc.file and doc.file.name:
        name = doc.file.name.lower()
        if name.endswith(".pdf"):
            file_kind = "pdf"
        elif name.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            file_kind = "image"
        else:
            file_kind = "other"
    return render(request, "web/import_document.html",
                  {"doc": doc, "file_kind": file_kind})


@login_required
def import_relationships(request):
    """Relationships — the reconstructed business graph: each historical job and
    the customer + documents that make it up (AI OS §12/§5)."""
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    jobs = list(HistoricalJob.objects.exclude(status=HistoricalJob.Status.DISMISSED)
                .order_by("status", "-confidence")[:200])
    graph = []
    for j in jobs:
        docs = list(j.documents.all().order_by("doc_type")[:20])
        graph.append({"job": j, "documents": docs})
    return render(request, "web/import_relationships.html", {"graph": graph})


@login_required
def import_customers_discovered(request):
    """Customers found across all imported history — click through to the real
    Lulaworks customer record once resolved."""
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    customers = imp.discovered_customers(request.user.active_company)
    return render(request, "web/import_customers.html", {"customers": customers})


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
        import logging
        queued = dupes = errors = 0
        for f in files:
            try:
                doc = imp.queue_document(batch, f.name, f.read(), request.user)
            except Exception as exc:                     # noqa: BLE001
                errors += 1
                logging.getLogger("apps.knowledge").warning(
                    "Import upload failed for %s: %s", f.name, exc)
                continue
            if doc.status == ImportedDocument.Status.DUPLICATE:
                dupes += 1
            else:
                queued += 1
                process_import_document.delay(str(doc.id))
        if queued:
            msg = f"{queued} document{'s' if queued != 1 else ''} uploaded — processing in "
            msg += "the background. You can leave this page and come back."
            if dupes:
                msg += f" {dupes} duplicate{'s' if dupes != 1 else ''} skipped."
            messages.success(request, msg)
        if errors:
            messages.error(request, f"{errors} file{'s' if errors != 1 else ''} could not be "
                                    "stored and were not imported — please try again.")
        elif not queued and dupes:
            messages.info(request, f"{dupes} duplicate{'s' if dupes != 1 else ''} skipped — "
                                   "already imported.")
        return redirect("web:import_batch", pk=pk)

    summary = imp.batch_summary(batch)
    documents = list(batch.documents.all().order_by("-created_at"))
    entities = list(batch.entities.all().order_by("review_status", "kind", "-confidence"))
    # Split into what needs a human vs. what's settled, so the queue is obvious.
    pending = [e for e in entities if e.review_status == StagedEntity.Review.PENDING]
    done = [e for e in entities if e.review_status != StagedEntity.Review.PENDING]
    jobs = list(batch.jobs.exclude(status=HistoricalJob.Status.DISMISSED)
                .order_by("status", "-confidence"))
    pending_customers = sum(1 for e in pending if e.kind == StagedEntity.Kind.CUSTOMER)
    pending_suppliers = sum(1 for e in pending if e.kind == StagedEntity.Kind.SUPPLIER)
    return render(request, "web/import_batch.html", {
        "batch": batch, "summary": summary, "documents": documents,
        "pending": pending, "done": done, "jobs": jobs,
        "pending_customers": pending_customers, "pending_suppliers": pending_suppliers})


@login_required
@require_POST
def import_consolidate(request, pk):
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    batch = get_object_or_404(ImportBatch.objects.all(), pk=pk)
    removed = imp.consolidate_batch(batch)
    return _bulk_done(request, pk,
                      f"Merged {removed} duplicate mention{'s' if removed != 1 else ''} — "
                      "each customer, supplier and person now appears once.")


@login_required
@require_POST
def import_commit_all(request, pk):
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    batch = get_object_or_404(ImportBatch.objects.all(), pk=pk)
    from apps.knowledge.models import StagedEntity
    kind = (request.POST.get("kind") or "").strip()
    valid = {StagedEntity.Kind.CUSTOMER, StagedEntity.Kind.SUPPLIER}
    if kind not in valid:
        return _bulk_done(request, pk, "Choose customers or suppliers to add.", ok=False, status=400)
    try:
        r = imp.commit_all(batch, request.user, kind=kind)
    except imp.CommitError as exc:
        return _bulk_done(request, pk, str(exc), ok=False, status=400)
    label = "customers" if kind == StagedEntity.Kind.CUSTOMER else "suppliers"
    parts = []
    if r["created"]:
        parts.append(f"{r['created']} created")
    if r["linked"]:
        parts.append(f"{r['linked']} linked to existing")
    msg = f"Added {', '.join(parts) or '0'} {label} to your CRM."
    if r["failed"]:
        msg += f" ({r['failed']} need a completed company setup — check the review list.)"
    return _bulk_done(request, pk, msg)


@login_required
@require_POST
def import_merge(request, pk):
    if not _can(request.user):
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    batch = get_object_or_404(ImportBatch.objects.all(), pk=pk)
    ids = request.POST.getlist("entity_ids")
    if len(ids) < 2:
        return _bulk_done(request, pk, "Pick at least two rows to merge.", ok=False, status=400)
    # Primary = the one with the most mentions (most evidence) so its name wins.
    from apps.knowledge.models import StagedEntity
    chosen = list(StagedEntity.objects.filter(batch=batch, pk__in=ids).order_by("-mentions"))
    if len(chosen) < 2:
        return _bulk_done(request, pk, "Those rows could not be merged.", ok=False, status=400)
    primary = chosen[0]
    try:
        n = imp.merge_entities(batch, primary.pk, [e.pk for e in chosen[1:]], request.user)
    except imp.CommitError as exc:
        return _bulk_done(request, pk, str(exc), ok=False, status=400)
    return _bulk_done(request, pk, f"Merged {n} row{'s' if n != 1 else ''} into “{primary.raw_name}”.")


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
    ajax = request.headers.get("x-requested-with") == "XMLHttpRequest"
    if not _can(request.user):
        if ajax:
            return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
        messages.error(request, "You don't have permission to import business history.")
        return redirect("web:dashboard")
    batch = get_object_or_404(ImportBatch.objects.all(), pk=pk)
    staged = get_object_or_404(batch.entities, pk=eid)
    decision = (request.POST.get("decision") or "").strip()
    customer_id = request.POST.get("customer_id") or None
    verb = {"link": "Linked", "create": "Created", "reject": "Rejected"}.get(decision, "Updated")
    try:
        imp.commit_entity(staged, request.user, decision=decision, customer_id=customer_id)
    except imp.CommitError as exc:
        if ajax:
            return JsonResponse({"ok": False, "error": str(exc)}, status=400)
        messages.error(request, str(exc))
        return redirect("web:import_batch", pk=pk)
    if ajax:
        # The row leaves the pending queue on any decision — tell the page to drop it.
        return JsonResponse({"ok": True, "removed": True,
                             "message": f"{verb} {staged.raw_name}."})
    messages.success(request, f"{verb} {staged.raw_name}.")
    return redirect("web:import_batch", pk=pk)
