"""Company setup — PROGRESSIVE REQUIREMENTS / CONTEXTUAL VALIDATION.

Owners are NEVER locked out of LulaWorks. A missing company setting blocks only
the specific business action that genuinely depends on it (issuing an invoice,
generating a document PDF, sending a document) — never navigation, never the
whole account. This module is the SINGLE SOURCE OF TRUTH shared by the web
console, the REST API, Flutter and LulaAI, so a requirement can never be
enforced in one place and bypassed in another.

    check_action(company, "CREATE_INVOICE")     → {allowed, missing, message}
    validate_document_requirements("invoice", company, template)
    status(company)                              → sections + per-action state
"""

# ── Field checkers (each answers: does the company have this yet?) ────────────

def _has_name(c):     return bool((c.name or "").strip())
def _has_address(c):  return bool(c.street_address and c.city)
def _has_contact(c):  return bool(c.phone or c.mobile or c.email)
def _has_tax(c):      return bool(c.registration_no or c.vat_no or c.tax_reference_no)
def _has_banking(c):  return c.bank_accounts.exists()
def _has_logo(c):     return bool(c.logo)
def _has_email(c):    return bool(c.email)


# section key → (label, [(field_key, field_label, checker)], settings anchor)
SECTIONS = {
    "business": ("Business information",
                 [("name", "Company name", _has_name),
                  ("address", "Business address", _has_address),
                  ("contact", "Phone or email", _has_contact)], "#business"),
    "tax": ("Tax / registration",
            [("tax_information", "Registration or VAT number", _has_tax)], "#statutory"),
    "banking": ("Banking details",
                [("banking_details", "A bank account", _has_banking)], "#banking"),
    "communication": ("Communication",
                      [("email", "A company email address", _has_email)], "#contact"),
    "branding": ("Documents & branding",
                 [("logo", "Company logo", _has_logo)], "#branding"),
}

# Recommended-only sections never block an action — they just improve polish.
RECOMMENDED_SECTIONS = {"branding"}

# action → the section keys it genuinely requires
ACTIONS = {
    "CREATE_QUOTATION":        ["business"],
    "EXPORT_QUOTATION_PDF":    ["business"],
    "SEND_QUOTATION":          ["business", "communication"],
    "CREATE_INVOICE":          ["business", "tax", "banking"],
    "EXPORT_INVOICE_PDF":      ["business", "tax", "banking"],
    "SEND_INVOICE":            ["business", "tax", "banking", "communication"],
    "CREATE_DELIVERY_NOTE":    ["business"],
    "EXPORT_DELIVERY_NOTE_PDF": ["business"],
    "SEND_DELIVERY_NOTE":      ["business", "communication"],
}

# document_type → (create action, export/issue action)
DOC_ACTION = {
    "quotation": ("CREATE_QUOTATION", "EXPORT_QUOTATION_PDF"),
    "invoice": ("CREATE_INVOICE", "EXPORT_INVOICE_PDF"),
    "tax_invoice": ("CREATE_INVOICE", "EXPORT_INVOICE_PDF"),
    "delivery_note": ("CREATE_DELIVERY_NOTE", "EXPORT_DELIVERY_NOTE_PDF"),
}

# Contextual, action-specific messages (§13 — clearer than "profile incomplete").
ACTION_MESSAGES = {
    "CREATE_INVOICE": "Complete your billing information before creating an invoice.",
    "EXPORT_INVOICE_PDF": "Complete your billing information before generating the invoice.",
    "SEND_INVOICE": "Complete your billing information before sending the invoice.",
    "CREATE_QUOTATION": "Add your company identity before creating a quotation.",
    "EXPORT_QUOTATION_PDF": "Add your company identity before generating the quotation.",
    "SEND_QUOTATION": "Add your company identity and a contact email before sending.",
    "CREATE_DELIVERY_NOTE": "Add your company identity before creating a delivery note.",
    "EXPORT_DELIVERY_NOTE_PDF": "Add your company identity before generating the delivery note.",
}
_DEFAULT_MESSAGE = "Complete the required company information first."


def _settings_url(anchor=""):
    from django.urls import reverse
    try:
        return reverse("web:company_profile") + anchor
    except Exception:                                # noqa: BLE001
        return "/company/" + anchor


def _missing_for_sections(company, section_keys):
    missing, seen = [], set()
    for key in section_keys:
        label, fields, anchor = SECTIONS[key]
        for fkey, flabel, checker in fields:
            if fkey in seen or checker(company):
                continue
            seen.add(fkey)
            missing.append({"field": fkey, "label": flabel, "section": key,
                            "settings_url": _settings_url(anchor)})
    return missing


def check_action(company, action) -> dict:
    """The core call: may this company perform `action` yet, and if not, exactly
    what's missing and where to fix it."""
    section_keys = ACTIONS.get(action, [])
    missing = _missing_for_sections(company, section_keys)
    return {
        "code": "COMPANY_SETUP_REQUIRED" if missing else "OK",
        "allowed": not missing,
        "action": action,
        "missing": missing,
        "message": (ACTION_MESSAGES.get(action, _DEFAULT_MESSAGE) if missing else ""),
        "settings_url": _settings_url(),
    }


def can_perform(company, action) -> bool:
    return not _missing_for_sections(company, ACTIONS.get(action, []))


def validate_document_requirements(document_type, company, template=None, *,
                                   issuing=True) -> dict:
    """Document-generation gate (§8). `issuing=True` checks the export/issue
    requirement (PDF, send); False checks the lighter create requirement.
    `template` is accepted for future template-aware rules (e.g. a template that
    omits banking) — the default mapping is conservative and correct today."""
    create, export = DOC_ACTION.get(document_type, (None, None))
    action = export if issuing else create
    if action is None:
        return {"allowed": True, "action": None, "missing": [], "code": "OK"}
    return check_action(company, action)


def section_status(company) -> dict:
    out = {}
    for key, (label, fields, anchor) in SECTIONS.items():
        out[key] = {
            "label": label,
            "complete": all(ck(company) for _, _, ck in fields),
            "recommended": key in RECOMMENDED_SECTIONS,
            "settings_url": _settings_url(anchor),
            "missing": [fl for _, fl, ck in fields if not ck(company)],
        }
    return out


def overall_percentage(company) -> int:
    secs = section_status(company)
    return round(sum(1 for s in secs.values() if s["complete"]) / len(secs) * 100) \
        if secs else 100


def status(company) -> dict:
    """Everything web + Flutter need to render setup state (§21)."""
    secs = section_status(company)
    required = [k for k in SECTIONS if k not in RECOMMENDED_SECTIONS]
    req_done = sum(1 for k in required if secs[k]["complete"])
    return {
        "overall_percentage": overall_percentage(company),
        "required_complete": req_done == len(required),
        "items_remaining": len(required) - req_done,
        "sections": secs,
        "actions": {a: {"allowed": can_perform(company, a)} for a in ACTIONS},
        "settings_url": _settings_url(),
    }


# ── Enforcement helpers (kept out of the views so nothing is duplicated) ───────

class CompanySetupRequired(Exception):
    """Raise to block one action. Carries the structured check_action result."""

    def __init__(self, result):
        self.result = result
        super().__init__(result.get("message") or _DEFAULT_MESSAGE)


def require_action(company, action):
    """Raise CompanySetupRequired if `action` isn't allowed yet (else return)."""
    result = check_action(company, action)
    if not result["allowed"]:
        raise CompanySetupRequired(result)
