"""Historical Import REST surface — the "Bring Your Business History" flow.

    POST /api/v1/import-batches/                     create a batch
    POST /api/v1/import-batches/<id>/upload/         upload+process one document
    GET  /api/v1/import-batches/<id>/summary/        discovery counts (§22)
    GET  /api/v1/import-batches/<id>/entities/       staged entities (?verdict, ?kind)
    POST /api/v1/import-batches/<id>/entities/<eid>/commit/  link/create/reject

Thin wrappers over apps.knowledge.historical_import — all the logic, guardrails
and tenant/permission scoping live there. Writes need customers.manage.
"""
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from apps.core.api import TenantViewSet

from . import historical_import as imp
from .models import ImportBatch, StagedEntity
from .serializers import ImportBatchSerializer, StagedEntitySerializer


class ImportBatchViewSet(TenantViewSet):
    """Historical-import batches. Reads are open to any tenant member; every
    write (create a batch, upload a document, commit an entity) needs
    customers.manage."""

    model = ImportBatch
    serializer_class = ImportBatchSerializer
    parser_classes = [MultiPartParser, FormParser]
    search_fields = ["label"]
    required_perms = {
        "create": "customers.manage",
        "update": "customers.manage",
        "partial_update": "customers.manage",
        "destroy": "customers.manage",
        "upload": "customers.manage",
        "commit": "customers.manage",
    }

    def get_queryset(self):
        return ImportBatch.objects.all().order_by("-created_at")

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, updated_by=self.request.user,
                        status=ImportBatch.Status.PROCESSING)

    @action(detail=True, methods=["post"])
    def upload(self, request, pk=None):
        batch = self.get_object()
        f = request.FILES.get("file")
        if f is None:
            return Response({"error": {"code": "no_file", "message": "Attach a file."}},
                            status=400)
        doc = imp.ingest(batch, f.name, f.read(), request.user)
        return Response({"document_id": str(doc.id), "doc_type": doc.doc_type,
                         "summary": imp.batch_summary(batch)}, status=201)

    @action(detail=True, methods=["get"])
    def summary(self, request, pk=None):
        return Response(imp.batch_summary(self.get_object()))

    @action(detail=True, methods=["get"])
    def entities(self, request, pk=None):
        qs = self.get_object().entities.all().order_by("kind", "-confidence")
        verdict = request.query_params.get("verdict")
        kind = request.query_params.get("kind")
        if verdict:
            qs = qs.filter(verdict=verdict)
        if kind:
            qs = qs.filter(kind=kind)
        return Response(StagedEntitySerializer(qs, many=True).data)

    @action(detail=True, methods=["post"],
            url_path=r"entities/(?P<eid>[^/.]+)/commit")
    def commit(self, request, pk=None, eid=None):
        batch = self.get_object()
        staged = batch.entities.filter(pk=eid).first()
        if staged is None:
            return Response({"error": {"code": "not_found",
                             "message": "No such staged entity."}}, status=404)
        decision = (request.data.get("decision") or "").strip()
        customer_id = request.data.get("customer_id") or None
        try:
            result = imp.commit_entity(staged, request.user, decision=decision,
                                       customer_id=customer_id)
        except imp.CommitError as exc:
            return Response({"error": {"code": "commit", "message": str(exc)}},
                            status=400)
        return Response(result)
