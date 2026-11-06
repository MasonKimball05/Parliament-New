"""
Admin v2 console (`admin_console_ui`): pin / unpin a page to Favorites.

POST only (it changes the admin's saved preferences). From the sidebar's
star, console.js posts in the background and reads the JSON; without JS the
form submits normally and comes back to the page it was on.
"""
from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from src.admin_console import favorite_names, toggle_favorite
from src.admin_v2_nav import console_item
from src.view.admin_v2 import _safe_referer, require_admin_v2_auth


@require_admin_v2_auth
@require_POST
def admin_v2_console_favorite(request):
    name = request.POST.get('page', '')
    wants_json = request.headers.get('Accept', '').startswith('application/json')
    try:
        pinned = toggle_favorite(request.user, name)
    except ValueError:
        if wants_json:
            return JsonResponse({'ok': False, 'error': 'Unknown page'}, status=400)
        messages.error(request, 'That page cannot be pinned.')
        return redirect(_safe_referer(request, 'admin_v2_dashboard'))
    if wants_json:
        item = console_item(name, favorites=favorite_names(request.user))
        return JsonResponse({'ok': True, 'favorite': pinned, 'page': name,
                             'label': item['label'] if item else name,
                             'url': item['url'] if item else ''})
    return redirect(_safe_referer(request, 'admin_v2_dashboard'))
