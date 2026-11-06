/* static/js/admin_v2/notification_schedules.js — the console Notification
 * schedules list (templates/admin_v2/console/notification_schedules.html).
 *
 * Same requests as the classic page's inline script, against URLs the
 * template reversed (no paths built here):
 *   "New schedule" (a link to #new-schedule) → opens the <dialog>; its form
 *     POSTs JSON to data-create-url (admin_v2_create_notification_schedule).
 *   .js-toggle-schedule → POST data-toggle-url, then reload.
 *   .js-delete-schedule → confirm, POST data-delete-url, then reload.
 * The CSRF token comes from the dialog form's {% csrf_token %} input.
 */
(function () {
    'use strict';
    var dialog = document.getElementById('new-schedule');
    var form = document.getElementById('schedule-form');
    if (!dialog || !form) return;
    var errorBox = form.querySelector('[data-schedule-error]');

    function csrf() {
        var input = form.querySelector('input[name="csrfmiddlewaretoken"]');
        return input ? input.value : '';
    }
    function showError(msg) {
        if (errorBox) { errorBox.textContent = msg; errorBox.hidden = false; } else { window.alert(msg); }
    }
    function post(url, body, json) {
        var headers = { 'X-CSRFToken': csrf(), 'Accept': 'application/json' };
        if (json) headers['Content-Type'] = 'application/json';
        return fetch(url, { method: 'POST', credentials: 'same-origin', headers: headers, body: body })
            .then(function (r) { return r.json(); });
    }
    function openDialog() {
        form.reset();
        if (errorBox) errorBox.hidden = true;
        if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
        var first = form.querySelector('input[name="name"]');
        if (first) first.focus();
    }
    function closeDialog() {
        if (typeof dialog.close === 'function') dialog.close(); else dialog.removeAttribute('open');
    }

    document.addEventListener('click', function (e) {
        var opener = e.target.closest('a[href="#new-schedule"]');
        if (opener) { e.preventDefault(); openDialog(); return; }
        if (e.target.closest('.js-close-modal')) { closeDialog(); return; }
        if (e.target === dialog) { closeDialog(); return; } // the backdrop

        var toggle = e.target.closest('.js-toggle-schedule[data-toggle-url]');
        if (toggle) {
            toggle.disabled = true;
            post(toggle.getAttribute('data-toggle-url'))
                .then(function (data) {
                    if (data.success) { window.location.reload(); return; }
                    toggle.disabled = false;
                    window.alert(data.error || 'Error toggling schedule');
                })
                .catch(function () { toggle.disabled = false; window.alert('Error toggling schedule'); });
            return;
        }
        var del = e.target.closest('.js-delete-schedule[data-delete-url]');
        if (del) {
            if (!window.confirm('Are you sure you want to delete "' + del.getAttribute('data-schedule-name') + '"?')) return;
            del.disabled = true;
            post(del.getAttribute('data-delete-url'))
                .then(function (data) {
                    if (data.success) { window.location.reload(); return; }
                    del.disabled = false;
                    window.alert(data.error || 'Error deleting schedule');
                })
                .catch(function () { del.disabled = false; window.alert('Error deleting schedule'); });
        }
    });

    if (window.location.hash === '#new-schedule') openDialog();

    form.addEventListener('submit', function (e) {
        e.preventDefault();
        var fd = new FormData(form);
        var data = {
            name: fd.get('name'),
            notification_type: fd.get('notification_type'),
            description: fd.get('description'),
            hours_before: parseInt(fd.get('hours_before'), 10),
            target_audience: fd.get('target_audience'),
            send_email: fd.get('send_email') === 'on',
            send_in_app: fd.get('send_in_app') === 'on',
            message_template: fd.get('message_template')
        };
        var submit = form.querySelector('button[type="submit"]');
        if (submit) submit.disabled = true;
        post(form.getAttribute('data-create-url'), JSON.stringify(data), true)
            .then(function (res) {
                if (res.success) { window.location.reload(); return; }
                if (submit) submit.disabled = false;
                showError(res.error || 'Error creating schedule');
            })
            .catch(function (err) {
                if (submit) submit.disabled = false;
                showError('Error: ' + err);
            });
    });
})();
