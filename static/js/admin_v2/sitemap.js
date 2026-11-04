/* static/js/admin_v2/sitemap.js — v4.4.4. The admin-v2 Site map page.
 *
 * Data: the json_script #sm-data (src/view/admin_v2_sitemap.py client_payload):
 * routes [{n name, k kind, l label, w why}] and nodes [{i id | s id past the
 * parent's, p parent index (-1 top level), r route index, l label, m unrouted
 * parent}]. Details for one node come from the node endpoint, the "who can
 * open" verdict from the check endpoint; both are GETs that change nothing.
 * Changes are links to the server-rendered confirm panel (?confirm=), never
 * POSTs from here.
 *
 * Wide screens: a collapsible tree drawn in SVG, pan by dragging, zoom with
 * Ctrl/Cmd + wheel or the buttons. Narrow screens (< 1024px): the same tree as
 * an indented list. No inline handlers, no libraries (CSP: script-src 'self').
 */
(function () {
    'use strict';
    var dataEl = document.getElementById('sm-data');
    var cfg = document.getElementById('sm-config');
    if (!dataEl || !cfg) return;
    var DATA = JSON.parse(dataEl.textContent);
    var routes = DATA.routes;
    var SHOWN = { page: true };
    var NODE_W = 220, NODE_H = 30, COL_W = 270, ROW_H = 38;

    // ── Nodes ────────────────────────────────────────────────────────────
    var nodes = DATA.nodes.map(function (n, idx) {
        var r = routes[n.r];
        return { idx: idx, raw: n, parent: n.p, route: r, kind: r.k, name: r.n || '',
                 label: n.l || r.l, missing: n.m || '', children: [], id: null };
    });
    function resolveId(n, depth) {
        if (n.id !== null) return n.id;
        if (n.raw.i !== undefined || n.parent < 0 || depth > 50) { n.id = n.raw.i || n.raw.s || ''; return n.id; }
        n.id = resolveId(nodes[n.parent], depth + 1) + n.raw.s;
        return n.id;
    }
    var byId = {};
    nodes.forEach(function (n) { resolveId(n, 0); byId[n.id] = n; });
    var root = { idx: -1, id: '', label: 'Site', kind: 'root', name: '', children: [], parent: null, missing: '' };
    nodes.forEach(function (n) { (n.parent >= 0 ? nodes[n.parent] : root).children.push(n); n.up = n.parent >= 0 ? nodes[n.parent] : root; });
    function sortKids(list) {
        list.sort(function (a, b) {
            if (a.name === 'home') return -1;
            if (b.name === 'home') return 1;
            return a.id < b.id ? -1 : (a.id > b.id ? 1 : 0);
        });
    }
    sortKids(root.children);
    nodes.forEach(function (n) { sortKids(n.children); });

    var expanded = new Set([root]);
    var showAll = false;
    var selected = null;
    var matches = new Set();
    var links = [];   // node objects linked to the selected node (dashed edges)

    function kindShown(n) { return n.kind === 'root' || showAll || SHOWN[n.kind]; }
    // A hidden kind still shows when something under it does.
    var visCache = new Map();
    function visible(n) {
        if (visCache.has(n)) return visCache.get(n);
        var v = kindShown(n) || n.children.some(visible);
        visCache.set(n, v);
        return v;
    }
    function visibleKids(n) { return n.children.filter(visible); }

    // ── Helpers ──────────────────────────────────────────────────────────
    var SVGNS = 'http://www.w3.org/2000/svg';
    function svg(tag, attrs) {
        var e = document.createElementNS(SVGNS, tag);
        for (var k in attrs) e.setAttribute(k, attrs[k]);
        return e;
    }
    function h(tag, attrs, kids) {
        var e = document.createElement(tag);
        if (attrs) for (var k in attrs) {
            if (k === 'text') e.textContent = attrs[k];
            else if (k === 'class') e.className = attrs[k];
            else e.setAttribute(k, attrs[k]);
        }
        (kids || []).forEach(function (c) { if (c) e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
        return e;
    }
    function trunc(s, n) { return s.length > n ? s.slice(0, n - 1) + '…' : s; }
    function qs(params) {
        return Object.keys(params).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); }).join('&');
    }
    function getJSON(url, params) {
        return fetch(url + '?' + qs(params), { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.json(); });
    }
    function ancestors(n) { var out = []; for (var p = n.up; p; p = p.up) out.push(p); return out; }
    function reveal(n) { ancestors(n).forEach(function (a) { expanded.add(a); }); if (!kindShown(n)) setShowAll(true); }

    // ── Layout (tidy tree: leaves in rows, parents centred on their children) ──
    var pos = new Map();
    function layout() {
        pos = new Map();
        var row = 0;
        function walk(n, depth) {
            var kids = expanded.has(n) ? visibleKids(n) : [];
            var y;
            if (!kids.length) { y = row * ROW_H; row++; }
            else {
                // A parent sits level with its first child (an outline, like a
                // file tree), so opening a branch pushes the rows below it down
                // instead of moving the parent to the middle of a long list.
                var ys = kids.map(function (k) { return walk(k, depth + 1); });
                y = ys[0];
            }
            pos.set(n, { x: depth * COL_W, y: y });
            return y;
        }
        walk(root, 0);
        return row;
    }

    // ── SVG view ─────────────────────────────────────────────────────────
    var canvas = document.getElementById('sm-canvas');
    var list = document.getElementById('sm-list');
    var wide = window.matchMedia('(min-width: 1024px)');
    var view = { x: 40, y: 40, k: 1 };
    var svgEl = null, gEl = null;

    function applyView() { if (gEl) gEl.setAttribute('transform', 'translate(' + view.x + ',' + view.y + ') scale(' + view.k + ')'); }

    function drawSVG() {
        layout();
        canvas.textContent = '';
        svgEl = svg('svg', { role: 'tree', 'aria-label': 'Site map' });
        gEl = svg('g', {});
        svgEl.appendChild(gEl);
        var edges = svg('g', {}), linkG = svg('g', {}), boxes = svg('g', {});
        gEl.appendChild(edges); gEl.appendChild(linkG); gEl.appendChild(boxes);
        pos.forEach(function (p, n) {
            if (n.up && pos.has(n.up)) {
                var q = pos.get(n.up);
                // An elbow (across, down the parent's trunk, across), so a parent's
                // children hang off one straight vertical line instead of a fan of curves.
                var x1 = q.x + NODE_W, y1 = q.y + NODE_H / 2, x2 = p.x, y2 = p.y + NODE_H / 2, mx = (x1 + x2) / 2;
                var r = Math.min(8, Math.abs(y2 - y1) / 2), dir = y2 > y1 ? 1 : -1;
                var d = Math.abs(y2 - y1) < 1 ? 'M' + x1 + ',' + y1 + ' H' + x2
                    : 'M' + x1 + ',' + y1 + ' H' + (mx - r) + ' Q' + mx + ',' + y1 + ' ' + mx + ',' + (y1 + dir * r) +
                      ' V' + (y2 - dir * r) + ' Q' + mx + ',' + y2 + ' ' + (mx + r) + ',' + y2 + ' H' + x2;
                edges.appendChild(svg('path', { 'class': 'sm-edge', d: d }));
            }
            boxes.appendChild(nodeEl(n, p));
        });
        // Pages that link to the selected one are outlined (sm-linked) rather
        // than joined by lines: with dozens of links across columns, lines
        // tangle. The panel lists them by name.
        canvas.appendChild(svgEl);
        applyView();
    }

    function nodeEl(n, p) {
        var cls = 'sm-node' + (n === selected ? ' sm-selected' : '') + (matches.has(n) ? ' sm-match' : '') +
            (n !== selected && links.indexOf(n) >= 0 ? ' sm-linked' : '') +
            (n.kind !== 'root' && !SHOWN[n.kind] ? ' sm-hiddenkind' : '');
        var g = svg('g', { 'class': cls, transform: 'translate(' + p.x + ',' + p.y + ')', role: 'treeitem',
                           'aria-selected': n === selected ? 'true' : 'false' });
        var t = svg('title', {});
        t.textContent = n.label + '\n' + (n.id || '/') + (n.name ? '\n' + n.name : '') + (n.route && n.route.w ? '\n' + n.route.w : '') +
            (n.missing ? '\nNo page at ' + n.missing : '');
        g.appendChild(t);
        g.appendChild(svg('rect', { 'class': 'sm-box', width: NODE_W, height: NODE_H, rx: 6 }));
        g.appendChild(svg('circle', { 'class': 'sm-dot-' + n.kind, cx: 10, cy: NODE_H / 2, r: 4 }));
        var label = svg('text', { x: 20, y: 10 });
        label.textContent = trunc(n.label, 30);
        g.appendChild(label);
        var sub = svg('text', { 'class': 'sm-sub', x: 20, y: 22 });
        sub.textContent = trunc(n.id || 'Every route, by path', 36);
        g.appendChild(sub);
        if (n.missing) {
            var w = svg('text', { 'class': 'sm-warn', x: NODE_W - 26, y: NODE_H / 2 });
            w.textContent = '!';
            g.appendChild(w);
        }
        g.addEventListener('click', function (e) { e.stopPropagation(); if (n.kind !== 'root') select(n); });
        var kids = visibleKids(n);
        if (kids.length && n.kind !== 'root') {
            var tg = svg('g', { 'class': 'sm-toggle', transform: 'translate(' + NODE_W + ',' + NODE_H / 2 + ')' });
            var label2 = expanded.has(n) ? '−' : '+';
            tg.appendChild(svg('circle', { r: 9 }));
            var tt = svg('text', {});
            tt.textContent = label2;
            tg.appendChild(tt);
            var tt2 = svg('title', {});
            tt2.textContent = (expanded.has(n) ? 'Close' : 'Open') + ' (' + kids.length + ' under it)';
            tg.appendChild(tt2);
            tg.addEventListener('click', function (e) { e.stopPropagation(); toggle(n); });
            g.appendChild(tg);
        }
        g.addEventListener('dblclick', function (e) { e.stopPropagation(); if (kids.length) toggle(n); });
        return g;
    }

    function center(n) {
        if (!pos.has(n) || !canvas.clientWidth) return;
        var p = pos.get(n);
        view.x = canvas.clientWidth / 2 - (p.x + NODE_W / 2) * view.k;
        view.y = canvas.clientHeight / 2 - (p.y + NODE_H / 2) * view.k;
        applyView();
    }
    function fit() {
        var rows = layout();
        var maxX = 0;
        pos.forEach(function (p) { maxX = Math.max(maxX, p.x + NODE_W + 20); });
        var w = canvas.clientWidth || 800, hgt = canvas.clientHeight || 600;
        view.k = Math.max(0.15, Math.min(1.2, Math.min(w / (maxX + 60), hgt / (rows * ROW_H + 60))));
        view.x = 30; view.y = 30;
        applyView();
    }
    function home() { view.x = 40; view.y = 40; view.k = 1; applyView(); }
    function zoomAt(f, cx, cy) {
        var k = Math.max(0.1, Math.min(2.5, view.k * f));
        view.x = cx - (cx - view.x) * (k / view.k);
        view.y = cy - (cy - view.y) * (k / view.k);
        view.k = k;
        applyView();
    }

    // Pan and zoom
    var drag = null;
    canvas.addEventListener('pointerdown', function (e) {
        if (e.target.closest && e.target.closest('.sm-node')) return;
        drag = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
        canvas.classList.add('sm-dragging');
        canvas.setPointerCapture(e.pointerId);
    });
    canvas.addEventListener('pointermove', function (e) {
        if (!drag) return;
        view.x = drag.vx + e.clientX - drag.x;
        view.y = drag.vy + e.clientY - drag.y;
        applyView();
    });
    function endDrag() { drag = null; canvas.classList.remove('sm-dragging'); }
    canvas.addEventListener('pointerup', endDrag);
    canvas.addEventListener('pointercancel', endDrag);
    canvas.addEventListener('wheel', function (e) {
        e.preventDefault();
        var rect = canvas.getBoundingClientRect();
        if (e.ctrlKey || e.metaKey) zoomAt(Math.exp(-e.deltaY * 0.0025), e.clientX - rect.left, e.clientY - rect.top);
        else { view.x -= e.deltaX; view.y -= e.deltaY; applyView(); }
    }, { passive: false });

    // ── List view (narrow screens) ───────────────────────────────────────
    function drawList() {
        list.textContent = '';
        function item(n) {
            var kids = visibleKids(n);
            var open = expanded.has(n);
            var li = h('li', { role: 'treeitem', 'aria-expanded': kids.length ? String(open) : null });
            if (!kids.length) li.removeAttribute('aria-expanded');
            var rowEl = h('div', { 'class': 'flex items-center gap-2 py-1' });
            var tg = h('button', { type: 'button', 'class': 'w-6 text-center text-gray-500 dark:text-gray-400', 'aria-label': open ? 'Close' : 'Open', text: kids.length ? (open ? '−' : '+') : '' });
            if (kids.length) tg.addEventListener('click', function () { toggle(n); });
            else tg.disabled = true;
            rowEl.appendChild(tg);
            rowEl.appendChild(h('span', { 'class': 'sm-kind flex-shrink-0 ' + n.kind }));
            var b = h('button', { type: 'button', 'class': 'text-left min-w-0 rounded px-1 ' +
                (n === selected ? 'bg-primary-50 dark:bg-primary-900/40 font-semibold ' : 'hover:bg-gray-100 dark:hover:bg-gray-700 ') +
                (matches.has(n) ? 'bg-amber-50 dark:bg-amber-900/20 ' : '') + 'text-gray-900 dark:text-gray-100' });
            b.appendChild(h('span', { 'class': 'block truncate', text: n.label + (n.missing ? ' (!)' : '') }));
            b.appendChild(h('span', { 'class': 'block truncate text-xs text-gray-500 dark:text-gray-400 font-mono', text: n.id || '/' }));
            if (n.kind !== 'root') b.addEventListener('click', function () { select(n); });
            rowEl.appendChild(b);
            li.appendChild(rowEl);
            if (open && kids.length) {
                var ul = h('ul', { role: 'group' });
                kids.forEach(function (k) { ul.appendChild(item(k)); });
                li.appendChild(ul);
            }
            return li;
        }
        var ul = h('ul', { role: 'tree', 'aria-label': 'Site map' });
        visibleKids(root).forEach(function (k) { ul.appendChild(item(k)); });
        list.appendChild(ul);
    }

    function draw() {
        visCache = new Map();
        if (wide.matches) { canvas.classList.remove('hidden'); list.classList.add('hidden'); drawSVG(); }
        else { list.classList.remove('hidden'); canvas.classList.add('hidden'); drawList(); }
    }
    if (wide.addEventListener) wide.addEventListener('change', draw);

    // The top "Site" node always stays open: closing it would hide every page
    // and leave the map blank, so it has no open/close control.
    function toggle(n) {
        if (n.kind === 'root') return;
        var before = pos.get(n);
        if (expanded.has(n)) expanded.delete(n); else expanded.add(n);
        draw();
        // Keep the box that was clicked where it was on screen.
        var after = pos.get(n);
        if (before && after && gEl) { view.x -= (after.x - before.x) * view.k; view.y -= (after.y - before.y) * view.k; applyView(); }
    }
    function setShowAll(v) {
        showAll = v;
        document.getElementById('sm-show-all').checked = v;
        visCache = new Map();
    }

    // ── Toolbar ──────────────────────────────────────────────────────────
    document.querySelectorAll('[data-sm]').forEach(function (b) {
        b.addEventListener('click', function () {
            var a = b.getAttribute('data-sm');
            if (a === 'expand') { visCache = new Map(); expanded = new Set([root]); nodes.forEach(function (n) { if (n.children.length && visible(n)) expanded.add(n); }); home(); draw(); }
            else if (a === 'collapse') { expanded = new Set([root]); home(); draw(); }
            else if (a === 'zoom-in') zoomAt(1.25, canvas.clientWidth / 2, canvas.clientHeight / 2);
            else if (a === 'zoom-out') zoomAt(0.8, canvas.clientWidth / 2, canvas.clientHeight / 2);
            else if (a === 'fit') fit();
        });
    });
    document.getElementById('sm-show-all').addEventListener('change', function (e) { setShowAll(e.target.checked); draw(); });

    // Search: highlight every match, list the first few, Enter / click goes to one.
    var input = document.getElementById('sm-search');
    var results = document.getElementById('sm-search-results');
    var hits = [], active = -1;
    function runSearch() {
        var words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
        matches = new Set();
        hits = [];
        if (words.length) {
            nodes.forEach(function (n) {
                var hay = (n.label + ' ' + n.name + ' ' + n.id).toLowerCase();
                if (words.every(function (w) { return hay.indexOf(w) >= 0; })) { matches.add(n); hits.push(n); }
            });
            hits.sort(function (a, b) { return (kindShown(b) - kindShown(a)) || (a.id.length - b.id.length); });
        }
        active = hits.length ? 0 : -1;
        renderHits();
        draw();
    }
    function renderHits() {
        results.textContent = '';
        if (!input.value.trim()) { results.classList.add('hidden'); input.setAttribute('aria-expanded', 'false'); return; }
        if (!hits.length) results.appendChild(h('li', { 'class': 'px-3 py-2 text-gray-500 dark:text-gray-400', text: 'Nothing matches.' }));
        hits.slice(0, 12).forEach(function (n, i) {
            var li = h('li', { role: 'option', 'aria-selected': String(i === active),
                'class': 'px-3 py-1.5 cursor-pointer ' + (i === active ? 'bg-gray-100 dark:bg-gray-700' : 'hover:bg-gray-100 dark:hover:bg-gray-700') });
            li.appendChild(h('span', { 'class': 'block text-gray-900 dark:text-gray-100', text: n.label }));
            li.appendChild(h('span', { 'class': 'block text-xs font-mono text-gray-500 dark:text-gray-400 truncate', text: n.id + (n.name ? ' · ' + n.name : '') }));
            li.addEventListener('mousedown', function (e) { e.preventDefault(); go(n); });
            results.appendChild(li);
        });
        if (hits.length > 12) results.appendChild(h('li', { 'class': 'px-3 py-1 text-xs text-gray-500 dark:text-gray-400', text: (hits.length - 12) + ' more highlighted in the map.' }));
        results.classList.remove('hidden');
        input.setAttribute('aria-expanded', 'true');
    }
    function go(n) {
        results.classList.add('hidden');
        input.setAttribute('aria-expanded', 'false');
        reveal(n);
        select(n);
    }
    var searchTimer = null;
    input.addEventListener('input', function () { clearTimeout(searchTimer); searchTimer = setTimeout(runSearch, 150); });
    input.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
            e.preventDefault();
            if (!hits.length) return;
            active = (active + (e.key === 'ArrowDown' ? 1 : -1) + Math.min(hits.length, 12)) % Math.min(hits.length, 12);
            renderHits();
        } else if (e.key === 'Enter') {
            e.preventDefault();
            clearTimeout(searchTimer);
            if (!hits.length) runSearch();
            if (hits[active]) go(hits[active]);
        } else if (e.key === 'Escape') { results.classList.add('hidden'); }
    });
    input.addEventListener('blur', function () { setTimeout(function () { results.classList.add('hidden'); }, 100); });

    // ── Details panel ────────────────────────────────────────────────────
    var panel = document.getElementById('sm-panel');
    var NODE_URL = cfg.getAttribute('data-node-url');
    var OPEN_URL = cfg.getAttribute('data-open-url');
    var CHECK_URL = cfg.getAttribute('data-check-url');
    var MEMBERS_URL = cfg.getAttribute('data-members-url');
    var token = 0;

    function select(n) {
        selected = n;
        links = [];
        reveal(n);
        draw();
        if (wide.matches) center(n);
        try { history.replaceState(null, '', '?' + qs({ node: n.id })); } catch (e) { /* ignore */ }
        var mine = ++token;
        panel.textContent = '';
        panel.appendChild(h('p', { 'class': 'text-gray-500 dark:text-gray-400', text: 'Loading ' + n.label + '…' }));
        getJSON(NODE_URL, { id: n.id }).then(function (d) {
            if (mine !== token) return;
            if (d.error) { panel.textContent = d.error; return; }
            links = d.linked_from.map(function (l) { return byId[l.id]; }).filter(Boolean);
            renderPanel(n, d);
            draw();
        }).catch(function () { if (mine === token) panel.textContent = "Couldn't load the details."; });
    }

    function section(title, kids) {
        return h('section', { 'class': 'mt-5' }, [
            h('h3', { 'class': 'text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400', text: title })
        ].concat(kids));
    }
    function muted(text) { return h('p', { 'class': 'mt-1 text-xs text-gray-500 dark:text-gray-400', text: text }); }
    function pill(text, tone) {
        var tones = {
            ok: 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300',
            bad: 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300',
            warn: 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300',
            info: 'bg-gray-50 dark:bg-gray-700 text-gray-700 dark:text-gray-200'
        };
        return h('span', { 'class': 'inline-block ml-1 px-1.5 py-0.5 rounded text-xs font-medium ' + (tones[tone] || tones.info), text: text });
    }
    function confirmLink(n, kind, target, text) {
        return h('a', { 'class': 'btn-secondary btn-sm', href: '?' + qs({ node: n.id, confirm: kind, target: target }), text: text });
    }
    function nodeLink(l) {
        var a = h('a', { href: '?' + qs({ node: l.id }), title: l.id, 'class': 'text-primary-600 dark:text-primary-400 hover:underline', text: l.label });
        a.addEventListener('click', function (e) { var t = byId[l.id]; if (t) { e.preventDefault(); select(t); } });
        return a;
    }
    var WHERE = { code: 'Set in code', env: 'Set in the environment (ADMIN_V2_USER_IDS)', member: "Set per member (their admin-v2 page)", data: 'Set in data (adjustable below where a switch exists)' };

    function renderPanel(n, d) {
        panel.textContent = '';
        panel.appendChild(h('h2', { 'class': 'text-lg font-semibold text-gray-900 dark:text-gray-100', text: d.label }));
        panel.appendChild(h('p', { 'class': 'mt-0.5 font-mono text-xs break-all text-gray-600 dark:text-gray-300', text: d.path }));
        var meta = h('p', { 'class': 'mt-1 text-xs text-gray-500 dark:text-gray-400' }, [
            d.name ? h('code', { 'class': 'font-mono', text: d.name }) : 'Unnamed route', ' · ', d.kind_label + (d.why ? ' (' + d.why + ')' : '')]);
        panel.appendChild(meta);
        panel.appendChild(h('p', { 'class': 'mt-1 text-xs text-gray-500 dark:text-gray-400 break-all' }, [
            'View ', h('code', { 'class': 'font-mono', text: d.view }), ' · ', h('code', { 'class': 'font-mono', text: d.where })]));
        if (d.kind === 'group') {
            panel.appendChild(h('p', { 'class': 'mt-3 rounded-lg px-3 py-2 text-xs bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300',
                text: 'No page at ' + d.path + ': it only groups the ' + n.children.length + ' route' +
                      (n.children.length === 1 ? '' : 's') + ' under it.' +
                      (d.missing ? ' Someone who shortens an address to it sees "not found".' : '') }));
            return;
        }
        if (n.up && n.up.kind === 'group') panel.appendChild(muted('Nothing is routed at ' + n.up.id + ' (the group above).'));
        // Opens in this tab; the page shows a "Back to the site map" bar (admin_v2_sitemap_open).
        if (d.open_url) panel.appendChild(h('p', { 'class': 'mt-3 flex flex-wrap gap-2' }, [
            h('a', { 'class': 'btn-primary btn-sm', href: OPEN_URL + '?node=' + encodeURIComponent(d.id), text: 'Open page' }),
            h('a', { 'class': 'btn-secondary btn-sm', href: d.open_url, target: '_blank', rel: 'noopener', text: 'Open in a new tab' })]));
        else if (d.path.indexOf('<') >= 0) panel.appendChild(muted('No "Open page" link: the path still has a parameter to fill in.'));

        if (d.missing) {
            var up = n.up && n.up.id ? ' It is shown under ' + n.up.id + '.' : '';
            panel.appendChild(h('p', { 'class': 'mt-3 rounded-lg px-3 py-2 text-xs bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300',
                text: 'No page at ' + d.missing + ': someone who shortens this address to it sees "not found".' + up }));
        }

        // Guards
        var guardKids = [];
        if (!d.layers.length) guardKids.push(muted('No decorators: the view decides everything itself.'));
        var ul = h('ul', { 'class': 'mt-2 space-y-2' });
        d.layers.forEach(function (l) {
            var tone = l.type === 'guard' ? 'info' : (l.type === 'unknown' ? 'warn' : 'info');
            var li = h('li', { 'class': 'border-l-4 pl-3 ' + (l.type === 'guard' ? 'border-primary-600' : (l.type === 'unknown' ? 'border-amber-500' : 'border-gray-200 dark:border-gray-600')) }, [
                h('p', { 'class': 'text-gray-900 dark:text-gray-100 font-medium' }, [l.label + (l.args ? ': ' + l.args : ''), l.type !== 'guard' ? pill(l.type === 'info' ? 'not an access check' : 'unknown', tone) : null]),
                l.rule ? muted(l.rule) : null,
                h('p', { 'class': 'mt-0.5 text-xs text-gray-500 dark:text-gray-400' }, [
                    (WHERE[l.where] || 'Set in code') + ': ', h('code', { 'class': 'font-mono break-all', text: '@' + l.name + ' on ' + l.source }),
                    ' (defined in ', h('code', { 'class': 'font-mono break-all', text: l.defined }), ')'])
            ]);
            ul.appendChild(li);
        });
        if (d.layers.length) guardKids.push(ul);
        if (d.body_checks.length) guardKids.push(muted("The view's own code also checks access (" + d.body_checks.join('; ') + '). Set in code, in ' + d.where + '; not evaluated below.'));
        panel.appendChild(section('Who decides access', guardKids));

        // Switches
        var sw = [];
        d.page_switches.forEach(function (t) {
            var row = h('div', { 'class': 'mt-2 flex flex-wrap items-center gap-2' }, [
                h('span', { 'class': 'text-gray-900 dark:text-gray-100', text: 'Page switch ' }), h('code', { 'class': 'font-mono text-xs text-gray-700 dark:text-gray-200', text: t.url_name }),
                pill(t.enabled ? 'On' : 'Off', t.enabled ? 'ok' : 'bad')]);
            if (t.exists) row.appendChild(confirmLink(n, 'page', t.url_name, t.enabled ? 'Turn off…' : 'Turn on…'));
            sw.push(row);
            if (!t.exists) sw.push(muted('No PageToggle row, so it is always on (rows are added by the seed_feature_flags command, then appear on the Maintenance page).'));
            if (!t.enforced) sw.push(muted('This PageToggle row exists but the view has no @require_page_enabled, so switching it changes nothing on this page.'));
            if (t.last) sw.push(muted('Last switched by ' + t.last + '.'));
        });
        d.feature_flags.forEach(function (f) {
            var row = h('div', { 'class': 'mt-2 flex flex-wrap items-center gap-2' }, [
                h('span', { 'class': 'text-gray-900 dark:text-gray-100', text: 'Feature flag ' }), h('code', { 'class': 'font-mono text-xs text-gray-700 dark:text-gray-200', text: f.name }),
                pill(f.enabled ? 'On' : 'Off', f.enabled ? 'ok' : 'bad')]);
            if (f.exists) row.appendChild(confirmLink(n, 'flag', f.name, f.enabled ? 'Turn off…' : 'Turn on…'));
            sw.push(row);
            if (!f.exists) sw.push(muted('No FeatureFlag row: its default applies (FeatureFlag.DISABLED_BY_DEFAULT). Not adjustable here.'));
        });
        if (!sw.length) sw.push(muted('No page switch or feature flag guards this page.'));
        panel.appendChild(section('Switches', sw));

        // Pledges
        if (d.pledge) {
            var p = d.pledge, pk = [];
            if (p.always_allowed) pk.push(muted('Always open to pledges: they need it to sign in or set up their account. Set in code (src/middleware/pledge_pages.py ALWAYS_ALLOWED).'));
            else if (p.row) {
                pk.push(h('div', { 'class': 'mt-2 flex flex-wrap items-center gap-2' }, [
                    h('span', { 'class': 'text-gray-900 dark:text-gray-100', text: 'Restricted: allowed in ' + (p.row.phases.length ? p.row.phases.join(', ') : 'no phase') }),
                    confirmLink(n, 'pledge_set', p.url_name, 'Change…'), confirmLink(n, 'pledge_remove', p.url_name, 'Remove…')]));
            } else if (p.restrictable) {
                pk.push(h('div', { 'class': 'mt-2 flex flex-wrap items-center gap-2' }, [
                    h('span', { 'class': 'text-gray-900 dark:text-gray-100', text: 'Open to every pledge (no restriction).' }),
                    confirmLink(n, 'pledge_set', p.url_name, 'Restrict…')]));
            } else pk.push(muted("Can't be restricted (admin-v2 and honeypot pages: pledges can never open them anyway)."));
            if (p.decorator) pk.push(muted('Also checked by @pledge_page_allowed on the view, with the same rule.'));
            panel.appendChild(section('Pledges', pk));
        }

        // Committee
        if (d.committee) {
            var c = d.committee, ck = [];
            ck.push(h('p', { 'class': 'mt-1 text-gray-900 dark:text-gray-100' }, [c.name + ' ', pill(c.state, c.state === 'active' ? 'ok' : 'warn'), c.exec_board ? pill('exec board: every member is chair-level', 'info') : null]));
            var chairs = h('p', { 'class': 'mt-1 text-gray-700 dark:text-gray-200' }, ['Chairs: ']);
            if (!c.chairs.length) chairs.appendChild(document.createTextNode('none'));
            c.chairs.forEach(function (ch, i) {
                if (i) chairs.appendChild(document.createTextNode(', '));
                chairs.appendChild(h('a', { href: ch.url, 'class': 'text-primary-600 dark:text-primary-400 hover:underline', text: ch.name }));
            });
            ck.push(chairs);
            ck.push(muted((c.admin ? 'Committee admin: ' + c.admin + '. ' : '') + (c.vp_role ? 'VP role: ' + c.vp_role + '. ' : '') +
                c.members + ' members, ' + c.voting + ' voting, ' + c.advisors + ' advisors.'));
            if (c.grants.length) {
                var gl = h('ul', { 'class': 'mt-1 text-xs text-gray-700 dark:text-gray-200' });
                c.grants.forEach(function (g) { gl.appendChild(h('li', {}, [h('a', { href: g.url, 'class': 'hover:underline', text: g.name }), ': ' + g.held])); });
                ck.push(muted('Granted past reading:'));
                ck.push(gl);
            }
            var ed = h('ul', { 'class': 'mt-2 space-y-1' });
            c.editors.forEach(function (e) { ed.appendChild(h('li', {}, [h('a', { href: e.url, 'class': 'text-primary-600 dark:text-primary-400 hover:underline', text: e.label + ' →' })])); });
            ck.push(ed);
            panel.appendChild(section('Committee permissions', ck));
        }

        panel.appendChild(section('Who can open this?', [whoBox(n)]));

        // Links
        var lk = [];
        if (d.in_nav) lk.push(muted('In the site navigation (base.html), so linked from every page.'));
        function linkList(title, items) {
            if (!items.length) return;
            var p2 = h('p', { 'class': 'mt-1 text-gray-700 dark:text-gray-200' }, [title + ': ']);
            items.forEach(function (l, i) { if (i) p2.appendChild(document.createTextNode(', ')); p2.appendChild(nodeLink(l)); });
            lk.push(p2);
        }
        linkList('Linked from', d.linked_from);
        linkList('Links to', d.links_to);
        if (!lk.length) lk.push(muted('No {% url %} links to or from this page were found in the templates.'));
        else lk.push(muted('From {% url %} tags in the templates the views render. Dashed lines in the map are the "linked from" pages.'));
        panel.appendChild(section('Links', lk));

        if (d.site_wide.length) {
            var det = h('details', { 'class': 'mt-5 text-xs text-gray-500 dark:text-gray-400' }, [h('summary', { 'class': 'cursor-pointer', text: 'Site-wide checks not evaluated here' })]);
            var sl = h('ul', { 'class': 'mt-1 space-y-1' });
            d.site_wide.forEach(function (s) { sl.appendChild(h('li', {}, [h('code', { 'class': 'font-mono', text: s.name }), ': ' + s.about])); });
            det.appendChild(sl);
            panel.appendChild(det);
        }
    }

    function whoBox(n) {
        var box = h('div', { 'class': 'mt-2' });
        var inp = h('input', { type: 'search', placeholder: 'Find a member', autocomplete: 'off',
            'class': 'w-full rounded-lg border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-gray-100 text-sm', 'aria-label': 'Find a member to check' });
        var res = h('ul', { 'class': 'mt-1' });
        var out = h('div', { 'class': 'mt-2' });
        box.appendChild(inp); box.appendChild(res); box.appendChild(out);
        var timer = null;
        inp.addEventListener('input', function () {
            clearTimeout(timer);
            timer = setTimeout(function () {
                getJSON(MEMBERS_URL, { q: inp.value }).then(function (d) {
                    res.textContent = '';
                    (d.members || []).forEach(function (m) {
                        var b = h('button', { type: 'button', 'class': 'w-full text-left px-2 py-1 rounded hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-900 dark:text-gray-100' },
                            [m.name + ' ', h('span', { 'class': 'text-xs text-gray-500 dark:text-gray-400', text: m.meta })]);
                        b.addEventListener('click', function () { res.textContent = ''; inp.value = m.name; check(n, m.id, out); });
                        res.appendChild(h('li', {}, [b]));
                    });
                });
            }, 200);
        });
        return box;
    }

    function check(n, memberId, out) {
        out.textContent = 'Checking…';
        getJSON(CHECK_URL, { id: n.id, member: memberId }).then(function (d) {
            out.textContent = '';
            if (d.error) { out.textContent = d.error; return; }
            var tone = { yes: 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300',
                         no: 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300',
                         unknown: 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300',
                         guards: 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-300' }[d.verdict];
            var word = { yes: 'Yes', no: 'No', unknown: "Can't tell", guards: 'Passes the guards' }[d.verdict];
            out.appendChild(h('div', { 'class': 'rounded-lg px-3 py-2 ' + tone }, [
                h('p', { 'class': 'font-semibold' }, [word + ': ', h('a', { href: d.member.url, 'class': 'hover:underline', text: d.member.name }), ' (' + d.member.meta + ')']),
                h('p', { 'class': 'text-xs mt-0.5', text: d.reason })]));
            var ul = h('ul', { 'class': 'mt-2 space-y-1 text-xs' });
            d.steps.forEach(function (s) {
                var mark = { pass: '✓', deny: '✗', unknown: '?', error: '?' }[s.result];
                ul.appendChild(h('li', { 'class': s.result === 'pass' ? 'text-green-700 dark:text-green-300' : (s.result === 'deny' ? 'text-red-700 dark:text-red-300' : 'text-amber-700 dark:text-amber-300') },
                    [mark + ' ' + s.label + (s.detail ? ': ' + s.detail : '')]));
            });
            if (d.steps.length) out.appendChild(ul);
            d.notes.forEach(function (t) { out.appendChild(muted(t)); });
            if (d.standing) {
                var st = h('p', { 'class': 'mt-2 text-xs text-gray-700 dark:text-gray-200' }, ['On ' + d.standing.committee + ': ']);
                st.appendChild(document.createTextNode(d.standing.facts.filter(function (f) { return f[1]; }).map(function (f) { return f[0]; }).join(', ') || 'no role'));
                out.appendChild(st);
                out.appendChild(muted("Context for the view's own checks, which read these. Not a verdict."));
            }
        }).catch(function () { out.textContent = "Couldn't run the check."; });
    }

    // ── Start ────────────────────────────────────────────────────────────
    draw();
    var start = cfg.getAttribute('data-selected');
    if (start && byId[start]) select(byId[start]);
})();
