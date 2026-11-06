/* static/js/admin_v2/console.js — the Admin v2 console frame (admin_console_ui).
 *
 * Loaded by templates/admin_v2/console/base.html. No inline handlers (prod CSP
 * has no 'unsafe-inline'); everything is delegated from data-* attributes:
 *
 *   data-ac-menu                 on a button: toggles the menu named by its
 *                                aria-controls (or the next .ac-menu sibling).
 *                                Esc / outside click closes; ↑↓ move inside.
 *   data-ac-drawer-open / -close the phone drawer (the sidebar off-canvas).
 *   form[data-ac-favorite]       the ☆ pin forms: posted in the background,
 *                                stars and the Favorites list update in place.
 *   [data-ac-bulk="tableId"]     an Actions ▾ dropdown fed by that table's
 *                                ticked rows:
 *     table [data-ac-select-all]   header checkbox (ticks every row)
 *     table [data-ac-row-select]   row checkbox; its value is the row's id
 *     form[data-ac-bulk-form]      one per action; on submit one hidden
 *                                  <input name="{data-ac-bulk-field}"> per
 *                                  ticked row is added. data-confirm on the
 *                                  form is asked first (data-actions.js).
 */
(function () {
    'use strict';

    // ---- Menus ----------------------------------------------------------
    function menuFor(btn) {
        var id = btn.getAttribute('aria-controls');
        if (id) return document.getElementById(id);
        var n = btn.nextElementSibling;
        while (n && !n.classList.contains('ac-menu')) n = n.nextElementSibling;
        return n;
    }
    function items(menu) {
        return Array.prototype.filter.call(menu.querySelectorAll('a[href], button:not([disabled])'),
            function (el) { return el.offsetParent !== null; });
    }
    var openBtn = null;
    function closeMenu(focusBack) {
        if (!openBtn) return;
        var menu = menuFor(openBtn);
        if (menu) menu.classList.add('hidden');
        openBtn.setAttribute('aria-expanded', 'false');
        if (focusBack) openBtn.focus();
        openBtn = null;
    }
    function openMenu(btn) {
        closeMenu(false);
        var menu = menuFor(btn);
        if (!menu) return;
        menu.classList.remove('hidden');
        btn.setAttribute('aria-expanded', 'true');
        openBtn = btn;
    }

    document.addEventListener('click', function (e) {
        var btn = e.target.closest('[data-ac-menu]');
        if (btn) {
            e.preventDefault();
            if (btn.disabled) return;
            if (openBtn === btn) closeMenu(false); else openMenu(btn);
            return;
        }
        if (openBtn) {
            var menu = menuFor(openBtn);
            if (!menu || !menu.contains(e.target)) closeMenu(false);
        }
    });

    document.addEventListener('keydown', function (e) {
        if (openBtn) {
            var menu = menuFor(openBtn);
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); closeMenu(true); return; }
            if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                var list = items(menu);
                if (!list.length) return;
                e.preventDefault();
                var i = list.indexOf(document.activeElement);
                i = e.key === 'ArrowDown' ? (i + 1) % list.length : (i - 1 + list.length) % list.length;
                list[i].focus();
            }
            return;
        }
        if (e.key === 'Escape' && shell && shell.classList.contains('is-drawer-open')) closeDrawer();
    }, true);

    // ---- Drawer (phones) --------------------------------------------------
    var shell = document.getElementById('ac-shell');
    var drawerBtn = document.querySelector('[data-ac-drawer-open]');
    function openDrawer() {
        if (!shell) return;
        shell.classList.add('is-drawer-open');
        var backdrop = shell.querySelector('.ac-drawer-backdrop');
        if (backdrop) backdrop.hidden = false;
        if (drawerBtn) drawerBtn.setAttribute('aria-expanded', 'true');
        var first = shell.querySelector('.ac-sidebar a, .ac-sidebar button');
        if (first) first.focus();
    }
    function closeDrawer() {
        if (!shell) return;
        shell.classList.remove('is-drawer-open');
        var backdrop = shell.querySelector('.ac-drawer-backdrop');
        if (backdrop) backdrop.hidden = true;
        if (drawerBtn) { drawerBtn.setAttribute('aria-expanded', 'false'); drawerBtn.focus(); }
    }
    document.addEventListener('click', function (e) {
        if (e.target.closest('[data-ac-drawer-open]')) { e.preventDefault(); openDrawer(); }
        else if (e.target.closest('[data-ac-drawer-close]')) { e.preventDefault(); closeDrawer(); }
    });

    // ---- Favorites ----------------------------------------------------------
    function setStars(page, on, label) {
        document.querySelectorAll('form[data-ac-favorite] input[name="page"][value="' + page + '"]').forEach(function (input) {
            var b = input.form.querySelector('button');
            b.classList.toggle('is-on', on);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
            b.setAttribute('aria-label', (on ? 'Remove ' : 'Add ') + label + (on ? ' from favorites' : ' to favorites'));
            b.title = on ? 'Remove from favorites' : 'Add to favorites';
        });
        var group = document.querySelector('[data-ac-favorites-group]');
        var list = group && group.querySelector('[data-ac-favorites-list]');
        if (!list) return;
        var existing = list.querySelector('[data-ac-fav-row="' + page + '"]');
        if (on && !existing) {
            var source = document.querySelector('.ac-sidebar [data-ac-page="' + page + '"] .ac-nav-link');
            if (source) {
                var li = document.createElement('li');
                li.className = 'ac-nav-row';
                li.setAttribute('data-ac-fav-row', page);
                li.appendChild(source.cloneNode(true));
                list.appendChild(li);
            }
        } else if (!on && existing) {
            existing.remove();
        }
        group.hidden = !list.children.length;
    }
    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-ac-favorite')) return;
        if (!window.fetch) return;
        e.preventDefault();
        fetch(form.action, {
            method: 'POST', body: new FormData(form), credentials: 'same-origin',
            headers: { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
        }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
          .then(function (data) { setStars(data.page, data.favorite, data.label); })
          .catch(function () { form.submit(); });
    });

    // ---- Resource list: row selection and the Actions menu -------------------
    function bulkParts(dropdown) {
        return {
            table: document.getElementById(dropdown.getAttribute('data-ac-bulk')),
            button: dropdown.querySelector('[data-ac-bulk-button]'),
            count: dropdown.querySelector('[data-ac-bulk-count]'),
            field: dropdown.getAttribute('data-ac-bulk-field') || 'ids'
        };
    }
    function checked(table) {
        return table ? Array.prototype.filter.call(table.querySelectorAll('[data-ac-row-select]'),
            function (box) { return box.checked; }) : [];
    }
    function refreshBulk() {
        document.querySelectorAll('[data-ac-bulk]').forEach(function (dropdown) {
            var p = bulkParts(dropdown);
            var n = checked(p.table).length;
            if (p.button) {
                p.button.disabled = n === 0;
                p.button.title = n ? n + ' selected' : 'Tick rows to act on them';
            }
            if (p.count) p.count.textContent = n ? ' (' + n + ')' : '';
        });
        document.querySelectorAll('table[data-ac-select]').forEach(function (table) {
            var boxes = table.querySelectorAll('[data-ac-row-select]');
            var on = 0;
            boxes.forEach(function (box) {
                var row = box.closest('tr');
                if (row) row.classList.toggle('is-selected', box.checked);
                if (box.checked) on++;
            });
            var all = table.querySelector('[data-ac-select-all]');
            if (all) {
                all.checked = boxes.length > 0 && on === boxes.length;
                all.indeterminate = on > 0 && on < boxes.length;
            }
        });
    }
    document.addEventListener('change', function (e) {
        var el = e.target;
        if (el.hasAttribute && el.hasAttribute('data-ac-select-all')) {
            var table = el.closest('table');
            if (table) table.querySelectorAll('[data-ac-row-select]').forEach(function (box) { box.checked = el.checked; });
            refreshBulk();
        } else if (el.hasAttribute && el.hasAttribute('data-ac-row-select')) {
            refreshBulk();
        }
    });
    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!(form instanceof HTMLFormElement) || !form.hasAttribute('data-ac-bulk-form')) return;
        var dropdown = form.closest('[data-ac-bulk]');
        if (!dropdown) return;
        var p = bulkParts(dropdown);
        var rows = checked(p.table);
        if (!rows.length) { e.preventDefault(); return; }
        form.querySelectorAll('input[data-ac-bulk-id]').forEach(function (i) { i.remove(); });
        rows.forEach(function (box) {
            var input = document.createElement('input');
            input.type = 'hidden';
            input.name = p.field;
            input.value = box.value;
            input.setAttribute('data-ac-bulk-id', '');
            form.appendChild(input);
        });
    });
    // Back/forward cache can restore ticked boxes: recount on show.
    window.addEventListener('pageshow', refreshBulk);
    refreshBulk();
})();
