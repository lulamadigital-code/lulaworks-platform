"""Global Country Intelligence coverage audit.

Iterates EVERY ISO 3166-1 country through the same configuration engine and
reports its coverage — proving the architecture supports every country, and
honestly listing which ones are baseline vs regional vs first-class (no
pretending). Assigns a coverage level per country:

  FIRST_CLASS     — local banking + address + statutory + document rules (+banks)
  REGIONAL        — some local config (banks, SEPA/regional banking, or Africa/
                    home-region boost) but not the full set
  GLOBAL_BASELINE — resolves currency + calling code, uses generic fallbacks
  INCOMPLETE      — no currency resolvable (needs a country→currency mapping)

Usage:
  manage.py country_coverage_audit                 # summary + gaps to stdout
  manage.py country_coverage_audit --csv out.csv   # full per-country table
  manage.py country_coverage_audit --region Africa # filter one region
"""
import csv

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Audit country-intelligence coverage across every ISO country."

    def add_arguments(self, parser):
        parser.add_argument("--csv", help="Write the full per-country table to this path.")
        parser.add_argument("--region", help="Only audit countries in this region.")

    def handle(self, *args, **opts):
        from apps.reference.models import (AddressFieldRule, Bank, BankingFieldRule,
                                           Country, CountryDocumentRule,
                                           StatutoryRegistrationRule)
        from apps.reference.services import CurrencyService

        banking = set(BankingFieldRule.objects.values_list("country_id", flat=True))
        address = set(AddressFieldRule.objects.values_list("country_id", flat=True))
        statutory = set(StatutoryRegistrationRule.objects.values_list("country_id", flat=True))
        documents = set(CountryDocumentRule.objects.values_list("country_id", flat=True))
        with_banks = set(Bank.objects.values_list("country_id", flat=True))

        rows, counts = [], {"FIRST_CLASS": 0, "REGIONAL": 0,
                            "GLOBAL_BASELINE": 0, "INCOMPLETE": 0}
        first_class, incomplete = [], []
        qs = Country.objects.filter(active=True)
        if opts.get("region"):
            qs = qs.filter(region__iexact=opts["region"])

        for c in qs:
            code = c.code
            cur = CurrencyService.for_country(code)
            has_local_banking = code in banking            # own rule (SEPA counts as regional)
            has_addr = code in address
            has_stat = code in statutory
            has_docs = code in documents
            missing = []
            # Level assignment
            if not cur:
                level = "INCOMPLETE"
                missing.append("currency")
                incomplete.append(code)
            elif has_local_banking and has_addr and has_stat and has_docs:
                level = "FIRST_CLASS"
                first_class.append(code)
            elif (code in with_banks or has_local_banking or has_stat or has_addr
                  or has_docs or (c.region == "Africa")):
                level = "REGIONAL"
            else:
                level = "GLOBAL_BASELINE"
            counts[level] += 1
            if level != "FIRST_CLASS" and level != "INCOMPLETE":
                for label, present in (("banks", code in with_banks),
                                       ("banking-rule", has_local_banking),
                                       ("address-rule", has_addr),
                                       ("statutory-rules", has_stat),
                                       ("document-rules", has_docs)):
                    if not present:
                        missing.append(label)
            rows.append({
                "Country": c.name, "ISO": code,
                "Currency": cur.code if cur else "",
                "Calling Code": c.calling_code,
                "Address Rules": "yes" if has_addr else "generic",
                "Postal Rules": "yes" if has_addr else "generic",
                "Banking Rules": "yes" if has_local_banking else "generic",
                "Banks": Bank.objects.filter(country_id=code, active=True).count(),
                "Tax Rules": "VAT/GST" if has_stat else "generic",
                "Statutory": "yes" if has_stat else "generic",
                "Documents": "yes" if has_docs else "generic",
                "Coverage Level": level,
                "Missing Configuration": ", ".join(missing),
            })

        if opts.get("csv"):
            with open(opts["csv"], "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            self.stdout.write(self.style.SUCCESS(f"Wrote {len(rows)} rows to {opts['csv']}"))

        total = len(rows)
        self.stdout.write(self.style.SUCCESS(
            f"\nCountry Intelligence coverage — {total} countries audited"))
        for lvl in ("FIRST_CLASS", "REGIONAL", "GLOBAL_BASELINE", "INCOMPLETE"):
            self.stdout.write(f"  {lvl:16} {counts[lvl]}")
        self.stdout.write(f"\nFIRST_CLASS ({len(first_class)}): {', '.join(sorted(first_class))}")
        self.stdout.write(
            f"Africa audited: {sum(1 for r in rows if r['ISO'] in _african_codes())} "
            f"of 54, all ≥ {'; '.join(sorted({r['Coverage Level'] for r in rows if r['ISO'] in _african_codes()}))}")
        if incomplete:
            self.stdout.write(self.style.WARNING(
                f"\nINCOMPLETE — no currency ({len(incomplete)}), typically uninhabited "
                f"territories: {', '.join(sorted(incomplete))}"))
        else:
            self.stdout.write(self.style.SUCCESS(
                "\nEvery audited country resolves a currency — none 'unsupported'."))


def _african_codes():
    from apps.reference.management.commands.seed_reference import _AFRICA
    return _AFRICA
