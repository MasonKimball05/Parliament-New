/* static/js/admin_v2/two_factor.js — Two-factor in the console look
 * (admin_v2/console/two_factor_dashboard.html). The classic page's inline
 * script, minus the bulk bar (the console list's Actions menu posts that):
 *
 *   .js-policy-radio[data-policy-value]   POST {policy} as JSON to
 *                                         #tf-policy-card[data-policy-url]
 *   .js-open-req-modal[data-url]          #requirementModal → POST
 *                                         {requirement, reason} to data-url
 *   .js-reset-2fa[data-url]               #reset2FAModal → POST {} to data-url
 *
 * Esc or a click on the backdrop closes a modal. Names go in with textContent.
 */
(function () {
    'use strict';

    function csrf() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        return input ? input.value : '';
    }
    function postJSON(url, body) {
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), 'Accept': 'application/json' },
            body: JSON.stringify(body)
        }).then(function (r) { return r.json(); });
    }
    function done(data) {
        if (data && data.success) { location.reload(); return; }
        window.alert('Error: ' + ((data && data.error) || 'request failed'));
    }
    function failed() { window.alert('An error occurred'); }

    var lastOpener = null;
    function openModal(id, opener) {
        var modal = document.getElementById(id);
        if (!modal) return;
        lastOpener = opener || null;
        modal.classList.remove('hidden');
        var focus = modal.querySelector('input[type="radio"], button');
        if (focus) focus.focus();
    }
    function closeModal(id) {
        var modal = document.getElementById(id);
        if (!modal || modal.classList.contains('hidden')) return;
        modal.classList.add('hidden');
        if (lastOpener) { lastOpener.focus(); lastOpener = null; }
    }

    // Policy
    var card = document.getElementById('tf-policy-card');
    document.querySelectorAll('.js-policy-radio').forEach(function (radio) {
        radio.addEventListener('change', function () {
            if (!card) return;
            postJSON(card.getAttribute('data-policy-url'), { policy: radio.getAttribute('data-policy-value') })
                .then(done).catch(failed);
        });
    });

    // Requirement modal
    var reqUrl = null, resetUrl = null;
    document.addEventListener('click', function (e) {
        var open = e.target.closest('.js-open-req-modal');
        if (open) {
            reqUrl = open.getAttribute('data-url');
            document.getElementById('modalUserId').value = open.getAttribute('data-user-id');
            document.getElementById('modalMemberName').textContent = open.getAttribute('data-user-name');
            document.getElementById('modalReason').value = '';
            document.querySelectorAll('input[name="modalRequirement"]').forEach(function (r) { r.checked = false; });
            openModal('requirementModal', open);
            return;
        }
        var reset = e.target.closest('.js-reset-2fa');
        if (reset) {
            resetUrl = reset.getAttribute('data-url');
            document.getElementById('reset2FAUserId').value = reset.getAttribute('data-user-id');
            document.getElementById('reset2FAMemberName').textContent = reset.getAttribute('data-user-name');
            openModal('reset2FAModal', reset);
        }
    });

    var closeReq = document.getElementById('js-close-req-modal');
    if (closeReq) closeReq.addEventListener('click', function () { closeModal('requirementModal'); });
    var saveReq = document.getElementById('js-save-req');
    if (saveReq) saveReq.addEventListener('click', function () {
        var picked = document.querySelector('input[name="modalRequirement"]:checked');
        if (!picked) { window.alert('Please select a requirement option'); return; }
        if (!reqUrl) return;
        postJSON(reqUrl, { requirement: picked.value, reason: document.getElementById('modalReason').value })
            .then(done).catch(failed);
    });

    var closeReset = document.getElementById('js-close-reset-modal');
    if (closeReset) closeReset.addEventListener('click', function () { closeModal('reset2FAModal'); });
    var confirmReset = document.getElementById('js-confirm-reset');
    if (confirmReset) confirmReset.addEventListener('click', function () {
        if (!resetUrl) return;
        postJSON(resetUrl, {}).then(function (data) {
            if (data && data.success && data.message) window.alert(data.message);
            done(data);
        }).catch(failed);
    });

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') { closeModal('requirementModal'); closeModal('reset2FAModal'); }
    });
    ['requirementModal', 'reset2FAModal'].forEach(function (id) {
        var modal = document.getElementById(id);
        if (modal) modal.addEventListener('click', function (e) { if (e.target === modal) closeModal(id); });
    });
})();
