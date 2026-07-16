from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import TenantViewSet

from .models import Estimate
from .serializers import (
    ActualsSerializer,
    EstimateActualSerializer,
    EstimateCreateSerializer,
    EstimateSerializer,
)
from .services import (
    approval_required,
    approve_estimate,
    calibration_advice,
    capture_actuals,
    create_estimate,
    create_revision,
    generate_quotation,
    submit_for_approval,
)


class EstimateViewSet(TenantViewSet):
    """Internal estimates. Money is Golden-Rule gated at the serializer; the
    external quotation is derived (price-only) via the generate-quotation action."""

    model = Estimate
    serializer_class = EstimateSerializer
    search_fields = ["number", "client_name", "title", "work_type"]
    ordering_fields = ["created_at", "number"]
    required_perms = {
        "create": "estimating.manage",
        "update": "estimating.manage",
        "partial_update": "estimating.manage",
        "destroy": "estimating.manage",
        "submit": "estimating.manage",
        "revise": "estimating.manage",
        "generate_quotation": "estimating.manage",
        "capture_actuals": "estimating.manage",
    }

    def get_queryset(self):
        return Estimate.objects.all().prefetch_related("sections__lines")

    def create(self, request, *args, **kwargs):
        payload = EstimateCreateSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        est = create_estimate(request.user.active_company, request.user, **payload.validated_data)
        return Response(self._data(est, request), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        """Move to review / awaiting-approval per the margin/discount gate."""
        est = submit_for_approval(self.get_object(), request.user)
        return Response({**self._data(est, request), "gate": approval_required(est)})

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        if not request.user.has_perm_code("estimating.approve"):
            return Response(
                {"error": {"code": "forbidden", "message": "Need estimating.approve."}},
                status=status.HTTP_403_FORBIDDEN,
            )
        est = approve_estimate(self.get_object(), request.user)
        return Response(self._data(est, request))

    @action(detail=True, methods=["post"])
    def revise(self, request, pk=None):
        """Create a new version; the prior one is marked superseded (never overwritten)."""
        new = create_revision(self.get_object(), request.user,
                              reason=request.data.get("reason", ""))
        return Response(self._data(new, request), status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="generate-quotation")
    def generate_quotation(self, request, pk=None):
        """Derive the external, price-only quotation (Golden Rule at the doc boundary)."""
        try:
            quote = generate_quotation(self.get_object(), request.user)
        except ValueError as exc:
            return Response({"error": {"code": "invalid", "message": str(exc)}},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"quotation": str(quote.id), "number": quote.number},
                        status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get", "post"], url_path="actuals")
    def capture_actuals(self, request, pk=None):
        """GET: variance rows + calibration advice. POST: capture actuals (learning loop)."""
        est = self.get_object()
        if request.method == "POST":
            payload = ActualsSerializer(data=request.data)
            payload.is_valid(raise_exception=True)
            rows = capture_actuals(est, request.user, payload.validated_data["actuals"])
            return Response(EstimateActualSerializer(rows, many=True).data,
                            status=status.HTTP_201_CREATED)
        return Response({
            "actuals": EstimateActualSerializer(est.actuals.all(), many=True).data,
            "advice": calibration_advice(request.user.active_company, est.work_type),
        })

    def _data(self, est, request):
        return EstimateSerializer(est, context={"request": request}).data
