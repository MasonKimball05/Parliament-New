/* static/js/base/data-actions.js — v3.58.1 (10-27-26).
 * Delegated replacements for inline onclick=/onchange=/onsubmit= attributes.
 *
 * Prod CSP has no 'unsafe-inline' in script-src (src/middleware/security.py),
 * so an inline handler attribute is dead in prod while working in dev, where
 * no CSP is sent. src/tests/guards/test_csp_templates.py listed 17 templates
 * still doing it (the chat "mark all read" × on every page among them). They
 * now use these data-* attributes instead, handled once here:
 *
 *   data-confirm="Message?"   on a <form>: confirm before it submits;
 *                             on a button/link: confirm before the click goes on.
 *   data-autosubmit           on a <select>/<input>: submit its form on change.
 *   data-href="/url/"         on any element (e.g. a table row): navigate on click.
 *   data-copy="text"          on a button: copy the text to the clipboard.
 *   data-select-on-click      on an input: select its contents on click.
 *   data-show="#id"           on a button: remove `hidden` from the target.
 *   data-hide="#id"           on a button: add `hidden` to the target.
 *   data-check-all="name"     on a checkbox: tick/untick every checkbox named
 *                             `name` in the same form.
 *
 * Page-specific functions (skipProfileItem, toggleItem, ...) are wired by the
 * page's own nonce'd script instead.
 */
(function () {
    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!(form instanceof HTMLFormElement)) return;
        var msg = form.getAttribute('data-confirm');
        if (msg && !window.confirm(msg)) {
            e.preventDefault();
            e.stopImmediatePropagation();
        }
    }, true);

    document.addEventListener('click', function (e) {
        var el = e.target.closest('[data-confirm]:not(form)');
        if (el && !window.confirm(el.getAttribute('data-confirm'))) {
            e.preventDefault();
            e.stopImmediatePropagation();
            return;
        }

        var copy = e.target.closest('[data-copy]');
        if (copy && navigator.clipboard) {
            navigator.clipboard.writeText(copy.getAttribute('data-copy'));
        }

        var sel = e.target.closest('[data-select-on-click]');
        if (sel && typeof sel.select === 'function') sel.select();

        var show = e.target.closest('[data-show]');
        if (show) {
            var s = document.querySelector(show.getAttribute('data-show'));
            if (s) s.classList.remove('hidden');
        }
        var hide = e.target.closest('[data-hide]');
        if (hide) {
            var h = document.querySelector(hide.getAttribute('data-hide'));
            if (h) h.classList.add('hidden');
        }

        // Rows that act as links: a real link or button inside still wins.
        var row = e.target.closest('[data-href]');
        if (row && !e.target.closest('a, button, input, select, textarea, label')) {
            window.location = row.getAttribute('data-href');
        }
    }, true);

    document.addEventListener('change', function (e) {
        var el = e.target;
        if (el && el.hasAttribute && el.hasAttribute('data-check-all') && el.form) {
            var name = el.getAttribute('data-check-all');
            Array.prototype.forEach.call(el.form.elements, function (box) {
                if (box.type === 'checkbox' && box.name === name) box.checked = el.checked;
            });
        }
        if (el && el.hasAttribute && el.hasAttribute('data-autosubmit') && el.form) {
            if (typeof el.form.requestSubmit === 'function') el.form.requestSubmit();
            else el.form.submit();
        }
    });
})();
