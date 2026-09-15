"""Translation registry for strings that reach templates as VARIABLES (so
`makemessages` can extract them). The sidebar renders nav labels via
`{% trans label %}` where `label` is a runtime variable — these `gettext_lazy`
calls mark the underlying literals for extraction into the message catalogs.
Not imported at runtime; it exists purely for `makemessages`."""
from django.utils.translation import gettext_lazy as _

# Sidebar navigation labels (passed to web/_navlink.html as `label`).
_NAV = [
    _("Dashboard"), _("Intelligence"), _("CRM"), _("Marketing"), _("Procurement"),
    _("Projects"), _("Job Card"), _("Quotations"), _("Purchase Orders"),
    _("Tax Invoice"), _("Finance"), _("People"), _("Business history"),
    _("Automations"), _("Settings"), _("Help & Support"),
]
