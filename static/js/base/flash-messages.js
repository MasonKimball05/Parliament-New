/* static/js/base/flash-messages.js — moved out of templates/base.html in v3.45.6.
 * Flash messages: success/info fade after 5 s, errors stay.
 * Loaded with a plain <script src> at the same place in the page the inline
 * block used to be, so it runs at the same moment as before. base.html is
 * rendered for every page; this way the browser downloads this once and
 * caches it (see src/templatetags/static_version.py for the ?v= part). */
        // v3.15.0 QOL: success/info flashes fade after 5 s (pause on hover);
        // errors and warnings stay until the user navigates — they may need
        // to be read carefully or acted on.
        (function () {
            document.querySelectorAll('.js-flash-message').forEach(function (el) {
                var tags = el.dataset.messageTags || '';
                if (tags.indexOf('error') !== -1 || tags.indexOf('warning') !== -1) return;
                var timer = null;
                function arm() {
                    timer = setTimeout(function () {
                        el.style.opacity = '0';
                        setTimeout(function () { el.remove(); }, 500);
                    }, 5000);
                }
                el.addEventListener('mouseenter', function () {
                    if (timer) { clearTimeout(timer); timer = null; }
                    el.style.opacity = '1';
                });
                el.addEventListener('mouseleave', arm);
                arm();
            });
        })();
        
