/*
 * Alpine components (v3.45.7).
 *
 * WHY THIS FILE EXISTS
 * The site's CSP has no 'unsafe-eval'. The standard Alpine build turns every
 * x-data / x-show / @click string into a function with the AsyncFunction
 * constructor, which that CSP refuses, so from 06-14-26 until v3.45.7 no
 * Alpine expression ran in production (DEBUG=True skips the CSP, which is why
 * it worked locally). static/js/alpinejs.min.js is now the CSP build.
 *
 * The CSP build parses simple expressions itself (`open = !open`,
 * `step === 'email'`, `{ enabled: true }`), but it cannot parse methods
 * written inside an x-data attribute, and it cannot see globals such as
 * `document`. Components that need either are registered here with
 * Alpine.data and named in the template: x-data="guideSearch".
 *
 * Do NOT "fix" an Alpine error by adding 'unsafe-eval' to the CSP: with the
 * standard build and unsafe-eval, any HTML that reaches a page with an
 * x-data / x-init attribute in it runs as script, nonce or not.
 * src/tests/guards/test_alpine_csp_build.py checks these rules.
 */
document.addEventListener('alpine:init', function () {
    function contains(text, q) {
        return String(text || '').toLowerCase().indexOf(q) !== -1;
    }

    // templates/components/onboarding_checklist.html: collapse + Dismiss.
    // (The CSP build cannot reach `window`, so the Dismiss button calls this.)
    window.Alpine.data('onboardingChecklist', function () {
        return {
            open: true,
            dismiss: function () {
                if (typeof window.dismissChecklist === 'function') window.dismissChecklist();
            },
        };
    });

    // templates/guide/index.html: filter the guide cards as you type.
    window.Alpine.data('guideSearch', function () {
        return {
            q: '',
            m: function (s) {
                return !this.q || contains(s, this.q.toLowerCase());
            },
            any: function () {
                if (!this.q) return true;
                var q = this.q.toLowerCase();
                for (var i = 0; i < arguments.length; i++) {
                    if (contains(arguments[i], q)) return true;
                }
                return false;
            },
        };
    });

    // templates/admin_v2/url_explorer.html: collapsible sections + route filter.
    // Each row carries its text in data-* attributes (auto-escaped by the
    // template) instead of a JS object literal built from template values.
    window.Alpine.data('urlExplorer', function () {
        return {
            q: '',
            expanded: {},
            toggleSection: function (s) {
                this.expanded[s] = !this.expanded[s];
            },
            isOpen: function (s) {
                return !!this.expanded[s] || !!this.q;
            },
            rowMatches: function (row) {
                if (!this.q) return true;
                var q = this.q.toLowerCase();
                var d = row.dataset;
                return contains(d.pattern, q) || contains(d.name, q)
                    || contains(d.auth, q) || contains(d.doc, q);
            },
            noMatches: function () {
                if (!this.q) return false;
                var rows = this.$root.querySelectorAll('tr[data-pattern]');
                for (var i = 0; i < rows.length; i++) {
                    if (this.rowMatches(rows[i])) return false;
                }
                return true;
            },
        };
    });
});
