"""Read-only reference API. One authoritative source of countries / currencies /
banks / rules for the web pickers now and Flutter later — the frontend never
keeps its own copy. Cached briefly; data changes only on a re-seed."""
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from .models import Bank, Country
from .services import (AddressValidationService, BankDirectoryService,
                       BankingValidationService, CurrencyService,
                       DocumentRulesService, LanguageService, LocaleService,
                       StatutoryService)

_CACHE = 60 * 15


class _RefView(APIView):
    """Base for the read-only reference endpoints. Accepts BOTH the web session
    (so the company page's live country-change fetches work) and JWT (mobile)."""
    permission_classes = [IsAuthenticated]
    authentication_classes = [SessionAuthentication, JWTAuthentication]


def _country_dict(c: Country) -> dict:
    return {"code": c.code, "name": c.name, "calling_code": c.calling_code,
            "flag": c.flag_emoji, "region": c.region,
            "currency": c.default_currency_id}


def _bank_dict(b: Bank) -> dict:
    return {"id": b.id, "name": b.name, "short_name": b.short_name,
            "bank_code": b.bank_code, "swift_bic": b.swift_bic, "is_major": b.is_major}


@method_decorator(cache_page(_CACHE), name="get")
class CountryList(_RefView):

    def get(self, request):
        rows = Country.objects.filter(active=True)
        return Response([_country_dict(c) for c in rows])


class CountryCurrencyView(_RefView):

    def get(self, request, code):
        cur = CurrencyService.for_country(code)
        if not cur:
            return Response({"currency": None})
        return Response({"currency": cur.code, "name": cur.name, "symbol": cur.symbol,
                         "label": CurrencyService.label(cur)})


class CountryBanks(_RefView):

    def get(self, request, code):
        q = request.query_params.get("q")
        rows = BankDirectoryService.for_country(code, q=q)
        return Response([_bank_dict(b) for b in rows])


class BankSearch(_RefView):

    def get(self, request):
        code = request.query_params.get("country", "")
        q = request.query_params.get("q")
        rows = BankDirectoryService.for_country(code, q=q)
        return Response([_bank_dict(b) for b in rows])


class BankingRules(_RefView):

    def get(self, request, code):
        rule = BankingValidationService.rule(code)
        if not rule:
            return Response({"country": code.upper(), "fields": [], "show_swift": True})
        return Response({"country": code.upper(), "branch_label": rule.branch_label,
                         "branch_hint": rule.branch_hint, "show_swift": rule.show_swift,
                         "fields": rule.fields})


class AddressRules(_RefView):

    def get(self, request, code):
        rule = AddressValidationService.rule(code)
        if not rule:
            return Response({"country": code.upper(), "fields": [], "postal_label": "Postal code"})
        return Response({"country": code.upper(), "fields": rule.fields,
                         "postal_label": rule.postal_label, "postal_hint": rule.postal_hint})


class StatutoryRules(_RefView):

    def get(self, request, code):
        return Response({"country": code.upper(), "fields": StatutoryService.rules(code)})


class DocumentRules(_RefView):

    def get(self, request, code):
        return Response({"country": code.upper(),
                         "documents": DocumentRulesService.recommended(code)})


def _lang_dict(l):
    return {"code": l.code, "name": l.name, "native_name": l.native_name,
            "direction": l.direction}


@method_decorator(cache_page(_CACHE), name="get")
class LanguageList(_RefView):
    def get(self, request):
        return Response([_lang_dict(l) for l in LanguageService.all_active()])


@method_decorator(cache_page(_CACHE), name="get")
class LocaleList(_RefView):
    def get(self, request):
        from .models import Locale
        rows = Locale.objects.filter(active=True).select_related("language")
        return Response([{"code": lo.code, "language": lo.language_id,
                          "region": lo.region, "date_format": lo.date_format}
                         for lo in rows])


class CountryLanguages(_RefView):
    def get(self, request, code):
        langs = LanguageService.for_country(code)
        default = LanguageService.default_for_country(code)
        return Response({"country": code.upper(),
                         "default": default.code if default else None,
                         "languages": [_lang_dict(l) for l in langs]})


class CountryLocales(_RefView):
    def get(self, request, code):
        langs = LanguageService.for_country(code)
        out = []
        for l in langs:
            out += LocaleService.for_language(l.code)
        seen, rows = set(), []
        for lo in out:
            if lo.code not in seen:
                seen.add(lo.code)
                rows.append({"code": lo.code, "language": lo.language_id, "region": lo.region})
        return Response({"country": code.upper(), "locales": rows})
