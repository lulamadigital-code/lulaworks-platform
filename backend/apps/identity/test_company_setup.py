"""Progressive company setup — owners are never locked out; a missing setting
blocks only the specific action that needs it."""
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.identity.company_setup import (CompanySetupRequired, can_perform,
                                         check_action, require_action, status,
                                         validate_document_requirements)
from apps.identity.models import (Company, CompanyBankAccount, Membership,
                                  Permission, Role, User)


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class SetupEngineTests(TestCase):
    def test_new_company_blocks_invoice_with_specific_missing(self):
        c = Company.objects.create(name="Acme")
        r = check_action(c, "CREATE_INVOICE")
        self.assertFalse(r["allowed"])
        self.assertEqual(r["code"], "COMPANY_SETUP_REQUIRED")
        fields = {m["field"] for m in r["missing"]}
        self.assertIn("banking_details", fields)
        self.assertIn("tax_information", fields)
        self.assertTrue(all("settings_url" in m for m in r["missing"]))

    def test_progressive_unlock(self):
        c = Company.objects.create(name="Acme", street_address="1 Main",
                                   city="Johannesburg", phone="011 555")
        # Company identity is enough for a quotation, not for an invoice.
        self.assertTrue(can_perform(c, "CREATE_QUOTATION"))
        self.assertFalse(can_perform(c, "CREATE_INVOICE"))
        c.vat_no = "4001234567"
        c.save()
        self.assertFalse(can_perform(c, "CREATE_INVOICE"))   # banking still missing
        CompanyBankAccount.objects.create(company=c, bank_name="FNB",
                                          account_name="Acme", account_number="620...")
        self.assertTrue(can_perform(c, "CREATE_INVOICE"))    # now unlocked

    def test_delivery_note_does_not_need_banking(self):
        c = Company.objects.create(name="Acme", street_address="1 Main",
                                   city="Jhb", email="a@acme.co")
        self.assertTrue(can_perform(c, "CREATE_DELIVERY_NOTE"))

    def test_validate_document_requirements_maps_to_action(self):
        c = Company.objects.create(name="Acme")
        r = validate_document_requirements("invoice", c)
        self.assertFalse(r["allowed"])
        self.assertEqual(r["action"], "EXPORT_INVOICE_PDF")

    def test_status_shape(self):
        s = status(Company.objects.create(name="Acme"))
        for k in ("overall_percentage", "sections", "actions",
                  "required_complete", "items_remaining"):
            self.assertIn(k, s)
        self.assertFalse(s["actions"]["CREATE_INVOICE"]["allowed"])

    def test_require_action_raises_structured(self):
        with self.assertRaises(CompanySetupRequired) as ctx:
            require_action(Company.objects.create(name="Acme"), "CREATE_INVOICE")
        self.assertEqual(ctx.exception.result["action"], "CREATE_INVOICE")


class NoLockTests(TestCase):
    def test_owner_with_incomplete_company_is_not_locked_out(self):
        c = Company.objects.create(name="Fresh Co")     # nothing filled in
        u = _user(c, ["company.manage"], "owner@fresh.co")
        self.client.force_login(u)
        r = self.client.get(reverse("web:dashboard"))
        self.assertEqual(r.status_code, 200)             # NOT redirected to profile
        self.assertContains(r, "Finish your company setup")   # gentle card, not a lock


class SetupApiTests(APITestCase):
    def test_setup_status_endpoint(self):
        c = Company.objects.create(name="Api Co")
        u = _user(c, ["company.manage"], "api@co.co")
        self.client.force_authenticate(u)
        r = self.client.get("/api/v1/company/setup/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("overall_percentage", r.data)
        self.assertFalse(r.data["actions"]["CREATE_INVOICE"]["allowed"])
        self.assertTrue(r.data["can_edit"])
