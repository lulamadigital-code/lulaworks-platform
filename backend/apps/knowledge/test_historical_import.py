"""Historical Business Import — the "Bring Your Business History" pipeline.
Locks the guardrail: documents are classified and entities resolved, but
NOTHING enters the ERP until a person confirms."""
from django.test import TestCase

from apps.core.context import tenant_scope
from apps.customers.models import Customer, CustomerContact
from apps.identity.models import Company, Membership, Permission, Role, User
from apps.knowledge import historical_import as imp
from apps.knowledge.classifier import classify_document
from apps.knowledge.models import ImportedDocument, StagedEntity
from apps.procurement.models import Supplier


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


_PO_TEXT = b"""PURCHASE ORDER
PO Number: PO-2024-0912
Customer: ABC Mining (Pty) Ltd
Supplier: Hydraulics SA
Contact: thabo@abcmining.co.za
VAT No: 4123456789
Total: R485 000
"""


class ClassifierTests(TestCase):
    def test_classifies_po(self):
        t, conf = classify_document("po_2024.pdf", _PO_TEXT.decode())
        self.assertEqual(t, ImportedDocument.DocType.CUSTOMER_PO)
        self.assertGreater(conf, 0.5)

    def test_unknown_is_other(self):
        t, conf = classify_document("notes.txt", "just some random words here")
        self.assertEqual(t, ImportedDocument.DocType.OTHER)
        self.assertEqual(conf, 0.0)


class ImportPipelineTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Contractor A")
        self.mgr = _user(self.company, ["customers.manage"], "mgr@a.co")
        self.viewer = _user(self.company, ["projects.view"], "v@a.co")
        # An existing customer the import should MATCH rather than duplicate.
        with tenant_scope(self.company.id):
            self.existing = Customer.objects.create(
                company=self.company, name="ABC Mining (Pty) Ltd")

    def test_ingest_classifies_extracts_and_resolves(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="History")
            doc = imp.ingest(batch, "po_2024.txt", _PO_TEXT, self.mgr)
            summary = imp.batch_summary(batch)

            self.assertEqual(doc.doc_type, ImportedDocument.DocType.CUSTOMER_PO)
            # The customer mention resolves to the existing record (no duplicate).
            cust = batch.entities.filter(kind=StagedEntity.Kind.CUSTOMER).first()
            self.assertIsNotNone(cust)
            self.assertEqual(cust.verdict, StagedEntity.Verdict.MATCHED)
            self.assertEqual(cust.match_id, str(self.existing.id))
            # A supplier and a contact were also discovered.
            self.assertTrue(batch.entities.filter(kind=StagedEntity.Kind.SUPPLIER).exists())
            self.assertTrue(batch.entities.filter(kind=StagedEntity.Kind.CONTACT).exists())
        self.assertGreaterEqual(summary["documents"], 1)
        self.assertGreaterEqual(summary["auto_matched"], 1)

    def test_nothing_written_until_commit(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="History")
            imp.ingest(batch, "po.txt", _PO_TEXT, self.mgr)
            # Ingest alone creates NO new customers/suppliers.
            self.assertEqual(Customer.objects.count(), 1)   # only the pre-existing
            self.assertEqual(Supplier.objects.count(), 0)

    def test_commit_link_and_create(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="History")
            imp.ingest(batch, "po.txt", _PO_TEXT, self.mgr)
            cust = batch.entities.get(kind=StagedEntity.Kind.CUSTOMER)
            supp = batch.entities.get(kind=StagedEntity.Kind.SUPPLIER)

            r1 = imp.commit_entity(cust, self.mgr, decision="link")
            self.assertEqual(r1["resolved_id"], str(self.existing.id))
            self.assertEqual(Customer.objects.count(), 1)   # linked, not duplicated

            r2 = imp.commit_entity(supp, self.mgr, decision="create")
            self.assertTrue(r2["ok"])
            self.assertEqual(Supplier.objects.count(), 1)   # new supplier created

    def test_commit_requires_permission(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="History")
            imp.ingest(batch, "po.txt", _PO_TEXT, self.mgr)
            cust = batch.entities.get(kind=StagedEntity.Kind.CUSTOMER)
            with self.assertRaises(imp.CommitError):
                imp.commit_entity(cust, self.viewer, decision="link")

    def test_contact_create_needs_customer(self):
        with tenant_scope(self.company.id):
            batch = imp.create_batch(self.mgr, label="History")
            imp.ingest(batch, "po.txt", _PO_TEXT, self.mgr)
            contact = batch.entities.filter(kind=StagedEntity.Kind.CONTACT).first()
            with self.assertRaises(imp.CommitError):
                imp.commit_entity(contact, self.mgr, decision="create")
            imp.commit_entity(contact, self.mgr, decision="create",
                              customer_id=str(self.existing.id))
            self.assertEqual(CustomerContact.objects.count(), 1)
