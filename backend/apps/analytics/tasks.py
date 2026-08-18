from celery import shared_task


@shared_task(ignore_result=True)
def record_event(payload):
    """Persist one analytics event off the request path."""
    from .services import _write
    try:
        _write(payload)
    except Exception:  # noqa: BLE001
        pass
