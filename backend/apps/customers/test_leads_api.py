"""Leads REST API — parity with the web CRM's lead management (create, list,
convert to customer, mark lost), permission-gated on crm.manage."""
from rest_framework.test import APITestCase

from apps.core.context import tenant_scope
from apps.customers.models import Lead
from apps.identity.models import Company, Membership, Permission, Role, User


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class LeadApiTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        self.mgr = _user(self.c, ["crm.manage", "customers.manage"], "mgr@acme.co")
        self.viewer = _user(self.c, ["projects.view"], "view@acme.co")

    def test_create_list_filter(self):
        self.client.force_authenticate(self.mgr)
        r = self.client.post("/api/v1/leads/",
                             {"company_name": "Zenith Mining", "email": "z@x.co",
                              "estimated_value": "50000"}, format="json")
        self.assertEqual(r.status_code, 201)
        lst = self.client.get("/api/v1/leads/?status=open")
        self.assertEqual(lst.status_code, 200)
        names = [x["company_name"] for x in (lst.data.get("results") or lst.data)]
        self.assertIn("Zenith Mining", names)

    def test_convert_makes_customer(self):
        with tenant_scope(self.c.id):
            lead = Lead.objects.create(company=self.c, company_name="Convert Co",
                                       created_by=self.mgr, updated_by=self.mgr)
        self.client.force_authenticate(self.mgr)
        r = self.client.post(f"/api/v1/leads/{lead.pk}/convert/", {}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["ok"])
        self.assertTrue(r.data["customer_id"])
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.CONVERTED)

    def test_mark_lost(self):
        with tenant_scope(self.c.id):
            lead = Lead.objects.create(company=self.c, company_name="Lost Co",
                                       created_by=self.mgr, updated_by=self.mgr)
        self.client.force_authenticate(self.mgr)
        r = self.client.post(f"/api/v1/leads/{lead.pk}/lost/",
                             {"reason": "went cold"}, format="json")
        self.assertEqual(r.status_code, 200)
        lead.refresh_from_db()
        self.assertEqual(lead.status, Lead.Status.LOST)
        self.assertEqual(lead.lost_reason, "went cold")

    def test_create_needs_crm_manage(self):
        self.client.force_authenticate(self.viewer)
        r = self.client.post("/api/v1/leads/", {"company_name": "Nope"}, format="json")
        self.assertEqual(r.status_code, 403)


class OpportunityApiTests(APITestCase):
    def setUp(self):
        from apps.customers.services import create_customer
        self.c = Company.objects.create(name="Acme")
        self.mgr = _user(self.c, ["crm.manage", "customers.manage"], "omgr@acme.co")
        with tenant_scope(self.c.id):
            self.customer = create_customer(self.c, self.mgr, name="Zenith")

    def test_create_list_stages_and_move(self):
        self.client.force_authenticate(self.mgr)
        # stage catalogue (columns)
        st = self.client.get("/api/v1/opportunities/stages/")
        self.assertEqual(st.status_code, 200)
        self.assertTrue(any(s["value"] == "won" for s in st.data))
        # create
        r = self.client.post("/api/v1/opportunities/", {
            "customer": str(self.customer.id), "title": "Pump refurb",
            "estimated_value": "120000"}, format="json")
        self.assertEqual(r.status_code, 201)
        oid = r.data["id"]
        # open list includes it
        lst = self.client.get("/api/v1/opportunities/?stage=open")
        titles = [x["title"] for x in (lst.data.get("results") or lst.data)]
        self.assertIn("Pump refurb", titles)
        # move to won
        mv = self.client.post(f"/api/v1/opportunities/{oid}/stage/",
                              {"stage": "won"}, format="json")
        self.assertEqual(mv.status_code, 200)
        self.assertEqual(mv.data["stage"], "won")

    def test_create_needs_crm_manage(self):
        viewer = _user(self.c, ["projects.view"], "oview@acme.co")
        self.client.force_authenticate(viewer)
        r = self.client.post("/api/v1/opportunities/",
                             {"customer": str(self.customer.id), "title": "X"}, format="json")
        self.assertEqual(r.status_code, 403)
