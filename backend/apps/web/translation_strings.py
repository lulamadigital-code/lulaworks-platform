"""Translation registry for strings that reach templates as VARIABLES (so
`makemessages` can extract them). The sidebar renders nav labels via
`{% trans label %}` where `label` is a runtime variable — these `gettext_lazy`
calls mark the underlying literals for extraction into the message catalogs.
Not imported at runtime; it exists purely for `makemessages`."""
from django.utils.translation import gettext_lazy as _

# Sidebar navigation labels (passed to web/_navlink.html as `label`).
_NAV = [
    _("Dashboard"), _("Intelligence"), _("CRM"), _("Marketing"), _("Procurement"),
    _("Projects"), _("Jobs"), _("Quotations"), _("Purchase Orders"),
    _("Invoices"), _("Finance"), _("People"), _("Business history"),
    _("Automations"), _("Settings"), _("Help & Support"),
]

# Company-setup section labels (rendered on the dashboard via {% trans sec.label %};
# defined in apps/identity/company_setup.py).
_SETUP = [
    _("Business information"), _("Tax / registration"), _("Banking details"),
    _("Communication"), _("Documents & branding"),
]

# Jobs view-switcher tab labels (passed to web/_viewtab.html as `label`).
_VIEWTABS = [
    _("List"), _("Board"), _("Table"), _("Calendar"), _("Workload"),
]
