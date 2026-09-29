"""
PWA assets rendered from the chapter config (multi-chapter phase 2, 09-27-26).

These used to be three static files — `static/manifest.json`,
`static/js/service-worker.js` and `static/offline.html` — and a static file
cannot run `{% chapter %}`, so each carried the original chapter's name and
crest as literals. Now:

  GET /manifest.webmanifest  web_manifest    — app name, colours, crest icons
  GET /service-worker.js     service_worker  — push handler + offline fallback

There is deliberately NO separate offline URL. The offline page is rendered
here and EMBEDDED in the service worker as a string, and the worker answers a
failed navigation with `new Response(OFFLINE_HTML)`. The old design cached
`/static/offline.html`, which worked because /static/ is exempt from every
middleware. A Django route is not: during maintenance or lockdown, or for a
session that is still pending 2FA, `cache.addAll()` would have stored a
redirect, and a redirected response cannot answer a navigation. Embedding the
page removes that fetch — and that failure mode — altogether.

Both views are anonymous on purpose: browsers fetch the manifest WITHOUT
cookies (no `crossorigin="use-credentials"` on the <link>), and neither asset
contains anything that is not already on the public landing page.
"""
import json

from django.http import HttpResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_GET

from src.chapter import get_chapter

# Bump when the worker's cached assets change; `activate` deletes every other
# `parliament-offline-*` cache. v1 = the static-file era (cached offline.html).
OFFLINE_CACHE = 'parliament-offline-v2'


@require_GET
def web_manifest(request):
    chapter = get_chapter(request)
    icon = chapter.crest_url
    manifest = {
        'name': f'Parliament — {chapter.chapter_name}',
        'short_name': 'Parliament',
        'description': f'{chapter.chapter_name} Chapter Management System',
        'start_url': '/home/',
        'scope': '/',
        'display': 'standalone',
        'display_override': ['window-controls-overlay', 'standalone'],
        'background_color': chapter.primary_color,
        'theme_color': chapter.primary_color,
        'orientation': 'any',
        'icons': [
            {'src': icon, 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
            {'src': icon, 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
            {'src': icon, 'sizes': '1024x1024', 'type': 'image/png', 'purpose': 'maskable'},
        ],
        'categories': ['productivity'],
        'lang': 'en-US',
    }
    response = HttpResponse(json.dumps(manifest, ensure_ascii=False, indent=2),
                            content_type='application/manifest+json; charset=utf-8')
    response['Cache-Control'] = 'public, max-age=3600'
    return response


@require_GET
def service_worker(request):
    """
    Serve the service worker from the ROOT path so the browser grants it scope
    over the whole site (a worker at /static/js/ could only control
    /static/js/). `Service-Worker-Allowed: /` is belt and braces.
    """
    offline_html = render_to_string('pwa/offline.html', request=request)
    body = render_to_string('pwa/service-worker.js', {
        'icon_url': get_chapter(request).crest_url,
        'offline_cache': OFFLINE_CACHE,
        'offline_html': offline_html,
    }, request=request)
    response = HttpResponse(body, content_type='application/javascript; charset=utf-8')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache'
    return response
