"""One-shot (idempotent) legacy data normalization — run AFTER seed_reference.

Brings existing company data onto the international foundation without destroying
anything:
  • currency free-text/symbol  → ISO 4217 code (else derived from the company country)
  • bank_name free-text        → reference.Bank FK (unmatched → needs_review=True,
                                  the original bank_name is kept as-is)
  • user emails                → lowercased where that doesn't collide

Depends on the reference tables being seeded (bank matching needs the directory),
so it is a management command rather than a migration. Safe to re-run: already
normalized rows are left untouched. Use --dry-run to preview.
"""
from django.core.management.base import BaseCommand

# Legacy currency spellings/symbols → ISO 4217.
_CCY_ALIASES = {
    "r": "ZAR", "rand": "ZAR", "south african rand": "ZAR", "zar": "ZAR",
    "$": "USD", "us$": "USD", "usd": "USD", "dollar": "USD", "us dollar": "USD",
    "£": "GBP", "gbp": "GBP", "pound": "GBP", "pound sterling": "GBP",
    "€": "EUR", "eur": "EUR", "euro": "EUR",
    "a$": "AUD", "aud": "AUD", "c$": "CAD", "cad": "CAD",
    "₦": "NGN", "ngn": "NGN", "naira": "NGN", "₹": "INR", "inr": "INR", "rupee": "INR",
}


class Command(BaseCommand):
    help = "Normalize legacy currency/bank/email data onto the reference foundation."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report what would change without saving.")

    def handle(self, *args, **opts):
        from apps.identity.models import Company, CompanyBankAccount, User
        from apps.reference.models import Currency
        from apps.reference.services import BankDirectoryService, CurrencyService

        dry = opts["dry_run"]
        valid_codes = set(Currency.objects.values_list("code", flat=True))
        name_to_code = {c.name.lower(): c.code for c in Currency.objects.all()}

        def resolve_ccy(raw, country_code=""):
            v = (raw or "").strip()
            if v.upper() in valid_codes:
                return v.upper()
            hit = _CCY_ALIASES.get(v.lower()) or name_to_code.get(v.lower())
            if hit:
                return hit
            cur = CurrencyService.for_country(country_code) if country_code else None
            return cur.code if cur else None

        ccy_fixed = bank_matched = bank_flagged = email_fixed = 0

        # 1) Company currency
        for c in Company.objects.all():
            code = resolve_ccy(c.currency, c.country_code)
            if code and code != c.currency:
                self.stdout.write(f"  currency: {c.name!r} {c.currency!r} → {code}")
                if not dry:
                    c.currency = code
                    c.save(update_fields=["currency"])
                ccy_fixed += 1

        # 2) Bank accounts: currency + bank FK
        for a in CompanyBankAccount.objects.select_related("company").all():
            updates = []
            code = resolve_ccy(a.currency, getattr(a.company, "country_code", ""))
            if code and code != a.currency:
                a.currency = code
                updates.append("currency")
                ccy_fixed += 1
            if a.bank_id is None and (a.bank_name or "").strip():
                country = getattr(a.company, "country_code", "") or "ZA"
                bank = BankDirectoryService.match(country, a.bank_name)
                if bank is not None:
                    a.bank = bank
                    if a.needs_review:
                        a.needs_review = False
                        updates.append("needs_review")
                    updates.append("bank")
                    bank_matched += 1
                    self.stdout.write(f"  bank: {a.bank_name!r} → {bank.name} ({country})")
                elif not a.needs_review:
                    a.needs_review = True
                    updates.append("needs_review")
                    bank_flagged += 1
                    self.stdout.write(f"  bank UNMATCHED (needs review): {a.bank_name!r} ({country})")
            if updates and not dry:
                a.save(update_fields=list(set(updates)))

        # 3) Emails → lowercase where it doesn't collide with an existing user
        for u in User.objects.all():
            low = (u.email or "").strip().lower()
            if low and low != u.email:
                if not User.objects.filter(email__iexact=low).exclude(pk=u.pk).exists():
                    self.stdout.write(f"  email: {u.email!r} → {low}")
                    if not dry:
                        u.email = low
                        u.save(update_fields=["email"])
                    email_fixed += 1
                else:
                    self.stdout.write(self.style.WARNING(
                        f"  email collision, left as-is: {u.email!r}"))

        prefix = "[dry-run] would change" if dry else "Normalized"
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}: {ccy_fixed} currencies, {bank_matched} banks matched, "
            f"{bank_flagged} banks flagged for review, {email_fixed} emails."))
