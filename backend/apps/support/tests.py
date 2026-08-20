"""Live support chat — the customer and technician exchange messages via the
poll/send JSON endpoints, and internal notes never reach the customer."""

from django.test import Client, TestCase
from django.urls import reverse

from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Role, User

from . import services as support
from .models import TicketCategory, TicketPriority


class SupportChatTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="ChatCo")
        role = Role.objects.create(name="Member", is_system=True)
        self.customer = User.objects.create_user("c@chatco.test", "x",
                                                 active_company=self.company)
        Membership.objects.create(user=self.customer, company=self.company, role=role)
        with tenant_scope(self.company.id):
            self.ticket = support.create_ticket(
                company=self.company, user=self.customer, subject="Help",
                category=TicketCategory.choices[0][0],
                priority=TicketPriority.choices[1][0], description="It broke")
        self.tech = User.objects.create_user("t@lulaworks.test", "x",
                                             is_superuser=True, is_staff=True)

    def test_customer_and_technician_exchange_messages(self):
        cc, tc = Client(), Client()
        cc.force_login(self.customer)
        tc.force_login(self.tech)

        # Customer sends via the AJAX endpoint.
        r = cc.post(reverse("web:support_send", args=[self.ticket.id]),
                    {"body": "please help"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("message", r.json())

        # Technician polls and sees it.
        tj = tc.get(reverse("web:platform_support_messages", args=[self.ticket.id])).json()
        self.assertTrue(any("please help" in m["body"] for m in tj["messages"]))

        # Technician replies + adds an internal note.
        tc.post(reverse("web:platform_support_send", args=[self.ticket.id]),
                {"body": "try a reset"})
        tc.post(reverse("web:platform_support_send", args=[self.ticket.id]),
                {"body": "flagged internally", "internal": "1"})

        # Customer sees the reply but NEVER the internal note.
        cj = cc.get(reverse("web:support_messages", args=[self.ticket.id])).json()
        bodies = [m["body"] for m in cj["messages"]]
        self.assertTrue(any("try a reset" in b for b in bodies))
        self.assertFalse(any("flagged internally" in b for b in bodies))

        # Technician's feed does include the internal note.
        tj2 = tc.get(reverse("web:platform_support_messages", args=[self.ticket.id])).json()
        self.assertTrue(any(m["is_internal"] for m in tj2["messages"]))

    def test_empty_message_is_rejected(self):
        cc = Client()
        cc.force_login(self.customer)
        r = cc.post(reverse("web:support_send", args=[self.ticket.id]), {"body": " "})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.json())
