/* static/js/admin_v2/home.js — console home (admin_v2/console/home.html).
 * Moved from the classic dashboard's inline script: the Performance widget's
 * health check, and the test email's "Preview in browser" link carrying the
 * "include a test poll" box (a GET link can't otherwise see the checkbox).
 * Also the Customize dialog (<dialog id="home-customize">): opens from the
 * [data-home-customize] button; the up/down buttons move a row (and its
 * hidden `order` input, so the form posts the new order); Save and Reset are
 * plain form submits to admin_v2_console_home_widgets. Cancel and Escape put
 * the rows back the way they were.
 */
(function () {
    'use strict';
    var btn = document.getElementById('health-check-btn');
    var out = document.getElementById('health-result');
    if (btn && out) {
        btn.addEventListener('click', function () {
            btn.disabled = true;
            var label = btn.lastChild;
            var was = label.textContent;
            label.textContent = 'Checking…';
            var start = performance.now();
            fetch(btn.getAttribute('data-health-url'), { credentials: 'same-origin' })
                .then(function (r) { return r.json(); })
                .then(function (data) {
                    var ms = Math.round(performance.now() - start);
                    out.className = 'ac-small ac-text-ok';
                    out.textContent = 'Healthy · response ' + ms + ' ms · server ' + (data.server_time_ms || 'n/a') + ' ms';
                })
                .catch(function (err) {
                    out.className = 'ac-small ac-text-error';
                    out.textContent = 'Error · ' + err.message;
                })
                .then(function () {
                    out.hidden = false;
                    btn.disabled = false;
                    label.textContent = was;
                });
        });
    }

    var poll = document.getElementById('include-test-poll');
    var preview = document.getElementById('preview-test-email-link');
    if (poll && preview) {
        var base = preview.getAttribute('href');
        preview.addEventListener('click', function () {
            preview.setAttribute('href', poll.checked ? base + '?include_test_poll=1' : base);
        });
    }

    var dialog = document.getElementById('home-customize');
    var opener = document.querySelector('[data-home-customize]');
    var list = dialog && dialog.querySelector('[data-home-customize-list]');
    if (dialog && opener && list) {
        var status = dialog.querySelector('[data-home-customize-status]');
        var saved = null; // rows and switches as they were when the dialog opened

        var rows = function () { return Array.prototype.slice.call(list.children); };
        var refresh = function () {
            var all = rows();
            all.forEach(function (row, i) {
                row.querySelector('[data-move="up"]').disabled = i === 0;
                row.querySelector('[data-move="down"]').disabled = i === all.length - 1;
            });
        };
        var snapshot = function () {
            return rows().map(function (row) {
                return { row: row, on: row.querySelector('input[name="show"]').checked };
            });
        };
        var restore = function (state) {
            state.forEach(function (s) {
                s.row.querySelector('input[name="show"]').checked = s.on;
                s.row.classList.remove('is-moved');
                list.appendChild(s.row);
            });
            refresh();
        };
        var open = function () {
            saved = snapshot();
            if (status) status.textContent = '';
            if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
            var first = list.querySelector('input[name="show"]');
            if (first) first.focus();
        };
        var close = function () {
            if (typeof dialog.close === 'function') dialog.close(); else dialog.removeAttribute('open');
        };

        opener.addEventListener('click', open);
        // Escape / Cancel / the backdrop: discard unsaved moves and switches.
        dialog.addEventListener('close', function () {
            if (saved && dialog.returnValue !== 'submitted') restore(saved);
            saved = null;
            opener.focus();
        });
        dialog.addEventListener('submit', function () { dialog.returnValue = 'submitted'; });
        dialog.addEventListener('click', function (e) {
            if (e.target === dialog || e.target.closest('[data-home-customize-close]')) { close(); return; }
            var btn = e.target.closest('[data-move]');
            if (!btn || btn.disabled) return;
            var row = btn.closest('.ac-customize-row');
            var up = btn.getAttribute('data-move') === 'up';
            var sibling = up ? row.previousElementSibling : row.nextElementSibling;
            if (!sibling) return;
            list.insertBefore(row, up ? sibling : sibling.nextElementSibling);
            rows().forEach(function (r) { r.classList.toggle('is-moved', r === row); });
            refresh();
            // Keep focus on the row; if this button is now disabled (top or
            // bottom), hand it to the other one so the keyboard isn't dropped.
            var target = btn.disabled ? row.querySelector('[data-move="' + (up ? 'down' : 'up') + '"]') : btn;
            target.focus();
            if (status) {
                status.textContent = row.getAttribute('data-label') + ' moved to position '
                    + (rows().indexOf(row) + 1) + ' of ' + rows().length + '.';
            }
        });
        refresh();
    }
})();
