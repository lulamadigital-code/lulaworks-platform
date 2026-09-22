"""Payment & Communication business events (§8/§42/§44): recording a payment or
logging a communication emits a DomainEvent on the backbone, and a payment shows
up on the customer timeline."""
from django.test import TestCase

from apps.core.context import tenant_scope
from apps.core.models import DomainEvent
from apps.customers.models import Customer
from apps.customers.services import customer_timeline, log_interaction
from apps.identity.models import Company, User
from apps.quotes.models import CommercialDocument, Quotation
from apps.quotes.services import record_payment


class PaymentEventTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.u = User.objects.create_user("u@acme.co", "x", active_company=self.c)
        with tenant_scope(self.c.id):
            self.cust = Customer.objects.create(company=self.c, name="ABC Mining")
            self.q = Quotation.objects.create(company=self.c, customer=self.cust,
                                              number="QTN-1", client_name="ABC Mining")
            self.inv = CommercialDocument.objects.create(
                company=self.c, quotation=self.q,
                kind=CommercialDocument.Kind.INVOICE, number="INV-1")

    def test_record_payment_emits_event_and_creates_payment(self):
        with tenant_scope(self.c.id):
            p = record_payment(self.inv, self.u, amount=5000, reference="EFT-9")
            self.assertIsNotNone(p.pk)
            ev = DomainEvent.objects.filter(type="PaymentReceived", company=self.c)
            self.assertEqual(ev.count(), 1)
            self.assertEqual(ev.first().payload["invoice"], "INV-1")
            self.assertEqual(ev.first().payload["customer_id"], str(self.cust.pk))

    def test_payment_appears_on_customer_timeline(self):
        with tenant_scope(self.c.id):
            record_payment(self.inv, self.u, amount=5000)
            kinds = [e["kind"] for e in customer_timeline(self.cust)]
        self.assertIn("Payment", kinds)


class CommunicationEventTests(TestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.u = User.objects.create_user("u@acme.co", "x", active_company=self.c)

    def test_log_interaction_emits_communication_event(self):
        with tenant_scope(self.c.id):
            cust = Customer.objects.create(company=self.c, name="ABC Mining")
            log_interaction(self.c, self.u, summary="Called about the pump quote",
                            customer=cust)
            ev = DomainEvent.objects.filter(type="CommunicationLogged", company=self.c)
            self.assertEqual(ev.count(), 1)
            self.assertEqual(ev.first().payload["customer_id"], str(cust.pk))
