/* static/js/base/modal-a11y.js — moved out of templates/base.html in v3.45.6.
 * Modal accessibility: Escape, focus trap, ARIA.
 * Loaded with a plain <script src> at the same place in the page the inline
 * block used to be, so it runs at the same moment as before. base.html is
 * rendered for every page; this way the browser downloads this once and
 * caches it (see src/templatetags/static_version.py for the ?v= part). */
    /**
     * Modal Accessibility Helper
     * Provides Escape key handling, focus trapping, and ARIA support for modals.
     *
     * Usage:
     *   // Initialize a modal with accessibility features
     *   ParliamentModal.init('myModal', {
     *     closeOnEscape: true,           // Close when Escape is pressed (default: true)
     *     closeOnBackdrop: false,        // Close when clicking backdrop (default: false)
     *     onClose: function() { ... },   // Callback when modal closes
     *     focusFirst: 'input[type="email"]'  // Selector for element to focus on open
     *   });
     *
     *   // Open/close programmatically
     *   ParliamentModal.open('myModal');
     *   ParliamentModal.close('myModal');
     */
    window.ParliamentModal = (function() {
        const modals = {};
        let previousActiveElement = null;

        // Get all focusable elements within a container
        function getFocusableElements(container) {
            return container.querySelectorAll(
                'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
            );
        }

        // Trap focus within modal
        function trapFocus(e, modal) {
            const focusable = getFocusableElements(modal);
            if (focusable.length === 0) return;

            const first = focusable[0];
            const last = focusable[focusable.length - 1];

            if (e.shiftKey && document.activeElement === first) {
                e.preventDefault();
                last.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
                e.preventDefault();
                first.focus();
            }
        }

        // Initialize modal with accessibility features
        function init(modalId, options = {}) {
            const modal = document.getElementById(modalId);
            if (!modal) return;

            const config = {
                closeOnEscape: options.closeOnEscape !== false,
                closeOnBackdrop: options.closeOnBackdrop === true,
                onClose: options.onClose || null,
                focusFirst: options.focusFirst || null
            };

            modals[modalId] = { modal, config };

            // Add ARIA attributes
            modal.setAttribute('role', 'dialog');
            modal.setAttribute('aria-modal', 'true');

            // Escape key handler
            if (config.closeOnEscape) {
                modal.addEventListener('keydown', function(e) {
                    if (e.key === 'Escape') {
                        e.preventDefault();
                        close(modalId);
                    }
                });
            }

            // Tab key for focus trapping
            modal.addEventListener('keydown', function(e) {
                if (e.key === 'Tab') {
                    trapFocus(e, modal);
                }
            });

            // Backdrop click handler
            if (config.closeOnBackdrop) {
                modal.addEventListener('click', function(e) {
                    if (e.target === modal) {
                        close(modalId);
                    }
                });
            }

            // If modal is already visible, set up focus
            if (modal.style.display !== 'none' && !modal.classList.contains('hidden')) {
                setupFocus(modalId);
            }
        }

        function setupFocus(modalId) {
            const data = modals[modalId];
            if (!data) return;

            previousActiveElement = document.activeElement;

            // Focus the specified element or first focusable
            setTimeout(function() {
                if (data.config.focusFirst) {
                    const target = data.modal.querySelector(data.config.focusFirst);
                    if (target) {
                        target.focus();
                        return;
                    }
                }
                const focusable = getFocusableElements(data.modal);
                if (focusable.length > 0) {
                    focusable[0].focus();
                }
            }, 100);
        }

        function open(modalId) {
            const data = modals[modalId];
            if (!data) return;

            data.modal.style.display = 'flex';
            data.modal.classList.remove('hidden');
            setupFocus(modalId);
        }

        function close(modalId) {
            const data = modals[modalId];
            if (!data) return;

            data.modal.style.display = 'none';
            data.modal.classList.add('hidden');

            // Restore focus
            if (previousActiveElement) {
                previousActiveElement.focus();
                previousActiveElement = null;
            }

            // Call onClose callback
            if (data.config.onClose) {
                data.config.onClose();
            }
        }

        return { init, open, close };
    })();

    // ⚠️ v3.31.1 — generic modal open/close via data attributes, not onclick.
    // Prod CSP is `script-src 'self' 'nonce-…'` with NO 'unsafe-inline', which
    // blocks EVERY inline event handler attribute (onclick=, onchange=,
    // onsubmit=) outright, regardless of whether the function it calls lives in
    // a properly nonced script element — the nonce only covers that element's
    // own content, not attributes on other tags. Dev sends no CSP header at
    // all, so this class of bug is invisible until someone clicks the button
    // in prod. Reported directly by Mason: the education dashboard's "Add
    // Meeting" button did nothing — traced to exactly this on the shared
    // modal shell's own open/close include files (see components/ — the
    // close/cancel buttons on every modal), plus every onclick/onchange/
    // onsubmit on the education committee dashboard template itself.
    //
    // These two attributes are the general fix for the SHARED modal shell —
    // `data-modal-open`/`data-modal-close` name the target modal id, and one
    // delegated listener here (present on every page via base.html) opens or
    // closes it by plain classList toggle, exactly matching what the onclick
    // attributes used to do. Deliberately NOT routed through ParliamentModal
    // above: that module requires `ParliamentModal.init(modalId, …)` to have
    // registered the modal first, or `.open()`/`.close()` silently no-op —
    // the same "does nothing" failure mode this fix exists to remove. A page
    // that wants the accessibility features (focus trap, Escape-to-close) can
    // still call `ParliamentModal.init()` itself; this listener only covers
    // the plain show/hide every modal on this site already relied on.
    document.addEventListener('click', function(e) {
        const opener = e.target.closest('[data-modal-open]');
        if (opener) {
            document.getElementById(opener.dataset.modalOpen)?.classList.remove('hidden');
            return;
        }
        const closer = e.target.closest('[data-modal-close]');
        if (closer) {
            document.getElementById(closer.dataset.modalClose)?.classList.add('hidden');
        }
    });

    /**
     * Helper function to open a confirmation modal with dynamic content.
     *
     * Usage:
     *   confirmAction('deleteModal', '/delete/123/', {
     *       message: 'Delete "My Item"?',
     *       details: 'This action cannot be undone.'
     *   });
     */
    window.confirmAction = function(modalId, formAction, options = {}) {
        const modal = document.getElementById(modalId);
        if (!modal) return;

        // Update form action if provided
        const form = modal.querySelector('form');
        if (form && formAction) {
            form.action = formAction;
        }

        // Update message if provided
        if (options.message) {
            const msgEl = document.getElementById(modalId + '-message');
            if (msgEl) msgEl.textContent = options.message;
        }

        // Update details if provided
        if (options.details) {
            const detailsEl = document.getElementById(modalId + '-details');
            if (detailsEl) detailsEl.textContent = options.details;
        }

        // Open the modal
        ParliamentModal.open(modalId);
    };

    // Initialize email modal if present
    document.addEventListener('DOMContentLoaded', function() {
        if (document.getElementById('emailModal')) {
            ParliamentModal.init('emailModal', {
                closeOnEscape: true,
                closeOnBackdrop: false,
                focusFirst: '#email'
            });
        }

        // PWA instructions modal
        document.getElementById('js-pwa-modal-backdrop')?.addEventListener('click', function() {
            document.getElementById('pwa-instructions-modal').classList.add('hidden');
        });
        document.getElementById('js-pwa-modal-close')?.addEventListener('click', function() {
            document.getElementById('pwa-instructions-modal').classList.add('hidden');
        });

        // Maintenance banner dismiss
        document.getElementById('js-maintenance-dismiss')?.addEventListener('click', function() {
            this.parentElement.parentElement.parentElement.style.display = 'none';
        });

        // DevTools toolbar buttons (static HTML)
        document.getElementById('js-devtools-toggle-banner')?.addEventListener('click', function() { window.DevTools?.toggle(); });
        document.getElementById('js-devtools-refresh')?.addEventListener('click', function() { window.DevTools?.refresh(); });
        document.getElementById('js-devtools-close')?.addEventListener('click', function() { window.DevTools?.toggle(); });

        // DevTools panel delegation — handles data-devtools-action buttons injected by renderCache/Session/Flags/Errors
        document.getElementById('devtools-panel')?.addEventListener('click', function(e) {
            const btn = e.target.closest('[data-devtools-action]');
            if (!btn) return;
            const action = btn.dataset.devtoolsAction;
            const key = btn.dataset.key;
            const flagId = btn.dataset.flagId ? parseInt(btn.dataset.flagId) : null;
            const flagType = btn.dataset.flagType;
            if (action === 'clearCache') window.DevTools?.clearCache(key);
            else if (action === 'clearSession') window.DevTools?.clearSession();
            else if (action === 'deleteSessionKey') window.DevTools?.deleteSessionKey(key);
            else if (action === 'toggleFlag') window.DevTools?.toggleFlag(flagId, flagType);
            else if (action === 'clearLogs') window.DevTools?.clearLogs();
        });

        // Email modal close buttons — "Skip for Now" snoozes for 15 min
        document.getElementById('js-close-email-modal')?.addEventListener('click', function() { closeEmailModal(true); });
        document.getElementById('js-close-email-flagged-modal')?.addEventListener('click', closeEmailFlaggedModal);

        // Announcement dismiss buttons — delegation (loop-rendered)
        document.querySelectorAll('.js-dismiss-announcement').forEach(function(btn) {
            btn.addEventListener('click', function() {
                dismissAnnouncement(parseInt(this.dataset.announcementId));
            });
        });
    });
    
