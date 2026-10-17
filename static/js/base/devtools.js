/* static/js/base/devtools.js — moved out of templates/base.html in v3.45.6.
 * Admin maintenance-mode DevTools panel.
 * Loaded with a plain <script src> at the same place in the page the inline
 * block used to be, so it runs at the same moment as before. base.html is
 * rendered for every page; this way the browser downloads this once and
 * caches it (see src/templatetags/static_version.py for the ?v= part). */
    window.DevTools = (function() {
        const panel = document.getElementById('devtools-panel');
        const tabs = document.querySelectorAll('.devtools-tab');
        const contents = document.querySelectorAll('.devtools-content');
        let currentTab = 'request';
        let cache = {};

        // Tab switching
        tabs.forEach(tab => {
            tab.addEventListener('click', () => {
                const tabName = tab.dataset.tab;
                switchTab(tabName);
            });
        });

        function switchTab(tabName) {
            currentTab = tabName;
            tabs.forEach(t => {
                t.classList.toggle('bg-gray-700', t.dataset.tab === tabName);
                t.classList.toggle('text-amber-400', t.dataset.tab === tabName);
                t.classList.toggle('text-gray-400', t.dataset.tab !== tabName);
            });
            contents.forEach(c => {
                c.classList.toggle('hidden', c.id !== `devtools-${tabName}`);
            });
            loadTab(tabName);
        }

        function loadTab(tabName, force = false) {
            if (cache[tabName] && !force) return;

            const endpoint = `/api/debug/${tabName === 'flags' ? 'flags' : tabName === 'context' ? 'context' : tabName === 'users' ? 'users-online' : tabName}/`;
            const container = document.getElementById(`devtools-${tabName}`);

            container.innerHTML = '<div class="text-gray-400 animate-pulse">Loading...</div>';

            fetch(endpoint)
                .then(r => r.json())
                .then(data => {
                    cache[tabName] = data;
                    renderTab(tabName, data);
                })
                .catch(err => {
                    container.innerHTML = `<div class="text-red-400">Error: ${escapeHtml(err.message)}</div>`;
                });
        }

        function renderTab(tabName, data) {
            const container = document.getElementById(`devtools-${tabName}`);
            let html = '';

            switch(tabName) {
                case 'request':
                    html = renderRequest(data);
                    break;
                case 'server':
                    html = renderServer(data);
                    break;
                case 'database':
                    html = renderDatabase(data);
                    break;
                case 'cache':
                    html = renderCache(data);
                    break;
                case 'session':
                    html = renderSession(data);
                    break;
                case 'flags':
                    html = renderFlags(data);
                    break;
                case 'context':
                    html = renderContext(data);
                    break;
                case 'errors':
                    html = renderErrors(data);
                    break;
                case 'users':
                    html = renderUsers(data);
                    break;
            }

            container.innerHTML = html;
            attachHandlers(tabName);
        }

        function renderRequest(data) {
            return `
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <div class="space-y-3">
                        <h3 class="text-amber-400 font-semibold text-xs uppercase tracking-wider">General</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2">
                            ${kvRow('Method', data.method, 'text-green-400')}
                            ${kvRow('Path', data.full_path)}
                            ${kvRow('Host', data.host)}
                            ${kvRow('Scheme', data.scheme)}
                            ${kvRow('Secure', data.is_secure ? 'Yes' : 'No', data.is_secure ? 'text-green-400' : 'text-red-400')}
                            ${kvRow('AJAX', data.is_ajax ? 'Yes' : 'No')}
                        </div>
                    </div>
                    <div class="space-y-3">
                        <h3 class="text-amber-400 font-semibold text-xs uppercase tracking-wider">Headers</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-1 max-h-48 overflow-auto">
                            ${Object.entries(data.headers || {}).map(([k, v]) => kvRow(k, v, 'text-gray-300', 'text-xs')).join('')}
                        </div>
                    </div>
                    <div class="space-y-3">
                        <h3 class="text-amber-400 font-semibold text-xs uppercase tracking-wider">Cookies</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-1 max-h-32 overflow-auto">
                            ${Object.entries(data.cookies || {}).length ? Object.entries(data.cookies).map(([k, v]) => kvRow(k, v, 'text-gray-300', 'text-xs')).join('') : '<div class="text-gray-500">No cookies</div>'}
                        </div>
                    </div>
                    <div class="space-y-3">
                        <h3 class="text-amber-400 font-semibold text-xs uppercase tracking-wider">Query Params</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-1">
                            ${Object.entries(data.query_params || {}).length ? Object.entries(data.query_params).map(([k, v]) => kvRow(k, v)).join('') : '<div class="text-gray-500">No query params</div>'}
                        </div>
                    </div>
                </div>
            `;
        }

        function renderServer(data) {
            return `
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-4">
                    <div class="space-y-3">
                        <h3 class="text-green-400 font-semibold text-xs uppercase tracking-wider">Environment</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2">
                            ${kvRow('Python', data.python_version?.split(' ')[0])}
                            ${kvRow('Django', data.django_version)}
                            ${kvRow('Platform', data.platform)}
                            ${kvRow('Debug Mode', data.debug_mode ? 'ON' : 'OFF', data.debug_mode ? 'text-yellow-400' : 'text-green-400')}
                            ${kvRow('PID', data.pid)}
                        </div>
                    </div>
                    <div class="space-y-3">
                        <h3 class="text-green-400 font-semibold text-xs uppercase tracking-wider">Time</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2">
                            ${kvRow('Server Time', data.server_time)}
                            ${kvRow('Timezone', data.timezone)}
                        </div>
                        ${data.memory ? `
                        <h3 class="text-green-400 font-semibold text-xs uppercase tracking-wider mt-4">Memory</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2">
                            ${kvRow('RSS', data.memory.rss_mb + ' MB')}
                            ${kvRow('VMS', data.memory.vms_mb + ' MB')}
                            ${kvRow('Percent', data.memory.percent + '%')}
                        </div>
                        ` : ''}
                    </div>
                    <div class="space-y-3">
                        <h3 class="text-green-400 font-semibold text-xs uppercase tracking-wider">Middleware</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-1 max-h-48 overflow-auto">
                            ${(data.middleware || []).map((m, i) => `<div class="text-xs"><span class="text-gray-500">${i+1}.</span> ${m}</div>`).join('')}
                        </div>
                    </div>
                </div>
            `;
        }

        function renderDatabase(data) {
            return `
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <div class="space-y-3">
                        <h3 class="text-blue-400 font-semibold text-xs uppercase tracking-wider">Connection</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2">
                            ${kvRow('Engine', data.engine)}
                            ${kvRow('Database', data.name)}
                            ${kvRow('Host', data.host)}
                            ${kvRow('Port', data.port)}
                            ${kvRow('Conn Max Age', data.conn_max_age + 's')}
                            ${kvRow('Query Count', data.query_count)}
                        </div>
                        ${data.stats && Object.keys(data.stats).length ? `
                        <h3 class="text-blue-400 font-semibold text-xs uppercase tracking-wider mt-4">Stats</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2">
                            ${data.stats.db_size_mb ? kvRow('DB Size', data.stats.db_size_mb + ' MB') : ''}
                            ${data.stats.active_connections ? kvRow('Active Connections', data.stats.active_connections) : ''}
                        </div>
                        ` : ''}
                    </div>
                    <div class="space-y-3">
                        <h3 class="text-blue-400 font-semibold text-xs uppercase tracking-wider">Recent Queries</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2 max-h-64 overflow-auto">
                            ${(data.recent_queries || []).length ? data.recent_queries.map(q => `
                                <div class="border-b border-gray-700 pb-2 mb-2 last:border-0 last:pb-0 last:mb-0">
                                    <div class="text-xs text-blue-300 font-mono break-all">${escapeHtml(q.sql)}</div>
                                    <div class="text-xs text-gray-500 mt-1">${q.time}s</div>
                                </div>
                            `).join('') : '<div class="text-gray-500">No queries (DEBUG=False or no queries captured)</div>'}
                        </div>
                    </div>
                </div>
            `;
        }

        function renderCache(data) {
            return `
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <div class="space-y-3">
                        <h3 class="text-purple-400 font-semibold text-xs uppercase tracking-wider">Backend</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2">
                            ${kvRow('Backend', data.backend)}
                            ${kvRow('Location', data.location)}
                        </div>
                        ${data.stats ? `
                        <h3 class="text-purple-400 font-semibold text-xs uppercase tracking-wider mt-4">Stats</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2">
                            ${kvRow('Memory Used', (data.stats.used_memory_mb || 0) + ' MB')}
                            ${kvRow('Clients', data.stats.connected_clients)}
                            ${kvRow('Total Keys', data.stats.total_keys)}
                            ${kvRow('Hits', data.stats.hits)}
                            ${kvRow('Misses', data.stats.misses)}
                        </div>
                        ` : ''}
                        <div class="mt-4">
                            <button data-devtools-action="clearCache" data-key="__all__" class="bg-red-600 hover:bg-red-700 px-3 py-1.5 rounded text-xs font-medium transition">
                                Clear All Cache
                            </button>
                        </div>
                    </div>
                    <div class="space-y-3">
                        <h3 class="text-purple-400 font-semibold text-xs uppercase tracking-wider">Known Values</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2">
                            ${Object.entries(data.known_values || {}).length ? Object.entries(data.known_values).map(([k, v]) => `
                                <div class="flex items-center justify-between">
                                    <div class="flex-1">
                                        ${kvRow(k, v)}
                                    </div>
                                    <button data-devtools-action="clearCache" data-key="${escapeHtml(k)}" class="text-red-400 hover:text-red-300 text-xs ml-2">Clear</button>
                                </div>
                            `).join('') : '<div class="text-gray-500">No known cache values</div>'}
                        </div>
                        ${data.sample_keys?.length ? `
                        <h3 class="text-purple-400 font-semibold text-xs uppercase tracking-wider mt-4">Sample Keys</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-1 max-h-32 overflow-auto">
                            ${data.sample_keys.map(k => `<div class="text-xs text-gray-300 font-mono">${k}</div>`).join('')}
                        </div>
                        ` : ''}
                    </div>
                </div>
            `;
        }

        function renderSession(data) {
            return `
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <div class="space-y-3">
                        <h3 class="text-cyan-400 font-semibold text-xs uppercase tracking-wider">Session Info</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2">
                            ${kvRow('Session Key', data.session_key)}
                            ${kvRow('Is Empty', data.is_empty ? 'Yes' : 'No')}
                            ${kvRow('Expiry Age', data.expiry_age + 's')}
                            ${kvRow('Expiry Date', data.expiry_date)}
                        </div>
                        <div class="mt-4">
                            <button data-devtools-action="clearSession" class="bg-red-600 hover:bg-red-700 px-3 py-1.5 rounded text-xs font-medium transition">
                                Clear Session
                            </button>
                        </div>
                    </div>
                    <div class="space-y-3">
                        <h3 class="text-cyan-400 font-semibold text-xs uppercase tracking-wider">Session Data</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2 max-h-64 overflow-auto">
                            ${Object.entries(data.data || {}).length ? Object.entries(data.data).map(([k, v]) => `
                                <div class="flex items-start justify-between border-b border-gray-700 pb-2 mb-2 last:border-0">
                                    <div class="flex-1">
                                        <div class="text-xs text-cyan-300">${escapeHtml(k)}</div>
                                        <div class="text-xs text-gray-400 font-mono mt-1 break-all">${escapeHtml(typeof v === 'object' ? JSON.stringify(v) : v)}</div>
                                    </div>
                                    <button data-devtools-action="deleteSessionKey" data-key="${escapeHtml(k)}" class="text-red-400 hover:text-red-300 text-xs ml-2">Delete</button>
                                </div>
                            `).join('') : '<div class="text-gray-500">No session data</div>'}
                        </div>
                    </div>
                </div>
            `;
        }

        function renderFlags(data) {
            return `
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <div class="space-y-3">
                        <h3 class="text-yellow-400 font-semibold text-xs uppercase tracking-wider">Feature Flags</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2 max-h-64 overflow-auto">
                            ${(data.feature_flags || []).map(f => `
                                <div class="flex items-center justify-between">
                                    <div>
                                        <span class="text-sm ${f.is_enabled ? 'text-green-400' : 'text-gray-400'}">${escapeHtml(f.name)}</span>
                                        ${f.description ? `<div class="text-xs text-gray-500">${escapeHtml(f.description)}</div>` : ''}
                                    </div>
                                    <button data-devtools-action="toggleFlag" data-flag-id="${f.id}" data-flag-type="feature"
                                            class="px-2 py-1 rounded text-xs font-medium transition ${f.is_enabled ? 'bg-green-600 hover:bg-green-700' : 'bg-gray-600 hover:bg-gray-700'}">
                                        ${f.is_enabled ? 'ON' : 'OFF'}
                                    </button>
                                </div>
                            `).join('')}
                        </div>
                    </div>
                    <div class="space-y-3">
                        <h3 class="text-yellow-400 font-semibold text-xs uppercase tracking-wider">Page Toggles</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2 max-h-64 overflow-auto">
                            ${(data.page_toggles || []).length ? data.page_toggles.map(p => `
                                <div class="flex items-center justify-between">
                                    <div>
                                        <span class="text-sm ${p.is_enabled ? 'text-green-400' : 'text-gray-400'}">${escapeHtml(p.display_name || p.url_name)}</span>
                                        <div class="text-xs text-gray-500">${escapeHtml(p.url_name)}</div>
                                    </div>
                                    <button data-devtools-action="toggleFlag" data-flag-id="${p.id}" data-flag-type="page"
                                            class="px-2 py-1 rounded text-xs font-medium transition ${p.is_enabled ? 'bg-green-600 hover:bg-green-700' : 'bg-gray-600 hover:bg-gray-700'}">
                                        ${p.is_enabled ? 'ON' : 'OFF'}
                                    </button>
                                </div>
                            `).join('') : '<div class="text-gray-500">No page toggles</div>'}
                        </div>
                    </div>
                </div>
            `;
        }

        function renderContext(data) {
            return `
                <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
                    <div class="space-y-3">
                        <h3 class="text-pink-400 font-semibold text-xs uppercase tracking-wider">Current User</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-2">
                            ${Object.entries(data.user_info || {}).map(([k, v]) => kvRow(k, Array.isArray(v) ? v.join(', ') : v)).join('')}
                        </div>
                    </div>
                    <div class="space-y-3">
                        <h3 class="text-pink-400 font-semibold text-xs uppercase tracking-wider">Context Processors</h3>
                        <div class="bg-gray-800 rounded p-3 space-y-1 max-h-32 overflow-auto">
                            ${(data.context_processors || []).map(cp => `<div class="text-xs text-gray-300">${cp}</div>`).join('')}
                        </div>
                        <h3 class="text-pink-400 font-semibold text-xs uppercase tracking-wider mt-4">Available Context Keys</h3>
                        <div class="bg-gray-800 rounded p-3">
                            <div class="flex flex-wrap gap-1">
                                ${(data.available_context_keys || []).map(k => `<span class="text-xs bg-gray-700 px-2 py-0.5 rounded">${k}</span>`).join('')}
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }

        function renderErrors(data) {
            return `
                <div class="space-y-3">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center space-x-4">
                            <h3 class="text-red-400 font-semibold text-xs uppercase tracking-wider">Recent Errors/Warnings (${data.total_errors || 0})</h3>
                            <button data-devtools-action="clearLogs" class="bg-red-600 hover:bg-red-700 px-2 py-1 rounded text-xs font-medium transition flex items-center space-x-1">
                                <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                                </svg>
                                <span>Clear Log</span>
                            </button>
                        </div>
                        <div class="text-xs text-gray-500">${escapeHtml(data.log_file)}</div>
                    </div>
                    <div class="bg-gray-800 rounded p-3 space-y-1 max-h-72 overflow-auto font-mono text-xs">
                        ${(data.recent_errors || []).length ? data.recent_errors.map(e => {
                            const isError = e.includes('ERROR');
                            const isWarning = e.includes('WARNING');
                            const isCritical = e.includes('CRITICAL');
                            const colorClass = isCritical ? 'text-red-500' : isError ? 'text-red-400' : isWarning ? 'text-yellow-400' : 'text-gray-300';
                            return `<div class="${colorClass} break-all">${escapeHtml(e)}</div>`;
                        }).join('') : '<div class="text-gray-500">No recent errors</div>'}
                    </div>
                </div>
            `;
        }

        function renderUsers(data) {
            return `
                <div class="space-y-3">
                    <div class="flex items-center justify-between">
                        <h3 class="text-emerald-400 font-semibold text-xs uppercase tracking-wider">Active Sessions (${data.total_active_sessions || 0})</h3>
                    </div>
                    <div class="bg-gray-800 rounded p-3 overflow-auto max-h-72">
                        ${(data.online_users || []).length ? `
                        <table class="w-full text-xs">
                            <thead class="text-gray-400">
                                <tr>
                                    <th class="text-left py-1">User</th>
                                    <th class="text-left py-1">ID</th>
                                    <th class="text-left py-1">Admin</th>
                                    <th class="text-left py-1">Session Expires</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${data.online_users.map(u => `
                                    <tr class="border-t border-gray-700">
                                        <td class="py-2">${escapeHtml(u.name)}</td>
                                        <td class="py-2 text-gray-400">${escapeHtml(u.user_id)}</td>
                                        <td class="py-2">${u.is_admin ? '<span class="text-amber-400">Yes</span>' : 'No'}</td>
                                        <td class="py-2 text-gray-400">${escapeHtml(u.session_expires)}</td>
                                    </tr>
                                `).join('')}
                            </tbody>
                        </table>
                        ` : '<div class="text-gray-500">No active sessions found</div>'}
                    </div>
                </div>
            `;
        }

        function kvRow(key, value, valueColor = 'text-gray-300', size = '') {
            return `<div class="${size}"><span class="text-gray-500">${escapeHtml(key)}:</span> <span class="${valueColor}">${escapeHtml(value ?? 'N/A')}</span></div>`;
        }

        function escapeHtml(text) {
            // v3.44.9: escapes quotes too, so the result is safe inside an attribute.
            return String(text === null || text === undefined ? '' : text)
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
        }

        function attachHandlers(tabName) {
            // Add any tab-specific event handlers here
        }

        // Public API
        return {
            toggle: function() {
                panel.classList.toggle('hidden');
                if (!panel.classList.contains('hidden')) {
                    loadTab(currentTab);
                }
            },
            refresh: function() {
                cache = {};
                loadTab(currentTab, true);
            },
            clearCache: function(key) {
                if (!confirm(`Clear cache${key === '__all__' ? ' (ALL)' : ': ' + key}?`)) return;
                fetch('/api/debug/cache/clear/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
                    body: JSON.stringify({ key: key })
                }).then(r => r.json()).then(data => {
                    if (data.success) {
                        cache = {};
                        loadTab('cache', true);
                    } else {
                        alert('Error: ' + (data.error || 'Unknown error'));
                    }
                });
            },
            clearSession: function() {
                if (!confirm('Clear entire session? You will be logged out.')) return;
                fetch('/api/debug/session/edit/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
                    body: JSON.stringify({ action: 'clear' })
                }).then(() => {
                    window.location.reload();
                });
            },
            deleteSessionKey: function(key) {
                if (!confirm(`Delete session key: ${key}?`)) return;
                fetch('/api/debug/session/edit/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
                    body: JSON.stringify({ action: 'delete', key: key })
                }).then(r => r.json()).then(data => {
                    if (data.success) {
                        cache = {};
                        loadTab('session', true);
                    }
                });
            },
            toggleFlag: function(id, type) {
                fetch('/api/debug/flags/toggle/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() },
                    body: JSON.stringify({ id: id, type: type })
                }).then(r => r.json()).then(data => {
                    if (data.success) {
                        cache = {};
                        loadTab('flags', true);
                    } else {
                        alert('Error: ' + (data.error || 'Unknown error'));
                    }
                });
            },
            clearLogs: function() {
                if (!confirm('Clear all error logs? This cannot be undone.')) return;
                fetch('/api/debug/errors/clear/', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCsrfToken() }
                }).then(r => r.json()).then(data => {
                    if (data.success) {
                        cache = {};
                        loadTab('errors', true);
                    } else {
                        alert('Error: ' + (data.error || 'Unknown error'));
                    }
                });
            }
        };

        function getCsrfToken() {
            const meta = document.querySelector('meta[name="csrf-token"]');
            return meta ? meta.getAttribute('content') : '';
        }
    })();
    
