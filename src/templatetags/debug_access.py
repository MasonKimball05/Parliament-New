"""v4.4.15 — whether to draw the maintenance-mode debug panel for this request."""
from django import template
from django.conf import settings

register = template.Library()


@register.filter
def debug_api_allowed(request):
    """
    Same rule as `src.view.debug_panel.admin_required`: DEBUG, or an admin who
    passes the full admin-v2 gate. Only evaluated inside the maintenance banner,
    so it costs nothing on a normal page.
    """
    user = getattr(request, 'user', None)
    if not getattr(user, 'is_authenticated', False) or not getattr(user, 'is_admin', False):
        return False
    if settings.DEBUG:
        return True
    from src.view.admin_v2 import admin_v2_fully_authenticated
    return admin_v2_fully_authenticated(request)
