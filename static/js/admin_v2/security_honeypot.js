/* static/js/admin_v2/security_honeypot.js — console Honeypot logs
 * (admin_v2/console/honeypot_logs.html) and one hit's full page.
 *
 * The classic page's hooks, without its inline script (prod CSP):
 *   #js-blacklist-all-btn  confirm, submit #blacklist-all-form
 *   #js-clear-all-btn      confirm, submit #clear-all-form
 *   #js-clear-ip-btn       confirm, put data-ip in #clear-ip-value, submit #clear-ip-form
 *   .js-delete-log[data-log-id][data-url]
 *                          confirm, POST admin_v2_delete_honeypot_log; the
 *                          row (#row-{id}) goes, and the panel closes if it
 *                          was showing that hit (full page: back to the list).
 *   .js-honeypot-row       a click on the row outside its links, buttons and
 *                          forms opens the hit in the slide-out panel (the
 *                          row's [data-ac-panel] link, so panel.js does it).
 * Delegated from the document: the panel's HTML arrives after load.
 */
(function () {
    'use strict';

    function submitHidden(formId, message) {
        var form = document.getElementById(formId);
        if (form && window.confirm(message)) form.submit();
    }

    document.addEventListener('click', function (e) {
        var t = e.target;
        if (t.closest('#js-blacklist-all-btn')) {
            submitHidden('blacklist-all-form', 'Blacklist every unique IP from honeypot logs that is not already blocked? This will block them immediately.');
            return;
        }
        if (t.closest('#js-clear-all-btn')) {
            submitHidden('clear-all-form', 'Delete ALL honeypot logs? This cannot be undone.');
            return;
        }
        var clearIp = t.closest('#js-clear-ip-btn');
        if (clearIp) {
            var ip = clearIp.getAttribute('data-ip');
            var field = document.getElementById('clear-ip-value');
            if (field && window.confirm('Delete all logs for IP ' + ip + '? This cannot be undone.')) {
                field.value = ip;
                document.getElementById('clear-ip-form').submit();
            }
            return;
        }
        var del = t.closest('.js-delete-log[data-url]');
        if (del) {
            e.preventDefault();
            if (del.disabled || !window.confirm('Delete this log entry?')) return;
            del.disabled = true;
            var id = del.getAttribute('data-log-id');
            window.Parliament.post(del.getAttribute('data-url'))
                .then(function (data) {
                    if (!data.success) throw new Error(data.error || 'failed');
                    var row = document.getElementById('row-' + id);
                    if (row) row.remove();
                    var panel = document.getElementById('ac-panel');
                    if (panel && panel.contains(del)) {
                        var close = panel.querySelector('[data-ac-panel-close]');
                        if (close) close.click();
                    } else if (!row && del.getAttribute('data-list-url')) {
                        window.location.href = del.getAttribute('data-list-url');
                    }
                })
                .catch(function () { del.disabled = false; });
            return;
        }
        var row = t.closest('.js-honeypot-row');
        if (row && !t.closest('a, button, form, input, label')) {
            var link = row.querySelector('a[data-ac-panel]');
            if (link) link.click();
        }
    });
})();
