from django import template

from src.admin_v2_nav import nav_groups

register = template.Library()


@register.simple_tag(takes_context=True)
def admin_v2_nav(context):
    """The admin-v2 sidebar groups for the current request (src/admin_v2_nav.py)."""
    return nav_groups(context['request'])
