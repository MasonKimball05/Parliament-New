/* static/js/base/core.js — moved out of templates/base.html in v3.45.6.
 * Parliament.post / CSRF token refresh and auto-fill / fetch error toasts. Runs in <head>, before any page script.
 * Loaded with a plain <script src> at the same place in the page the inline
 * block used to be, so it runs at the same moment as before. base.html is
 * rendered for every page; this way the browser downloads this once and
 * caches it (see src/templatetags/static_version.py for the ?v= part). */
    /*
     * Parliament.post — the one way to send a state-changing request (v3.20.1).
     *
     * ⚠️ WHY THIS EXISTS. v3.20.0 found that every interactive control on the
     * education dashboard had been answering 403 since the page was written,
     * and nobody could tell, because every handler caught the rejection and
     * only logged it to the console. The page just did nothing. The same shape
     * sat on the CSP-violations page. A silent failure is worse than a crash:
     * a crash gets reported on day one.
     *
     * (This comment describes that pattern rather than quoting it. The first
     * draft spelled it out and `src/test_fetch_error_visibility.py` counted the
     * comment as an instance — the third time in one day that writing a rule
     * down created one.)
     *
     * Two jobs, and they are the two halves of that bug:
     *
     *   1. ONE PLACE KNOWS WHERE THE CSRF TOKEN COMES FROM. Three templates had
     *      each written their own token read, two of them against a cookie that
     *      `CSRF_COOKIE_HTTPONLY = True` makes unreadable. A helper cannot be
     *      copied wrong.
     *   2. A FAILED REQUEST IS VISIBLE. Non-2xx raises, and the rejection is
     *      shown to the user as a toast unless the caller opts out.
     *
     * Defined in <head>, deliberately: page scripts live inside the content
     * block, which base.html renders BEFORE its own later script blocks — a
     * helper defined further down would not exist yet. Nothing here touches
     * the DOM at parse time; the toast container is created on first use.
     *
     * ⚠️ And do not name that block literally in a comment here. Django's
     * template engine parses tags inside JS comments perfectly happily, so the
     * first draft of this note created a SECOND content block and took the
     * whole site down with `'block' tag with name 'content' appears more than
     * once`. Fifth time in one day that writing a rule down produced an
     * instance of it — and the first that broke something.
     */
    window.Parliament = window.Parliament || {};

    (function(P) {
        function csrfToken() {
            var meta = document.querySelector('meta[name="csrf-token"]');
            return meta ? meta.getAttribute('content') : '';
        }
        P.csrfToken = csrfToken;

        function toastHost() {
            var host = document.getElementById('parliament-toasts');
            if (!host) {
                host = document.createElement('div');
                host.id = 'parliament-toasts';
                host.setAttribute('role', 'status');
                host.setAttribute('aria-live', 'polite');
                host.className = 'fixed bottom-4 right-4 z-[100] flex flex-col gap-2 max-w-sm';
                document.body.appendChild(host);
            }
            return host;
        }

        /* `kind` is 'success' | 'error' | 'warning' | 'info' — the same
         * four tones the full-page flash-message partial further down
         * this file already uses for `message.tags`, so a toasted
         * message and that same message's full-page rendering never
         * disagree about how alarmed the member should be. Anything
         * else (an unrecognized tag, or none passed) falls back to the
         * 'info' treatment rather than 'error' — an unknown level should
         * read as neutral, not alarming. Everything but 'success'
         * persists longer, because the whole point of a warning or an
         * info notice — same as an error — is that somebody reads it.
         *
         * ⚠️ And do not name that partial's own loop tag literally in a
         * comment here, same warning as the one a few lines up — this
         * exact mistake was made and caught while writing this comment. */
        var TOAST_STYLES = {
            success: 'bg-green-600 text-white',
            error: 'bg-red-600 text-white',
            warning: 'bg-yellow-600 text-white',
            info: 'bg-blue-600 text-white',
        };
        P.toast = function(message, kind) {
            var host = toastHost();
            var el = document.createElement('div');
            el.className =
                'rounded-lg px-4 py-3 text-sm shadow-lg transition-opacity duration-300 ' +
                (TOAST_STYLES[kind] || TOAST_STYLES.info);
            el.textContent = message;
            host.appendChild(el);
            setTimeout(function() {
                el.style.opacity = '0';
                setTimeout(function() { el.remove(); }, 300);
            }, kind === 'success' ? 3000 : 8000);
            return el;
        };

        /*
         * POST `body` (FormData, URLSearchParams, a plain object, or nothing)
         * to `url`. Resolves with the parsed JSON body, or with `{}` when the
         * response has none.
         *
         * REJECTS on any non-2xx, and shows the reason. Options:
         *   quiet: true   — do not toast (the caller is rendering its own error)
         *
         * The error message prefers the server's own `error` field, because
         * these endpoints already return `{'error': '...'}` on a 400 and that
         * text is written for a human.
         */
        P.post = function(url, body, options) {
            options = options || {};
            var payload = body;
            if (body && !(body instanceof FormData) && !(body instanceof URLSearchParams)) {
                payload = new FormData();
                Object.keys(body).forEach(function(key) {
                    var value = body[key];
                    if (Array.isArray(value)) {
                        value.forEach(function(v) { payload.append(key, v); });
                    } else {
                        payload.append(key, value);
                    }
                });
            }

            return fetch(url, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrfToken(),
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body: payload,
            }).then(function(response) {
                return response.text().then(function(text) {
                    var data = {};
                    try { data = text ? JSON.parse(text) : {}; } catch (e) { /* not JSON */ }

                    if (response.ok) { return data; }

                    /* ⚠️ 403 is called out by name. It is what a missing or
                     * wrong CSRF token produces, it is the failure that hid for
                     * months, and "Forbidden" alone sends the reader looking
                     * for a permissions problem that is not there. */
                    var message = data.error
                        || (response.status === 403
                            ? 'Refused by the server (403). If this keeps happening, reload the page — your session may have expired.'
                            : 'Something went wrong (' + response.status + '). Nothing was saved.');
                    var error = new Error(message);
                    error.status = response.status;
                    error.data = data;
                    throw error;
                });
            }).catch(function(error) {
                /* A network failure has no response, so give it words too. */
                if (!error.status) {
                    error.message = error.message || 'Could not reach the server. Nothing was saved.';
                }
                if (!options.quiet) { P.toast(error.message, 'error'); }
                throw error;
            });
        };

        /*
         * v3.26.5 — reload a page restored from the back/forward cache.
         *
         * WHY: reported 08-25-26 as CSRF 403s hitting mobile members
         * disproportionately, on login and on other actions. `Cache-Control:
         * no-store` (v3.26.2) does not reliably prevent this — Safari has
         * always bfcached `no-store` pages, and Chrome stopped excluding
         * them in March 2025 — so a page can come back from a swipe-back or
         * an app-switch-and-return holding a CSRF token that was baked in
         * when the page was frozen, no longer matching a since-rotated
         * cookie (login rotates it). This is disproportionately a mobile
         * bug because swipe-back / backgrounding-and-returning is the
         * normal way a phone is used, not an edge case.
         *
         * Fixed client-side, because bfcache eligibility is the browser's
         * call regardless of server headers. `pageshow`'s `persisted` flag
         * is true exactly when the browser restored a frozen page instead of
         * loading fresh — reloading then re-renders from the server, which
         * is the only fix that reaches every place this app embeds a token:
         * the meta tag, every `{% csrf_token %}` hidden field, AND the ~40
         * templates (chat, Kai, slating, admin-v2, profile, calendar…) that
         * bake the token into a JS constant at render time and don't read
         * this meta tag at all — without touching each one individually.
         *
         * GUARDED against wiping unsaved typing. bfcache's other job is
         * preserving exactly that — a half-written chat message or Kai
         * report draft — across a back-navigation, and silently discarding
         * it to fix a *different* staleness bug would be a worse bug than
         * the one being fixed. If a text field, textarea, or contenteditable
         * element holds content that differs from what the server rendered,
         * skip the reload — but see v3.28.4 below, because "skip" used to
         * mean "do nothing," and that was the bug.
         */
        function hasUnsavedInput() {
            var fields = document.querySelectorAll(
                'input[type="text"], input[type="search"], input[type="email"], ' +
                'input[type="tel"], input[type="url"], input[type="number"], ' +
                'input:not([type]), textarea'
            );
            for (var i = 0; i < fields.length; i++) {
                if (fields[i].value !== fields[i].defaultValue) return true;
            }
            var editable = document.querySelectorAll('[contenteditable="true"]');
            for (var j = 0; j < editable.length; j++) {
                if (editable[j].textContent.trim() !== '') return true;
            }
            return false;
        }
        P._hasUnsavedInput = hasUnsavedInput; // exposed for tests only

        /*
         * v3.28.4 — silently refresh the token in place, instead of doing
         * nothing, whenever the reload above is skipped.
         *
         * WHY: reported live 09-01-26 — a committee-document upload (a
         * plain `<form>` with a REQUIRED title field ahead of the file
         * picker) 403'd with "CSRF token missing" for a mobile member.
         * That page is exactly the shape `hasUnsavedInput()` was written
         * to protect: typing the required title *is* normal use of the
         * form, so the reload-skip guard fires on essentially every real
         * submission, and the "rare case" the v3.26.5 comment above
         * accepted as a known residual risk turned out not to be rare at
         * all on upload-style pages. Confirmed at least 17 templates in
         * this codebase have a file input; this shape isn't unique to the
         * committee-document page.
         *
         * FIX: fetch a fresh token from `/internal/csrf-token/`
         * (`src/view/csrf_token.py`) and patch it into every
         * `csrfmiddlewaretoken` hidden field and the `<meta>` tag on the
         * CURRENT DOM — no navigation, so nothing typed or selected is
         * touched. This is strictly better than the reload it replaces for
         * plain `<form>` submissions. It does NOT reach the ~40 templates
         * that bake the token into a JS constant at render time rather
         * than reading `Parliament.csrfToken()`/the meta tag — those still
         * only get a fresh token when the reload path runs (no unsaved
         * input), same as before this change. Converting them is the
         * pre-existing, tracked, too-large-to-do-at-once population
         * `test_fetch_error_visibility.py`'s ratchet already covers; this
         * fix doesn't grow or shrink it.
         *
         * Also used as a submit-time safety net, below: whatever the exact
         * browser mechanism that leaves a token empty (bfcache holding a
         * stale value, or — per the reproduction above, where the CSRF log
         * recorded the token as literally ABSENT from the POST body rather
         * than merely wrong — some other iOS Safari form-state-restoration
         * quirk after the native file-picker sheet), checking the field's
         * actual value right before submission and fixing it then is the
         * one guard that's correct regardless of which of those it is.
         *
         * v3.29.24 — that safety net's own check ("is the field EMPTY")
         * turned out not to be the whole story either. Reported live still
         * failing a full week after the above shipped and was deployed —
         * same page, same mobile member population. The field wasn't
         * empty this time; it held a value that no longer matched the
         * `csrftoken` cookie, which Django reports as "CSRF token
         * incorrect" rather than "missing" — a value the empty-check
         * treats as fine. The native file-picker sheet is what makes this
         * plausible here specifically: it can leave the tab backgrounded
         * for an unpredictable stretch (browsing Photos, searching Files),
         * long enough for the cookie behind the page's embedded token to
         * have moved on. So: a form with a file input now refreshes
         * unconditionally before submit rather than trusting a non-empty
         * value; every other form keeps the cheaper empty-only check,
         * since nothing else on this site plausibly backgrounds the tab
         * this way.
         *
         * v3.29.25 — v3.29.24 shipped, was deployed, and the SAME failure
         * (`reason=CSRF token missing.`, `posted_token_present=False`)
         * still happened on a fresh mobile retest. Root cause was never
         * "which forms ask for a refresh" at all — it was what happens
         * when the refresh's own `fetch()` call fails. The instant a
         * backgrounded mobile tab resumes, a fetch issued right then can
         * fail before the network stack has actually reconnected. Before
         * this, that failure was silently swallowed (`.catch(() => null)`)
         * and the submit-time safety net below submitted anyway — with a
         * still-empty field if there had never been a usable token to fall
         * back on, i.e. exactly the original failure, immediately, again.
         * Two changes: one retry here after a short pause, so a fetch that
         * fails only because the network wasn't back up yet gets a second
         * chance; and below, if the field is STILL empty after that, the
         * submit-time guard now stops and says so instead of submitting
         * into a guaranteed second 403.
         */
        function refreshCsrfToken(attempt) {
            return fetch(window.PARLIAMENT_CONFIG.csrfRefreshUrl, { credentials: 'same-origin' })
                .then(function(response) { return response.ok ? response.json() : null; })
                .then(function(data) {
                    if (!data || !data.csrfToken) { throw new Error('no token in response'); }
                    document.querySelectorAll('input[name="csrfmiddlewaretoken"]').forEach(function(field) {
                        field.value = data.csrfToken;
                    });
                    var meta = document.querySelector('meta[name="csrf-token"]');
                    if (meta) { meta.setAttribute('content', data.csrfToken); }
                    return data.csrfToken;
                })
                .catch(function() {
                    // ONE retry only — this must terminate. The delay is
                    // long enough to matter for "network still reconnecting"
                    // without meaningfully slowing the common (already
                    // failing) path down further.
                    if (!attempt) {
                        return new Promise(function(resolve) {
                            setTimeout(function() { resolve(refreshCsrfToken(1)); }, 400);
                        });
                    }
                    return null; // best-effort; caller decides what "still nothing" means
                });
        }
        P._refreshCsrfToken = refreshCsrfToken; // exposed for tests only

        window.addEventListener('pageshow', function(event) {
            if (!event.persisted) return;
            if (hasUnsavedInput()) { refreshCsrfToken(); return; }
            window.location.reload();
        });

        /*
         * Safety net, independent of the `pageshow` signal above: if a
         * plain `<form>` submit is about to go out with an EMPTY CSRF
         * field — whatever the cause — refresh the token first rather than
         * letting the request 403. Calling `form.submit()` (not
         * `.requestSubmit()`) does not re-fire the `submit` event per
         * spec, so this cannot loop.
         *
         * ⚠️ POST-ONLY, FOUND 09-02-26. A `method="get"` form (global_search.html,
         * songbook.html, announcements.html, kai/view_reports.html,
         * directory.html's export form, every `onchange="this.form.submit()"`
         * filter dropdown in admin-v2) never renders `{% csrf_token %}` in the
         * first place — Django only checks CSRF on state-changing methods.
         * Without a method check, `tokenField` reads `null` there for a reason
         * that has nothing to do with what this guard exists to catch, and the
         * code below still treats "null" as "go fix it": preventDefault() the
         * real submission, await an async refresh, then resubmit with a
         * newly-created hidden field — which on a GET form serializes straight
         * into the query string, leaking a live CSRF token into the URL
         * (browser history, server/CDN logs, any Referer sent to a
         * third-party resource on the results page) on every ordinary site
         * search, plus a needless round-trip before the page loads.
         */
        document.addEventListener('submit', function(event) {
            var form = event.target;
            if (!(form instanceof HTMLFormElement)) return;
            if (form.method.toLowerCase() !== 'post') return; // GET forms never carry this field — see above
            var tokenField = form.querySelector('input[name="csrfmiddlewaretoken"]');
            // ⚠️ A MISSING FIELD IS NOT "NOTHING TO DO." The first version of
            // this guard treated "no such input at all" the same as "token
            // present" (`!tokenField` fell through the same early-return as a
            // filled-in field), on the assumption that `{% csrf_token %}`
            // guarantees the element exists. `posted_token_present=False` in
            // the security log records that the key was ABSENT from the POST
            // body, not merely empty — which a browser produces just as
            // readily by dropping the hidden `<input>` node itself as by
            // clearing its value, and nothing here can tell those apart from
            // the DOM alone. So: create the field if it's missing, same as
            // refreshing it if it's empty.
            //
            // ⚠️ A PRESENT VALUE IS ALSO NOT "NOTHING TO DO" — v3.29.24, see
            // the doc comment above the token-refresh helper's definition
            // for why a file-input form can't trust a non-empty field the
            // way every other form can.
            var hasFileInput = !!form.querySelector('input[type="file"]');
            if (!hasFileInput && tokenField && tokenField.value) return; // token present, and nothing about this form casts doubt on it
            event.preventDefault();
            refreshCsrfToken().then(function(token) {
                if (!tokenField) {
                    tokenField = document.createElement('input');
                    tokenField.type = 'hidden';
                    tokenField.name = 'csrfmiddlewaretoken';
                    form.appendChild(tokenField);
                }
                // ⚠️ v3.29.25 — A FAILED REFRESH IS NOT "SUBMIT ANYWAY." The
                // old code only ever set the field on SUCCESS and otherwise
                // fell through to the resubmit regardless — so a refresh
                // that failed (even after the retry above) sent a field that
                // was still empty straight back into the same 403, silently.
                // If there's a fresh token, use it (refreshCsrfToken() already
                // patched an existing field's value; a newly-created one
                // needs it set directly here). If there isn't AND the field
                // has nothing usable to fall back on, stop — this submit is
                // a guaranteed second failure — and say so, rather than
                // sending the member to a confusing error page.
                if (token) {
                    tokenField.value = token;
                } else if (!tokenField.value) {
                    P.toast('Could not verify your session before submitting — please try again.', 'error');
                    return;
                }
                // ⚠️ v3.29.30 — THE ACTUAL FIX. A round of live diagnostics
                // on 09-08-26 proved this resubmit reaches this exact point
                // with a real, freshly-refreshed token set on the form — and
                // the request that then reached the server carried the
                // RIGHT multipart Content-Type header (a fresh WebKit
                // boundary, proving it WAS this resubmit) but a completely
                // EMPTY body (zero fields, no file). Calling `form.submit()`
                // a SECOND time on a form whose native submission was
                // already prevented once silently drops the body in Safari
                // (and Chrome for iOS, which uses the same WebKit engine
                // under Apple's platform policy) — most visible on a form
                // holding a `<input type="file">`. Native `form.submit()`
                // gives zero visibility into this: no error, no event,
                // nothing to catch.
                //
                // Fix: stop asking the browser to silently re-serialize the
                // form natively a second time. Build the request ourselves
                // — `new FormData(form)` reads every current field
                // (including the file input's current File) at the moment
                // we're actually about to send, and `fetch()` returns a
                // real response we can act on instead of a silent native
                // failure with no signal at all. Confirmed fixed live
                // 09-08-26 on both Safari and CriOS. Full diagnostic history
                // in `changelogs/v3.29.28.md` if this class of bug ever
                // needs revisiting.
                //
                // ⚠️ v3.29.33 — THE FOLLOW-UP FIX. `fetch()`'s default
                // `redirect: 'follow'` makes the browser silently execute
                // the redirect TARGET request *inside* this fetch call,
                // invisibly to this code — and that target request is a
                // real page render that consumes this app's one-shot
                // session flash message (base.html renders `{% for
                // message in messages %}` on every page). So the message
                // set by a successful upload, or the error set by a
                // rejected one, was gone by the time the `window.location`
                // navigation below ever requested the page a SECOND time.
                // Verified directly with the Django test client, not just
                // reasoned about — see `src/middleware/js_resubmit.py`'s
                // module docstring and `changelogs/v3.29.33.md`.
                //
                // Fix: send a header only this fetch call ever sends
                // (deliberately not the pre-existing X-Requested-With,
                // which ~13 other views already branch on for unrelated
                // JSON responses of their own). `JsResubmitEnvelopeMiddleware`
                // sees it and hands back `{redirect, messages}` as the
                // ONE HTTP response this fetch ever receives — never a
                // 3xx it could silently follow — with the message text
                // already read out of Django's messages framework
                // server-side, so there's nothing left to consume twice.
                var formData = new FormData(form);
                fetch(form.action, {
                    method: 'POST',
                    body: formData,
                    credentials: 'same-origin',
                    headers: { 'X-Parliament-Resubmit': '1' },
                }).then(function(response) {
                    if (!response.ok) {
                        P.toast('Your submission could not be completed — please try again.', 'error');
                        return;
                    }
                    return response.json().then(function(data) {
                        // v3.29.34 — relay the message's own level (one of
                        // Django's five tags; `js_resubmit.py` reads it
                        // straight off `message.tags`) instead of
                        // collapsing everything non-success to 'error'.
                        // A `messages.warning()`/`.info()` call on one of
                        // these forms used to show as an alarming red
                        // toast; P.toast() now has a matching style for
                        // each tone, same as the full-page partial does.
                        (data.messages || []).forEach(function(m) {
                            P.toast(m.text, m.level);
                        });
                        if (data.redirect) {
                            // Mirror what a native form POST would have
                            // done — a real navigation, not a DOM swap.
                            // The flash message (if any) was already
                            // toasted above from the JSON payload, not
                            // read from whatever session state survives
                            // to this new page.
                            window.location.href = data.redirect;
                        }
                        // else: the server re-rendered the form in place
                        // (a validation error) — stay on this page, with
                        // the member's typed input and selected file
                        // still intact. The toast above already said why.
                    });
                }).catch(function() {
                    P.toast('Your submission could not be sent — please check your connection and try again.', 'error');
                });
            });
        }, true);
    })(window.Parliament);
    
