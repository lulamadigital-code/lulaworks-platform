"""Scheduled compliance validation sweep (COMPLIANCE §8). Wire to Celery beat:
flips expired certificates to EXPIRED (re-blocking their projects) and reports
upcoming expiries for the alert engine."""

from django.core.management.base import BaseCommand

from apps.compliance.services import validate_expiries


class Command(BaseCommand):
    help = "Sweep compliance items: expire lapsed certificates, report upcoming expiries."

    def add_arguments(self, parser):
        parser.add_argument("--within-days", type=int, default=30)

    def handle(self, *args, **options):
        result = validate_expiries(within_days=options["within_days"])
        self.stdout.write(self.style.SUCCESS(
            f"Expired: {result['expired']} · re-blocked projects: "
            f"{result['reblocked_projects']} · upcoming: {len(result['upcoming'])}"
        ))
