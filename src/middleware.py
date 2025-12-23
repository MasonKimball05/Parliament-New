"""
Custom middleware for Parliament application
"""
from django.shortcuts import redirect
from django.urls import reverse


class ForcePasswordChangeMiddleware:
    """
    Middleware to force users to change password if force_password_change flag is set.
    Redirects authenticated users to the password change page if needed.
    """

    def __init__(self, get_response):
        self.get_response = get_response
        # Paths that should be accessible even when password change is forced
        self.exempt_paths = [
            reverse('forced_password_change'),
            reverse('logout'),
            '/admin/',  # Allow admin access
        ]

    def __call__(self, request):
        # Check if user is authenticated and needs to change password
        if request.user.is_authenticated and hasattr(request.user, 'force_password_change'):
            if request.user.force_password_change:
                # Allow access to certain paths
                if not any(request.path.startswith(path) for path in self.exempt_paths):
                    # Don't redirect if already on the password change page
                    if request.path != reverse('forced_password_change'):
                        return redirect('forced_password_change')

        response = self.get_response(request)
        return response
