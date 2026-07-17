"""Template context processor — exposes the Golden-Rule flag to every page so the
nav can hide money-only surfaces (the view still enforces it authoritatively),
plus whether a real logo image is present (else the SVG mark is used)."""

from django.contrib.staticfiles import finders


def has_logo_file() -> bool:
    """True once someone drops apps/web/static/web/logo.png (or .svg) into place —
    the header/login then use the real file instead of the recreated SVG mark."""
    return bool(finders.find("web/logo.png") or finders.find("web/logo.svg"))


def logo_static_name() -> str:
    return "web/logo.png" if finders.find("web/logo.png") else "web/logo.svg"


def nav_flags(request):
    user = getattr(request, "user", None)
    can = bool(user and user.is_authenticated and user.has_perm_code("finance.view_money"))
    return {"perms_money": can, "has_logo": has_logo_file(), "logo_static": logo_static_name()}
