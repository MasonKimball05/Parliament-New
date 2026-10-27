"""
Site CSS/JS links must change URL when the file changes (v3.58.1).

nginx serves /static/ with `immutable` and a 30-day expiry (see
src/templatetags/static_version.py), and file names are not hashed. A
`{% static 'css/tailwind.css' %}` link therefore kept every returning visitor
on the old stylesheet for up to a month after a deploy; every CSS fix shipped
late. `{% static_v %}` adds ?v=<hash of the served file>. The onboarding
wizard also pointed at css/output.css, a file that does not exist, so a new
member's first screen had no styles at all.

Vendor files (static/vendor/) never change in place and may use plain static.
"""
import re

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

PLAIN_SITE_ASSET = re.compile(r"\{%\s*static\s+'((?:css|js)/[^']+\.(?:css|js))'\s*%\}")
ANY_ASSET = re.compile(r"\{%\s*static(?:_v)?\s+'((?:css|js|vendor)/[^']+\.(?:css|js))'\s*%\}")


def _templates():
    root = settings.BASE_DIR / 'templates'
    for path in sorted(root.rglob('*.html')):
        yield path.relative_to(root), path.read_text(encoding='utf-8', errors='ignore')


class SiteAssetsAreVersionedTests(SimpleTestCase):

    def test_site_css_and_js_use_static_v(self):
        offenders = [f'{rel}: {m}' for rel, text in _templates() for m in PLAIN_SITE_ASSET.findall(text)]
        self.assertEqual(offenders, [], "Use {% static_v '...' %} for site CSS/JS (nginx caches /static/ "
                                        'as immutable for 30 days):\n  ' + '\n  '.join(offenders))

    def test_every_linked_asset_exists(self):
        missing = [f'{rel}: {m}' for rel, text in _templates() for m in ANY_ASSET.findall(text)
                   if finders.find(m) is None]
        self.assertEqual(missing, [], 'Templates link static files that do not exist:\n  ' + '\n  '.join(missing))
