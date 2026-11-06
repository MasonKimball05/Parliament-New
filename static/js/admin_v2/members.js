/* static/js/admin_v2/members.js — the console Members list (admin_v2/console/members.html).
 *
 * "Check" default password, in the member's slide-out panel (Security tab).
 * Delegated from the document because the panel's HTML arrives after load.
 * GET {data-check-url} (check_default_password) → a pill saying Default /
 * Changed. Values go in with textContent / title, never as HTML.
 */
(function () {
    'use strict';
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.js-check-password[data-check-url]');
        if (!btn || btn.disabled) return;
        e.preventDefault();
        btn.disabled = true;
        btn.textContent = 'Checking…';
        fetch(btn.getAttribute('data-check-url'), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.success) throw new Error('failed');
                var pill = document.createElement('span');
                if (data.has_default_password) {
                    pill.className = 'ac-pill is-warn';
                    pill.textContent = 'Default password';
                    pill.title = 'Matches: ' + (data.matched_pattern || '');
                } else {
                    pill.className = 'ac-pill is-ok';
                    pill.textContent = 'Changed';
                    pill.title = 'Checked: ' + (data.patterns_checked || []).join(', ');
                }
                btn.replaceWith(pill);
            })
            .catch(function () {
                btn.disabled = false;
                btn.textContent = 'Error, retry';
            });
    });
})();
