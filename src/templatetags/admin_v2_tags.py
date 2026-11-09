from django import template
from django.utils.html import format_html

from src.admin_console_icons import ICON_PATHS
from src.admin_v2_nav import nav_groups

register = template.Library()


@register.simple_tag(takes_context=True)
def admin_v2_nav(context):
    """The admin-v2 sidebar groups for the current request (src/admin_v2_nav.py)."""
    return nav_groups(context['request'])


@register.simple_tag(takes_context=True)
def admin_console_frame(context):
    """The console frame's data (src/admin_console.py `frame`), once per request."""
    from src.admin_console import frame
    return frame(context['request'])


@register.simple_tag
def console_icon(name, css='', filled=False):
    """An inline outline icon (src/admin_console_icons.py), hidden from screen readers.

    {% console_icon "users" %} · {% console_icon "star" "ac-star" filled=True %}"""
    path = ICON_PATHS.get(name) or ICON_PATHS['dot']
    fill = 'currentColor' if filled else 'none'
    return format_html(
        '<svg class="ac-icon {}" viewBox="0 0 24 24" fill="{}" stroke="currentColor" stroke-width="2" '
        'aria-hidden="true" focusable="false"><path stroke-linecap="round" stroke-linejoin="round" d="{}"/></svg>',
        css, fill, path)  # icon paths are constants: digits, letters, spaces, '.', '-' (escaping is a no-op)
