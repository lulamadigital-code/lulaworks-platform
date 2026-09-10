"""Historical Job Reconstruction (AI OS §12).

Imported documents rarely arrive labelled as belonging to one job — but a
quotation, the customer PO that accepted it, the supplier quotes, the delivery
note and the invoice usually share identifiers (a quote number quoted on the
PO, a PO number on the invoice…). This clusters a batch's documents by the
references they share and proposes each cluster as one past job — with the
evidence and a confidence — for a human to confirm. Nothing is asserted as a
job until confirmed.
"""
from __future__ import annotations

import re

from .models import HistoricalJob, ImportedDocument

# Reference tokens: a labelled number (Quote No: Q-123) or a standalone code
# (PO-2024-0912, INV/55). Two documents that share one are likely one job.
_LABELLED = re.compile(
    r"(?:quote|quotation|po|purchase\s*order|order|invoice|inv|ref|reference|job)"
    r"\s*(?:no\.?|number|#)?\s*[:\-]?\s*([A-Za-z]{0,4}[-/]?\d{2,}[-/]?\d*)", re.I)
_CODE = re.compile(r"\b([A-Za-z]{1,4}[-/]\d{3,}(?:[-/]\d+)*)\b")


def _tokens(text: str) -> set[str]:
    """The reference tokens a document carries, normalised for comparison."""
    if not text:
        return set()
    out: set[str] = set()
    for m in _LABELLED.finditer(text):
        out.add(_norm(m.group(1)))
    for m in _CODE.finditer(text):
        out.add(_norm(m.group(1)))
    # Drop tokens too short to be meaningful identifiers.
    return {t for t in out if len(t) >= 4}


def _norm(token: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (token or "").lower())


def reconstruct_jobs(batch, user) -> list[HistoricalJob]:
    """Cluster this batch's documents into proposed historical jobs by shared
    references. Idempotent: clears prior PROPOSED jobs for the batch and rebuilds
    (confirmed/dismissed ones are left untouched)."""
    HistoricalJob.objects.filter(batch=batch, status=HistoricalJob.Status.PROPOSED).delete()

    docs = list(batch.documents.all())
    doc_tokens = {d.id: _tokens(d.text) for d in docs}

    # Union-find over documents that share at least one reference token.
    parent = {d.id: d.id for d in docs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    ids = [d.id for d in docs]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if doc_tokens[ids[i]] & doc_tokens[ids[j]]:
                union(ids[i], ids[j])

    groups: dict = {}
    for d in docs:
        groups.setdefault(find(d.id), []).append(d)

    by_id = {d.id: d for d in docs}
    created: list[HistoricalJob] = []
    for members in groups.values():
        if len(members) < 2:
            continue                       # a job is a chain of documents, not a lone file
        types = {m.doc_type for m in members}
        if len(types) < 2:
            continue                       # duplicates of one type aren't a job
        shared = set.intersection(*[doc_tokens[m.id] for m in members]) or \
            {t for m in members for t in doc_tokens[m.id]}
        ref = sorted(shared, key=len, reverse=True)[0] if shared else ""

        # Confidence: more shared refs and more distinct document types = stronger.
        confidence = min(0.95, 0.4 + 0.15 * len(types) + 0.1 * len(shared))
        customer_id, customer_name = _infer_customer(batch, members)
        title = customer_name or f"Job {ref.upper()}" if ref else "Reconstructed job"
        evidence = (f"{len(members)} documents ({', '.join(sorted(types))}) "
                    f"sharing reference {ref.upper()}.")

        job = HistoricalJob.objects.create(
            batch=batch, title=title[:200], reference=ref[:120],
            customer_id=customer_id, customer_name=customer_name[:255],
            confidence=round(confidence, 2), evidence=evidence,
            created_by=user)
        ImportedDocument.objects.filter(id__in=[m.id for m in members]).update(job=job)
        created.append(job)

    return created


def _infer_customer(batch, members) -> tuple[str, str]:
    """Best-guess customer for a job cluster: a customer entity staged from one
    of its documents (prefer a matched one)."""
    from .models import StagedEntity

    ents = list(StagedEntity.objects.filter(
        batch=batch, kind=StagedEntity.Kind.CUSTOMER,
        document_id__in=[m.id for m in members]).order_by("-confidence"))
    if not ents:
        return "", ""
    matched = next((e for e in ents if e.match_id), None)
    chosen = matched or ents[0]
    return (chosen.match_id or ""), (chosen.match_label or chosen.raw_name)


def confirm_job(job: HistoricalJob, user, *, decision) -> dict:
    """Confirm or dismiss a proposed historical job. Read-only w.r.t. the live
    ERP — a HistoricalJob is a knowledge record, not a new active project."""
    if not user.has_perm_code("customers.manage"):
        raise PermissionError("You don't have permission to confirm historical jobs.")
    if decision == "confirm":
        job.status = HistoricalJob.Status.CONFIRMED
    elif decision == "dismiss":
        job.status = HistoricalJob.Status.DISMISSED
    else:
        raise ValueError(f"Unknown decision '{decision}'.")
    job.save(update_fields=["status", "updated_at"])
    return {"ok": True, "status": job.status}
