/**
 * Parliament Service Worker
 *
 * Handles incoming Web Push notifications.
 * Install path: /static/js/service-worker.js
 * Registered from base.html at the root scope ('/').
 *
 * Notification payload shape (sent by send_push_notification task):
 *   { title, body, url, tag }
 *
 * - title / body: shown in the OS notification tray
 * - url: page to open when the user taps the notification
 * - tag: deduplication key (e.g. "vote-42") — replaces any prior
 *         notification with the same tag rather than stacking
 */

const ICON = '/exportable_media/am-coat-of-arms.png';

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
