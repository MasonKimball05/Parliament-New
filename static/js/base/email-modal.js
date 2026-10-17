/* static/js/base/email-modal.js — moved out of templates/base.html in v3.45.6.
 * "Add your email" modal and its 15-minute snooze.
 * Loaded with a plain <script src> at the same place in the page the inline
 * block used to be, so it runs at the same moment as before. base.html is
 * rendered for every page; this way the browser downloads this once and
 * caches it (see src/templatetags/static_version.py for the ?v= part). */
        var EMAIL_MODAL_SNOOZE_KEY = 'parliament_email_modal_snoozed_until';
        var EMAIL_MODAL_SNOOZE_MS = 15 * 60 * 1000; // 15 minutes

        function closeEmailModal(snooze) {
            if (snooze) {
                try { localStorage.setItem(EMAIL_MODAL_SNOOZE_KEY, Date.now() + EMAIL_MODAL_SNOOZE_MS); } catch(e) {}
            }
            if (window.ParliamentModal) {
                ParliamentModal.close('emailModal');
            } else {
                const modal = document.getElementById('emailModal');
                if (modal) modal.style.display = 'none';
            }
        }

        // Hide immediately if within snooze window
        (function() {
            try {
                var until = parseInt(localStorage.getItem(EMAIL_MODAL_SNOOZE_KEY) || '0', 10);
                if (Date.now() < until) {
                    var modal = document.getElementById('emailModal');
                    if (modal) modal.style.display = 'none';
                }
            } catch(e) {}
        })();

        // Prevent closing modal by clicking outside (force user interaction)
        document.getElementById('emailModal')?.addEventListener('click', function(e) {
            if (e.target === this) {
                e.stopPropagation();
            }
        });
    
