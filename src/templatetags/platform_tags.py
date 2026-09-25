"""
`{% if user|is_platform_owner %}` — the pinned platform-owner check in
templates (09-25-26). Builtin (settings.TEMPLATES), so no {% load %}.
Replaces the literal `user.user_id == '73'`; see src.permissions.is_platform_owner.
"""
from django import template

from src.permissions import is_platform_owner as _is_platform_owner

register = template.Library()


@register.filter
def is_platform_owner(user):
    return _is_platform_owner(user)
