"""
v3.45.6 — base.html's JavaScript lives in static files, loaded where it used to sit.

WHY. base.html is rendered for every page, and it carried 94 KB of inline
JavaScript in 15 blocks: even a 404 page was 91 KB. Every request built that
string again, and every page view downloaded it again. Measured 10-13-26
(Mason: the 1 GB server idles at 70-80%): the largest pages peak at 2-8 MB of
memory each while rendering, and a burst of 20 members at once is 20 of them.
The code now sits in static/js/base/*.js. The browser downloads each file
once and caches it.

Each file is loaded with a plain, synchronous `<script src>` at the same
place the inline block was, so it runs at the same moment as before. That
matters: pages call `Parliament.post()` and `Parliament.toast()` from their
own inline scripts while the page is still loading.

Three small blocks stay inline because they use template values (the theme,
the user's role flags, the chat-unread URL). The csrf-refresh URL that
core.js needs is passed in `window.PARLIAMENT_CONFIG`, set just before it.
"""
import re
from pathlib import Path

from django.conf import settings
from django.template import Context, Template
from django.test import SimpleTestCase, override_settings

from src.tests.base_template_source import base_html_source, moved_script_files

BASE = Path(settings.BASE_DIR)
SRC = re.compile(r'<script src="\{% static_v \'js/base/([^\']+\.js)\' %\}"></script>')


def base():
    return (BASE / 'templates' / 'base.html').read_text(encoding='utf-8')


class MovedScriptsTests(SimpleTestCase):

    def test_every_moved_file_is_loaded_exactly_once(self):
        loaded = SRC.findall(base())
        files = [p.name for p in moved_script_files()]
        self.assertEqual(sorted(loaded), sorted(files))
        self.assertEqual(len(loaded), len(set(loaded)))
        self.assertGreaterEqual(len(files), 10)

    def test_base_html_stays_small(self):
        self.assertLess(len(base().encode('utf-8')), 80 * 1024)

    def test_scripts_load_synchronously(self):
        """`defer`/`async` would run them after the page's own scripts.

        Only the files moved out of base.html (js/base/). Alpine itself is
        loaded with `defer` on purpose (v3.45.7), as Alpine's docs require.
        """
        for tag in re.findall(r'<script src="\{% static_v \'js/base/[^>]*>', base()):
            self.assertNotIn('defer', tag)
            self.assertNotIn('async', tag)

    def test_core_runs_in_the_head_after_its_config(self):
        text = base()
        config = text.index('window.PARLIAMENT_CONFIG = {csrfRefreshUrl:')
        core = text.index("static_v 'js/base/core.js'")
        self.assertLess(config, core)
        self.assertLess(core, text.index('</head>'))
        self.assertIn("{% url 'csrf_token_refresh' %}", text[config:core])

    def test_the_moved_files_hold_no_template_syntax(self):
        """They are served as-is, so a `{{ }}` would reach the browser literally."""
        for path in moved_script_files():
            code = re.sub(r'/\*.*?\*/', '', path.read_text(encoding='utf-8'), flags=re.S)
            code = re.sub(r'^\s*//.*$', '', code, flags=re.M)
            self.assertNotRegex(code, r'\{\{|\{%', path.name)

    def test_the_reconstructed_source_still_defines_the_helpers(self):
        text = base_html_source()
        self.assertIn('P.post = function', text)
        self.assertIn('P.toast = function', text)


class StaticVersionTagTests(SimpleTestCase):

    def setUp(self):
        from src.templatetags import static_version
        static_version._versions.clear()
        self.addCleanup(static_version._versions.clear)
        # An empty STATIC_ROOT, so a developer's old collectstatic output
        # cannot decide these tests.
        import tempfile
        self.static_root = tempfile.mkdtemp(prefix='parliament-static-root-')
        override = override_settings(STATIC_ROOT=self.static_root)
        override.enable()
        self.addCleanup(override.disable)

    def render(self, path):
        return Template("{% load static_version %}{% static_v '" + path + "' %}").render(Context())

    def test_the_url_carries_the_files_hash(self):
        import hashlib
        url = self.render('js/base/core.js')
        digest = hashlib.sha256((BASE / 'static' / 'js' / 'base' / 'core.js').read_bytes()).hexdigest()[:12]
        self.assertTrue(url.endswith(f'js/base/core.js?v={digest}'), url)

    def test_a_missing_file_gets_the_plain_url(self):
        self.assertTrue(self.render('js/base/no-such-file.js').endswith('js/base/no-such-file.js'))

    def test_production_hashes_the_collected_copy(self):
        """v3.45.7: the url follows the file nginx serves (STATIC_ROOT), not the source."""
        import hashlib
        collected = Path(self.static_root) / 'js' / 'base' / 'core.js'
        collected.parent.mkdir(parents=True)
        collected.write_bytes(b'// an older core.js, not yet replaced by collectstatic')
        digest = hashlib.sha256(collected.read_bytes()).hexdigest()[:12]
        self.assertTrue(self.render('js/base/core.js').endswith(f'?v={digest}'))

    @override_settings(DEBUG=True)
    def test_debug_hashes_the_source(self):
        import hashlib
        collected = Path(self.static_root) / 'js' / 'base' / 'core.js'
        collected.parent.mkdir(parents=True)
        collected.write_bytes(b'// stale')
        digest = hashlib.sha256((BASE / 'static' / 'js' / 'base' / 'core.js').read_bytes()).hexdigest()[:12]
        self.assertTrue(self.render('js/base/core.js').endswith(f'?v={digest}'))
