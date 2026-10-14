"""
src.W007 (v3.45.5) — persistent database connections under ASGI.

The site is served by Daphne (ASGI). Django runs each request's sync code in a
thread made for that request, and a persistent connection belongs to the
thread that opened it. With CONN_MAX_AGE above 0, every request left an open
Postgres connection behind when its thread ended. Measured 10-13-26: 20
members loading 10 pages each left 100 idle backends (~220 MB on a 1 GB
server), then "too many clients" errors. See the comment on CONN_MAX_AGE in
Parliament/settings.py.

The default is now 0, but an older .env may still set DB_CONN_MAX_AGE (an old
RAM guide told it to). Say so at `manage.py check` / `preflight`.
"""
from django.conf import settings
from django.core.checks import Warning as CheckWarning, register


@register()
def no_persistent_connections_under_asgi(app_configs, **kwargs):
    database = settings.DATABASES.get('default', {})
    if 'sqlite' in database.get('ENGINE', ''):
        return []
    age = database.get('CONN_MAX_AGE', 0)
    if age is None or age > 0:
        return [CheckWarning(
            f'CONN_MAX_AGE is {age!r}. Under Daphne (ASGI) each request leaves an '
            f'open Postgres connection behind, which uses memory and can exhaust '
            f'max_connections.',
            hint='Remove DB_CONN_MAX_AGE from .env, or set DB_CONN_MAX_AGE=0, and restart '
                 'parliament-gunicorn.service.',
            id='src.W007',
        )]
    return []
