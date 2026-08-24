"""DRF permission classes for the granular RBAC engine (no `is_admin`).

A view declares `required_perms = {"create": "users.invite", ...}` and/or a
blanket `required_perm = "..."`. Superuser bypasses (handled in has_perm_code).

A requirement may be a single codename OR an iterable of codenames, in which case
holding ANY one of them passes. This lets a field action accept both the
granular field permission and the management umbrella, e.g.
`{"create": ("work.edit", "execution.manage")}`.
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
        if isinstance(codename, (list, tuple, set)):
            return any(user.has_perm_code(c) for c in codename)
        return user.has_perm_code(codename)
