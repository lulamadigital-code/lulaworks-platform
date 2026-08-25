"""Support tickets — JSON API for the mobile app's Help & Support ("log a call").

A member logs a ticket from the app; it lands in the same support system the web
platform desk works from. They see only their own tickets and never the internal
notes support staff add.
"""
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.core.api import TenantViewSet

from . import services as support
from .models import SupportTicket
from .serializers import SupportTicketDetailSerializer, SupportTicketSerializer


class SupportTicketViewSet(TenantViewSet):
    """The signed-in member's support tickets. Create logs a ticket; retrieve
    shows the conversation; `reply` adds a message. Read+create only."""

    http_method_names = ["get", "post", "head", "options"]
    model = SupportTicket
    serializer_class = SupportTicketSerializer

    def get_queryset(self):
        return (SupportTicket.objects.filter(created_by=self.request.user)
                .order_by("-last_activity_at", "-created_at"))

    def get_serializer_class(self):
        if self.action in ("retrieve", "create"):
            return SupportTicketDetailSerializer
        return SupportTicketSerializer

    def create(self, request, *args, **kwargs):
        try:
            ticket = support.create_ticket(
                company=request.user.active_company, user=request.user,
                subject=request.data.get("subject", ""),
                category=request.data.get("category", "mobile"),
                priority=request.data.get("priority", "normal"),
                description=request.data.get("description", ""))
        except support.SupportError as exc:
            return Response({"error": {"code": "invalid", "message": str(exc)}},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(
            SupportTicketDetailSerializer(ticket, context={"request": request}).data,
            status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def reply(self, request, pk=None):
        ticket = self.get_object()
        try:
            support.add_message(ticket=ticket, sender=request.user,
                                body=request.data.get("body", ""), from_support=False)
        except support.SupportError as exc:
            return Response({"error": {"code": "invalid", "message": str(exc)}},
                            status=status.HTTP_400_BAD_REQUEST)
        return Response(
            SupportTicketDetailSerializer(ticket, context={"request": request}).data)
