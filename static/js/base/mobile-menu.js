/* static/js/base/mobile-menu.js — moved out of templates/base.html in v3.45.6.
 * Mobile navigation menu.
 * Loaded with a plain <script src> at the same place in the page the inline
 * block used to be, so it runs at the same moment as before. base.html is
 * rendered for every page; this way the browser downloads this once and
 * caches it (see src/templatetags/static_version.py for the ?v= part). */
        const mobileMenuButton = document.getElementById('mobile-menu-button');
        const mobileMenu = document.getElementById('mobile-menu');
        const menuOpenIcon = document.getElementById('menu-open-icon');
        const menuCloseIcon = document.getElementById('menu-close-icon');

        if (mobileMenuButton && mobileMenu && menuOpenIcon && menuCloseIcon) {
            mobileMenuButton.addEventListener('click', () => {
                const isHidden = mobileMenu.classList.contains('hidden');

                if (isHidden) {
                    // Open menu
                    mobileMenu.classList.remove('hidden');
                    menuOpenIcon.classList.add('hidden');
                    menuCloseIcon.classList.remove('hidden');

                    // Prevent body scroll when menu is open
                    document.body.style.overflow = 'hidden';
                } else {
                    // Close menu
                    mobileMenu.classList.add('hidden');
                    menuOpenIcon.classList.remove('hidden');
                    menuCloseIcon.classList.add('hidden');

                    // Restore body scroll
                    document.body.style.overflow = '';
                }
            });

            // Close menu when clicking on a link
            const menuLinks = mobileMenu.querySelectorAll('a');
            menuLinks.forEach(link => {
                link.addEventListener('click', () => {
                    mobileMenu.classList.add('hidden');
                    menuOpenIcon.classList.remove('hidden');
                    menuCloseIcon.classList.add('hidden');
                    document.body.style.overflow = '';
                });
            });

            // Close menu on window resize to desktop size
            window.addEventListener('resize', () => {
                if (window.innerWidth >= 1280) { // xl breakpoint
                    mobileMenu.classList.add('hidden');
                    menuOpenIcon.classList.remove('hidden');
                    menuCloseIcon.classList.add('hidden');
                    document.body.style.overflow = '';
                }
            });
        }
    
