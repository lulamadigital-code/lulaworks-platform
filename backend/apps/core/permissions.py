"""DRF permission classes for the granular RBAC engine (no `is_admin`).

A view declares `required_perms = {"create": "users.invite", ...}` and/or a
blanket `required_perm = "..."`. Superuser bypasses (handled in has_perm_code).
"""

from rest_framework.permissions import BasePermission


class HasPermission(BasePermission):
    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated):
            return False
        codename = None
        per_action = getattr(view, "required_perms", None)
        if per_action:
            codename = per_action.get(getattr(view, "action", None))
        if codename is None:
            codename = getattr(view, "required_perm", None)
        if codename is None:
            return True  # authenticated is enough for this view
        return user.has_perm_code(codename)
