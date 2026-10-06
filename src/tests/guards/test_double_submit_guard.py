"""
v3.44.3 — base.html's double-submit guard must not eat the pressed button.

WHY THIS EXISTS. The guard used to disable the form's first submit button
inside the `submit` event. A browser builds the POST after that event and
leaves disabled controls out, so a first button carrying `name=`/`value=`
never reached the server:

- Education dashboard, absence requests: "Approve & excuse" posted no
  `decision` and the view answered 400 "Unknown decision". "Deny" worked.
- Service event attendance: "Save Attendance" posted no `action`, so nothing
  was saved and the page reloaded with no message. "Finalize" worked.
- v3.41.2's resolution Save button (fixed there with a hidden input).

The view tests post the field directly, so none of them could see it. These
tests cannot run a browser either; they pin the two properties of the script
that the browser check (10-04-26 auto-run report) relied on.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

MARKER = '<!-- Global Form Submit Protection'


def _guard_script():
    text = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(encoding='utf-8')
    start = text.index(MARKER)
    return text[start:text.index('</script>', start)]


def _strip_js_comments(script):
    return re.sub(r'^\s*//.*$', '', script, flags=re.MULTILINE)


class DoubleSubmitGuardKeepsTheSubmitterTests(SimpleTestCase):

    def test_the_guard_is_where_this_test_expects_it(self):
        """The control: every assertion below reads this block."""
        script = _guard_script()
        self.assertIn("addEventListener('submit'", script)
        self.assertIn('submittedForms', script)

    def test_the_button_is_disabled_after_the_event_not_during_it(self):
        script = _strip_js_comments(_guard_script())
        disable = script.index('submitBtn.disabled = true')
        deferred = script.rfind('setTimeout(function()', 0, disable)
        self.assertNotEqual(
            deferred, -1,
            'base.html disables the submit button synchronously inside the '
            'submit event. The browser then drops that button\'s name/value '
            'from the POST. Disable it inside setTimeout(..., 0).',
        )
        between = script[deferred:disable]
        self.assertNotIn(
            '}, ', between,
            'The setTimeout before `submitBtn.disabled = true` closes before '
            'the disable, so the disable is synchronous again.',
        )

    def test_the_guard_acts_on_the_button_that_was_pressed(self):
        """Otherwise "Deny" shows its spinner on "Approve"."""
        self.assertIn('e.submitter', _strip_js_comments(_guard_script()))
