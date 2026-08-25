"""
v3.26.5 — reload a page restored from the back/forward cache, unless doing so
would discard something the member typed.

Context: 08-25-26, CSRF 403s reported disproportionately on mobile, on login
and other actions. `Cache-Control: no-store` (v3.26.2) does not reliably stop
this — Safari has always bfcached `no-store` pages and Chrome stopped
excluding them in March 2025 — so a page restored via swipe-back or an
app-switch-and-return can hold a CSRF token baked in when the page was
frozen, no longer matching a since-rotated cookie. Fixed client-side in
`base.html`: on `pageshow` with `event.persisted === true`, reload — unless a
text field, textarea, or contenteditable element holds unsaved content, in
which case skip the reload so a half-written chat message or Kai report
draft isn't silently discarded.

These are structural tests (base.html has no JS test runner in this repo) —
they assert the mechanism is present and wired in the right order, mirroring
`test_fetch_error_visibility.py::TheHelperExistsTests`. The guard's actual
truth-table (clean page reloads, dirty page doesn't) is exercised with a
small Node harness during review — see `changelogs/v3.26.5.md` — because
that logic has no Python side to assert on directly.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class TheBfcacheReloadExistsTests(SimpleTestCase):
    def setUp(self):
        self.base = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(encoding='utf-8')

    def test_it_listens_for_pageshow(self):
        self.assertIn("addEventListener('pageshow'", self.base)

    def test_it_checks_persisted_before_doing_anything(self):
        self.assertIn('if (!event.persisted) return;', self.base)

    def test_it_reloads_on_a_clean_restore(self):
        self.assertIn('window.location.reload();', self.base)

    def test_the_guard_runs_before_the_reload(self):
        """
        ⚠️ ORDER MATTERS. The whole point is that a dirty page never reaches
        `location.reload()`. A version that reloaded first and checked after
        would already have discarded the draft it exists to protect.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        guard_at = stripped.index('if (hasUnsavedInput())')
        reload_at = stripped.index('window.location.reload();')
        self.assertLess(
            guard_at, reload_at,
            'hasUnsavedInput() must be checked before location.reload() — '
            'otherwise a dirty page gets reloaded before the guard can stop it.',
        )

    def test_the_guard_is_defined_before_the_listener_uses_it(self):
        helper_at = self.base.index('function hasUnsavedInput()')
        listener_at = self.base.index("addEventListener('pageshow'")
        self.assertLess(helper_at, listener_at)

    def test_the_field_selector_covers_textareas_and_plain_text_inputs(self):
        """
        The two highest-value cases named in the changelog: a chat reply
        (`<textarea id="message-input">` in chat/channel.html) and any
        plain `<input>` with no explicit type (defaults to text per spec).
        """
        self.assertIn('textarea', self.base)
        self.assertIn('input:not([type])', self.base)

    def test_the_guard_also_covers_contenteditable(self):
        self.assertIn('[contenteditable="true"]', self.base)

    def test_no_second_pageshow_listener_was_left_lying_around(self):
        """
        A second listener wouldn't break anything functionally, but it would
        mean this fix was pasted rather than reasoned about — and two
        listeners independently deciding whether to reload is exactly the
        kind of thing that's hard to debug later.
        """
        self.assertEqual(self.base.count("addEventListener('pageshow'"), 1)
