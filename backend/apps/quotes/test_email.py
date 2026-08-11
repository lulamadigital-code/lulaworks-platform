"""Emailing documents — quotation / tax invoice / delivery note with PDF.

Guarantees: the email goes through the platform service (logged, branded), the
PDF is attached (rebuilt from the record by the worker, not carried as bytes),
the recipient is suggested from the CRM routing, and every send is recorded on
the document.
"""

from django.core import mail
from django.test import TestCase, override_settings

from apps.customers.models import Customer, CustomerContact
from apps.customers.services import create_customer
from apps.core.context import tenant_scope
from apps.identity.models import Company

from . import email as doc_email
from .models import CommercialDocument, Quotation, QuotationLine

EAGER = override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CELERY_TASK_ALWAYS_EAGER=True)


@EAGER
class DocumentEmailTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Demo Contractor")

    def _quote(self):
        with tenant_scope(self.company.id):
            cust = create_customer(self.company, None, name="Harmony Gold",
                                   seed_departments=False, email="ap@harmony.co.za")
            quote = Quotation.objects.create(company=self.company, number="QT-1",
                                             client_name="Harmony Gold",
                                             customer=cust, title="Conveyor overhaul")
            QuotationLine.objects.create(company=self.company, quotation=quote,
                                         position=1, description="Fitter", qty=10,
                                         unit="hr", unit_cost=350)
            return quote, cust

    def test_send_quotation_attaches_pdf_and_logs(self):
        quote, _ = self._quote()
        with tenant_scope(self.company.id):
            log = doc_email.send_quotation(quote, None, to="buyer@harmony.co.za")
        self.assertEqual(log.to_email, "buyer@harmony.co.za")
        self.assertEqual(log.entity_type, "Quotation")
        self.assertEqual(log.entity_id, quote.id)
        # Delivered with a real PDF attachment.
        self.assertEqual(len(mail.outbox), 1)
        atts = mail.outbox[0].attachments
        self.assertEqual(len(atts), 1)
        name, content, mimetype = atts[0]
        self.assertEqual(name, "Quotation QT-1.pdf")
        self.assertEqual(mimetype, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF"))

    def test_recipient_defaults_to_the_approving_contact(self):
        quote, cust = self._quote()
        with tenant_scope(self.company.id):
            CustomerContact.objects.create(
                company=self.company, customer=cust, full_name="Sarah Approver",
                email="sarah@harmony.co.za",
                responsibilities=["approve_quotation"], is_primary=True)
            # No explicit `to` → routed to the person who approves quotations.
            log = doc_email.send_quotation(quote, None)
        self.assertEqual(log.to_email, "sarah@harmony.co.za")

    def test_recipient_falls_back_to_customer_email(self):
        quote, _ = self._quote()   # customer email set, no contacts
        with tenant_scope(self.company.id):
            to = doc_email.suggested_recipient(quote.customer, "quotation")
        self.assertEqual(to, "ap@harmony.co.za")

    def test_send_invoice_attaches_its_pdf(self):
        quote, _ = self._quote()
        with tenant_scope(self.company.id):
            doc = CommercialDocument.objects.create(
                company=self.company, quotation=quote,
                kind=CommercialDocument.Kind.INVOICE, number="INV-1")
            log = doc_email.send_commercial_document(doc, None, to="ap@harmony.co.za")
        self.assertEqual(log.entity_type, "CommercialDocument")
        self.assertEqual(mail.outbox[0].attachments[0][0], "Tax invoice INV-1.pdf")

    def test_send_records_history_on_the_document(self):
        quote, _ = self._quote()
        with tenant_scope(self.company.id):
            doc_email.send_quotation(quote, None, to="a@b.co")
            doc_email.send_quotation(quote, None, to="c@d.co")
            history = list(doc_email.send_history("Quotation", quote.id))
        self.assertEqual(len(history), 2)

    def test_no_recipient_raises(self):
        from apps.identity.services import MemberError
        with tenant_scope(self.company.id):
            cust = create_customer(self.company, None, name="No Email Co",
                                   seed_departments=False)
            quote = Quotation.objects.create(company=self.company, number="QT-2",
                                             client_name="X", customer=cust)
            with self.assertRaises(MemberError):
                doc_email.send_quotation(quote, None)   # no contact, no email
