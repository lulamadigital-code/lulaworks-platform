"""Work Execution System — a task is an operational record, not a checkbox.

Covers the P1 data model and the P2 services: GPS verification, resource
allocation + reconciliation, financial rollups, and the operational timeline.
"""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from apps.administration.models import NumberingRule
from apps.core.context import tenant_scope
from apps.identity.models import Company, Membership, Permission, Role, User

from .models import (
    AllocationKind,
    AllocationStatus,
    ReportKind,
    Task,
    TaskReport,
    TaskResourceAllocation,
)
from .work_execution import (
    add_report_item,
    allocate_task_resource,
    create_task_report,
    haversine_m,
    reconcile_allocation,
    task_financials,
    task_operational_dashboard,
    task_timeline,
    verify_report_location,
)


def make_company(name="Lulama"):
    c = Company.objects.create(name=name)
    for dt, pfx in [("quotation", "QT"), ("project", "PRJ"), ("po", "PO")]:
        NumberingRule.objects.create(company=c, doc_type=dt, prefix=pfx,
                                     fmt="{prefix}-{yyyy}-{seq:05d}")
    return c


# Two points ~1.1 km apart in Johannesburg — used to exercise the tolerance.
SITE = (-26.204103, 28.047305)
NEARBY = (-26.205000, 28.048000)      # ~120 m from SITE
FAR = (-26.214103, 28.057305)         # ~1.5 km from SITE


class HaversineTests(APITestCase):
    def test_zero_distance(self):
        self.assertEqual(round(haversine_m(*SITE, *SITE), 1), 0.0)

    def test_known_distance_is_reasonable(self):
        d = haversine_m(*SITE, *FAR)
        self.assertTrue(1200 < d < 1800, d)


class GpsVerificationTests(APITestCase):
    def setUp(self):
        self.c = make_company()

    def _task(self, **kw):
        with tenant_scope(self.c.id):
            return Task.objects.create(company=self.c, name="Deliver hoses",
                                       site_latitude=Decimal(str(SITE[0])),
                                       site_longitude=Decimal(str(SITE[1])), **kw)

    def test_checkin_within_tolerance_is_not_flagged(self):
        with tenant_scope(self.c.id):
            task = self._task()
            report = create_task_report(
                task, None, kind=ReportKind.TIME_EVENT, title="Arrived at site",
                event="Arrived at site",
                latitude=Decimal(str(NEARBY[0])), longitude=Decimal(str(NEARBY[1])))
        self.assertFalse(report.location_flagged)
        self.assertIsNotNone(report.distance_m)
        self.assertLess(report.distance_m, Decimal("500"))

    def test_checkin_beyond_tolerance_is_flagged(self):
        with tenant_scope(self.c.id):
            task = self._task()
            report = create_task_report(
                task, None, kind=ReportKind.TIME_EVENT, title="Arrived at site",
                latitude=Decimal(str(FAR[0])), longitude=Decimal(str(FAR[1])))
        self.assertTrue(report.location_flagged)
        self.assertGreater(report.distance_m, Decimal("500"))

    def test_no_expected_coords_means_nothing_to_verify(self):
        with tenant_scope(self.c.id):
            task = Task.objects.create(company=self.c, name="No-geo task")
            report = create_task_report(
                task, None, title="Progress",
                latitude=Decimal(str(FAR[0])), longitude=Decimal(str(FAR[1])))
        self.assertFalse(report.location_flagged)
        self.assertIsNone(report.distance_m)

    def test_report_without_gps_is_never_flagged(self):
        with tenant_scope(self.c.id):
            task = self._task()
            report = create_task_report(task, None, title="Desk note")
        self.assertFalse(report.location_flagged)
        self.assertIsNone(report.distance_m)


