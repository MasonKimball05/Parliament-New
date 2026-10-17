"""
The page source base.html produces, for tests that read it (v3.45.6).

v3.45.6 moved ~90 KB of base.html's inline JavaScript into
static/js/base/*.js, loaded with a plain `<script src>` at the same spot, so
the browser caches it instead of every page carrying it. Many tests check
that code by reading base.html's text. `base_html_source()` gives them what
they meant: base.html with each moved file put back inline where it loads.

`moved_script_files()` lists those files, for scanners that walk the
templates and must keep seeing this code.
"""
import re
from pathlib import Path

from django.conf import settings

_BASE = Path(settings.BASE_DIR)
_SCRIPT_SRC = re.compile(r'<script src="\{% static_v \'(js/base/[^\']+\.js)\' %\}"></script>')


def moved_script_files():
    return sorted((_BASE / 'static' / 'js' / 'base').glob('*.js'))


def base_html_source():
    text = (_BASE / 'templates' / 'base.html').read_text(encoding='utf-8')

    def inline(match):
        body = (_BASE / 'static' / match.group(1)).read_text(encoding='utf-8')
        return '<script nonce="{{ request.csp_nonce }}">\n' + body + '</script>'

    return _SCRIPT_SRC.sub(inline, text)
