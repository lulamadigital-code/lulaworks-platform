"""Client-side event beacon. Accepts small JSON events from the browser, keeps
an anonymous id cookie, and records via the central track() service."""
import json

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .services import ALLOWED_CLIENT_EVENTS, track


@csrf_exempt
@require_POST
def collect(request):
    try:
        data = json.loads(request.body.decode() or "{}")
    except Exception:  # noqa: BLE001
        return JsonResponse({"ok": False}, status=400)

    name = (data.get("event") or "").strip()
    if name not in ALLOWED_CLIENT_EVENTS:
        return JsonResponse({"ok": False, "error": "event not allowed"}, status=400)

    track(name, request=request, path=(data.get("path") or "")[:300],
          module=(data.get("module") or "")[:40], feature=(data.get("feature") or "")[:64],
          metadata=data.get("meta") or {})

    resp = HttpResponse(status=204)
    # Ensure a durable anonymous id for funnel/session stitching (not personal).
    if not request.COOKIES.get("lw_aid"):
        import uuid
        resp.set_cookie("lw_aid", uuid.uuid4().hex, max_age=60 * 60 * 24 * 365,
                        samesite="Lax")
    return resp
