"""
Custom error handlers for 400, 403, 404 and 500 pages.

⚠️ v3.51.3 — EVERY HANDLER HERE CLOSES ITS DATABASE CONNECTION WHEN IT'S DONE.

Under ASGI (Daphne), Django renders an error page for an exception that reaches
its outermost async layer with `sync_to_async(response_for_exception,
thread_sensitive=False)` (`django/core/handlers/exception.py`). That runs the
handler on asgiref's shared thread pool, not on the request's own thread, and
the end-of-request `close_old_connections` never sees the connection it
opened. Every unmatched URL is such an exception (`Resolver404`), and so is an
`Http404` / `PermissionDenied` raised by a view.

These pages touch the database: the templates run the context processors
(`feature_flags` re-reads `src_featureflag` + `src_pagetoggle` once its
60-second cache expires) and `request.user` loads the session. So a bot 404
during a quiet minute left an idle PostgreSQL backend in Daphne until the next
restart. Seen on prod 10-21-26: 5 idle connections, each last used by a
`src_pagetoggle` SELECT during a bot 404 (`/robots.txt`, `/.gitignore`,
`/hello.world`). Reproduced on a cloud copy with Daphne + Postgres 16.

`_closes_db_connections` closes them after the response is built, unless a
transaction is open (when the handler runs on the request's own thread inside
`atomic()`, leave it to the normal end-of-request close).
"""
from functools import wraps

from django.db import close_old_connections, connections
from django.shortcuts import render
from django.views import defaults


def _closes_db_connections(handler):
    @wraps(handler)
    def wrapper(request, *args, **kwargs):
        try:
            return handler(request, *args, **kwargs)
        finally:
            if not any(c.in_atomic_block for c in connections.all(initialized_only=True)):
                close_old_connections()
    return wrapper


@_closes_db_connections
def custom_404(request, exception=None):
    """
    Custom 404 page - Page Not Found
    """
    return render(request, '404.html', status=404)


@_closes_db_connections
def custom_500(request):
    """
    Custom 500 page - Server Error
    """
    return render(request, '500.html', status=500)


@_closes_db_connections
def custom_403(request, exception=None):
    """Django's default 403 page, closing its connection (v3.51.3)."""
    return defaults.permission_denied(request, exception)


@_closes_db_connections
def custom_400(request, exception=None):
    """Django's default 400 page, closing its connection (v3.51.3)."""
    return defaults.bad_request(request, exception)
