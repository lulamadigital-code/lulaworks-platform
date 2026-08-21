"""`{% learn_prompt "quotations" %}` — drops a small, dismissible 'Learn → Apply'
banner into any in-app page, linking to the best published resource for that
Lulaworks feature. Renders nothing when there is no matching content, so it is
always safe to place."""

from django import template

from ..services import prompt_for

register = template.Library()


@register.inclusion_tag("web/_learn_prompt.html")
def learn_prompt(feature):
    return {"resource": prompt_for(feature)}
