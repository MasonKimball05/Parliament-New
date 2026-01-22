import re
from django import template
from django.utils.safestring import mark_safe

register = template.Library()

@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)

@register.filter
def render_description(value):
    """Convert markdown bold and line breaks to HTML"""
    if not value:
        return value
    from django.utils.html import escape
    # First escape any HTML to prevent XSS
    result = escape(str(value))
    # Convert **text** to <strong>text</strong>
    result = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', result)
    # Also handle <strong> that was already in the text (now escaped)
    result = result.replace('&lt;strong&gt;', '<strong>')
    result = result.replace('&lt;/strong&gt;', '</strong>')
    # Convert newlines to <br>
    result = result.replace('\n', '<br>')
    return mark_safe(result)

@register.filter
def split(value, arg):
    """Split a string by the given separator"""
    return value.split(arg)

@register.filter
def filter_by_user(queryset, user):
    """Filter a queryset by user"""
    return queryset.filter(user=user)
