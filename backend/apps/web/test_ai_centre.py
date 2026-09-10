"""AI command centre + Automations console render and gate correctly."""
from django.test import TestCase

from apps.ai_platform.models import Automation
from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User


def _user(company, codes, email):
    role = Role.objects.create(name=f"R-{email}", is_system=True)
    for c in codes:
        p, _ = Permission.objects.get_or_create(codename=c, defaults={"module": "x", "label": c})
        role.permissions.add(p)
    u = User.objects.create_user(email, "x", active_company=company)
    Membership.objects.create(user=u, company=company, role=role)
    return u


class AiCentreTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Contractor A")
        self.ai = _user(self.company, ["ai.generate"], "ai@a.co")
        self.plain = _user(self.company, ["projects.view"], "p@a.co")

    def test_centre_renders_for_ai_user(self):
        self.client.force_login(self.ai)
        r = self.client.get("/intelligence/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Lulaworks Intelligence")

    def test_centre_blocked_without_ai_perm(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get("/intelligence/").status_code, 302)

    def test_automations_create_and_render(self):
        self.client.force_login(self.ai)
        r = self.client.post("/automations/create/", {
            "name": "Warn on price rises", "trigger": "price_rise", "action": "notify_me"})
        self.assertEqual(r.status_code, 302)
        with tenant_scope(self.company.id):
            self.assertEqual(Automation.objects.count(), 1)
        page = self.client.get("/automations/")
        self.assertContains(page, "Warn on price rises")
