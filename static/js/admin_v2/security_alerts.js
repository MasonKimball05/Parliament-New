/* static/js/admin_v2/security_alerts.js — console Security alerts
 * (admin_v2/console/security_alerts.html) and one alert's full page.
 *
 * Delegated from the document, because the slide-out panel's HTML arrives
 * after load and fragments don't run scripts:
 *
 *   .js-dismiss-alert[data-alert-id][data-url]  POST admin_v2_dismiss_alert;
 *       on success every status pill for that alert (list row
 *       [data-alert-row], panel) reads Resolved and its Dismiss buttons go.
 *   #dismiss-all-btn[data-url]                  confirm, POST
 *       admin_v2_dismiss_all_alerts, reload.
 *
 * Parliament.post (static/js/base/core.js) sends the CSRF token and toasts a
 * failure; the button is made usable again so it can be retried.
 */
(function () {
    'use strict';

    function markResolved(id) {
        var row = document.querySelector('[data-alert-row="' + id + '"]');
        var panel = document.getElementById('ac-panel');
        if (panel && !panel.querySelector('.js-dismiss-alert[data-alert-id="' + id + '"]')) panel = null;
        [row, panel, document.querySelector('.ac-sec-detail')].forEach(function (scope) {
            if (!scope) return;
            scope.querySelectorAll('[data-alert-status]').forEach(function (pill) {
                pill.className = 'ac-pill is-ok';
                pill.textContent = 'Resolved';
            });
        });
        document.querySelectorAll('.js-dismiss-alert[data-alert-id="' + id + '"]').forEach(function (b) { b.remove(); });
    }

    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.js-dismiss-alert[data-url]');
        if (btn) {
            e.preventDefault();
            if (btn.disabled) return;
            btn.disabled = true;
            btn.textContent = 'Dismissing…';
            window.Parliament.post(btn.getAttribute('data-url'))
                .then(function (data) {
                    if (!data.success) throw new Error('failed');
                    markResolved(btn.getAttribute('data-alert-id'));
                })
                .catch(function () {
                    btn.disabled = false;
                    btn.textContent = 'Dismiss';
                });
            return;
        }
        var all = e.target.closest('#dismiss-all-btn[data-url]');
        if (all) {
            e.preventDefault();
            if (all.disabled || !window.confirm('Dismiss all new security alerts?')) return;
            all.disabled = true;
            all.textContent = 'Dismissing…';
            window.Parliament.post(all.getAttribute('data-url'))
                .then(function (data) { if (data.success) window.location.reload(); })
                .catch(function () {
                    all.disabled = false;
                    all.textContent = 'Dismiss all new';
                });
        }
    });
})();
