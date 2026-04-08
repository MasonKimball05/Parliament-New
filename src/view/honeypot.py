"""
Honeypot (poison pill) views for Parliament.
These are fake endpoints that real users would never access.
Any access to these endpoints is suspicious and triggers immediate action.
"""
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.core.cache import cache
from django.conf import settings
from src.models import HoneypotAccess
from src.security_notifications import alert_honeypot_triggered
import logging
import json

logger = logging.getLogger('admin_actions')

# How long to ban an IP after honeypot access (seconds)
HONEYPOT_BAN_DURATION = 24 * 60 * 60  # 24 hours


def get_client_ip(request):
    """Get the client's IP address from the request."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR', 'unknown')
    return ip


def log_and_block_honeypot_access(request, endpoint):
    """
    Log honeypot access and block the IP.
    Returns an HttpResponse.
    """
    ip_address = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]
    referer = request.META.get('HTTP_REFERER', '')[:500]

    # Sanitize POST body (don't log sensitive data)
    request_body = ''
    if request.method == 'POST':
        try:
            # Only log field names, not values (could contain passwords)
            if request.POST:
                request_body = f"POST fields: {list(request.POST.keys())}"
            elif request.body:
                request_body = f"Raw body length: {len(request.body)} bytes"
        except Exception:
            request_body = "Unable to parse body"

    # Log to database
    honeypot_record = HoneypotAccess.objects.create(
        endpoint=endpoint,
        ip_address=ip_address,
        user_agent=user_agent,
        referer=referer,
        request_method=request.method,
        request_body=request_body,
        action_taken='blocked',
        additional_data={
            'headers': {
                k: v for k, v in request.META.items()
                if k.startswith('HTTP_') and k not in ['HTTP_COOKIE', 'HTTP_AUTHORIZATION']
            }
        }
    )

    # Ban the IP
    ban_key = f'honeypot_ban_{ip_address}'
    cache.set(ban_key, True, HONEYPOT_BAN_DURATION)

    # Also add to attack attempts counter
    attack_key = f'attack_attempts_{ip_address}'
    attack_count = cache.get(attack_key, 0) + 10  # Honeypot access = instant high count
    cache.set(attack_key, attack_count, 3600)

    # Log the event
    logger.critical(
        f"HONEYPOT TRIGGERED: {endpoint} from IP {ip_address}. "
        f"UA: {user_agent[:100]}. Referer: {referer[:100]}. "
        f"IP banned for {HONEYPOT_BAN_DURATION // 3600} hours."
    )

    # Send security alert
    alert_honeypot_triggered(endpoint, ip_address, user_agent)

    # Return a convincing fake response based on endpoint
    return get_fake_response(endpoint)


def get_fake_response(endpoint):
    """Return a realistic-looking fake response to waste attacker time."""
    if 'wp-admin' in endpoint:
        # Fake WordPress response
        return HttpResponseForbidden(
            '<html><head><title>WordPress &rsaquo; Error</title></head>'
            '<body><h1>Error establishing a database connection</h1></body></html>',
            content_type='text/html'
        )
    elif 'phpmyadmin' in endpoint:
        # Fake phpMyAdmin response
        return HttpResponseForbidden(
            '<html><head><title>phpMyAdmin</title></head>'
            '<body><h1>Access denied.</h1></body></html>',
            content_type='text/html'
        )
    elif '.env' in endpoint:
        # Fake .env file (obviously fake data)
        return HttpResponse(
            "APP_DEBUG=false\n"
            "DB_CONNECTION=mysql\n"
            "DB_HOST=HONEYPOT_DETECTED\n"
            "DB_DATABASE=fake_database\n",
            content_type='text/plain'
        )
    elif 'backup' in endpoint:
        # Fake backup endpoint
        return HttpResponse(
            '{"error": "Insufficient permissions", "code": 403}',
            content_type='application/json',
            status=403
        )
    else:
        # Generic 404-ish response
        return HttpResponseForbidden(
            '<html><body><h1>403 Forbidden</h1></body></html>',
            content_type='text/html'
        )


def is_ip_honeypot_banned(ip_address):
    """Check if an IP is banned due to honeypot access."""
    ban_key = f'honeypot_ban_{ip_address}'
    return cache.get(ban_key, False)


# Honeypot view functions
@csrf_exempt
def honeypot_wp_admin(request, path=''):
    """Fake WordPress admin endpoint."""
    return log_and_block_honeypot_access(request, f'/wp-admin/{path}')


@csrf_exempt
def honeypot_wp_login(request):
    """Fake WordPress login endpoint."""
    return log_and_block_honeypot_access(request, '/wp-login.php')


@csrf_exempt
def honeypot_phpmyadmin(request, path=''):
    """Fake phpMyAdmin endpoint."""
    endpoint = '/phpmyadmin' + (f'/{path}' if path else '')
    return log_and_block_honeypot_access(request, endpoint)


@csrf_exempt
def honeypot_env(request):
    """Fake .env file endpoint."""
    return log_and_block_honeypot_access(request, '/.env')


@csrf_exempt
def honeypot_admin_backup(request):
    """Fake admin backup endpoint."""
    return log_and_block_honeypot_access(request, '/admin/backup/')


@csrf_exempt
def honeypot_api_export(request):
    """Fake API data export endpoint."""
    return log_and_block_honeypot_access(request, '/api/v1/users/export/')


@csrf_exempt
def honeypot_xmlrpc(request):
    """Fake WordPress XML-RPC endpoint."""
    return log_and_block_honeypot_access(request, '/xmlrpc.php')


@csrf_exempt
def honeypot_config(request, filename=''):
    """Fake config file endpoint."""
    endpoint = f'/{filename}' if filename else '/config.php'
    return log_and_block_honeypot_access(request, endpoint)


@csrf_exempt
def honeypot_shell(request, path=''):
    """Fake shell/webshell endpoint."""
    return log_and_block_honeypot_access(request, f'/shell/{path}' if path else '/shell.php')


@csrf_exempt
def honeypot_setup(request, path=''):
    """Fake setup/install endpoint."""
    return log_and_block_honeypot_access(request, f'/setup/{path}' if path else '/install.php')
