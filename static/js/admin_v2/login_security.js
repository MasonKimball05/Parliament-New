/* static/js/admin_v2/login_security.js — Login security in the console look
 * (admin_v2/console/user_login_security.html). The classic page's inline
 * scripts, same hooks:
 *
 *   #js-copy-password-btn       copies #temp-password (the one-time password)
 *   #watch-flag-add-btn / -edit-btn   show / hide #watch-flag-add-form / -edit-form
 *   form.js-confirm-submit[data-confirm-msg]   confirm before submitting
 *   #js-pw-random / #js-pw-custom     show #custom_password_field for a custom one
 *   .js-toggle-meta[data-meta]  shows the row's metadata (pretty JSON when it
 *                               parses) in the <pre> after it; textContent only
 */
(function () {
    'use strict';

    var copyBtn = document.getElementById('js-copy-password-btn');
    if (copyBtn) copyBtn.addEventListener('click', function () {
        var pw = document.getElementById('temp-password');
        if (!pw || !navigator.clipboard) return;
        navigator.clipboard.writeText(pw.textContent.trim()).then(function () {
            copyBtn.textContent = 'Copied';
        }).catch(function () {
            copyBtn.textContent = 'Copy failed';
        });
    });

    [['watch-flag-add-btn', 'watch-flag-add-form'], ['watch-flag-edit-btn', 'watch-flag-edit-form']].forEach(function (pair) {
        var btn = document.getElementById(pair[0]);
        var form = document.getElementById(pair[1]);
        if (!btn || !form) return;
        btn.setAttribute('aria-expanded', 'false');
        btn.addEventListener('click', function () {
            var open = form.classList.toggle('hidden') === false;
            btn.setAttribute('aria-expanded', open ? 'true' : 'false');
            if (open) {
                var first = form.querySelector('input[type="text"], textarea');
                if (first) first.focus();
            }
        });
    });

    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!(form instanceof HTMLFormElement) || !form.classList.contains('js-confirm-submit')) return;
        if (!window.confirm(form.getAttribute('data-confirm-msg') || 'Are you sure?')) e.preventDefault();
    });

    var customField = document.getElementById('custom_password_field');
    function syncPasswordType() {
        var custom = document.getElementById('js-pw-custom');
        if (customField && custom) customField.classList.toggle('hidden', !custom.checked);
    }
    ['js-pw-random', 'js-pw-custom'].forEach(function (id) {
        var radio = document.getElementById(id);
        if (radio) radio.addEventListener('change', syncPasswordType);
    });
    syncPasswordType();

    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.js-toggle-meta');
        if (!btn) return;
        var pre = btn.nextElementSibling;
        if (!pre) return;
        if (pre.classList.contains('hidden')) {
            var raw = btn.getAttribute('data-meta') || '';
            try {
                pre.textContent = JSON.stringify(JSON.parse(raw.replace(/'/g, '"')), null, 2);
            } catch (err) {
                pre.textContent = raw;
            }
            pre.classList.remove('hidden');
            btn.textContent = 'Hide';
            btn.setAttribute('aria-expanded', 'true');
        } else {
            pre.classList.add('hidden');
            btn.textContent = 'Show';
            btn.setAttribute('aria-expanded', 'false');
        }
    });
})();
