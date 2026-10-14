/* static/js/base/announcements.js — moved out of templates/base.html in v3.45.6.
 * Dismissing banner announcements.
 * Loaded with a plain <script src> at the same place in the page the inline
 * block used to be, so it runs at the same moment as before. base.html is
 * rendered for every page; this way the browser downloads this once and
 * caches it (see src/templatetags/static_version.py for the ?v= part). */
        function dismissAnnouncement(announcementId) {
            fetch(`/api/dismiss-announcement/${announcementId}/`, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': getCookie('csrftoken'),
                    'Content-Type': 'application/json'
                }
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    // Fade out and remove the announcement banner
                    const banner = document.getElementById(`announcement-${announcementId}`);
                    if (banner) {
                        banner.style.transition = 'opacity 0.3s ease-out';
                        banner.style.opacity = '0';
                        setTimeout(() => banner.remove(), 300);
                    }
                } else {
                    console.error('Failed to dismiss announcement:', data.error);
                }
            })
            .catch(error => {
                console.error('Error dismissing announcement:', error);
            });
        }

        // Helper function to get CSRF token (meta tag first, then cookie fallback)
        function getCookie(name) {
            // Prefer meta tag (works even with CSRF_COOKIE_HTTPONLY=True)
            if (name === 'csrftoken') {
                const meta = document.querySelector('meta[name="csrf-token"]');
                if (meta) return meta.getAttribute('content');
            }
            let cookieValue = null;
            if (document.cookie && document.cookie !== '') {
                const cookies = document.cookie.split(';');
                for (let i = 0; i < cookies.length; i++) {
                    const cookie = cookies[i].trim();
                    if (cookie.substring(0, name.length + 1) === (name + '=')) {
                        cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                        break;
                    }
                }
            }
            return cookieValue;
        }
    
