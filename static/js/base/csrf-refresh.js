/* static/js/base/csrf-refresh.js — moved out of templates/base.html in v3.45.6.
 * CSRF token refresh after load / bfcache restore.
 * Loaded with a plain <script src> at the same place in the page the inline
 * block used to be, so it runs at the same moment as before. base.html is
 * rendered for every page; this way the browser downloads this once and
 * caches it (see src/templatetags/static_version.py for the ?v= part). */
    window.addEventListener('load', function() {
        // ⚠️ v3.20.0 — the cookie fallback that used to be the `:` branch here
        // was DEAD CODE: `CSRF_COOKIE_HTTPONLY = True`, so JavaScript can never
        // read that cookie. Removed rather than left in place, because a dead
        // fallback reads as an endorsement — three other templates were written
        // against the cookie, two of them with no fallback at all, and every one
        // of their requests was a 403. `src/test_csrf_token_source.py` now
        // enforces the meta tag as the single source.
        var meta = document.querySelector('meta[name="csrf-token"]');
        var csrf = meta ? meta.getAttribute('content') : '';
        var fd = new FormData();
        fd.append('path', window.location.pathname);
        fd.append('csrfmiddlewaretoken', csrf);
        if (navigator.sendBeacon) {
            navigator.sendBeacon('/internal/track-visit/', fd);
        } else {
            fetch('/internal/track-visit/', { method: 'POST', body: fd, keepalive: true });
        }
    });
    
