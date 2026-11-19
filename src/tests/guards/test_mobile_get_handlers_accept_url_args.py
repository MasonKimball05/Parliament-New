"""
v4.4.15 — a mobile view's handlers must accept its route's URL arguments.

The 11-19-26 pentest sweep found `GET officer/excuses/<excuse_id>/` answering 500:
OfficerExcuseReviewView subclasses the list view and inherited its `get(self,
request)`, so DRF called it with `excuse_id=` and it raised TypeError. A GET to
a POST-only route should be a 405, never a crash. This walks every mobile route
and checks each handler the view will dispatch to.
"""
import inspect
import re

from django.test import SimpleTestCase
from django.urls import URLPattern, URLResolver, get_resolver

_ARG = re.compile(r'<(?:\w+:)?(\w+)>')


def _routes():
    def walk(patterns, prefix=''):
        for p in patterns:
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern):
                route = prefix + str(p.pattern)
                cls = getattr(p.callback, 'view_class', None)
                if route.startswith('api/mobile/') and cls is not None:
                    yield route, cls, getattr(p.callback, 'view_initkwargs', {}) or {}
    return list(walk(get_resolver().url_patterns))


class MobileHandlersAcceptUrlArgsTests(SimpleTestCase):
    def test_every_handler_takes_the_route_arguments(self):
        routes = _routes()
        self.assertGreater(len(routes), 100, 'did not find the mobile URLconf')
        broken = []
        for route, cls, initkwargs in routes:
            args = set(_ARG.findall(route))
            if not args:
                continue
            allowed = initkwargs.get('http_method_names', cls.http_method_names)
            for method in allowed:
                if method == 'options':
                    continue
                handler = getattr(cls, method, None)
                if handler is None:
                    continue
                params = inspect.signature(handler).parameters
                if any(p.kind == p.VAR_KEYWORD for p in params.values()):
                    continue
                missing = args - set(params)
                if missing:
                    broken.append(f'{method.upper()} {route} → {cls.__name__}.{method}() lacks {sorted(missing)}')
        self.assertEqual(broken, [], 'Handlers that would raise TypeError (a 500) for their own route:\n  '
                         + '\n  '.join(broken) + '\nSet http_method_names, or give the handler the argument.')
