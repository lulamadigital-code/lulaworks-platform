from django.urls import path

from . import views_api as v

urlpatterns = [
    path("reference/countries/", v.CountryList.as_view(), name="reference_countries"),
    path("reference/countries/<str:code>/currency/", v.CountryCurrencyView.as_view(),
         name="reference_country_currency"),
    path("reference/countries/<str:code>/banks/", v.CountryBanks.as_view(),
         name="reference_country_banks"),
    path("reference/countries/<str:code>/banking-rules/", v.BankingRules.as_view(),
         name="reference_banking_rules"),
    path("reference/countries/<str:code>/address-rules/", v.AddressRules.as_view(),
         name="reference_address_rules"),
    path("reference/countries/<str:code>/statutory-rules/", v.StatutoryRules.as_view(),
         name="reference_statutory_rules"),
    path("reference/countries/<str:code>/documents/", v.DocumentRules.as_view(),
         name="reference_document_rules"),
    path("reference/banks/search/", v.BankSearch.as_view(), name="reference_bank_search"),
]
