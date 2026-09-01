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

v3.28.4 — "skip the reload" used to mean "do nothing," and that was the bug:
reported live 09-01-26 on a committee-document upload (a required title field
ahead of the file picker — typing it *is* normal use of the form, so the
guard above fires on essentially every real submission on pages shaped like
that one). Skipping the reload now silently refreshes just the token in
place (`refreshCsrfToken()`, `src/view/csrf_token.py`) instead of leaving it
stale, plus an independent submit-time safety net that refreshes the token
if a form is about to submit with an empty `csrfmiddlewaretoken` field,
whatever the cause.

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


class TheCsrfTokenRefreshExistsTests(SimpleTestCase):
    """
    v3.28.4. `refreshCsrfToken()` is what the reload-skip guard calls instead
    of doing nothing, and what the submit-time safety net calls when a
    form's token field is empty. Structural, same reasoning as the class
    above.
    """

    def setUp(self):
        self.base = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(encoding='utf-8')

    def test_the_helper_is_defined(self):
        self.assertIn('function refreshCsrfToken()', self.base)

    def test_it_fetches_the_refresh_endpoint(self):
        self.assertIn("{% url \"csrf_token_refresh\" %}", self.base)

    def test_it_patches_every_hidden_token_field(self):
        self.assertIn('input[name="csrfmiddlewaretoken"]', self.base)

    def test_it_patches_the_meta_tag_too(self):
        """
        `Parliament.post` reads the token from the `<meta>` tag, not a
        hidden form field (`P.csrfToken()`, earlier in this file) — a
        refresh that only patched form fields would leave every
        JS-driven POST still holding the stale value.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        refresh_at = stripped.index('function refreshCsrfToken()')
        next_helper_at = stripped.index('P._refreshCsrfToken')
        body = stripped[refresh_at:next_helper_at]
        self.assertIn('meta[name="csrf-token"]', body)
        self.assertIn('setAttribute(\'content\'', body)

    def test_the_helper_is_defined_before_the_pageshow_listener_uses_it(self):
        helper_at = self.base.index('function refreshCsrfToken()')
        listener_at = self.base.index("addEventListener('pageshow'")
        self.assertLess(helper_at, listener_at)

    def test_the_pageshow_listener_calls_it_instead_of_doing_nothing(self):
        """
        ⚠️ THE ASSERTION v3.26.5 FAILED. The old guard was `if
        (hasUnsavedInput()) { return; }` — a bare return with no side
        effect. This must now call `refreshCsrfToken()` on that branch
        rather than silently leaving the stale token in place.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        match = re.search(
            r'if\s*\(hasUnsavedInput\(\)\)\s*\{([^}]*)\}',
            stripped,
        )
        self.assertIsNotNone(match, 'could not find the hasUnsavedInput() branch in the pageshow listener')
        self.assertIn('refreshCsrfToken()', match.group(1))

    def test_the_reload_branch_is_unchanged(self):
        """Control: the clean-page path still reloads, same as v3.26.5."""
        self.assertIn('window.location.reload();', self.base)


class TheCsrfSubmitSafetyNetExistsTests(SimpleTestCase):
    """
    v3.28.4. Independent of the `pageshow` signal: if a plain `<form>` is
    about to submit with an empty `csrfmiddlewaretoken` field, refresh the
    token first rather than letting the request 403. This is the guard that
    matches what was actually observed live — the failing request's token
    field was reported empty, not merely stale — regardless of which browser
    mechanism produced that.
    """

    def setUp(self):
        self.base = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(encoding='utf-8')

    def test_it_listens_for_submit_on_the_document(self):
        self.assertIn("addEventListener('submit'", self.base)

    def test_it_only_acts_on_form_elements(self):
        self.assertIn('instanceof HTMLFormElement', self.base)

    def test_it_checks_the_token_field_value_before_acting(self):
        self.assertIn('tokenField.value', self.base)

    def test_a_completely_missing_field_is_created_not_skipped(self):
        """
        ⚠️ THE GAP FOUND 09-01-26, HOURS AFTER THIS FILE WAS FIRST WRITTEN.
        `posted_token_present=False` in the security log means the key was
        absent from the POST body — which a browser produces just as
        readily by dropping the hidden `<input>` node entirely as by
        clearing its value. The first version of this guard read `if
        (!tokenField || tokenField.value) return;`, so a MISSING field took
        the exact same early-return as a FILLED-IN one — the guard did
        nothing in precisely the case its own reproduction described. Must
        create the field (`document.createElement('input')`, `type =
        'hidden'`, `name = 'csrfmiddlewaretoken'`) rather than bail out.
        """
        self.assertIn("document.createElement('input')", self.base)
        self.assertIn("tokenField.name = 'csrfmiddlewaretoken'", self.base)
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        # The early-return must require BOTH a present field AND a value —
        # `!tokenField` alone must not be enough to skip.
        self.assertIn('if (tokenField && tokenField.value) return;', tail)

    def test_it_prevents_the_original_submit(self):
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        prevent_at = tail.index('event.preventDefault()')
        self.assertLess(prevent_at, 2000, 'preventDefault() moved unexpectedly far from the submit listener')

    def test_it_resubmits_via_the_form_element_not_requestsubmit(self):
        """
        `form.submit()` does not re-fire the `submit` event per spec —
        `form.requestSubmit()` would, and this listener is registered on
        the document with no way to distinguish "the original attempt" from
        "the resubmit," so using `requestSubmit()` here would infinite-loop
        the moment the refresh failed and the field was still empty.
        """
        self.assertIn('form.submit();', self.base)
        self.assertNotIn('form.requestSubmit()', self.base)

    def test_the_prevent_default_happens_before_the_refresh(self):
        """
        ⚠️ ORDER MATTERS, same reasoning as the reload-vs-guard test above.
        `refreshCsrfToken()` is async; if the original submit weren't
        already prevented before that call starts, the browser could
        submit the request with the still-empty field while the refresh is
        in flight.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        prevent_at = tail.index('event.preventDefault()')
        refresh_at = tail.index('refreshCsrfToken()')
        self.assertLess(prevent_at, refresh_at)
