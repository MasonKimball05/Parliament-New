/* static/js/admin_v2/home.js — console home (admin_v2/console/home.html).
 * Moved from the classic dashboard's inline script: the Performance widget's
 * health check, and the test email's "Preview in browser" link carrying the
 * "include a test poll" box (a GET link can't otherwise see the checkbox).
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
})();
