"""One-time backfill: populate the Historical Intelligence layer over documents
that were imported BEFORE the structuring code existed.

The Intelligence panels (customer / supplier / price-history / quotation / job)
and LulaAI's history grounding read two things that are written only while a
document is processed or a batch is reconstructed:

* HistoricalLineItem — the provenance-rich priced-item ledger (what we bought /
  what we charged), written by historical_import._capture_document_lines.
* HistoricalJob.value / work_type / occurred_on — written by
  job_reconstruction.reconstruct_jobs.

Documents imported earlier never ran that code, so those rows are empty and the
panels show nothing. This command re-runs capture + reconstruct over the
existing import, idempotently, and reports how much attached to LIVE records so
you can see whether anything still needs confirming out of the review queue.

Usage:
    python manage.py backfill_history_intelligence            # all tenants
    python manage.py backfill_history_intelligence --company <uuid>
    python manage.py backfill_history_intelligence --dry-run  # report only
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.core.context import tenant_scope


class Command(BaseCommand):
    help = "Backfill HistoricalLineItem + HistoricalJob structuring over already-imported documents."

    def add_arguments(self, parser):
        parser.add_argument("--company", default="", help="Limit to one company id.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Report current state only; write nothing.")

    def handle(self, *args, **opts):
        from apps.identity.models import Company, Membership
        from apps.knowledge.historical_import import (
            _LINE_DIRECTION,
            _capture_document_lines,
        )
        from apps.knowledge.job_reconstruction import reconstruct_jobs
        from apps.knowledge.models import (
            HistoricalJob,
            HistoricalLineItem,
            ImportBatch,
            ImportedDocument,
        )

        # Companies that actually have imported documents.
        docs = ImportedDocument.all_objects.all()
        if opts["company"]:
            docs = docs.filter(company_id=opts["company"])
        company_ids = list(docs.values_list("company_id", flat=True).distinct())
        if not company_ids:
            self.stdout.write("No imported documents found — nothing to backfill.")
            return

        dry = opts["dry_run"]
        grand = {"docs": 0, "lines": 0, "jobs": 0,
                 "lines_live": 0, "jobs_live": 0}

        for cid in company_ids:
            company = Company.objects.filter(pk=cid).first()
            if company is None:
                continue
            # A member for created_by / context (nullable, so None is acceptable).
            m = Membership.objects.filter(company_id=cid, status="active").first()
            user = m.user if m else None
            name = getattr(company, "name", str(cid))

            with tenant_scope(cid):
                doc_qs = ImportedDocument.objects.filter(
                    doc_type__in=list(_LINE_DIRECTION.keys()))
                lines = 0
                scanned = 0
                if not dry:
                    for doc in doc_qs.iterator():
                        scanned += 1
                        try:
                            lines += _capture_document_lines(doc, user)
                        except Exception as e:                       # noqa: BLE001
                            self.stderr.write(f"  capture failed on {doc.id}: {e}")
                    for batch in ImportBatch.objects.all().iterator():
                        try:
                            reconstruct_jobs(batch, user)
                        except Exception as e:                       # noqa: BLE001
                            self.stderr.write(f"  reconstruct failed on {batch.id}: {e}")
                else:
                    scanned = doc_qs.count()

                # Report the resulting state (whether we wrote or not).
                li = HistoricalLineItem.objects.all()
                jobs = HistoricalJob.objects.exclude(status=HistoricalJob.Status.DISMISSED)
                stats = {
                    "docs": scanned,
                    "lines": li.count(),
                    "jobs": jobs.count(),
                    "lines_live": li.exclude(party_id="").count(),
                    "jobs_live": jobs.exclude(customer_id="").count(),
                }

            for k in grand:
                grand[k] += stats[k]
            self.stdout.write(
                f"• {name}: {stats['docs']} priced docs → "
                f"{stats['lines']} ledger lines ({stats['lines_live']} linked to a live "
                f"customer/supplier), {stats['jobs']} jobs ({stats['jobs_live']} linked "
                f"to a live customer).")

        head = "DRY-RUN — current state (nothing written)" if dry else "Backfill complete"
        self.stdout.write(self.style.SUCCESS(
            f"\n{head}: {grand['lines']} priced ledger lines, {grand['jobs']} jobs across "
            f"{len(company_ids)} tenant(s)."))
        if not dry and grand["lines"] and not grand["lines_live"]:
            self.stdout.write(self.style.WARNING(
                "Ledger lines exist but none are linked to a live customer/supplier — "
                "confirm the discovered entities out of the review queue (/import/) so "
                "the panels can attach to their live records."))
