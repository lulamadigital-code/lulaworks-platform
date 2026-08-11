"""Customer & contact management.

The thing being protected: a customer is an ORGANISATION. The tests that matter
are the ones proving a document reaches the right person, and that it degrades
honestly when it cannot.
"""

from decimal import Decimal
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


# ══════════════════════════════════════════════════════════════════════════════
# CRM — leads, pipeline, activities, history, notes.
# ══════════════════════════════════════════════════════════════════════════════

from .models import (  # noqa: E402
    Activity,
    CustomerNote,
    Interaction,
    Lead,
    Opportunity,
    OpportunityStage,
)
from .services import (  # noqa: E402
    CRMError,
    add_note,
    convert_lead,
    create_lead,
    create_opportunity_for,
    crm_reports,
    crm_search,
    customer_dashboard,
    lose_opportunity,
    pipeline_summary,
    schedule_activity,
    set_opportunity_stage,
    win_opportunity,
)


class LeadConversionTests(TestCase):
    def test_convert_lead_mints_customer_contact_and_opportunity(self):
        company = make_company()
        with tenant_scope(company.id):
            lead = create_lead(company, None, company_name="Prospect Mining",
                               contact_name="Thabo Ncube", email="thabo@prospect.co.za",
                               industry="Mining", estimated_value=250000)
            customer = convert_lead(lead, None)

            self.assertEqual(customer.name, "Prospect Mining")
            self.assertEqual(customer.industry, "Mining")
            lead.refresh_from_db()
            self.assertEqual(lead.status, Lead.Status.CONVERTED)
            self.assertEqual(lead.converted_customer_id, customer.id)
            self.assertIsNotNone(lead.converted_at)
            # The human came across as the primary contact.
            self.assertTrue(customer.contacts.filter(full_name="Thabo Ncube",
                                                     is_primary=True).exists())
            # And a qualified opportunity was opened, carrying the value.
            opp = Opportunity.objects.get(customer=customer)
            self.assertEqual(opp.stage, OpportunityStage.QUALIFIED)
            self.assertEqual(opp.estimated_value, lead.estimated_value)
            self.assertEqual(opp.lead_id, lead.id)

    def test_convert_lead_is_idempotent(self):
        company = make_company()
        with tenant_scope(company.id):
            lead = create_lead(company, None, company_name="Once Off")
            first = convert_lead(lead, None, create_opportunity=False)
            second = convert_lead(lead, None, create_opportunity=False)
            self.assertEqual(first.id, second.id)
            self.assertEqual(Customer.objects.filter(name="Once Off").count(), 1)

    def test_create_lead_requires_a_name(self):
        company = make_company()
        with tenant_scope(company.id):
            with self.assertRaises(CRMError):
                create_lead(company, None, company_name="   ")


class PipelineTests(TestCase):
    def _customer(self, company):
        return create_customer(company, None, name="Deep Level Mining",
                               seed_departments=False)

    def test_stage_advance_updates_default_probability(self):
        company = make_company()
        with tenant_scope(company.id):
            opp = create_opportunity_for(self._customer(company), None,
                                         title="Conveyor overhaul")
            self.assertEqual(opp.probability, 10)          # LEAD default
            set_opportunity_stage(opp, None, OpportunityStage.NEGOTIATION)
            self.assertEqual(opp.stage, OpportunityStage.NEGOTIATION)
            self.assertEqual(opp.probability, 80)          # tracked the stage

    def test_custom_probability_is_respected_on_advance(self):
        company = make_company()
        with tenant_scope(company.id):
            opp = create_opportunity_for(self._customer(company), None,
                                         title="X", probability=55)
            set_opportunity_stage(opp, None, OpportunityStage.QUOTE_SENT)
            self.assertEqual(opp.probability, 55)          # human value untouched

    def test_win_stamps_close_date_and_links_quotation(self):
        company = make_company()
        with tenant_scope(company.id):
            opp = create_opportunity_for(self._customer(company), None, title="Y")
            win_opportunity(opp, None)
            self.assertTrue(opp.is_won)
            self.assertIsNotNone(opp.closed_at)
            self.assertEqual(opp.probability, 100)

    def test_lose_records_reason(self):
        company = make_company()
        with tenant_scope(company.id):
            opp = create_opportunity_for(self._customer(company), None, title="Z")
            lose_opportunity(opp, None, reason="Budget cut")
            self.assertTrue(opp.is_lost)
            self.assertEqual(opp.lost_reason, "Budget cut")

    def test_pipeline_summary_totals_open_value(self):
        company = make_company()
        with tenant_scope(company.id):
            cust = self._customer(company)
            create_opportunity_for(cust, None, title="A", estimated_value=100000,
                                   stage=OpportunityStage.QUALIFIED)
            create_opportunity_for(cust, None, title="B", estimated_value=50000,
                                   stage=OpportunityStage.NEGOTIATION)
            won = create_opportunity_for(cust, None, title="C",
                                         estimated_value=999999)
            win_opportunity(won, None)          # excluded from open pipeline
            summary = pipeline_summary(company)
            self.assertEqual(summary["open_count"], 2)
            self.assertEqual(summary["open_value"], 150000)


