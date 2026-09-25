"""
`{% chapter "field" %}` — the chapter's identity in any template (09-25-26).

Registered as a template BUILTIN in settings.TEMPLATES, so it needs no
{% load %} and works in emails rendered with render_to_string (which run
without a request or context processors). See src/chapter.py.

A misspelled field is a TemplateSyntaxError at render time rather than a
silent empty string — the empty string is how a crest or a school name would
disappear from a page with nobody noticing.
"""
from django import template

from src.chapter import PUBLIC_ATTRS, get_chapter

register = template.Library()


@register.simple_tag(takes_context=True)
def chapter(context, field):
    if field not in PUBLIC_ATTRS:
        raise template.TemplateSyntaxError(
            f'{{% chapter %}}: unknown field {field!r}. Known: {", ".join(sorted(PUBLIC_ATTRS))}'
        )
    return getattr(get_chapter(context.get('request')), field)
