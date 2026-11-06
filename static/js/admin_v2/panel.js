/* static/js/admin_v2/panel.js — the console's slide-out detail panel (Azure-style blade).
 *
 * API (data attributes; no inline script, so it works under the prod CSP):
 *
 *   <a href="{detail url}" data-ac-panel>   Click opens that page's panel
 *       fragment ({url}?panel=1) in the frame's #ac-panel instead of
 *       navigating. Cmd/Ctrl/Shift/middle click still open the full page, and
 *       without JS it is an ordinary link. Put it on the row's main link;
 *       the closest [data-ac-row] gets .is-open while its item is showing.
 *   [data-ac-panel-close]                   closes the panel (Esc does too).
 *   [role=tablist] [data-ac-tab]            tabs inside a fragment: each button
 *       has aria-controls naming a [role=tabpanel]; ←/→/Home/End move.
 *
 * The open item is kept in the URL hash (#panel=/admin-v2/…/), pushed to
 * history, so reload, Back and Forward restore it. Only same-origin paths
 * under the admin root are opened, and only responses carrying the
 * X-Admin-Panel header (src/admin_console.py panel_response) are inserted:
 * anything else (an expired session's redirect to the login page, an error)
 * falls back to loading the full page.
 *
 * Focus moves to the panel's title when it opens and back to the link that
 * opened it when it closes. On phones the panel is full-screen (CSS).
 */
(function () {
    'use strict';
    var host = document.querySelector('[data-ac-panel-host]');
    if (!host || !window.fetch) return;
    var shell = document.getElementById('ac-shell');
    var content = host.querySelector('[data-ac-panel-content]');
    var root = host.getAttribute('data-ac-admin-root') || '';
    var current = null;
    var trigger = null;
    var seq = 0;

    function adminPath(href) {
        try {
            var u = new URL(href, window.location.href);
            if (u.origin !== window.location.origin || u.pathname.indexOf(root) !== 0) return null;
            u.searchParams.delete('panel');
            return u.pathname + u.search;
        } catch (e) { return null; }
    }
    function fragmentUrl(path) {
        var u = new URL(path, window.location.href);
        u.searchParams.set('panel', '1');
        return u.pathname + u.search;
    }
    function hashPath() {
        var m = /(?:^#|&)panel=([^&]+)/.exec(window.location.hash);
        if (!m) return null;
        try { return adminPath(decodeURIComponent(m[1])); } catch (e) { return null; }
    }
    function writeHash(path, push) {
        var url = window.location.pathname + window.location.search + (path ? '#panel=' + encodeURIComponent(path) : '');
        var state = { acPanel: path || null };
        if (push) window.history.pushState(state, '', url); else window.history.replaceState(state, '', url);
    }
    function markRows(path) {
        document.querySelectorAll('[data-ac-panel]').forEach(function (a) {
            var on = !!path && adminPath(a.getAttribute('href')) === path;
            var row = a.closest('[data-ac-row]');
            if (row) row.classList.toggle('is-open', on);
            if (on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current');
        });
    }
    function linkFor(path) {
        var found = null;
        document.querySelectorAll('[data-ac-panel]').forEach(function (a) {
            if (!found && adminPath(a.getAttribute('href')) === path) found = a;
        });
        return found;
    }

    function showHost() {
        host.hidden = false;
        if (shell) shell.classList.add('is-panel-open');
    }

    function open(path, push) {
        var mine = ++seq;
        showHost();
        host.setAttribute('aria-busy', 'true');
        if (!current) {
            content.textContent = '';
            var loading = document.createElement('p');
            loading.className = 'ac-panel-loading';
            loading.textContent = 'Loading…';
            content.appendChild(loading);
        }
        fetch(fragmentUrl(path), {
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'text/html' }
        }).then(function (r) {
            if (!r.ok || r.headers.get('X-Admin-Panel') !== '1') throw new Error('not a panel');
            return r.text();
        }).then(function (html) {
            if (mine !== seq) return;
            content.innerHTML = html;  // same-origin admin fragment (checked header above)
            host.removeAttribute('aria-busy');
            if (path !== current && push) writeHash(path, true);
            current = path;
            markRows(path);
            setupTabs(content);
            content.scrollTop = 0;
            var title = content.querySelector('#ac-blade-title') || content.querySelector('h2');
            if (title) title.focus({ preventScroll: true });
        }).catch(function () {
            if (mine !== seq) return;
            window.location.href = path;  // no panel for this page: open it in full
        });
    }

    function close(push) {
        if (!current && host.hidden) return;
        seq++;
        var was = current;
        host.hidden = true;
        host.removeAttribute('aria-busy');
        content.textContent = '';
        if (shell) shell.classList.remove('is-panel-open');
        current = null;
        markRows(null);
        if (push !== false) writeHash(null, true);
        var back = (trigger && document.contains(trigger)) ? trigger : (was && linkFor(was));
        if (back) back.focus();
        trigger = null;
    }

    // ---- Tabs ------------------------------------------------------------
    function selectTab(tab, focus) {
        var list = tab.closest('[role="tablist"]');
        if (!list) return;
        list.querySelectorAll('[role="tab"]').forEach(function (t) {
            var on = t === tab;
            t.setAttribute('aria-selected', on ? 'true' : 'false');
            t.tabIndex = on ? 0 : -1;
            var pane = document.getElementById(t.getAttribute('aria-controls'));
            if (pane) pane.hidden = !on;
        });
        if (focus) tab.focus();
    }
    function setupTabs(scope) {
        scope.querySelectorAll('[role="tablist"]').forEach(function (list) {
            var selected = list.querySelector('[role="tab"][aria-selected="true"]') || list.querySelector('[role="tab"]');
            if (selected) selectTab(selected, false);
        });
    }
    content.addEventListener('click', function (e) {
        var tab = e.target.closest('[data-ac-tab]');
        if (tab) { e.preventDefault(); selectTab(tab, false); }
    });
    content.addEventListener('keydown', function (e) {
        var tab = e.target.closest('[role="tab"]');
        if (!tab) return;
        var tabs = Array.prototype.slice.call(tab.closest('[role="tablist"]').querySelectorAll('[role="tab"]'));
        var i = tabs.indexOf(tab);
        var next = null;
        if (e.key === 'ArrowRight') next = tabs[(i + 1) % tabs.length];
        else if (e.key === 'ArrowLeft') next = tabs[(i - 1 + tabs.length) % tabs.length];
        else if (e.key === 'Home') next = tabs[0];
        else if (e.key === 'End') next = tabs[tabs.length - 1];
        if (next) { e.preventDefault(); selectTab(next, true); }
    });

    // ---- Opening, closing, history ---------------------------------------------
    document.addEventListener('click', function (e) {
        if (e.target.closest('[data-ac-panel-close]')) { e.preventDefault(); close(true); return; }
        var a = e.target.closest('a[data-ac-panel]');
        if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        var path = adminPath(a.getAttribute('href'));
        if (!path) return;
        e.preventDefault();
        trigger = a;
        if (path === current) { var t = content.querySelector('#ac-blade-title'); if (t) t.focus(); return; }
        open(path, true);
    });

    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || host.hidden) return;
        var palette = document.getElementById('admin-v2-palette');
        if (palette && !palette.classList.contains('hidden')) return;
        e.preventDefault();
        close(true);
    });

    window.addEventListener('popstate', function () {
        var path = hashPath();
        if (path && path !== current) open(path, false);
        else if (!path && current) close(false);
    });

    var initial = hashPath();
    if (initial) open(initial, false);
})();
