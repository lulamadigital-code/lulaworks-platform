"""Template context processor — exposes the Golden-Rule flag to every page so the
nav can hide money-only surfaces (the view still enforces it authoritatively)."""


def nav_flags(request):
    user = getattr(request, "user", None)
    can = bool(user and user.is_authenticated and user.has_perm_code("finance.view_money"))
    return {"perms_money": can}
