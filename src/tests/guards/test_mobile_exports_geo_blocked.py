"""
v4.4.15 (pentest F02) — every app view that mirrors a geo-restricted web export
gets the abroad block.

The web's block is GeoRestrictionMiddleware matching RESTRICTED_EXPORT_VIEWS by
url_name, and it can't see a Bearer request's user. The app's copy lives in
MobileAPIView.initial (keyed on `web_page`), so the rule is simply: a view whose
`web_page` is in that set must be a MobileAPIView. This walks the mobile URLconf
so a new export view is covered without anyone remembering to add it here.
"""
from django.test import SimpleTestCase
from django.urls import URLPattern, URLResolver, get_resolver

from src.middleware.geo_restriction import RESTRICTED_EXPORT_VIEWS
from src.mobile.api import MobileAPIView


def _mobile_view_classes():
    def walk(patterns, prefix=''):
        for p in patterns:
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern) and (prefix + str(p.pattern)).startswith('api/mobile/'):
                cls = getattr(p.callback, 'view_class', None) or getattr(p.callback, 'cls', None)
                if cls is not None:
                    yield prefix + str(p.pattern), cls
    return list(walk(get_resolver().url_patterns))


class MobileExportsAreGeoBlockedTests(SimpleTestCase):
    def test_restricted_exports_inherit_the_block(self):
        views = _mobile_view_classes()
        self.assertGreater(len(views), 100, 'did not find the mobile URLconf')
        restricted = [(route, cls) for route, cls in views
                      if getattr(cls, 'web_page', None) in RESTRICTED_EXPORT_VIEWS]
        self.assertTrue(restricted, 'expected at least the activity-log export')
        not_covered = [f'{route} ({cls.__name__})' for route, cls in restricted
                       if not issubclass(cls, MobileAPIView)]
        self.assertEqual(not_covered, [], 'These mirror a geo-restricted export but bypass '
                         'MobileAPIView.initial, so the abroad block never runs:\n  ' + '\n  '.join(not_covered))
