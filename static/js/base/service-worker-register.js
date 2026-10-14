/* static/js/base/service-worker-register.js — moved out of templates/base.html in v3.45.6.
 * Service worker registration and push subscription.
 * Loaded with a plain <script src> at the same place in the page the inline
 * block used to be, so it runs at the same moment as before. base.html is
 * rendered for every page; this way the browser downloads this once and
 * caches it (see src/templatetags/static_version.py for the ?v= part). */
    (function() {
        // Register service worker
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/service-worker.js', { scope: '/' })
                .catch(function(err) { console.warn('SW registration failed:', err); });
        }

        // PWA install — works across Chrome/Edge (native prompt) and all other browsers (instructions modal)
        var deferredInstallPrompt = null;
        var isAlreadyInstalled = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone;

        // Detect browser for instructions
        var ua = navigator.userAgent;
        var isArc = window.hasOwnProperty('__arc_toolbar') || (CSS && CSS.supports && CSS.supports('-webkit-app-region', 'drag') && /Chrome/.test(ua) && /Arc/.test(navigator.userAgentData && navigator.userAgentData.brands ? JSON.stringify(navigator.userAgentData.brands) : ''));
        var isOpera = /OPR\//.test(ua) || /Opera/.test(ua);
        var isEdge = /Edg\//.test(ua);
        var isFirefox = /Firefox/.test(ua);
        var isSafari = /^((?!chrome|android).)*safari/i.test(ua);
        var isChrome = /Chrome/.test(ua) && !isEdge && !isOpera;

        function getBrowserInstructions() {
            // Opera / Opera GX: Chromium-based, supports beforeinstallprompt
            // This fallback only shows if the native prompt didn't fire
            if (isOpera) {
                return '<p class="font-medium mb-3">To install Parliament in Opera:</p>' +
                    '<ol class="list-decimal list-inside space-y-2">' +
                    '<li>Look for the <strong>install icon</strong> <span class="font-mono bg-gray-100 dark:bg-gray-700 px-1 rounded">⊕</span> in the address bar</li>' +
                    '<li>Click it and select <strong>Install</strong></li>' +
                    '</ol>' +
                    '<p class="mt-3 text-xs text-gray-500 dark:text-gray-400">Opera and Opera GX both support PWA installation. If you don\'t see the icon, try visiting a few more pages first.</p>';
            }
            // Arc: no PWA support
            if (isArc) {
                return '<div class="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 rounded-lg p-4">' +
                    '<p class="font-semibold text-amber-800 dark:text-amber-300 mb-2">Arc does not support PWA installation</p>' +
                    '<p class="text-amber-700 dark:text-amber-400">Arc has removed PWA support by design. To install Parliament as an app, open it in <strong>Chrome</strong> or <strong>Edge</strong> instead.</p>' +
                    '</div>';
            }
            // Safari on macOS
            if (isSafari && /Macintosh/.test(ua)) {
                return '<p class="font-medium mb-3">To install Parliament in Safari on macOS:</p>' +
                    '<ol class="list-decimal list-inside space-y-2">' +
                    '<li>Click <strong>File</strong> in the menu bar</li>' +
                    '<li>Select <strong>Add to Dock…</strong></li>' +
                    '<li>Click <strong>Add</strong></li>' +
                    '</ol>' +
                    '<p class="mt-3 text-xs text-gray-500 dark:text-gray-400">Requires macOS Sonoma (14) or later.</p>';
            }
            // Safari on iOS
            if (isSafari || /iPhone|iPad|iPod/.test(ua)) {
                return '<p class="font-medium mb-3">To install Parliament on iPhone or iPad:</p>' +
                    '<ol class="list-decimal list-inside space-y-2">' +
                    '<li>Tap the <strong>Share</strong> button <span class="font-mono bg-gray-100 dark:bg-gray-700 px-1 rounded">⬆</span> at the bottom of Safari</li>' +
                    '<li>Scroll down and tap <strong>Add to Home Screen</strong></li>' +
                    '<li>Tap <strong>Add</strong></li>' +
                    '</ol>';
            }
            // Firefox
            if (isFirefox) {
                return '<div class="bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-700 rounded-lg p-4">' +
                    '<p class="font-semibold text-amber-800 dark:text-amber-300 mb-2">Firefox does not support PWA installation</p>' +
                    '<p class="text-amber-700 dark:text-amber-400">To install Parliament as an app, open it in <strong>Chrome</strong> or <strong>Edge</strong>.</p>' +
                    '</div>';
            }
            // Chrome / Edge / other Chromium fallback
            var browserName = isEdge ? 'Edge' : (isOpera ? 'Opera' : 'Chrome');
            return '<p class="font-medium mb-3">To install Parliament in ' + browserName + ':</p>' +
                '<ol class="list-decimal list-inside space-y-2">' +
                '<li>Look for the <strong>install icon</strong> <span class="font-mono bg-gray-100 dark:bg-gray-700 px-1 rounded">⊕</span> in the address bar</li>' +
                '<li>Click it and select <strong>Install</strong></li>' +
                '</ol>' +
                '<p class="mt-3 text-xs text-gray-500 dark:text-gray-400">If you don\'t see the icon, you may already have Parliament installed, or try visiting a few more pages first.</p>';
        }

        function showInstructionsModal() {
            var content = document.getElementById('pwa-instructions-content');
            if (content) content.innerHTML = getBrowserInstructions();
            document.getElementById('pwa-instructions-modal').classList.remove('hidden');
        }

        function triggerInstall() {
            if (deferredInstallPrompt) {
                // Chrome/Edge: native prompt available
                deferredInstallPrompt.prompt();
                deferredInstallPrompt.userChoice.then(function() {
                    deferredInstallPrompt = null;
                    hideInstallButtons();
                });
            } else {
                // All other browsers: show instructions
                showInstructionsModal();
            }
        }

        function showInstallButtons() {
            var btn = document.getElementById('pwa-install-btn');
            var btnMobile = document.getElementById('pwa-install-btn-mobile');
            if (btn) btn.classList.remove('hidden');
            if (btnMobile) btnMobile.classList.remove('hidden');
        }

        function hideInstallButtons() {
            var btn = document.getElementById('pwa-install-btn');
            var btnMobile = document.getElementById('pwa-install-btn-mobile');
            if (btn) btn.classList.add('hidden');
            if (btnMobile) btnMobile.classList.add('hidden');
        }

        // Wire up nav install buttons
        var installBtn = document.getElementById('pwa-install-btn');
        var installBtnMobile = document.getElementById('pwa-install-btn-mobile');
        if (installBtn) installBtn.addEventListener('click', triggerInstall);
        if (installBtnMobile) installBtnMobile.addEventListener('click', triggerInstall);

        if (!isAlreadyInstalled) {
            // Show button immediately for all browsers (not just when beforeinstallprompt fires)
            showInstallButtons();

            // Chrome/Edge: capture native prompt if available
            window.addEventListener('beforeinstallprompt', function(e) {
                e.preventDefault();
                deferredInstallPrompt = e;
                // Button already visible; clicking it will now use the native prompt
            });
        }

        // Hide buttons once the app is installed
        window.addEventListener('appinstalled', function() {
            deferredInstallPrompt = null;
            hideInstallButtons();
        });

        // ── Mobile A2HS banner (existing, mobile-only) ──────────────────────
        var DISMISS_KEY = 'parliament_a2hs_dismissed';
        var banner = document.getElementById('a2hs-banner');
        if (!banner) return;

        var dismissed = localStorage.getItem(DISMISS_KEY);
        if (dismissed && (Date.now() - parseInt(dismissed, 10)) < 30 * 24 * 60 * 60 * 1000) return;

        if (window.innerWidth > 768) return;
        if (isAlreadyInstalled) return;

        var isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);

        // iOS: show the manual instructions hint (no beforeinstallprompt on iOS)
        if (isIOS) {
            var iosHint = document.getElementById('a2hs-ios-hint');
            if (iosHint) iosHint.style.display = '';
            banner.classList.remove('hidden');
        }

        var a2hsInstallBtn = document.getElementById('a2hs-install-btn');
        if (a2hsInstallBtn) {
            a2hsInstallBtn.addEventListener('click', function() {
                triggerInstall();
                banner.classList.add('hidden');
                localStorage.setItem(DISMISS_KEY, Date.now());
            });
        }

        var dismissBtn = document.getElementById('a2hs-dismiss-btn');
        if (dismissBtn) {
            dismissBtn.addEventListener('click', function() {
                banner.classList.add('hidden');
                localStorage.setItem(DISMISS_KEY, Date.now());
            });
        }
    })();
    
