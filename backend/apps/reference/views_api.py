"""Read-only reference API. One authoritative source of countries / currencies /
banks / rules for the web pickers now and Flutter later — the frontend never
keeps its own copy. Cached briefly; data changes only on a re-seed."""
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Bank, Country
from .services import (AddressValidationService, BankDirectoryService,
                       BankingValidationService, CurrencyService)

_CACHE = 60 * 15


def _country_dict(c: Country) -> dict:
    return {"code": c.code, "name": c.name, "calling_code": c.calling_code,
            "flag": c.flag_emoji, "region": c.region,
            "currency": c.default_currency_id}


def _bank_dict(b: Bank) -> dict:
    return {"id": b.id, "name": b.name, "short_name": b.short_name,
            "bank_code": b.bank_code, "swift_bic": b.swift_bic, "is_major": b.is_major}


@method_decorator(cache_page(_CACHE), name="get")
class CountryList(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        rows = Country.objects.filter(active=True)
        return Response([_country_dict(c) for c in rows])


class CountryCurrencyView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, code):
        cur = CurrencyService.for_country(code)
        if not cur:
            return Response({"currency": None})
        return Response({"currency": cur.code, "name": cur.name, "symbol": cur.symbol,
                         "label": CurrencyService.label(cur)})


class CountryBanks(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, code):
        q = request.query_params.get("q")
        rows = BankDirectoryService.for_country(code, q=q)
        return Response([_bank_dict(b) for b in rows])


class BankSearch(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        code = request.query_params.get("country", "")
        q = request.query_params.get("q")
        rows = BankDirectoryService.for_country(code, q=q)
        return Response([_bank_dict(b) for b in rows])


class BankingRules(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, code):
        rule = BankingValidationService.rule(code)
        if not rule:
            return Response({"country": code.upper(), "fields": [], "show_swift": True})
        return Response({"country": code.upper(), "branch_label": rule.branch_label,
                         "branch_hint": rule.branch_hint, "show_swift": rule.show_swift,
                         "fields": rule.fields})


class AddressRules(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, code):
        rule = AddressValidationService.rule(code)
        if not rule:
            return Response({"country": code.upper(), "fields": [], "postal_label": "Postal code"})
        return Response({"country": code.upper(), "fields": rule.fields,
                         "postal_label": rule.postal_label, "postal_hint": rule.postal_hint})
