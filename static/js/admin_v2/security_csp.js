/* static/js/admin_v2/security_csp.js — console CSP violations
 * (admin_v2/console/csp_violations.html).
 *
 * .js-dismiss-btn[data-directive][data-blocked]: POST the pair to
 * admin_v2_csp_violation_dismiss (#js-violation-list[data-dismiss-url]) with
 * Parliament.post (CSRF token from the page, failures toasted); on success the
 * group's .js-violation-row fades out (class, no inline style) and goes.
 */
(function () {
    'use strict';
    var list = document.getElementById('js-violation-list');
    if (!list) return;
    var url = list.getAttribute('data-dismiss-url');
    list.addEventListener('click', function (e) {
        var btn = e.target.closest('.js-dismiss-btn');
        if (!btn || btn.disabled) return;
        var row = btn.closest('.js-violation-row');
        btn.disabled = true;
        btn.textContent = 'Dismissing…';
        window.Parliament.post(url, {
            violated_directive: btn.getAttribute('data-directive'),
            blocked_uri: btn.getAttribute('data-blocked'),
        }).then(function (data) {
            if (data.ok) {
                row.classList.add('ac-sec-fading');
                setTimeout(function () { row.remove(); }, 300);
            } else {
                btn.disabled = false;
                btn.textContent = 'Dismiss';
                window.Parliament.toast(data.error || 'Could not dismiss that violation.', 'error');
            }
        }).catch(function () {
            btn.disabled = false;
            btn.textContent = 'Dismiss';
        });
    });
})();
