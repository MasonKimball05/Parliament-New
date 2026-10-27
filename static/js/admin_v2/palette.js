/* static/js/admin_v2/palette.js — v3.58.1.
 * The admin-v2 ⌘K search: admin pages (src/admin_v2_nav.py) and members, from
 * GET {data-search-url}?q=. Opens on ⌘K / Ctrl+K, "/" (outside a text field)
 * or the search button in the admin bar. Results are links; Enter opens the
 * highlighted one. No inline handlers (prod CSP), no innerHTML with server
 * data: every row is built with textContent.
 */
(function () {
    var palette = document.getElementById('admin-v2-palette');
    if (!palette) return;
    var input = document.getElementById('admin-v2-search-input');
    var list = document.getElementById('admin-v2-search-results');
    var url = palette.getAttribute('data-search-url');
    var items = [];
    var active = -1;
    var timer = null;
    var seq = 0;

    function open() {
        palette.classList.remove('hidden');
        input.value = '';
        render([], []);
        setTimeout(function () { input.focus(); }, 0);
        search('');
    }
    function close() { palette.classList.add('hidden'); }
    function isOpen() { return !palette.classList.contains('hidden'); }

    function row(label, sub, href) {
        var li = document.createElement('li');
        li.setAttribute('role', 'option');
        var a = document.createElement('a');
        a.href = href;
        a.className = 'flex items-baseline gap-2 px-4 py-2 text-sm text-gray-800 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-gray-800';
        var main = document.createElement('span');
        main.className = 'font-medium';
        main.textContent = label;
        a.appendChild(main);
        if (sub) {
            var s = document.createElement('span');
            s.className = 'text-xs text-gray-500 dark:text-gray-400 truncate';
            s.textContent = sub;
            a.appendChild(s);
        }
        li.appendChild(a);
        return li;
    }

    function heading(text) {
        var li = document.createElement('li');
        li.className = 'px-4 pt-2 pb-1 text-[11px] font-semibold tracking-wider uppercase text-gray-400';
        li.textContent = text;
        return li;
    }

    function render(pages, members) {
        list.textContent = '';
        items = [];
        active = -1;
        if (pages.length) {
            list.appendChild(heading('Pages'));
            pages.forEach(function (p) {
                var li = row(p.label, p.group, p.url);
                list.appendChild(li);
                items.push(li);
            });
        }
        if (members.length) {
            list.appendChild(heading('Members'));
            members.forEach(function (m) {
                var li = row(m.name, m.meta, m.url);
                list.appendChild(li);
                items.push(li);
            });
        }
        if (!pages.length && !members.length && input.value.trim()) {
            var empty = document.createElement('li');
            empty.className = 'px-4 py-6 text-sm text-center text-gray-500';
            empty.textContent = 'No matches';
            list.appendChild(empty);
        }
        highlight(items.length ? 0 : -1);
    }

    function highlight(i) {
        items.forEach(function (li, n) {
            var a = li.firstChild;
            a.classList.toggle('bg-gray-100', n === i);
            a.classList.toggle('dark:bg-gray-800', n === i);
            li.setAttribute('aria-selected', n === i ? 'true' : 'false');
        });
        active = i;
        if (i >= 0) items[i].scrollIntoView({ block: 'nearest' });
    }

    function search(q) {
        var mine = ++seq;
        fetch(url + '?q=' + encodeURIComponent(q), { headers: { 'Accept': 'application/json' }, credentials: 'same-origin' })
            .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
            .then(function (data) { if (mine === seq) render(data.pages || [], data.members || []); })
            .catch(function () { if (mine === seq) render([], []); });
    }

    input.addEventListener('input', function () {
        clearTimeout(timer);
        timer = setTimeout(function () { search(input.value.trim()); }, 120);
    });

    input.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown') { e.preventDefault(); if (items.length) highlight((active + 1) % items.length); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); if (items.length) highlight((active - 1 + items.length) % items.length); }
        else if (e.key === 'Enter' && active >= 0) { e.preventDefault(); window.location = items[active].firstChild.href; }
    });

    document.addEventListener('keydown', function (e) {
        var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement && document.activeElement.tagName)
            || (document.activeElement && document.activeElement.isContentEditable);
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); isOpen() ? close() : open(); }
        else if (e.key === '/' && !typing && !isOpen()) { e.preventDefault(); open(); }
        else if (e.key === 'Escape' && isOpen()) { close(); }
    });

    document.addEventListener('click', function (e) {
        if (e.target.closest('[data-admin-search-open]')) { e.preventDefault(); open(); }
        else if (e.target.closest('[data-admin-search-close]')) { close(); }
    });
})();
