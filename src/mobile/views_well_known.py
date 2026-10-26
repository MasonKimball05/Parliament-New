"""
``/.well-known/apple-app-site-association`` and ``/.well-known/assetlinks.json``
(mobile plan, Phase 1).

These let iOS universal links / Android app links open site URLs in the app,
and let both platforms offer the site's saved passwords in it (webcredentials
/ get_login_creds). Fetched anonymously by Apple's CDN and Google's verifier,
so: no auth, no redirect, JSON content type, and exempt from the 2FA,
maintenance, lockdown and pledge-page gates (see those middlewares). Each 404s
until its settings are configured.

URLs the app must NOT claim stay on the web: the web-view handoff
(``/m/handoff/``), both admins, the APIs and logout.
"""
import json

from django.conf import settings
from django.http import Http404, HttpResponse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

#: Universal-link paths that must keep opening in the browser.
AASA_EXCLUDED = ['/m/handoff/*', '/admin*', '/api/*', '/logout/*']


def _json(payload):
    return HttpResponse(json.dumps(payload, indent=2), content_type='application/json')


def setting_list(name):
    """
    A comma-separated setting as a list. ``MOBILE_IOS_BUNDLE_ID`` /
    ``MOBILE_ANDROID_PACKAGE`` take several ids (production first, then the
    dev / preview builds' suffixed ids); a single value still works, and a
    list or tuple (e.g. from ``override_settings``) is taken as is.
    """
    value = getattr(settings, name, '') or ''
    items = value.split(',') if isinstance(value, str) else list(value)
    seen = []
    for item in items:
        item = str(item).strip()
        if item and item not in seen:
            seen.append(item)
    return seen


def ios_app_ids():
    """``<team>.<bundle>`` for every configured bundle id ([] until both are set)."""
    team = (getattr(settings, 'MOBILE_IOS_TEAM_ID', '') or '').strip()
    if not team:
        return []
    return [f'{team}.{bundle}' for bundle in setting_list('MOBILE_IOS_BUNDLE_ID')]


@require_GET
@cache_control(public=True, max_age=3600)
def apple_app_site_association(request):
    app_ids = ios_app_ids()
    if not app_ids:
        raise Http404
    components = [{'/': p, 'exclude': True} for p in AASA_EXCLUDED]
    components.append({'/': '*'})
    return _json({
        'applinks': {
            'details': [{'appIDs': app_ids, 'components': components}],
        },
        'webcredentials': {'apps': app_ids},
    })


@require_GET
@cache_control(public=True, max_age=3600)
def assetlinks(request):
    packages = setting_list('MOBILE_ANDROID_PACKAGE')
    fingerprints = list(getattr(settings, 'MOBILE_ANDROID_CERT_SHA256', None) or [])
    if not packages or not fingerprints:
        raise Http404
    # One statement per package, all sharing the signing-cert list (EAS signs
    # every profile with the same keystore unless told otherwise).
    return _json([{
        'relation': [
            'delegate_permission/common.handle_all_urls',
            'delegate_permission/common.get_login_creds',
        ],
        'target': {
            'namespace': 'android_app',
            'package_name': package,
            'sha256_cert_fingerprints': fingerprints,
        },
    } for package in packages])
