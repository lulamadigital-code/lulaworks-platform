"""Customer & contact management.

The thing being protected: a customer is an ORGANISATION. The tests that matter
are the ones proving a document reaches the right person, and that it degrades
honestly when it cannot.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase

from apps.core.context import tenant_scope
from apps.identity.models import Company

from .models import (
    Customer,
    CustomerBranch,
    CustomerContact,
    CustomerSite,
    customer_doc_upload_path,
    customer_logo_upload_path,
)
from .services import (
    contacts_with,
    create_customer,
    customer_overview,
    get_or_create_by_name,
    next_customer_code,
    responsibility_matrix,
    route_document,
)


def make_company():
    return Company.objects.create(name="Lulama Projects")


def add_contact(company, customer, name, *, department=None, responsibilities=(),
                email=None, status=CustomerContact.Status.ACTIVE, primary=False):
    return CustomerContact.objects.create(
        company=company, customer=customer, full_name=name, department=department,
        responsibilities=list(responsibilities), status=status, is_primary=primary,
        email=email if email is not None else f"{name.split()[0].lower()}@client.co.za",
    )


class UploadPathTests(SimpleTestCase):
    """Uploads must land under the owning company's prefix. These are pure
    functions of `instance.company_id`, so no file or DB row is needed — a stub
    carrying the FK is enough."""

    def test_logo_path_is_scoped_to_the_company(self):
        obj = SimpleNamespace(company_id=42)
        path = customer_logo_upload_path(obj, "logo.png")
        self.assertTrue(path.startswith("c/42/"))
        self.assertTrue(path.endswith("logo.png"))

    def test_doc_path_is_scoped_to_the_company(self):
        obj = SimpleNamespace(company_id=7)
        path = customer_doc_upload_path(obj, "vendor_form.pdf")
        self.assertTrue(path.startswith("c/7/"))
        self.assertTrue(path.endswith("vendor_form.pdf"))

    def test_a_different_tenant_gets_a_different_prefix(self):
        """The whole point: one tenant's files never sit under another's prefix."""
        one = customer_doc_upload_path(SimpleNamespace(company_id=1), "x.pdf")
        two = customer_doc_upload_path(SimpleNamespace(company_id=2), "x.pdf")
        self.assertNotEqual(one, two)
        self.assertTrue(one.startswith("c/1/"))
        self.assertTrue(two.startswith("c/2/"))


class CustomerCodeTests(TestCase):
    def test_code_is_derived_from_the_name(self):
        c = make_company()
        with tenant_scope(c.id):
            self.assertEqual(next_customer_code(c, "Harmony Mining"), "HARMON")

    def test_codes_do_not_collide(self):
        c = make_company()
        with tenant_scope(c.id):
            first = create_customer(c, None, name="Harmony Mining")
            second_code = next_customer_code(c, "Harmony Minerals")
            self.assertEqual(first.code, "HARMON")
            self.assertNotEqual(second_code, first.code)

    def test_a_new_customer_gets_a_department_structure(self):
        """An empty customer is useless — you cannot file a contact without one."""
        c = make_company()
        with tenant_scope(c.id):
            customer = create_customer(c, None, name="Harmony Mining")
            self.assertGreater(customer.departments.count(), 5)
            self.assertTrue(customer.departments.filter(name="Procurement").exists())


class NameResolutionTests(TestCase):
    """Migrating away from free-text client names."""

    def test_matching_is_case_insensitive(self):
        c = make_company()
        with tenant_scope(c.id):
            create_customer(c, None, name="Sibanye Stillwater")
            found = get_or_create_by_name(c, None, "sibanye stillwater")
            self.assertEqual(Customer.objects.count(), 1)
            self.assertEqual(found.name, "Sibanye Stillwater")

    def test_unknown_name_creates_a_customer(self):
        c = make_company()
        with tenant_scope(c.id):
            found = get_or_create_by_name(c, None, "Brand New Client")
            self.assertIsNotNone(found)
            self.assertEqual(Customer.objects.count(), 1)

    def test_blank_name_resolves_to_nothing(self):
        c = make_company()
        with tenant_scope(c.id):
            self.assertIsNone(get_or_create_by_name(c, None, "   "))
            self.assertEqual(Customer.objects.count(), 0)


class SiteHierarchyTests(TestCase):
    def test_full_path_reads_the_way_a_person_would_say_it(self):
        c = make_company()
        with tenant_scope(c.id):
            customer = create_customer(c, None, name="Harmony", seed_departments=False)
            welkom = CustomerBranch.objects.create(company=c, customer=customer,
                                                   name="Welkom")
            plant = CustomerSite.objects.create(company=c, customer=customer,
                                                branch=welkom, name="Plant 1")
            crusher = CustomerSite.objects.create(company=c, customer=customer,
                                                  branch=welkom, parent=plant,
                                                  name="Crusher")
            self.assertEqual(crusher.full_path, "Welkom / Plant 1 / Crusher")


class RoutingTests(TestCase):
    """The reason the module exists."""

    def setUp(self):
        self.company = make_company()
        with tenant_scope(self.company.id):
            self.customer = create_customer(self.company, None, name="Harmony Mining")
            depts = {d.name: d for d in self.customer.departments.all()}
            self.sarah = add_contact(self.company, self.customer, "Sarah Brown",
                                     department=depts["Engineering"],
                                     responsibilities=["approve_quotation"])
            self.john = add_contact(self.company, self.customer, "John Smith",
                                    department=depts["Engineering"],
                                    responsibilities=["release_rfq"])
            self.jane = add_contact(self.company, self.customer, "Jane Williams",
                                    department=depts["Finance"],
                                    responsibilities=["receive_invoice"])

    def test_a_quotation_goes_to_the_approver_and_copies_the_releaser(self):
        with tenant_scope(self.company.id):
            routed = route_document(self.customer, "quotation")
        self.assertEqual([c.full_name for c in routed["to"]], ["Sarah Brown"])
        self.assertIn("John Smith", [c.full_name for c in routed["cc"]])
        self.assertIsNone(routed["fallback"])

    def test_an_invoice_goes_to_accounts_payable_not_the_engineer(self):
        with tenant_scope(self.company.id):
            routed = route_document(self.customer, "invoice")
        self.assertEqual([c.full_name for c in routed["to"]], ["Jane Williams"])
        self.assertNotIn("Sarah Brown", [c.full_name for c in routed["to"]])

    def test_nobody_is_listed_twice_across_to_and_cc(self):
        with tenant_scope(self.company.id):
            self.sarah.responsibilities = ["approve_quotation", "release_rfq"]
            self.sarah.save()
            routed = route_document(self.customer, "quotation")
        names = [c.full_name for c in routed["to"]] + [c.full_name for c in routed["cc"]]
        self.assertEqual(len(names), len(set(names)))

    def test_someone_who_left_is_never_routed_to(self):
        """Sending a quotation to someone who left is how deals go quiet."""
        with tenant_scope(self.company.id):
            self.sarah.status = CustomerContact.Status.LEFT
            self.sarah.save()
            routed = route_document(self.customer, "quotation")
        self.assertNotIn("Sarah Brown", [c.full_name for c in routed["to"]])
        self.assertIsNotNone(routed["fallback"])

    def test_missing_responsibility_falls_back_and_says_so(self):
        """Degrading silently would let someone assume routing worked."""
        with tenant_scope(self.company.id):
            customer = create_customer(self.company, None, name="Quiet Client",
                                       seed_departments=False)
            add_contact(self.company, customer, "Only Person", primary=True)
            routed = route_document(customer, "quotation")
        self.assertEqual([c.full_name for c in routed["to"]], ["Only Person"])
        self.assertIn("primary contact", routed["fallback"])

    def test_a_customer_with_nobody_reports_that_plainly(self):
        with tenant_scope(self.company.id):
            customer = create_customer(self.company, None, name="Empty Co",
                                       seed_departments=False)
            routed = route_document(customer, "quotation")
        self.assertEqual(routed["to"], [])
        self.assertIn("Nobody", routed["fallback"])

    def test_contacts_without_a_way_to_reach_them_are_skipped(self):
        with tenant_scope(self.company.id):
            customer = create_customer(self.company, None, name="No Reach Co",
                                       seed_departments=False)
            add_contact(self.company, customer, "Ghost Person", email="",
                        responsibilities=["approve_quotation"])
            routed = route_document(customer, "quotation")
        self.assertEqual(routed["to"], [])

    def test_unknown_document_kind_is_an_error_not_a_silent_empty(self):
        with tenant_scope(self.company.id), self.assertRaises(KeyError):
            route_document(self.customer, "birthday_card")

    def test_routing_can_be_scoped_to_a_department(self):
        with tenant_scope(self.company.id):
            finance = self.customer.departments.get(name="Finance")
            routed = route_document(self.customer, "quotation", department=finance)
        self.assertEqual(routed["to"], [])      # Sarah is in Engineering


class ResponsibilityMatrixTests(TestCase):
    def test_gaps_are_reported(self):
        company = make_company()
        with tenant_scope(company.id):
            customer = create_customer(company, None, name="Harmony",
                                       seed_departments=False)
            add_contact(company, customer, "Sarah Brown",
                        responsibilities=["approve_quotation"])
            matrix = responsibility_matrix(customer)

        covered = {row["key"] for row in matrix if row["covered"]}
        gaps = {row["key"] for row in matrix if not row["covered"]}
        self.assertIn("approve_quotation", covered)
        self.assertIn("receive_invoice", gaps)

    def test_contacts_with_finds_the_right_people(self):
        company = make_company()
        with tenant_scope(company.id):
            customer = create_customer(company, None, name="Harmony",
                                       seed_departments=False)
            add_contact(company, customer, "Buyer One",
                        responsibilities=["approve_po", "release_rfq"])
            add_contact(company, customer, "Other Person", responsibilities=[])
            found = contacts_with(customer, "approve_po")
            self.assertEqual([c.full_name for c in found], ["Buyer One"])


class PrimaryContactTests(TestCase):
    def test_only_one_primary_survives(self):
        company = make_company()
        with tenant_scope(company.id):
            customer = create_customer(company, None, name="Harmony",
                                       seed_departments=False)
            first = add_contact(company, customer, "First Person", primary=True)
            add_contact(company, customer, "Second Person", primary=True)
            first.refresh_from_db()
            # Asserted INSIDE the scope: the relation manager is fail-closed and
            # raises without an ambient tenant.
            self.assertFalse(first.is_primary)
            self.assertEqual(customer.contacts.filter(is_primary=True).count(), 1)


class IsolationTests(TestCase):
    def test_customers_never_leak_between_tenants(self):
        a, b = make_company(), make_company()
        with tenant_scope(b.id):
            create_customer(b, None, name="Company B Client", seed_departments=False)
        with tenant_scope(a.id):
            self.assertEqual(Customer.objects.count(), 0)


class OverviewTests(TestCase):
    def test_overview_counts_the_organisation(self):
        company = make_company()
        with tenant_scope(company.id):
            customer = create_customer(company, None, name="Harmony")
            add_contact(company, customer, "A Person")
            CustomerBranch.objects.create(company=company, customer=customer,
                                          name="Welkom")
            stats = customer_overview(customer)
            self.assertEqual(stats["contacts"], 1)
            self.assertEqual(stats["branches"], 1)
            self.assertGreater(stats["departments"], 0)
