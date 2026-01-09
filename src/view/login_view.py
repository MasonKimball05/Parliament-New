from django.contrib.auth.decorators import login_required
from django.contrib.messages import get_messages
from ..models import *
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import login, authenticate
from django.utils import timezone
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

        # Check if IP is blacklisted
        blacklist_entry = IPBlacklist.objects.filter(
            ip_address=ip_address,
            is_active=True
        ).first()

        if blacklist_entry:
            # Check if blacklist has expired
            if blacklist_entry.expires_at and blacklist_entry.expires_at < timezone.now():
                # Blacklist expired, deactivate it
                blacklist_entry.is_active = False
                blacklist_entry.save()
            else:
                # IP is actively blacklisted, update block count and deny access
                blacklist_entry.block_count += 1
                blacklist_entry.last_blocked = timezone.now()
                blacklist_entry.save()

                security_logger = logging.getLogger('admin_actions')
                security_logger.warning(
                    f"BLOCKED LOGIN: Blacklisted IP {ip_address} attempted login as '{username}'. "
                    f"Reason: {blacklist_entry.reason}"
                )

                messages.error(
                    request,
                    "Access denied. Your IP address has been blocked. Please contact an administrator if you believe this is an error."
                )
                return redirect('login')

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

            # Log failed login attempt
            security_logger = logging.getLogger('admin_actions')
            security_logger.warning(
                f"LOGIN FAILED: Invalid credentials for username '{username}' from IP {ip_address}"
            )

            return redirect('login')

    return render(request, 'registration/login.html')