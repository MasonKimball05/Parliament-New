/* static/js/slating/attendance.js — the slating Attendance page (11-05-26).
 *
 * At the meeting the chair marks ~60 people present. Without this file every
 * "Mark Present" is a normal form POST that reloads the page and jumps to the
 * top; with it:
 *   - the search box filters the list as you type (the GET ?q= search still
 *     works without JS),
 *   - Mark Present / Mark Absent post in place (same form, same view, which
 *     answers JSON when asked) and update the row and the sticky count,
 *   - "Mark all listed present" marks the rows currently shown, after the
 *     confirm (data-confirm, handled by static/js/base/data-actions.js).
 * Any failure falls back to the plain form POST.
 * No inline script: prod CSP has no 'unsafe-inline' (src/middleware/security.py).
 */
(function () {
    'use strict';
    var page = document.getElementById('attendance-page');
    if (!page) return;

    var rows = Array.prototype.slice.call(document.querySelectorAll('.js-attendance-row'));
    var search = document.getElementById('js-attendance-search');
    var noMatch = document.getElementById('js-no-match');
    var markAllForm = document.getElementById('js-mark-all-form');
    var markAllBtn = document.getElementById('js-mark-all-btn');
    var markAllIds = document.getElementById('js-mark-all-ids');
    var errorEl = document.getElementById('js-attendance-error');
    var searchSubmit = document.querySelector('.js-search-submit');
    if (searchSubmit) searchSubmit.classList.add('hidden');   // filtering is live

    function showError(msg) {
        if (!errorEl) return;
        errorEl.textContent = msg || '';
        errorEl.classList.toggle('hidden', !msg);
    }

    function setRow(row, present) {
        row.dataset.present = present ? '1' : '0';
        var dot = row.querySelector('.js-dot');
        if (dot) {
            dot.classList.toggle('bg-green-500', present);
            dot.classList.toggle('bg-gray-300', !present);
            dot.classList.toggle('dark:bg-gray-600', !present);
        }
        var form = row.querySelector('form');
        var action = form.querySelector('input[name="action"]');
        action.value = present ? 'mark_absent' : 'mark_present';
        var btn = form.querySelector('button');
        btn.className = present ? btn.dataset.presentClass : btn.dataset.absentClass;
        btn.textContent = present ? 'Mark Absent' : 'Mark Present';
    }

    function setCounts(data) {
        var count = document.getElementById('js-present-count');
        if (count) count.textContent = data.present_count;
        var met = document.getElementById('js-quorum-met');
        var notMet = document.getElementById('js-quorum-not-met');
        var needed = document.getElementById('js-quorum-needed');
        if (met && notMet) {
            met.classList.toggle('hidden', !data.quorum_met);
            notMet.classList.toggle('hidden', !!data.quorum_met);
        }
        if (needed) needed.textContent = data.quorum_needed;
    }

    function visibleRows() {
        return rows.filter(function (r) { return !r.classList.contains('hidden'); });
    }

    function refreshMarkAll() {
        if (!markAllForm) return;
        var shown = visibleRows();
        var absent = shown.filter(function (r) { return r.dataset.present !== '1'; });
        markAllIds.textContent = '';
        shown.forEach(function (r) {
            var input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'member_ids';
            input.value = r.dataset.memberId;
            markAllIds.appendChild(input);
        });
        markAllBtn.disabled = absent.length === 0;
        var n = absent.length;
        markAllForm.setAttribute('data-confirm', 'Mark ' + n + ' listed member' + (n === 1 ? '' : 's') + ' present?');
        markAllBtn.textContent = n ? 'Mark all listed present (' + n + ')' : 'Mark all listed present';
    }

    function applyFilter() {
        var q = (search ? search.value : '').trim().toLowerCase();
        var shown = 0;
        rows.forEach(function (r) {
            var hit = !q || r.dataset.search.indexOf(q) !== -1;
            r.classList.toggle('hidden', !hit);
            if (hit) shown++;
        });
        if (noMatch) noMatch.classList.toggle('hidden', shown > 0 || rows.length === 0);
        refreshMarkAll();
    }

    function post(form) {
        // getAttribute: these forms have an input named "action", which shadows form.action.
        return fetch(form.getAttribute('action') || window.location.pathname, {
            method: 'POST',
            body: new FormData(form),
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
        }).then(function (r) {
            if (!r.ok) throw new Error('HTTP ' + r.status);
            return r.json();
        });
    }

    if (search) {
        search.addEventListener('input', applyFilter);
        search.form.addEventListener('submit', function (e) { e.preventDefault(); applyFilter(); });
    }

    rows.forEach(function (row) {
        var form = row.querySelector('form');
        form.addEventListener('submit', function (e) {
            e.preventDefault();
            if (form.dataset.busy === '1') return;
            form.dataset.busy = '1';
            var btn = form.querySelector('button');
            btn.disabled = true;
            showError('');
            post(form).then(function (data) {
                setRow(row, !!data.present);
                setCounts(data);
                refreshMarkAll();
            }).catch(function () {
                form.submit();   // the plain POST: reloads with the result
            }).then(function () {
                form.dataset.busy = '';
                btn.disabled = false;
            });
        });
    });

    if (markAllForm) {
        // Runs after data-actions.js's capture-phase confirm; a Cancel never gets here.
        markAllForm.addEventListener('submit', function (e) {
            e.preventDefault();
            if (markAllForm.dataset.busy === '1') return;
            markAllForm.dataset.busy = '1';
            markAllBtn.disabled = true;
            showError('');
            post(markAllForm).then(function (data) {
                var present = {};
                (data.present_ids || []).forEach(function (id) { present[id] = true; });
                rows.forEach(function (r) { setRow(r, !!present[r.dataset.memberId]); });
                setCounts(data);
            }).catch(function () {
                markAllForm.submit();
            }).then(function () {
                markAllForm.dataset.busy = '';
                refreshMarkAll();
            });
        });
    }

    applyFilter();
})();
