/* static/js/base/double-submit.js — moved out of templates/base.html in v3.45.6.
 * Double-submit guard for forms.
 * Loaded with a plain <script src> at the same place in the page the inline
 * block used to be, so it runs at the same moment as before. base.html is
 * rendered for every page; this way the browser downloads this once and
 * caches it (see src/templatetags/static_version.py for the ?v= part). */
    (function() {
        // Track forms that have been submitted
        const submittedForms = new WeakSet();

        // v3.46.1 — on `window`, not `document`, so this runs after every
        // form- and document-level submit handler, whatever order the
        // scripts were loaded in. That matters for the check below.
        window.addEventListener('submit', function(e) {
            const form = e.target;

            // Skip forms with data-no-double-submit-protection attribute
            if (form.hasAttribute('data-no-double-submit-protection')) return;

            // v3.46.1 — a confirm() the member answered "Cancel" to. Those
            // handlers call preventDefault(); before this check the guard
            // still marked the form as submitted and put the button into its
            // spinner, disabled, for 10 seconds, for a POST that never left.
            // Only confirm forms are skipped: core.js's CSRF guard also calls
            // preventDefault() (file-upload forms, then it resubmits itself),
            // and AJAX forms do too; those keep the guard exactly as before.
            if (e.defaultPrevented && (
                form.classList.contains('js-confirm-submit') ||
                form.hasAttribute('data-confirm-msg') ||
                (e.submitter && e.submitter.hasAttribute('data-confirm'))
            )) return;

            // If form was already submitted, prevent duplicate
            if (submittedForms.has(form)) {
                e.preventDefault();
                return false;
            }

            // Mark form as submitted
            submittedForms.add(form);

            // The button that was actually pressed (e.submitter), falling
            // back to the form's first submit button for Enter-key submits
            // and old browsers.
            const submitBtn = e.submitter
                || form.querySelector('button[type="submit"], input[type="submit"]');

            // Disable it AFTER the browser has built the POST, not during this
            // event. A button that is disabled while the submit event runs is
            // left out of the form data, so <button name="decision"
            // value="approved"> arrived at the server with no "decision" at
            // all (v3.44.3). setTimeout(0) runs once the data is collected;
            // the WeakSet above already blocks a second submit in between.
            if (submitBtn) {
                setTimeout(function() {
                    submitBtn.disabled = true;
                    submitBtn.classList.add('opacity-60', 'cursor-not-allowed');

                    // Add loading indicator if button has text content
                    const originalText = submitBtn.textContent;
                    if (originalText && !submitBtn.querySelector('svg.animate-spin')) {
                        submitBtn.dataset.originalText = originalText;
                        submitBtn.innerHTML = '<svg class="animate-spin inline-block w-4 h-4 mr-2" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>';
                        // v3.44.9: the label goes back in as text. It used to
                        // be concatenated into innerHTML, which re-parsed text
                        // Django had already escaped (a member's name on a
                        // button, say) as markup.
                        submitBtn.appendChild(document.createTextNode(originalText + '...'));
                    }
                }, 0);
            }

            // Re-enable after 10 seconds (in case of error/reload)
            setTimeout(function() {
                submittedForms.delete(form);
                if (submitBtn) {
                    submitBtn.disabled = false;
                    submitBtn.classList.remove('opacity-60', 'cursor-not-allowed');
                    if (submitBtn.dataset.originalText) {
                        submitBtn.textContent = submitBtn.dataset.originalText;
                    }
                }
            }, 10000);
        });
    })();
    
