"""Password validators whose rules come from platform settings, so the owner can
tune policy from the Console without a redeploy."""
from django.core.exceptions import ValidationError
from django.utils.translation import ngettext


class DynamicMinimumLengthValidator:
    """Like Django's MinimumLengthValidator, but the minimum is read from
    PlatformSettings.password_min_length (Console → Settings → Security)."""

    def __init__(self, min_length=8):
        self.fallback = min_length

    def _min(self):
        try:
            from apps.administration.models import PlatformSettings
            return int(PlatformSettings.load().password_min_length) or self.fallback
        except Exception:
            return self.fallback

    def validate(self, password, user=None):
        n = self._min()
        if len(password) < n:
            raise ValidationError(
                ngettext(
                    "This password is too short. It must contain at least %(min)d character.",
                    "This password is too short. It must contain at least %(min)d characters.",
                    n) % {"min": n},
                code="password_too_short", params={"min": n})

    def get_help_text(self):
        return ngettext(
            "Your password must contain at least %(min)d character.",
            "Your password must contain at least %(min)d characters.",
            self._min()) % {"min": self._min()}
