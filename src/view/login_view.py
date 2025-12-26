from django.contrib.auth.decorators import login_required
from django.contrib.messages import get_messages
from ..models import *
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import login, authenticate
import logging


def get_client_ip(request):
    """Get the client's IP address from the request."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', 'unknown')
    return ip


def login_view(request):
    list(get_messages(request))  # Clear flash messages

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        ip_address = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', 'unknown')[:200]

        if not username or not password:
            messages.error(request, "Both username and password are required.")
            security_logger = logging.getLogger('admin_actions')
            security_logger.warning(
                f"Login attempt with missing credentials from IP {ip_address}"
            )
            return redirect('login')

        # Use Django's built-in authenticate method for secure password checking
        user = authenticate(request, username=username, password=password)

        if user is not None:
            if user.is_active:
                login(request, user)

                # Log successful login with IP and user agent
                logger = logging.getLogger('function_calls')
                logger.info(
                    f"Successful login: {user.name} ({user.member_type}) (user_id={user.user_id}) "
                    f"from IP {ip_address}"
                )

                # Also log to admin_actions for security audit
                security_logger = logging.getLogger('admin_actions')
                security_logger.info(
                    f"LOGIN SUCCESS: User '{username}' (ID: {user.user_id}) from IP {ip_address}"
                )

                messages.success(request, f"Welcome, {user.get_display_name() if hasattr(user, 'get_display_name') else user.name}!")

                next_url = request.GET.get('next', 'home')

                return redirect(next_url)
            else:
                messages.error(request, "This account has been disabled.")
                security_logger = logging.getLogger('admin_actions')
                security_logger.warning(
                    f"LOGIN FAILED: Attempt to access disabled account '{username}' from IP {ip_address}"
                )
                return redirect('login')
        else:
            messages.error(request, "Invalid username or password.")
            # Note: Detailed failed login logging is now handled by LoginRateLimitMiddleware
            # This prevents duplicate logging
            return redirect('login')

    return render(request, 'registration/login.html')