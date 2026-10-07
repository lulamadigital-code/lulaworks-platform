"""The app shell renders the left sidebar navigation (not the top-nav bar)."""
from django.test import Client
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.identity.models import Company, Membership, Permission, Role, User


class SidebarNavTests(APITestCase):
    def setUp(self):
        self.c = Company.objects.create(name="Acme")
        role = Role.objects.create(name="Owner", is_system=True)
        for code in ["projects.view", "customers.manage", "procurement.manage"]:
            p, _ = Permission.objects.get_or_create(
                codename=code, defaults={"module": "x", "label": code})
            role.permissions.add(p)
        self.u = User.objects.create_user("u@acme.co", "x", active_company=self.c)
        Membership.objects.create(user=self.u, company=self.c, role=role)

    def test_dashboard_has_sidebar(self):
        cl = Client(); cl.force_login(self.u)
        html = cl.get(reverse("web:dashboard")).content.decode()
        self.assertIn('class="sidebar"', html)       # the left nav is back
        self.assertIn('class="nav"', html)
        self.assertNotIn("topbar", html)             # old top-nav bar gone
