/* static/js/admin_v2/api_tokens.js — API tokens in the console look
 * (admin_v2/console/api_tokens.html). The classic page's inline script with
 * the same hooks, delegated from the document so the buttons also work inside
 * a token's slide-out panel:
 *
 *   .js-approve-btn / .js-reject-btn / .js-revoke-btn / .js-edit-scopes-btn
 *       open #approve-modal / #reject-modal / #revoke-modal / #scopes-modal;
 *       the modal's confirm POSTs (url-encoded) to the button's data-url.
 *   #toggle-rest-api / #toggle-auto-approve   POST to data-url, flip data-enabled.
 *
 * Endpoints answer JSON ({approved}, {rejected}, {revoked}, {scopes},
 * {is_enabled}, or {error}). Text goes in with textContent only.
 */
(function () {
    'use strict';

    function csrf() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        return input ? input.value : '';
    }
    function post(url, params) {
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': csrf(), 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json' },
            body: (params || new URLSearchParams()).toString()
        }).then(function (r) {
            var status = r.status;
            return r.text().then(function (text) {
                try { return JSON.parse(text); } catch (e) { throw new Error('HTTP ' + status + ': the server did not answer with JSON.'); }
            });
        });
    }
    function fail(err) { window.alert((err && err.message) || 'Request failed.'); }

    var current = { id: null, url: null, opener: null };
    var MODALS = ['approve-modal', 'reject-modal', 'revoke-modal', 'scopes-modal'];
    function byId(id) { return document.getElementById(id); }
    function open(id, btn) {
        current = { id: btn.getAttribute('data-token-id'), url: btn.getAttribute('data-url'), opener: btn };
        byId(id).classList.remove('hidden');
        var first = byId(id).querySelector('input, textarea, button');
        if (first) first.focus();
    }
    function close(id) {
        var modal = byId(id);
        if (!modal || modal.classList.contains('hidden')) return;
        modal.classList.add('hidden');
        if (current.opener && document.body.contains(current.opener)) current.opener.focus();
    }
    function removeRow(id) {
        var row = byId('token-row-' + id);
        if (row) row.remove();
        var panelClose = document.querySelector('[data-ac-panel-close]');
        if (panelClose && current.opener && current.opener.closest('[data-ac-blade]')) panelClose.click();
    }
    function scopeLabel(scope) {
        var box = document.querySelector('.js-scope-checkbox[value="' + scope.replace(/"/g, '') + '"]');
        return (box && box.getAttribute('data-label')) || scope;
    }

    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.js-approve-btn, .js-reject-btn, .js-revoke-btn, .js-edit-scopes-btn');
        if (!btn) return;
        var name = btn.getAttribute('data-token-name') || '';
        var user = btn.getAttribute('data-token-user') || '';
        if (btn.classList.contains('js-approve-btn')) {
            byId('approve-modal-user').textContent = user;
            byId('approve-modal-name').textContent = name;
            byId('approve-expires-input').value = btn.getAttribute('data-token-expires') || '';
            var list = byId('approve-scopes-list');
            list.textContent = '';
            (btn.getAttribute('data-token-scopes') || '').split(',').filter(Boolean).forEach(function (scope) {
                var label = document.createElement('label');
                label.className = 'ac-check';
                var cb = document.createElement('input');
                cb.type = 'checkbox'; cb.className = 'approve-scope-cb'; cb.value = scope; cb.checked = true;
                label.appendChild(cb);
                label.appendChild(document.createTextNode(' ' + scopeLabel(scope)));
                list.appendChild(label);
            });
            open('approve-modal', btn);
        } else if (btn.classList.contains('js-reject-btn')) {
            byId('reject-modal-user').textContent = user;
            byId('reject-modal-name').textContent = name;
            byId('reject-reason-input').value = '';
            open('reject-modal', btn);
        } else if (btn.classList.contains('js-revoke-btn')) {
            byId('revoke-modal-user').textContent = user;
            byId('revoke-modal-name').textContent = name;
            byId('revoke-reason-input').value = '';
            open('revoke-modal', btn);
        } else {
            byId('scopes-modal-name').textContent = name;
            var have = (btn.getAttribute('data-scopes') || '').split(',').map(function (s) { return s.trim(); });
            document.querySelectorAll('.js-scope-checkbox').forEach(function (cb) { cb.checked = have.indexOf(cb.value) !== -1; });
            open('scopes-modal', btn);
        }
    });

    MODALS.forEach(function (id) {
        var cancel = byId(id + '-cancel');
        if (cancel) cancel.addEventListener('click', function () { close(id); });
        var modal = byId(id);
        if (modal) modal.addEventListener('click', function (e) { if (e.target === modal) close(id); });
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') MODALS.forEach(close);
    });

    function on(id, handler) { var el = byId(id); if (el) el.addEventListener('click', handler); }

    on('approve-modal-confirm', function () {
        if (!current.url) return;
        var p = new URLSearchParams();
        var expires = byId('approve-expires-input').value;
        if (expires) p.append('expires_at', expires);
        p.append('scopes_submitted', '1');   // the backend's "scopes were chosen on purpose" marker
        document.querySelectorAll('.approve-scope-cb:checked').forEach(function (cb) { p.append('scopes', cb.value); });
        var id = current.id;
        post(current.url, p).then(function (data) {
            close('approve-modal');
            if (data.approved) removeRow(id); else window.alert(data.error || 'Failed to approve.');
        }).catch(fail);
    });

    [['reject', 'rejected', 'Failed to reject.'], ['revoke', 'revoked', 'Failed to revoke.']].forEach(function (k) {
        on(k[0] + '-modal-confirm', function () {
            if (!current.url) return;
            var p = new URLSearchParams();
            p.append('reason', byId(k[0] + '-reason-input').value.trim());
            var id = current.id;
            post(current.url, p).then(function (data) {
                close(k[0] + '-modal');
                if (data[k[1]]) removeRow(id); else window.alert(data.error || k[2]);
            }).catch(fail);
        });
    });

    on('scopes-modal-confirm', function () {
        if (!current.url) return;
        var p = new URLSearchParams();
        document.querySelectorAll('.js-scope-checkbox:checked').forEach(function (cb) { p.append('scopes', cb.value); });
        var id = current.id;
        post(current.url, p).then(function (data) {
            close('scopes-modal');
            if (data.scopes === undefined) { window.alert(data.error || 'Failed to update scopes.'); return; }
            document.querySelectorAll('#token-row-' + id + ' .js-token-scopes, [data-token-scopes-for="' + id + '"]').forEach(function (cell) {
                cell.textContent = '';
                data.scopes.forEach(function (s) {
                    var chip = document.createElement('span');
                    chip.className = 'ac-id-chip is-accent';
                    chip.textContent = s;
                    cell.appendChild(chip);
                });
            });
            document.querySelectorAll('.js-edit-scopes-btn[data-token-id="' + id + '"]').forEach(function (b) {
                b.setAttribute('data-scopes', data.scopes.join(','));
            });
        }).catch(fail);
    });

    ['toggle-rest-api', 'toggle-auto-approve'].forEach(function (id) {
        on(id, function () {
            var btn = byId(id);
            post(btn.getAttribute('data-url')).then(function (data) {
                if (data.is_enabled === undefined) { window.alert(data.error || 'Failed to toggle flag.'); return; }
                btn.setAttribute('data-enabled', data.is_enabled ? 'true' : 'false');
                btn.setAttribute('aria-checked', data.is_enabled ? 'true' : 'false');
            }).catch(fail);
        });
    });
})();