class AllocationReconciliationTests(APITestCase):
    def setUp(self):
        self.c = make_company()

    def test_allocate_then_spend_reconciles(self):
        with tenant_scope(self.c.id):
            task = Task.objects.create(company=self.c, name="Buy materials")
            alloc = allocate_task_resource(
                task, None, kind=AllocationKind.PURCHASE_BUDGET,
                amount_allocated="5000")
            self.assertEqual(alloc.amount_spent, Decimal("0.00"))
            self.assertTrue(alloc.is_monetary)

            create_task_report(task, None, kind=ReportKind.MATERIAL,
                               title="Hoses", amount="1200", allocation=alloc)
            create_task_report(task, None, kind=ReportKind.MATERIAL,
                               title="Fittings", amount="800", allocation=alloc)
            alloc.refresh_from_db()

        self.assertEqual(alloc.amount_spent, Decimal("2000.00"))
        self.assertEqual(alloc.remaining, Decimal("3000.00"))
        self.assertFalse(alloc.is_over_budget)

    def test_overspend_is_detected(self):
        with tenant_scope(self.c.id):
            task = Task.objects.create(company=self.c, name="Fuel")
            alloc = allocate_task_resource(task, None, kind=AllocationKind.FUEL_ADVANCE,
                                           amount_allocated="500")
            create_task_report(task, None, kind=ReportKind.FUEL, title="Diesel",
                               amount="650", allocation=alloc)
            alloc.refresh_from_db()
        self.assertTrue(alloc.is_over_budget)
        self.assertEqual(alloc.remaining, Decimal("-150.00"))

    def test_vehicle_allocation_is_non_monetary(self):
        with tenant_scope(self.c.id):
            task = Task.objects.create(company=self.c, name="Transport")
            alloc = allocate_task_resource(task, None, kind=AllocationKind.VEHICLE,
                                           label="Bakkie CA 123-456")
        self.assertFalse(alloc.is_monetary)
        self.assertEqual(alloc.status, AllocationStatus.REQUESTED)


class FinancialRollupTests(APITestCase):
    def setUp(self):
        self.c = make_company()

    def test_task_financials_sum_allocated_spent_materials(self):
        with tenant_scope(self.c.id):
            task = Task.objects.create(company=self.c, name="Supply job")
            allocate_task_resource(task, None, kind=AllocationKind.PURCHASE_BUDGET,
                                   amount_allocated="10000")
            allocate_task_resource(task, None, kind=AllocationKind.FUEL_ADVANCE,
                                   amount_allocated="1000")
            # non-monetary should not inflate 'allocated'
            allocate_task_resource(task, None, kind=AllocationKind.PPE, label="Boots")
            mat = create_task_report(task, None, kind=ReportKind.MATERIAL,
                                     title="Pipe", amount="3000")
            create_task_report(task, None, kind=ReportKind.FUEL, title="Diesel",
                               amount="900")
            add_report_item(mat, description="Pipe 2in", quantity="10",
                            unit="ea", unit_price="300")
            fin = task_financials(task)

        self.assertEqual(fin["allocated"], Decimal("11000.00"))
        self.assertEqual(fin["spent"], Decimal("3900.00"))
        self.assertEqual(fin["remaining"], Decimal("7100.00"))
        self.assertEqual(fin["materials_total"], Decimal("3000.00"))
        self.assertEqual(fin["materials_count"], 1)
        self.assertFalse(fin["over_budget"])


class ReportItemTests(APITestCase):
    def test_line_total_defaults_to_qty_times_price(self):
        c = make_company()
        with tenant_scope(c.id):
            task = Task.objects.create(company=c, name="Materials")
            report = create_task_report(task, None, kind=ReportKind.MATERIAL,
                                        title="Invoice 55")
            item = add_report_item(report, description="Bolt", quantity="4",
                                   unit_price="12.50")
        self.assertEqual(item.line_total, Decimal("50.000"))
        self.assertEqual(item.task_id, task.id)


class OperationalTimelineTests(APITestCase):
    def test_timeline_is_chronological_and_covers_lifecycle(self):
        c = make_company()
        with tenant_scope(c.id):
            task = Task.objects.create(company=c, name="Deliver")
            base = timezone.now()  # everything below happens strictly after creation
            allocate_task_resource(task, None, kind=AllocationKind.FUEL_ADVANCE,
                                   amount_allocated="400")
            task.started_at = base + timedelta(hours=1)
            task.save(update_fields=["started_at"])
            create_task_report(task, None, kind=ReportKind.TIME_EVENT,
                               title="Departed office",
                               reported_at=base + timedelta(hours=2))
            create_task_report(task, None, kind=ReportKind.FUEL, title="Diesel",
                               amount="380", reported_at=base + timedelta(hours=3))
            task.completed_at = base + timedelta(hours=4)
            task.save(update_fields=["completed_at"])
            timeline = task_timeline(task)

        labels = [e["label"] for e in timeline]
        self.assertIn("Task created", labels)
        self.assertIn("Task started", labels)
        self.assertIn("Task completed", labels)
        self.assertEqual(labels[0], "Task created")
        self.assertEqual(labels[-1], "Task completed")
        whens = [e["when"] for e in timeline]
        self.assertEqual(whens, sorted(whens))


