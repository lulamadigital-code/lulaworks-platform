"""Carry existing work onto the Module 8 engine.

Three jobs: map the old status/priority vocabularies onto the new 11-state
lifecycle, give every company a default Workspace (so single-team businesses
never see the concept) and seed the default StatusDefinition set that companies
then customise.
"""

from django.db import migrations

# old value → new lifecycle state
STATUS_MAP = {
    "planned": "draft",
    "on_hold": "waiting",
    "awaiting_inspection": "quality_check",
}
PRIORITY_MAP = {"normal": "medium"}

DEFAULT_STATUSES = [
    ("draft", "Draft", "#c4c7d0", "open"),
    ("ready", "Ready", "#579bfc", "open"),
    ("assigned", "Assigned", "#579bfc", "open"),
    ("accepted", "Accepted", "#a25ddc", "open"),
    ("in_progress", "In progress", "#fdab3d", "active"),
    ("waiting", "Waiting", "#fdab3d", "stuck"),
    ("blocked", "Blocked", "#e2445c", "stuck"),
    ("quality_check", "Quality check", "#a25ddc", "active"),
    ("client_signoff", "Client sign-off", "#a25ddc", "active"),
    ("completed", "Completed", "#00c875", "done"),
    ("closed", "Closed", "#676879", "done"),
    ("cancelled", "Cancelled", "#676879", "done"),
]


def forwards(apps, schema_editor):
    Task = apps.get_model("execution", "Task")
    Workspace = apps.get_model("execution", "Workspace")
    StatusDefinition = apps.get_model("execution", "StatusDefinition")
    Company = apps.get_model("identity", "Company")

    for old, new in STATUS_MAP.items():
        Task.objects.filter(status=old).update(status=new)
    for old, new in PRIORITY_MAP.items():
        Task.objects.filter(priority=old).update(priority=new)

    for company in Company.objects.all():
        workspace, _ = Workspace.objects.get_or_create(
            company=company, key="general",
            defaults={"name": "General", "is_default": True, "position": 0},
        )
        Task.objects.filter(company=company, workspace__isnull=True).update(
            workspace=workspace
        )
        for position, (key, label, colour, category) in enumerate(DEFAULT_STATUSES):
            StatusDefinition.objects.get_or_create(
                company=company, key=key,
                defaults={"label": label, "colour": colour,
                          "category": category, "position": position},
            )


def backwards(apps, schema_editor):
    """Best-effort reverse of the vocabulary mapping; seeded rows are left."""
    Task = apps.get_model("execution", "Task")
    for old, new in STATUS_MAP.items():
        Task.objects.filter(status=new).update(status=old)
    for old, new in PRIORITY_MAP.items():
        Task.objects.filter(priority=new).update(priority=old)


class Migration(migrations.Migration):
    dependencies = [
        ("execution", "0003_task_ai_summary_task_completed_at_task_department_and_more"),
        ("identity", "0001_initial"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