class ActivityAndHistoryTests(TestCase):
    def test_schedule_and_complete_activity(self):
        company = make_company()
        with tenant_scope(company.id):
            cust = create_customer(company, None, name="C", seed_departments=False)
            act = schedule_activity(company, None, subject="Call procurement",
                                    activity_type=Activity.Type.CALL, customer=cust)
            self.assertTrue(act.is_open)
            from apps.customers.services import complete_activity
            complete_activity(act, None, outcome="Spoke to buyer")
            self.assertEqual(act.status, Activity.Status.DONE)
            self.assertIsNotNone(act.completed_at)

    def test_activity_requires_an_anchor(self):
        company = make_company()
        with tenant_scope(company.id):
            with self.assertRaises(CRMError):
                schedule_activity(company, None, subject="Orphan")

    def test_note_pins_and_requires_body(self):
        company = make_company()
        with tenant_scope(company.id):
            cust = create_customer(company, None, name="C", seed_departments=False)
            note = add_note(company, None, body="Prefers morning deliveries",
                            customer=cust, is_pinned=True)
            self.assertTrue(note.is_pinned)
            with self.assertRaises(CRMError):
                add_note(company, None, body="", customer=cust)


class DashboardAndSearchTests(TestCase):
    def test_customer_dashboard_reads_crm_layer(self):
        company = make_company()
        with tenant_scope(company.id):
            cust = create_customer(company, None, name="Anglo Plats")
            create_opportunity_for(cust, None, title="Shutdown 2026",
                                   estimated_value=500000,
                                   stage=OpportunityStage.NEGOTIATION)
            schedule_activity(company, None, subject="Site visit",
                              activity_type=Activity.Type.SITE_VISIT, customer=cust)
            data = customer_dashboard(cust)
            self.assertEqual(data["open_opportunity_count"], 1)
            self.assertEqual(data["open_opportunity_value"], 500000)
            self.assertIsNotNone(data["next_activity"])

    def test_crm_search_finds_customer_and_lead(self):
        company = make_company()
        with tenant_scope(company.id):
            create_customer(company, None, name="Sasol Secunda")
            create_lead(company, None, company_name="Sasol Sasolburg")
            res = crm_search(company, "Sasol")
            names = {c.name for c in res["customers"]}
            self.assertIn("Sasol Secunda", names)
            self.assertEqual(len(res["leads"]), 1)
            self.assertGreaterEqual(res["total"], 2)

    def test_crm_search_ignores_short_queries(self):
        company = make_company()
        with tenant_scope(company.id):
            res = crm_search(company, "S")
            self.assertEqual(res["total"], 0)

    def test_crm_reports_counts_conversion(self):
        company = make_company()
        with tenant_scope(company.id):
            cust = create_customer(company, None, name="C", seed_departments=False)
            w = create_opportunity_for(cust, None, title="won deal")
            win_opportunity(w, None)
            lose_opportunity(create_opportunity_for(cust, None, title="lost deal"),
                             None)
            rep = crm_reports(company)
            self.assertEqual(rep["won"], 1)
            self.assertEqual(rep["lost"], 1)
            self.assertEqual(rep["conversion_rate"], 50.0)