class OperationalDashboardTests(APITestCase):
    def test_dashboard_answers_the_managers_questions(self):
        c = make_company()
        with tenant_scope(c.id):
            task = Task.objects.create(company=c, name="Site work",
                                       site_latitude=Decimal(str(SITE[0])),
                                       site_longitude=Decimal(str(SITE[1])))
            allocate_task_resource(task, None, kind=AllocationKind.PURCHASE_BUDGET,
                                   amount_allocated="2000")
            create_task_report(task, None, kind=ReportKind.MATERIAL, title="Valve",
                               amount="1500",
                               latitude=Decimal(str(NEARBY[0])),
                               longitude=Decimal(str(NEARBY[1])))
            create_task_report(task, None, kind=ReportKind.TIME_EVENT,
                               title="Left supplier",
                               latitude=Decimal(str(FAR[0])),
                               longitude=Decimal(str(FAR[1])))
            dash = task_operational_dashboard(task)

        self.assertEqual(dash["financials"]["allocated"], Decimal("2000.00"))
        self.assertEqual(dash["financials"]["spent"], Decimal("1500.00"))
        self.assertIsNotNone(dash["latest_gps"])
        self.assertIsNotNone(dash["latest_report"])
        self.assertEqual(len(dash["map_points"]), 2)
        self.assertEqual(len(dash["flagged_reports"]), 1)  # the FAR check-in
        self.assertTrue(dash["timeline"])


class ReceiptToSupplierDBTests(APITestCase):
    """Buying + attaching a seller's receipt feeds the Suppliers database and the
    price ledger — so next time we know where we bought this and what we paid."""

    def setUp(self):
        self.c = make_company()

    def _material_receipt(self, supplier, items):
        from apps.execution.work_execution import learn_supplier_from_receipt
        with tenant_scope(self.c.id):
            task = Task.objects.create(company=self.c, name="Buy pipes")
            report = create_task_report(task, None, kind=ReportKind.MATERIAL,
                                        title="Supplier invoice", supplier=supplier)
            for desc, unit, price in items:
                add_report_item(report, description=desc, unit=unit, unit_price=price)
            sup = learn_supplier_from_receipt(report, None)
        return report, sup

    def test_receipt_creates_supplier_and_records_prices(self):
        from apps.procurement.models import Supplier, SupplierPrice
        report, sup = self._material_receipt(
            "Hydraulics SA", [("Hose 1in", "ea", "300"), ("Fitting", "ea", "45")])
        with tenant_scope(self.c.id):
            self.assertIsNotNone(sup)
            self.assertEqual(Supplier.objects.filter(name="Hydraulics SA").count(), 1)
            self.assertEqual(SupplierPrice.objects.filter(supplier=sup).count(), 2)
            report.refresh_from_db()
            self.assertEqual(report.supplier_ref_id, sup.id)   # receipt is traceable

    def test_second_receipt_matches_existing_supplier_no_duplicate(self):
        from apps.procurement.models import Supplier, SupplierPrice
        self._material_receipt("Hydraulics SA", [("Hose 1in", "ea", "300")])
        self._material_receipt("hydraulics sa", [("Hose 2in", "ea", "420")])  # case-insensitive
        with tenant_scope(self.c.id):
            self.assertEqual(Supplier.objects.filter(company=self.c).count(), 1)
            self.assertEqual(SupplierPrice.objects.count(), 2)  # both under the one supplier

    def test_zero_priced_lines_are_not_recorded(self):
        from apps.procurement.models import SupplierPrice
        _, sup = self._material_receipt("Steel Co", [("Beam", "m", "0"), ("Bolt", "ea", "5")])
        with tenant_scope(self.c.id):
            self.assertEqual(SupplierPrice.objects.filter(supplier=sup).count(), 1)

    def test_non_material_report_leaves_supplier_db_alone(self):
        from apps.procurement.models import Supplier
        from apps.execution.work_execution import learn_supplier_from_receipt
        with tenant_scope(self.c.id):
            task = Task.objects.create(company=self.c, name="Fuel")
            r = create_task_report(task, None, kind=ReportKind.FUEL, title="Diesel",
                                   supplier="Engen", amount="500")
            self.assertIsNone(learn_supplier_from_receipt(r, None))
            self.assertEqual(Supplier.objects.count(), 0)


