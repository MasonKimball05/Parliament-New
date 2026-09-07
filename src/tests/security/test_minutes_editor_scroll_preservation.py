"""
v3.29.20 — Mason reported: on the meeting-minutes editor, pressing Enter
while typing scrolled the page back to the top instead of keeping the
user's place.

Root cause: `templates/officer/chapter_minutes_editor.html`'s auto-growing
`<textarea>` resizes on every `input` event by setting `style.height =
'auto'` (collapsing it back to its `rows`-based intrinsic height, fixed
once at creation) and then immediately re-measuring `scrollHeight` and
expanding it back out. On a long minutes document with the caret well
below the fold, that momentary collapse-then-regrow was enough to make
the browser re-anchor page scroll near the top of the (briefly short)
textarea — most visible on Enter, since a newline moves the caret onto a
fresh line and makes the collapse's height delta largest.

Fix: save `window.scrollX`/`window.scrollY` immediately before the two
height assignments and restore them immediately after, so the momentary
collapse never becomes a visible scroll jump.

Shared by both chapter minutes AND committee minutes (`src/view/officer/
chapter_minutes.py` and `src/view/committee/committee_minutes_editor.py`
both render this exact template) — one fix covers both.

These are structural tests (this template has no JS test runner in this
repo) — they assert the mechanism is present and wired around the right
two lines, mirroring `test_bfcache_reload.py::TheBfcacheReloadExistsTests`.
"""
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class TheAutoResizeScrollFixExistsTests(SimpleTestCase):
    def setUp(self):
        self.template = (
            Path(settings.BASE_DIR) / 'templates' / 'officer' / 'chapter_minutes_editor.html'
        ).read_text(encoding='utf-8')

    def test_scroll_position_is_captured_before_the_resize(self):
        self.assertIn('const scrollX = window.scrollX;', self.template)
        self.assertIn('const scrollY = window.scrollY;', self.template)

    def test_scroll_position_is_restored_after_the_resize(self):
        self.assertIn('window.scrollTo(scrollX, scrollY);', self.template)

    def test_capture_happens_before_the_height_collapse_and_restore_happens_after_the_regrow(self):
        """Order matters: capturing after the collapse or restoring before
        the regrow would just re-implement the bug with extra steps."""
        capture_idx = self.template.index('const scrollX = window.scrollX;')
        collapse_idx = self.template.index("this.style.height = 'auto';")
        regrow_idx = self.template.index('this.style.height = this.scrollHeight')
        restore_idx = self.template.index('window.scrollTo(scrollX, scrollY);')

        self.assertLess(capture_idx, collapse_idx)
        self.assertLess(collapse_idx, regrow_idx)
        self.assertLess(regrow_idx, restore_idx)

    def test_only_one_resize_handler_exists(self):
        """If a second auto-resize handler is ever added to this file
        without the same save/restore, the bug comes back through the new
        one. Pin the count so that's a deliberate, noticed change."""
        self.assertEqual(self.template.count("this.style.height = 'auto';"), 1)


class TheCommitteeMinutesEditorSharesTheSameFixTests(SimpleTestCase):
    """Both chapter and committee minutes render the exact same template
    file, so proving the fix once in the template proves it for both
    views — this just confirms that sharing is still true, so a future
    change that forks the template doesn't silently leave one path
    unfixed."""

    def test_committee_minutes_view_renders_the_same_template(self):
        committee_view = (
            Path(settings.BASE_DIR) / 'src' / 'view' / 'committee' / 'committee_minutes_editor.py'
        ).read_text(encoding='utf-8')
        self.assertIn("'officer/chapter_minutes_editor.html'", committee_view)
