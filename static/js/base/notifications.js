/* static/js/base/notifications.js — moved out of templates/base.html in v3.45.6.
 * Notification bell dropdown.
 * Loaded with a plain <script src> at the same place in the page the inline
 * block used to be, so it runs at the same moment as before. base.html is
 * rendered for every page; this way the browser downloads this once and
 * caches it (see src/templatetags/static_version.py for the ?v= part). */
    (function() {
        const bell = document.getElementById('notification-bell');
        const dropdown = document.getElementById('notification-dropdown');
        const listEl = document.getElementById('notification-list');
        const markAllBtn = document.getElementById('mark-all-read-btn');
        if (!bell || !dropdown) return;

        let loaded = false;

        // Toggle dropdown
        bell.addEventListener('click', function(e) {
            e.stopPropagation();
            const isHidden = dropdown.classList.contains('hidden');
            dropdown.classList.toggle('hidden');
            if (isHidden && !loaded) {
                loadNotifications();
                loaded = true;
            }
        });

        // Close on outside click
        document.addEventListener('click', function(e) {
            if (!dropdown.contains(e.target) && !bell.contains(e.target)) {
                dropdown.classList.add('hidden');
            }
        });

        // Type icons
        const TYPE_ICONS = {
            'announcement': '<svg class="w-5 h-5 text-blue-500" fill="currentColor" viewBox="0 0 20 20"><path d="M10 2a6 6 0 00-6 6v3.586l-.707.707A1 1 0 004 14h12a1 1 0 00.707-1.707L16 11.586V8a6 6 0 00-6-6zM10 18a3 3 0 01-3-3h6a3 3 0 01-3 3z"/></svg>',
            'legislation_new': '<svg class="w-5 h-5 text-purple-500" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M4 4a2 2 0 012-2h4.586A2 2 0 0112 2.586L15.414 6A2 2 0 0116 7.414V16a2 2 0 01-2 2H6a2 2 0 01-2-2V4z" clip-rule="evenodd"/></svg>',
            'vote_ended': '<svg class="w-5 h-5 text-green-500" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd"/></svg>',
            'event_new': '<svg class="w-5 h-5 text-orange-500" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M6 2a1 1 0 00-1 1v1H4a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-1V3a1 1 0 10-2 0v1H7V3a1 1 0 00-1-1zm0 5a1 1 0 000 2h8a1 1 0 100-2H6z" clip-rule="evenodd"/></svg>',
            'chat_mention': '<svg class="w-5 h-5 text-blue-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 12a4 4 0 10-8 0 4 4 0 008 0zm0 0v1.5a2.5 2.5 0 005 0V12a9 9 0 10-9 9m4.5-1.206a8.959 8.959 0 01-4.5 1.207"/></svg>',
        };

        function loadNotifications() {
            fetch('/api/notifications/')
                .then(r => r.json())
                .then(data => {
                    if (data.notifications.length === 0) {
                        listEl.innerHTML = '<div class="p-6 text-center text-gray-500 dark:text-gray-400 text-sm">No notifications yet</div>';
                        return;
                    }
                    listEl.innerHTML = data.notifications.map(n => `
                        <a href="#" data-id="${escapeHtml(n.id)}" data-link="${escapeHtml(n.link)}" class="notif-item block px-4 py-3 hover:bg-gray-50 dark:hover:bg-gray-700 border-b border-gray-100 dark:border-gray-700 transition ${n.is_read ? 'opacity-60' : ''}">
                            <div class="flex gap-3">
                                <div class="flex-shrink-0 mt-0.5">${TYPE_ICONS[n.type] || TYPE_ICONS['announcement']}</div>
                                <div class="flex-1 min-w-0">
                                    <p class="text-sm font-medium text-gray-900 dark:text-white truncate">${escapeHtml(n.title)}</p>
                                    ${n.message ? `<p class="text-xs text-gray-500 dark:text-gray-400 mt-0.5 truncate">${escapeHtml(n.message)}</p>` : ''}
                                    <p class="text-xs text-gray-400 dark:text-gray-500 mt-1">${escapeHtml(n.time_ago)}</p>
                                </div>
                                ${!n.is_read ? '<div class="flex-shrink-0 mt-2"><span class="w-2 h-2 bg-blue-500 rounded-full block"></span></div>' : ''}
                            </div>
                        </a>
                    `).join('');

                    // Click handler for each notification
                    listEl.querySelectorAll('.notif-item').forEach(item => {
                        item.addEventListener('click', function(e) {
                            e.preventDefault();
                            const id = this.dataset.id;
                            const link = this.dataset.link;
                            fetch(`/api/notifications/${id}/read/`, {
                                method: 'POST',
                                headers: { 'X-CSRFToken': getCookie('csrftoken') }
                            }).finally(() => {
                                if (link) window.location.href = link;
                            });
                        });
                    });
                })
                .catch(() => {
                    listEl.innerHTML = '<div class="p-4 text-center text-red-500 text-sm">Failed to load notifications</div>';
                });
        }

        // Mark all read
        markAllBtn.addEventListener('click', function(e) {
            e.stopPropagation();
            fetch('/api/notifications/read-all/', {
                method: 'POST',
                headers: { 'X-CSRFToken': getCookie('csrftoken') }
            }).then(r => r.json()).then(data => {
                if (data.success) {
                    // Remove badge
                    const badge = document.getElementById('notif-badge');
                    if (badge) badge.remove();
                    // Mark all items as read visually
                    listEl.querySelectorAll('.notif-item').forEach(item => {
                        item.classList.add('opacity-60');
                        const dot = item.querySelector('.bg-blue-500');
                        if (dot) dot.remove();
                    });
                }
            });
        });

        function escapeHtml(text) {
            // v3.44.9: escapes quotes too, so the result is safe inside an attribute.
            return String(text === null || text === undefined ? '' : text)
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        }
    })();
    