class WesApiTests(APITestCase):
    """The Flutter app's write surface: post a field report (with GPS), allocate
    a resource and reconcile it, and read the operational dashboard."""

    def setUp(self):
        self.company = make_company()
        manage = Permission.objects.create(codename="execution.manage",
                                            module="execution", label="M")
        self.role = Role.objects.create(name="Ops", is_system=True)
        self.role.permissions.add(manage)
        self.ops = User.objects.create_user("ops@lulama.co.za", "x",
                                             active_company=self.company)
        Membership.objects.create(user=self.ops, company=self.company, role=self.role)
        with tenant_scope(self.company.id):
            self.task = Task.objects.create(
                company=self.company, name="Deliver hoses",
                site_latitude=Decimal(str(SITE[0])), site_longitude=Decimal(str(SITE[1])))
        self.client.force_authenticate(self.ops)

    def test_post_report_verifies_gps_and_returns_it(self):
        resp = self.client.post("/api/v1/task-reports/", {
            "task": str(self.task.id), "kind": "time_event",
            "title": "Arrived at site", "event": "Arrived at site",
            "latitude": str(FAR[0]), "longitude": str(FAR[1]),
        }, format="json")
        self.assertEqual(resp.status_code, 201, resp.data)
        self.assertTrue(resp.data["location_flagged"])   # FAR is beyond tolerance
        self.assertIsNotNone(resp.data["distance_m"])

    def test_material_report_with_items_and_allocation_reconciles(self):
        alloc = self.client.post("/api/v1/task-allocations/", {
            "task": str(self.task.id), "kind": "purchase_budget",
            "amount_allocated": "5000",
        }, format="json")
        self.assertEqual(alloc.status_code, 201, alloc.data)
        alloc_id = alloc.data["id"]

        report = self.client.post("/api/v1/task-reports/", {
            "task": str(self.task.id), "kind": "material", "title": "Invoice 900",
            "supplier": "Hydraulics SA", "amount": "1800", "allocation": alloc_id,
            "items": [{"description": "Hose 1in", "quantity": "6", "unit_price": "300"}],
        }, format="json")
        self.assertEqual(report.status_code, 201, report.data)
        self.assertEqual(len(report.data["items"]), 1)

        # reconcile endpoint reflects the spend
        rec = self.client.post(f"/api/v1/task-allocations/{alloc_id}/reconcile/")
        self.assertEqual(rec.status_code, 200)
        self.assertEqual(rec.data["amount_spent"], "1800.00")
        self.assertEqual(rec.data["remaining"], "3200.00")

    def test_operational_dashboard_endpoint(self):
        self.client.post("/api/v1/task-reports/", {
            "task": str(self.task.id), "kind": "fuel", "title": "Diesel",
            "amount": "450",
        }, format="json")
        resp = self.client.get(f"/api/v1/tasks/{self.task.id}/operational/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["financials"]["spent"], "450.00")
        self.assertEqual(len(resp.data["reports"]), 1)
        self.assertTrue(resp.data["timeline"])

    def test_report_requires_permission(self):
        viewer = User.objects.create_user("v@lulama.co.za", "x",
                                           active_company=self.company)
        Membership.objects.create(user=viewer, company=self.company,
                                  role=Role.objects.create(name="V", is_system=True))
        self.client.force_authenticate(viewer)
        resp = self.client.post("/api/v1/task-reports/", {
            "task": str(self.task.id), "title": "x"}, format="json")
        self.assertEqual(resp.status_code, 403)
