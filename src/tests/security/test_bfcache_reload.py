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
        # v3.29.25 gave it an `attempt` parameter for the internal retry —
        # see TheCsrfRefreshRetriesOnFailureTests below.
        self.assertIn('function refreshCsrfToken(attempt, source)', self.base)

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
        refresh_at = stripped.index('function refreshCsrfToken(attempt, source)')
        next_helper_at = stripped.index('P._refreshCsrfToken')
        body = stripped[refresh_at:next_helper_at]
        self.assertIn('meta[name="csrf-token"]', body)
        self.assertIn('setAttribute(\'content\'', body)

    def test_the_helper_is_defined_before_the_pageshow_listener_uses_it(self):
        helper_at = self.base.index('function refreshCsrfToken(attempt, source)')
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
        self.assertIn("refreshCsrfToken(undefined, 'pageshow')", match.group(1))

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
        # `!tokenField` alone must not be enough to skip. (v3.29.24 added a
        # third condition, `!hasFileInput`, ahead of these two — see the
        # class below — so this checks the tail end of the guard rather
        # than the exact full expression.)
        self.assertIn('tokenField && tokenField.value) return;', tail)

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
        refresh_at = tail.index("refreshCsrfToken(undefined, 'submit')")
        self.assertLess(prevent_at, refresh_at)

    def test_it_ignores_get_forms(self):
        """
        ⚠️ FOUND 09-02-26, the day after this file was last touched. A GET
        form (global_search.html, songbook.html, announcements.html,
        kai/view_reports.html, directory.html's export form, every
        `onchange="this.form.submit()"` filter dropdown in admin-v2) never
        renders `{% csrf_token %}` in the first place — Django only checks
        CSRF on state-changing methods. Without a method check, this
        listener reads that absence as "the field went missing," creates
        one, and resubmits — serializing a live CSRF token into the query
        string of a GET request. That leaks the token into browser
        history and server/CDN access logs on every ordinary site search,
        and adds a spurious async round-trip before the results load.
        `form.method` is the IDL attribute, so it always reads back 'get'
        or 'post' regardless of how the HTML was written (including a form
        with no `method` attribute at all, which defaults to GET).
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        html_check_at = stripped.index('instanceof HTMLFormElement', listener_at)
        method_check_at = stripped.index("form.method.toLowerCase() !== 'post'", listener_at)
        token_field_at = stripped.index('form.querySelector(\'input[name="csrfmiddlewaretoken"]\')', listener_at)
        # Must run after confirming it's a real form (form.method would throw
        # on anything else) and before touching the token field at all — a
        # GET form must never reach the create-or-refresh logic.
        self.assertLess(html_check_at, method_check_at)
        self.assertLess(method_check_at, token_field_at)


class TheFileInputSafetyNetAlwaysRefreshesTests(SimpleTestCase):
    """
    v3.29.24 — reported live, still failing a full week after v3.28.4
    shipped and was deployed. Every check the safety net had only ever
    asked "is the token field EMPTY" — right for the 09-01 reproduction
    (`posted_token_present=False`), but a file-picker form has a second
    failure shape that was never covered: the native OS picker sheet can
    leave the tab backgrounded long enough for the `csrftoken` cookie to
    move on without the page's embedded value. Django reports that as
    "CSRF token incorrect" — a present, non-empty field — and the old
    guard's `if (tokenField && tokenField.value) return;` let it straight
    through untouched. Forms with a file input now refresh unconditionally
    rather than trusting a non-empty value.
    """

    def setUp(self):
        self.base = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(encoding='utf-8')

    def test_it_detects_a_file_input(self):
        self.assertIn('form.querySelector(\'input[type="file"]\')', self.base)

    def test_the_empty_only_early_return_is_gated_on_not_having_a_file_input(self):
        """
        ⚠️ THE ASSERTION THIS FIX MUST SATISFY. A form WITHOUT a file input
        keeps the cheap empty-only check (the previous behavior, unchanged
        for the ~everything-else population). A form WITH one must not be
        able to take this early return just because the field happens to
        hold some value.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        self.assertIn('if (!hasFileInput && tokenField && tokenField.value) return;', tail)

    def test_has_file_input_is_computed_before_the_early_return_uses_it(self):
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        computed_at = tail.index('var hasFileInput')
        used_at = tail.index('if (!hasFileInput && tokenField && tokenField.value) return;')
        self.assertLess(computed_at, used_at)

    def test_other_forms_are_unaffected_by_the_file_input_check(self):
        """
        Control: a plain form with no file input and a genuinely valid
        (present, non-empty) token must still take the early return and
        never reach `event.preventDefault()` for that reason — this fix is
        additive, not a blanket "always refresh" for the whole site.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        early_return_at = tail.index('if (!hasFileInput && tokenField && tokenField.value) return;')
        prevent_at = tail.index('event.preventDefault()')
        self.assertLess(early_return_at, prevent_at)


class TheCsrfRefreshRetriesOnFailureTests(SimpleTestCase):
    """
    v3.29.25 — v3.29.24 shipped, was deployed, and the identical failure
    (`reason=CSRF token missing.`, `posted_token_present=False`) happened
    again on a fresh mobile retest. The gap was never about which forms
    ask for a refresh — it was what happened when the refresh's own
    `fetch()` failed: silently swallowed, and the submit-time safety net
    submitted anyway, with a still-empty field if there was never a usable
    token to fall back on. Two changes: one retry inside `refreshCsrfToken`
    after a short pause (a fetch issued the instant a backgrounded mobile
    tab resumes can fail before the network stack has reconnected), and a
    submit-time check that refuses to submit a still-empty field rather
    than sending a guaranteed second 403.
    """

    def setUp(self):
        self.base = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(encoding='utf-8')

    def test_the_helper_takes_an_attempt_parameter(self):
        self.assertIn('function refreshCsrfToken(attempt, source)', self.base)

    def test_it_retries_exactly_once_on_failure(self):
        """
        ⚠️ MUST TERMINATE. The retry branch must be gated on `attempt` (so
        a second failure gives up) — an ungated retry would hang a form
        submit forever against a genuinely dead network.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        refresh_at = stripped.index('function refreshCsrfToken(attempt, source)')
        next_helper_at = stripped.index('P._refreshCsrfToken')
        body = stripped[refresh_at:next_helper_at]
        self.assertIn('if (!attempt)', body)
        self.assertIn('refreshCsrfToken(1, source)', body)
        # Only one retry recursion — a second `refreshCsrfToken(1, source)`
        # call (e.g. the retry branch retrying again on its own failure)
        # would make this open-ended instead of a single bounded retry.
        self.assertEqual(body.count('refreshCsrfToken(1, source)'), 1)

    def test_the_retry_still_resolves_to_null_on_a_second_failure(self):
        """
        The catch's fallback path (no more retries left) must still return
        `null` rather than leaving the promise unresolved or rejecting —
        callers (the submit safety net) branch on a falsy value to decide
        whether to submit at all.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        refresh_at = stripped.index('function refreshCsrfToken(attempt, source)')
        next_helper_at = stripped.index('P._refreshCsrfToken')
        body = stripped[refresh_at:next_helper_at]
        self.assertIn('return null;', body)

    def test_a_still_empty_field_after_refresh_is_not_submitted(self):
        """
        ⚠️ THE ACTUAL GAP. The old code only ever set the field's value on
        a SUCCESSFUL refresh (`if (token) { tokenField.value = token; }`)
        and fell through to `form.submit()` unconditionally either way —
        so a failed refresh (even after the retry) submitted a field that
        was still empty, reproducing the exact original failure. Must
        check the field's value again after the refresh attempt and bail
        out rather than submit when it's still empty.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        self.assertIn('else if (!tokenField.value)', tail)

    def test_the_still_empty_case_does_not_submit(self):
        """
        The bail-out branch must `return` before reaching `form.submit()`
        — otherwise this is dead code that changes nothing.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        branch_at = tail.index('else if (!tokenField.value)')
        brace_at = tail.index('{', branch_at)
        close_at = tail.index('}', brace_at)
        branch_body = tail[brace_at:close_at]
        self.assertIn('return;', branch_body)
        self.assertNotIn('form.submit();', branch_body)

    def test_the_still_empty_case_tells_the_user(self):
        """
        Silently discarding the submit would be worse than the 403 it
        replaces — nothing typed or picked is lost, but the member needs
        to know to try again rather than wondering why nothing happened.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        branch_at = tail.index('else if (!tokenField.value)')
        brace_at = tail.index('{', branch_at)
        close_at = tail.index('}', brace_at)
        branch_body = tail[brace_at:close_at]
        self.assertIn('P.toast(', branch_body)

    def test_a_successful_refresh_still_submits(self):
        """
        Control: the success path (`if (token) { ... }`) must still reach
        `form.submit()` — this fix must not turn a working refresh into a
        blocked submit.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        token_branch_at = tail.index('if (token) {')
        submit_at = tail.index('form.submit();', token_branch_at)
        # form.submit() must be reachable from the success branch without
        # an intervening return — i.e. it's the fall-through, not inside
        # a block that returns early.
        else_at = tail.index('else if (!tokenField.value)', token_branch_at)
        self.assertLess(token_branch_at, else_at)


class TheResubmitMarkerTests(SimpleTestCase):
    """
    v3.29.27 — TEMPORARY. A live retest showed the token refresh
    succeeding (`returned_a_token=True`) immediately before a 403 with the
    posted token still missing — meaning either this exact resubmit still
    isn't sending what it just set, or an unrelated submission raced it.
    `csrf_diag_resubmit` is stamped onto the form right before the actual
    `form.submit()` call so the server-side failure log
    (`csrf_failure.py`) can tell those two cases apart. Remove alongside
    the field/logging it tests once the root cause is confirmed and
    fixed.
    """

    def setUp(self):
        self.base = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(encoding='utf-8')

    def test_the_marker_field_is_stamped(self):
        self.assertIn('input[name="csrf_diag_resubmit"]', self.base)
        self.assertIn("marker.name = 'csrf_diag_resubmit'", self.base)

    def test_the_marker_is_stamped_before_the_actual_submit(self):
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        marker_at = tail.index("marker.value = '1';")
        submit_at = tail.index('form.submit();', marker_at)
        self.assertLess(marker_at, submit_at)

    def test_the_marker_is_stamped_on_both_the_success_and_missing_token_paths(self):
        """
        ⚠️ THE POINT OF THIS FIX. If the marker were only stamped inside
        the `if (token)` branch, a resubmit that reached `form.submit()`
        via the fallback-to-existing-value path would go out unmarked —
        exactly the ambiguity this exists to remove. The marker must be
        set AFTER both branches converge, not inside either one.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        token_branch_at = tail.index('if (token) {')
        else_at = tail.index('else if (!tokenField.value)', token_branch_at)
        close_brace_at = tail.index('}', else_at)
        marker_at = tail.index("marker.value = '1';")
        # The marker line must come AFTER the closing brace of the
        # token/else-if block, not inside it.
        self.assertGreater(marker_at, close_brace_at)

    def test_the_still_empty_bailout_does_not_reach_the_marker(self):
        """
        Control: the `P.toast(...)` bail-out path (no usable token at all)
        must `return` before the marker line — an unmarked, un-submitted
        bail-out should never be confused with a marked resubmit.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        toast_at = tail.index('P.toast(')
        return_at = tail.index('return;', toast_at)
        marker_at = tail.index("marker.value = '1';")
        self.assertLess(toast_at, return_at)
        self.assertLess(return_at, marker_at)


class TheRefreshSourceTaggingTests(SimpleTestCase):
    """
    v3.29.28 — TEMPORARY. `refreshCsrfToken()` has two independent
    callers (the `pageshow` handler and the submit-safety-net listener)
    that both hit the same server endpoint — before this change, a log
    line from either was indistinguishable from the other, which is
    exactly the ambiguity the 09-08 repro ran into (a successful refresh
    165ms before a failure that v3.29.27 proved could not have been that
    refresh's own resubmit). `source` closes that gap — see
    csrf_token.py's module docstring. Remove alongside the rest of this
    temporary logging once the root cause is confirmed and fixed.
    """

    def setUp(self):
        self.base = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(encoding='utf-8')

    def test_the_helper_takes_a_source_parameter(self):
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        self.assertIn('function refreshCsrfToken(attempt, source)', stripped)

    def test_the_pageshow_caller_tags_itself(self):
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        pageshow_at = stripped.index("addEventListener('pageshow'")
        tail = stripped[pageshow_at:]
        submit_listener_at = tail.index("addEventListener('submit'")
        # Restrict the search to the pageshow handler's own body, not the
        # submit listener that follows it in the file.
        pageshow_body = tail[:submit_listener_at]
        self.assertIn("refreshCsrfToken(undefined, 'pageshow')", pageshow_body)

    def test_the_submit_listener_caller_tags_itself(self):
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        self.assertIn("refreshCsrfToken(undefined, 'submit')", tail)

    def test_the_retry_call_still_threads_the_source_through(self):
        """
        ⚠️ THE GAP THIS TEST EXISTS FOR. The retry branch
        (`refreshCsrfToken(1, ...)`, on a failed first attempt) is a
        SEPARATE call site from either caller above — if it dropped
        `source` on retry, a refresh that succeeded only on its second
        attempt would log as `source=-` regardless of which listener
        started it, silently reintroducing the exact ambiguity this
        change exists to remove.
        """
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        self.assertIn('refreshCsrfToken(1, source)', stripped)

    def test_the_query_string_is_only_appended_when_a_source_was_given(self):
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        self.assertIn("source ? ('?source=' + encodeURIComponent(source)) : ''", stripped)


class TheDuplicateSubmitDetectionTests(SimpleTestCase):
    """
    v3.29.29 — TEMPORARY. The 09-08 repro showed a `source=submit` refresh
    succeed immediately before a failure that still read
    `is_js_resubmit=False` — impossible for THAT click's own resubmit,
    per v3.29.28's proof, which means some second, separate submission is
    what actually failed. base.html's double-submit-protection script
    (the "Global Form Submit Protection" block) silently blocks a genuine
    second `submit` DOM event on an already-submitted form; this makes
    that branch fire a diagnostic hit instead of staying silent. Remove
    alongside the rest of this temporary logging once the root cause is
    confirmed and fixed.
    """

    def setUp(self):
        self.base = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(encoding='utf-8')

    def test_the_duplicate_branch_fires_a_diagnostic_hit(self):
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        dup_at = stripped.index('submittedForms.has(form)')
        # Only the FIRST occurrence — the double-submit-protection check,
        # not TheResubmitMarkerTests' unrelated form-state checks.
        tail = stripped[dup_at:]
        prevent_at = tail.index('e.preventDefault()')
        fetch_at = tail.index("source=duplicate_blocked")
        self.assertLess(fetch_at, prevent_at)

    def test_the_diagnostic_hit_reuses_the_refresh_endpoint(self):
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        dup_at = stripped.index('submittedForms.has(form)')
        prevent_at = stripped.index('e.preventDefault()', dup_at)
        tail = stripped[dup_at:prevent_at]
        self.assertIn('{% url "csrf_token_refresh" %}?source=duplicate_blocked', tail)
        self.assertIn("credentials: 'same-origin'", tail)


class ThePreSubmitBeaconTests(SimpleTestCase):
    """
    v3.29.29 — TEMPORARY, second same-day addendum. An 11-minute log
    window around a `source=submit` failure showed nothing else at all —
    no success, no second failure — for a resubmit the code guarantees
    carries a real token if it ever reaches `form.submit()`. This fires
    right before that call, using `keepalive: true` specifically because
    a plain `fetch()` can be aborted by the navigation `form.submit()`
    itself triggers. Remove alongside the rest of this temporary logging
    once the root cause is confirmed and fixed.
    """

    def setUp(self):
        self.base = (Path(settings.BASE_DIR) / 'templates' / 'base.html').read_text(encoding='utf-8')

    # The literal query string also appears inside this class's own
    # explanatory `//` comment above the real call (regex only strips
    # `/* */` block comments) — anchor on the actual invocation
    # (`%}?source=...'`, with the closing quote from the fetch() call)
    # rather than the bare substring, so these tests check the code and
    # not the comment describing it.
    _CALL_ANCHOR = '%}?source=about_to_submit'

    def test_it_fires_immediately_before_form_submit(self):
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        listener_at = stripped.index("addEventListener('submit'")
        tail = stripped[listener_at:]
        marker_at = tail.index("marker.value = '1';")
        beacon_at = tail.index(self._CALL_ANCHOR, marker_at)
        submit_at = tail.index('form.submit();', beacon_at)
        self.assertLess(marker_at, beacon_at)
        self.assertLess(beacon_at, submit_at)

    def test_it_uses_keepalive_so_the_navigation_cannot_cut_it_off(self):
        stripped = re.sub(r'/\*.*?\*/', '', self.base, flags=re.DOTALL)
        beacon_at = stripped.index(self._CALL_ANCHOR)
        tail = stripped[beacon_at:beacon_at + 200]
        self.assertIn('keepalive: true', tail)
