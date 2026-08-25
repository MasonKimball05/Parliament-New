"""
Shared form-field rendering.

`{% render_field form.title %}` replaces the label/widget/help-text/errors
block that was previously hand-written per field, per form, per template —
see templates/includes/_form_field.html for the one canonical block. The
field's own widget already carries its CSS class (defined in the form's
Meta.widgets), so this only supplies the surrounding markup.
"""
from django import template

register = template.Library()


@register.inclusion_tag('includes/_form_field.html')
def render_field(field):
    """
    `field` must be a bound field (e.g. `form.title`), not a raw form or a
    field name — only a bound field carries `.errors`, `.help_text`, and
    `.id_for_label`.
    """
    return {'field': field}
