/*
 * System Logs (11-09-26): auto-refresh, copy, expand/collapse tracebacks.
 * Shared by the classic and console pages; the list is
 * templates/admin/_system_log_entries.html, re-fetched with ?partial=1.
 *
 * Auto-refresh polls every 10 s while the tab is visible, keeps the current
 * filters, and doesn't replace the list while you're reading an open
 * traceback or have text selected in it. The on/off choice is remembered in
 * this browser only.
 */
(function () {
    'use strict';
    var root = document.getElementById('sl-root');
    if (!root) return;

    var INTERVAL = 10000;
    var KEY = 'sl-live';
    var liveBtn = document.getElementById('sl-live');
    var updated = document.getElementById('sl-updated');
    var shownCount = document.getElementById('sl-shown');
    var timer = null;

    function entries() { return document.getElementById('sl-entries'); }

    function stamp() {
        if (!updated) return;
        var d = new Date();
        updated.textContent = 'Updated ' + d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', second: '2-digit' });
    }

    function busy() {
        var list = entries();
        if (!list) return false;
        var sel = window.getSelection && window.getSelection();
        if (sel && !sel.isCollapsed && list.contains(sel.anchorNode)) return true;
        return !!list.querySelector('details[open]');
    }

    function refresh() {
        if (document.hidden) return;
        if (busy()) {
            if (updated) updated.textContent = 'Waiting: close open details to update';
            return;
        }
        var url = new URL(window.location.href);
        url.searchParams.set('partial', '1');
        fetch(url.toString(), { credentials: 'same-origin', headers: { 'X-Requested-With': 'fetch' } })
            .then(function (r) { return r.ok ? r.text() : Promise.reject(r.status); })
            .then(function (html) {
                var doc = new DOMParser().parseFromString(html, 'text/html');
                var fresh = doc.getElementById('sl-entries');
                var current = entries();
                if (fresh && current) {
                    current.replaceWith(fresh);
                    if (shownCount) shownCount.textContent = fresh.getAttribute('data-sl-shown');
                    stamp();
                }
            })
            .catch(function () { if (updated) updated.textContent = 'Refresh failed, retrying'; });
    }

    function setLive(on) {
        if (!liveBtn) return;
        liveBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
        liveBtn.querySelector('[data-sl-live-label]').textContent = on ? 'Live' : 'Paused';
        if (timer) { clearInterval(timer); timer = null; }
        if (on) { timer = setInterval(refresh, INTERVAL); }
        try { localStorage.setItem(KEY, on ? '1' : '0'); } catch (e) { /* private mode */ }
    }

    if (liveBtn) {
        var saved = null;
        try { saved = localStorage.getItem(KEY); } catch (e) { /* private mode */ }
        setLive(saved === '1');
        liveBtn.addEventListener('click', function () {
            var on = liveBtn.getAttribute('aria-pressed') !== 'true';
            setLive(on);
            if (on) refresh();
        });
        document.addEventListener('visibilitychange', function () {
            if (!document.hidden && liveBtn.getAttribute('aria-pressed') === 'true') refresh();
        });
    }

    var copyBtn = document.getElementById('sl-copy');
    if (copyBtn) {
        copyBtn.addEventListener('click', function () {
            var plain = document.getElementById('sl-plain');
            var label = copyBtn.querySelector('[data-sl-copy-label]');
            if (!plain || !navigator.clipboard) return;
            navigator.clipboard.writeText(plain.value).then(function () {
                label.textContent = 'Copied';
            }, function () {
                label.textContent = 'Copy failed';
            }).then(function () {
                setTimeout(function () { label.textContent = 'Copy'; }, 2000);
            });
        });
    }

    var expandBtn = document.getElementById('sl-expand');
    if (expandBtn) {
        expandBtn.addEventListener('click', function () {
            var open = expandBtn.getAttribute('aria-pressed') !== 'true';
            expandBtn.setAttribute('aria-pressed', open ? 'true' : 'false');
            expandBtn.textContent = open ? 'Collapse details' : 'Expand details';
            var list = entries();
            if (!list) return;
            list.querySelectorAll('details.sl-detail').forEach(function (d) { d.open = open; });
        });
    }

    stamp();
})();
