"""Seed the Tool + Template catalogues from the code-defined specs, so the
now-editable catalogues start populated with the existing free tools and
templates. Idempotent (get_or_create by slug) and reversible (no-op)."""
from django.db import migrations


def seed(apps, schema_editor):
    Tool = apps.get_model("education", "Tool")
    Template = apps.get_model("education", "Template")
    from apps.education.templates_lib import TEMPLATES
    from apps.education.tools import TOOLS

    for order, (slug, s) in enumerate(TOOLS.items(), start=1):
        inputs = [{"name": f.name, "label": f.label, "kind": f.kind,
                   "default": f.default, "help": f.help,
                   "choices": [list(c) for c in f.choices]} for f in s.inputs]
        Tool.objects.get_or_create(slug=slug, defaults={
            "title": s.title, "summary": s.summary, "category": s.category,
            "related_feature": s.related_feature, "icon": s.icon,
            "problem": s.problem, "explainer": s.explainer, "inputs": inputs,
            "cta_label": s.cta_label, "cta_url": s.cta_url,
            "compute_key": slug, "status": "published", "order": order * 10})

    for order, (slug, s) in enumerate(TEMPLATES.items(), start=1):
        Template.objects.get_or_create(slug=slug, defaults={
            "title": s.title, "summary": s.summary, "kind": s.kind,
            "category": s.category, "related_feature": s.related_feature,
            "icon": s.icon, "problem": s.problem, "cta_label": s.cta_label,
            "cta_url": s.cta_url, "includes": list(s.includes),
            "items": list(s.items), "samples": list(s.samples),
            "status": "published", "order": order * 10})


def unseed(apps, schema_editor):
    pass  # keep any admin edits on reverse


class Migration(migrations.Migration):
    dependencies = [("education", "0004_template_tool")]
    operations = [migrations.RunPython(seed, unseed)]
