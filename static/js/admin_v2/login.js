/* static/js/admin_v2/login.js — the Admin v2 sign-in's passkey button in the
 * console look (templates/admin_v2/console/login.html). The same flow as the
 * classic page's inline script (templates/admin_v2/login.html): begin → the
 * browser's passkey prompt → complete → redirect; a cancelled prompt shows
 * nothing. The URLs come from the button's data-* attributes (no inline
 * script under the CSP).
 */
(function () {
    'use strict';
    var btn = document.getElementById('passkey-btn');
    var errorEl = document.getElementById('passkey-error');
    if (!btn || !errorEl) return;
    var label = btn.innerHTML;

    function bufToBase64url(buf) {
        var bytes = new Uint8Array(buf);
        var str = '';
        for (var i = 0; i < bytes.length; i++) str += String.fromCharCode(bytes[i]);
        return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
    }
    function base64urlToBuf(str) {
        str = str.replace(/-/g, '+').replace(/_/g, '/');
        while (str.length % 4) str += '=';
        var binary = atob(str);
        var buf = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i++) buf[i] = binary.charCodeAt(i);
        return buf;
    }
    function getCsrf() {
        var el = document.querySelector('[name=csrfmiddlewaretoken]');
        return el ? el.value : '';
    }

    // Hide if the browser doesn't support WebAuthn.
    if (!window.PublicKeyCredential) {
        btn.closest('div').classList.add('hidden');
        return;
    }

    btn.addEventListener('click', async function () {
        errorEl.classList.add('hidden');
        btn.disabled = true;
        btn.textContent = 'Waiting for passkey…';

        try {
            // Step 1: get a challenge from the server.
            var beginResp = await fetch(btn.dataset.beginUrl, {
                method: 'POST',
                headers: { 'X-CSRFToken': getCsrf() },
            });
            if (!beginResp.ok) {
                var err = await beginResp.json().catch(function () { return {}; });
                throw new Error(err.error || 'Could not start passkey authentication.');
            }
            var options = await beginResp.json();

            // Decode the base64url fields the browser expects as ArrayBuffers.
            options.challenge = base64urlToBuf(options.challenge);
            if (options.allowCredentials) {
                options.allowCredentials = options.allowCredentials.map(function (c) {
                    return Object.assign({}, c, { id: base64urlToBuf(c.id) });
                });
            }

            // Step 2: the authenticator (Touch ID / Face ID / hardware key).
            var assertion = await navigator.credentials.get({ publicKey: options });

            // Step 3: encode the response and send it to the server.
            var payload = {
                id: assertion.id,
                rawId: bufToBase64url(assertion.rawId),
                type: assertion.type,
                response: {
                    clientDataJSON: bufToBase64url(assertion.response.clientDataJSON),
                    authenticatorData: bufToBase64url(assertion.response.authenticatorData),
                    signature: bufToBase64url(assertion.response.signature),
                    userHandle: assertion.response.userHandle
                        ? bufToBase64url(assertion.response.userHandle) : null,
                },
            };

            var completeResp = await fetch(btn.dataset.completeUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrf(),
                },
                body: JSON.stringify(payload),
            });
            var result = await completeResp.json();
            if (!completeResp.ok) throw new Error(result.error || 'Passkey authentication failed.');

            window.location.href = result.redirect || btn.dataset.doneUrl;
        } catch (e) {
            if (e.name !== 'NotAllowedError') {
                // NotAllowedError = the user cancelled; no error for that.
                errorEl.textContent = e.message || 'Passkey authentication failed.';
                errorEl.classList.remove('hidden');
            }
        } finally {
            btn.disabled = false;
            btn.innerHTML = label;
        }
    });
})();
