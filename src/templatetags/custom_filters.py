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


@register.filter(needs_autoescape=True)
def linkify(value, autoescape=True):
    """Convert URLs in plain text to clickable links that open in new tabs.

    Safe against XSS: escapes the text first, then wraps detected URLs in <a> tags.
    """
    if not value:
        return value
    from django.utils.html import escape, urlize
    text = escape(str(value)) if autoescape else str(value)
    # urlize detects URLs and emails and wraps them in <a> tags
    linked = urlize(text, nofollow=True, autoescape=False)
    # Add target="_blank" so links open in a new tab, and style them as clickable links
    linked = linked.replace(
        '<a ',
        '<a target="_blank" rel="noopener noreferrer" class="text-blue-600 dark:text-blue-400 underline hover:text-blue-800 dark:hover:text-blue-300" '
    )
    return mark_safe(linked)