class SiteContactManagementTests(TestCase):
    """The save/delete services behind the Sites & Contacts screens — whitelisted
    writes, GPS parsing, on-site contact linking, and responsibility multi-select."""

    def _post(self, data):
        """A QueryDict-like object so save_contact's getlist() path is exercised."""
        from django.http import QueryDict
        qd = QueryDict(mutable=True)
        for key, value in data.items():
            if isinstance(value, (list, tuple)):
                for v in value:
                    qd.appendlist(key, v)
            else:
                qd[key] = value
        return qd

    def test_save_site_requires_a_name(self):
        from .services import CRMError, save_site
        company = make_company()
        with tenant_scope(company.id):
            customer = create_customer(company, None, name="Sibanye",
                                       seed_departments=False)
            with self.assertRaises(CRMError):
                save_site(customer, None, data=self._post({"name": "  "}))

    def test_save_site_parses_gps_and_links_contact(self):
        from .services import save_site
        company = make_company()
        with tenant_scope(company.id):
            customer = create_customer(company, None, name="Sibanye",
                                       seed_departments=False)
            gatekeeper = add_contact(company, customer, "Gate Keeper")
            site = save_site(customer, None, data=self._post({
                "name": "Plant 1", "physical_address": "N12, Welkom",
                "latitude": "-27.985", "longitude": "26.735",
                "safety_requirements": "Full PPE, induction required",
                "site_contact": str(gatekeeper.id),
            }))
            self.assertEqual(site.name, "Plant 1")
            self.assertEqual(site.latitude, Decimal("-27.985"))
            self.assertEqual(site.longitude, Decimal("26.735"))
            self.assertEqual(site.site_contact_id, gatekeeper.id)
            self.assertIn("PPE", site.safety_requirements)

    def test_save_site_updates_in_place(self):
        from .services import save_site
        company = make_company()
        with tenant_scope(company.id):
            customer = create_customer(company, None, name="Sibanye",
                                       seed_departments=False)
            site = save_site(customer, None, data=self._post({"name": "Plant 1"}))
            same = save_site(customer, None, site=site,
                             data=self._post({"name": "Plant 1 — Crusher"}))
            self.assertEqual(same.id, site.id)
            self.assertEqual(CustomerSite.objects.count(), 1)
            self.assertEqual(same.name, "Plant 1 — Crusher")

    def test_bad_gps_is_dropped_not_fatal(self):
        from .services import save_site
        company = make_company()
        with tenant_scope(company.id):
            customer = create_customer(company, None, name="Sibanye",
                                       seed_departments=False)
            site = save_site(customer, None, data=self._post({
                "name": "Plant 1", "latitude": "not-a-number"}))
            self.assertIsNone(site.latitude)

    def test_delete_site_removes_it(self):
        from .services import delete_site, save_site
        company = make_company()
        with tenant_scope(company.id):
            customer = create_customer(company, None, name="Sibanye",
                                       seed_departments=False)
            site = save_site(customer, None, data=self._post({"name": "Plant 1"}))
            delete_site(site, None)
            self.assertEqual(CustomerSite.objects.count(), 0)

    def test_save_contact_records_responsibilities(self):
        from .services import save_contact
        company = make_company()
        with tenant_scope(company.id):
            customer = create_customer(company, None, name="Sibanye",
                                       seed_departments=False)
            contact = save_contact(customer, None, data=self._post({
                "full_name": "Thabo Approver", "email": "thabo@client.co.za",
                "roles": ["Procurement Officer", "Buyer", "Supreme Overlord"],
                "responsibilities": ["approve_po", "approve_invoice", "bogus_key"],
            }))
            # Both multi-value fields are whitelisted — a forged value is dropped.
            self.assertEqual(contact.roles, ["Procurement Officer", "Buyer"])
            self.assertEqual(sorted(contact.responsibilities),
                             ["approve_invoice", "approve_po"])

    def test_save_contact_rejects_unknown_method_and_never_sets_status(self):
        """CharField choices aren't enforced on .save(); a forged preferred_method
        must be ignored, and status must NOT be writable here — it has its own
        audited path (set_contact_status)."""
        from .services import save_contact
        company = make_company()
        with tenant_scope(company.id):
            customer = create_customer(company, None, name="Sibanye",
                                       seed_departments=False)
            contact = save_contact(customer, None, data=self._post({
                "full_name": "Nomsa Buyer",
                "preferred_method": "carrier_pigeon",   # not a valid Method
                "status": "do_not_contact",              # must be ignored here
            }))
            # Unknown method ignored → model default stands.
            self.assertEqual(contact.preferred_method,
                             CustomerContact.Method.EMAIL)
            # Status was NOT taken from the POST — a new contact is ACTIVE.
            self.assertEqual(contact.status, CustomerContact.Status.ACTIVE)

    def test_save_contact_requires_a_name(self):
        from .services import CRMError, save_contact
        company = make_company()
        with tenant_scope(company.id):
            customer = create_customer(company, None, name="Sibanye",
                                       seed_departments=False)
            with self.assertRaises(CRMError):
                save_contact(customer, None, data=self._post({"full_name": ""}))

    def test_set_contact_status_deactivates_and_reactivates(self):
        from .services import set_contact_status
        company = make_company()
        with tenant_scope(company.id):
            customer = create_customer(company, None, name="Sibanye",
                                       seed_departments=False)
            contact = add_contact(company, customer, "Leaver")
            set_contact_status(contact, None, status=CustomerContact.Status.LEFT)
            contact.refresh_from_db()
            self.assertEqual(contact.status, CustomerContact.Status.LEFT)
            self.assertFalse(contact.is_contactable)
            set_contact_status(contact, None, status=CustomerContact.Status.ACTIVE)
            contact.refresh_from_db()
            self.assertTrue(contact.is_contactable)
