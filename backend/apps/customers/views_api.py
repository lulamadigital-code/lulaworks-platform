"""JSON API for Customers & Contacts — the dependency the mobile quotes/jobs/
invoices/CRM flows all hang off. Mirrors the web behaviour by reusing the
service layer (create_customer seeds departments + generates the code); the
backend stays the single source of truth for business rules and permissions.
"""
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import TenantViewSet

from .models import Customer, CustomerContact
from .serializers import (
    CustomerContactSerializer,
    CustomerListSerializer,
    CustomerSerializer,
)
from .services import (
    CRMError,
    add_note,
    create_customer,
    customer_overview,
    customer_timeline,
    log_interaction,
    schedule_activity,
)


def _parse_dt(value):
    """Best-effort parse of an ISO datetime string; None if absent/bad."""
    if not value:
        return None
    from django.utils.dateparse import parse_datetime
    return parse_datetime(str(value))


class CustomerViewSet(TenantViewSet):
    """Client organisations. Read for any member; writes need customers.manage."""

    model = Customer
    serializer_class = CustomerSerializer
    search_fields = ["name", "trading_name", "code", "city", "vat_no"]
    ordering_fields = ["name", "created_at"]
    required_perms = {
        "create": "customers.manage",
        "update": "customers.manage",
        "partial_update": "customers.manage",
        "destroy": "customers.manage",
    }

    def get_queryset(self):
        return Customer.objects.all()

    def get_serializer_class(self):
        return CustomerListSerializer if self.action == "list" else CustomerSerializer

    def create(self, request, *args, **kwargs):
        # Route through the service so the code is generated and default
        # departments are seeded — exactly as the web does.
        ser = CustomerSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        name = data.pop("name")
        customer = create_customer(
            request.user.active_company, request.user, name=name, **data
        )
        return Response(CustomerSerializer(customer).data, status=201)

    @action(detail=True, methods=["get"])
    def overview(self, request, pk=None):
        """The at-a-glance numbers for the customer page. `outstanding_value` is
        money, so it is stripped for users without finance.view_money."""
        data = customer_overview(self.get_object())
        if not request.user.has_perm_code("finance.view_money"):
            data.pop("outstanding_value", None)
        return Response(data)

    @action(detail=True, methods=["get"])
    def contacts(self, request, pk=None):
        qs = self.get_object().contacts.all()
        return Response(CustomerContactSerializer(qs, many=True).data)

    # ── CRM: the relationship layer around the customer ──────────────────────
    @action(detail=True, methods=["get"])
    def timeline(self, request, pk=None):
        """One merged, chronological history of the customer (quotes, jobs,
        invoices, activities, notes, interactions). Money amounts on events are
        withheld from users without finance.view_money (Golden Rule)."""
        events = customer_timeline(self.get_object())
        if not request.user.has_perm_code("finance.view_money"):
            for e in events:
                e["amount"] = ""
        return Response(events)

    def _crm_guard(self, request):
        return (request.user.has_perm_code("crm.manage")
                or request.user.has_perm_code("customers.manage"))

    @action(detail=True, methods=["post"], url_path="log-interaction")
    def log_interaction(self, request, pk=None):
        """Record a call/meeting/note/WhatsApp that happened."""
        if not self._crm_guard(request):
            return Response({"error": {"code": "forbidden",
                             "message": "Need crm.manage or customers.manage."}},
                            status=status.HTTP_403_FORBIDDEN)
        contact = self._contact(request)
        try:
            obj = log_interaction(
                request.user.active_company, request.user,
                summary=request.data.get("summary", ""),
                channel=request.data.get("channel", "note"),
                direction=request.data.get("direction", "out"),
                subject=request.data.get("subject", ""),
                occurred_at=_parse_dt(request.data.get("occurred_at")),
                customer=self.get_object(), contact=contact,
            )
        except CRMError as exc:
            return Response({"error": {"code": "invalid", "message": str(exc)}},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"id": str(obj.id)}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="add-note")
    def add_note(self, request, pk=None):
        if not self._crm_guard(request):
            return Response({"error": {"code": "forbidden",
                             "message": "Need crm.manage or customers.manage."}},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            obj = add_note(
                request.user.active_company, request.user,
                body=request.data.get("body", ""),
                customer=self.get_object(), contact=self._contact(request),
            )
        except CRMError as exc:
            return Response({"error": {"code": "invalid", "message": str(exc)}},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"id": str(obj.id)}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"], url_path="schedule-activity")
    def schedule_activity(self, request, pk=None):
        """Book a follow-up / call / meeting to do later."""
        if not self._crm_guard(request):
            return Response({"error": {"code": "forbidden",
                             "message": "Need crm.manage or customers.manage."}},
                            status=status.HTTP_403_FORBIDDEN)
        try:
            obj = schedule_activity(
                request.user.active_company, request.user,
                subject=request.data.get("subject", ""),
                activity_type=request.data.get("activity_type", "follow_up"),
                due_at=_parse_dt(request.data.get("due_at")),
                detail=request.data.get("detail", ""),
                customer=self.get_object(), contact=self._contact(request),
            )
        except CRMError as exc:
            return Response({"error": {"code": "invalid", "message": str(exc)}},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response({"id": str(obj.id)}, status=status.HTTP_201_CREATED)

    def _contact(self, request):
        cid = request.data.get("contact")
        if not cid:
            return None
        return CustomerContact.objects.filter(id=cid).first()


class CustomerContactViewSet(TenantViewSet):
    """People at a customer. Filter by ?customer=<id>."""

    model = CustomerContact
    serializer_class = CustomerContactSerializer
    search_fields = ["full_name", "email", "job_title", "mobile"]
    required_perms = {
        "create": "customers.manage",
        "update": "customers.manage",
        "partial_update": "customers.manage",
        "destroy": "customers.manage",
    }

    def get_queryset(self):
        qs = CustomerContact.objects.all().select_related("customer", "department")
        customer = self.request.query_params.get("customer")
        return qs.filter(customer_id=customer) if customer else qs
