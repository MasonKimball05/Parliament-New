/**
 * Parliament Service Worker
 *
 * Handles incoming Web Push notifications, plus an offline fallback page.
 * RENDERED BY DJANGO (src/view/pwa.py → GET /service-worker.js) since
 * 09-27-26, so the icon and offline page come from the chapter config.
 * Was static/js/service-worker.js. Registered from base.html at scope '/'.
 * This is a Django template: never write two opening braces in a row here.
 *
 * Notification payload shape (sent by send_push_notification task):
 *   { title, body, url, tag }
 *
 * - title / body: shown in the OS notification tray
 * - url: page to open when the user taps the notification
 * - tag: deduplication key (e.g. "vote-42") — replaces any prior
 *         notification with the same tag rather than stacking
 */

// v3.14.1: must be a /static/ path — /exportable_media/ is login-gated and
// SW notification-icon fetches are cookieless, so the icon 302'd to the
// login page and rendered broken (part of the mobile broken-seal bug).
const ICON = '{{ icon_url|escapejs }}';

// ─── Install / Activate ────────────────────────────────────────────────────

// v3.15.0: tiny offline fallback. We still cache NO app content (login-gated;
// intercepting credentialed requests causes session issues) and the fetch
// handler below touches nothing but failed top-level navigations.
// 09-27-26: the offline page is EMBEDDED here (rendered from
// templates/pwa/offline.html) instead of cached from /static/offline.html —
// see src/view/pwa.py for why there is no offline URL any more. Only the
// crest is cached, so the page's <img> still resolves offline.
const OFFLINE_CACHE = '{{ offline_cache|escapejs }}';
const OFFLINE_HTML = '{{ offline_html|escapejs }}';

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(OFFLINE_CACHE)
            .then((cache) => cache.addAll([ICON]))
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(
                keys.filter((k) => k.startsWith('parliament-offline-') && k !== OFFLINE_CACHE)
                    .map((k) => caches.delete(k))))
            .then(() => clients.claim())
    );
});

// ─── Fetch: offline fallback for navigations ONLY ──────────────────────────
// Non-navigation requests (API, static, POSTs) are deliberately not handled —
// the browser does its default thing and credentials flow untouched.

self.addEventListener('fetch', (event) => {
    if (event.request.mode !== 'navigate') return;
    event.respondWith(
        fetch(event.request).catch(() =>
            new Response(OFFLINE_HTML, {
                status: 503,
                headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' },
            })
        )
    );
});

// ─── Push event ────────────────────────────────────────────────────────────

self.addEventListener('push', (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch {
        data = { title: 'Parliament', body: event.data ? event.data.text() : '' };
    }

    const title = data.title || 'Parliament';
    const options = {
        body: data.body || '',
        icon: ICON,
        badge: ICON,
        tag: data.tag || 'parliament-default',
        data: { url: data.url || '/home/' },
        requireInteraction: false,
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

// ─── Notification click ─────────────────────────────────────────────────────

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    const target = event.notification.data?.url || '/home/';

    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
            // If a Parliament tab is already open, focus it and navigate
            for (const client of windowClients) {
                if (client.url.includes(self.location.origin) && 'focus' in client) {
                    client.focus();
                    return client.navigate(target);
                }
            }
            // Otherwise open a new tab
            if (clients.openWindow) {
                return clients.openWindow(target);
            }
        })
    );
});
