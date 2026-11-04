"""
The website refresh: the member-facing site in the mobile app's look.

Opt-in and reversible. Nothing changes unless ``ui_refresh`` is true for the
request, and then base.html adds one class (``ui-refresh`` on ``<html>``) and
one stylesheet (static/css/refresh.css); a handful of templates (Home,
Committees) swap a section for its app-style version. With it off, every page
renders exactly as before.

Who sees it:

* everyone, when the ``web_refresh`` feature flag is on (Admin v2 → Feature
  Flags, or the app's Site admin). It is in ``FeatureFlag.DISABLED_BY_DEFAULT``
  and seeded off, so a database without the row stays on the old look;
* one browser, with ``?ui=refresh`` on any page (a cookie, so it sticks), and
  ``?ui=classic`` to see the old look while the flag is on. ``?ui=default``
  clears the cookie and follows the flag again. Only CSS and layout change,
  so the preview needs no permission.

Never on Admin v2 ("360", ``/admin-v2/``): its design stays as it is, even
though templates/admin_v2/base.html extends base.html.
"""

COOKIE_NAME = 'parliament_ui'
FLAG_NAME = 'web_refresh'

#: ?ui= value → what the cookie should hold (None: delete it).
_PARAM_VALUES = {'refresh': 'refresh', 'classic': 'classic', 'default': None}
_COOKIE_VALUES = ('refresh', 'classic')
_COOKIE_MAX_AGE = 60 * 60 * 24 * 90

#: Paths that keep their own design whatever the switch says.
EXCLUDED_PATH_PREFIXES = ('/admin-v2/', '/admin/')


def preview_choice(request):
    """'refresh', 'classic' or None (follow the flag) for this browser."""
    pending = getattr(request, '_ui_preview', False)
    if pending is not False:
        return pending
    value = request.COOKIES.get(COOKIE_NAME)
    return value if value in _COOKIE_VALUES else None


def is_excluded(request):
    path = getattr(request, 'path', '') or ''
    if path.startswith(EXCLUDED_PATH_PREFIXES):
        return True
    match = getattr(request, 'resolver_match', None)
    url_name = getattr(match, 'url_name', None) or ''
    return url_name.startswith('admin_v2')


def ui_refresh_for(request, flag_enabled):
    """The decision, given the flag's value (read by the caller, so the
    feature_flags context processor can pass the value it already loaded)."""
    if request is None or is_excluded(request):
        return False
    choice = preview_choice(request)
    if choice == 'refresh':
        return True
    if choice == 'classic':
        return False
    return bool(flag_enabled)


class UIPreviewMiddleware:
    """``?ui=refresh|classic|default`` sets or clears the preview cookie.

    The value applies to the response that carries it too (``_ui_preview``),
    so the page you asked for already shows the choice. Requests without the
    parameter are untouched.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        raw = request.GET.get('ui')
        if raw not in _PARAM_VALUES:
            return self.get_response(request)
        value = _PARAM_VALUES[raw]
        request._ui_preview = value
        response = self.get_response(request)
        if value is None:
            response.delete_cookie(COOKIE_NAME, samesite='Lax')
        else:
            from django.conf import settings
            response.set_cookie(
                COOKIE_NAME, value, max_age=_COOKIE_MAX_AGE, httponly=True,
                samesite='Lax', secure=getattr(settings, 'SESSION_COOKIE_SECURE', False),
            )
        return response
