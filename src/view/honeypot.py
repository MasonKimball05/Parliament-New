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
from src.geo_utils import get_ip_geo
from src.utils.security_utils import get_client_ip as _get_client_ip
import logging
import json
import threading

logger = logging.getLogger('admin_actions')


def lookup_geo(ip_address, record_id):
    """
    Look up geolocation and store it in the HoneypotAccess record's additional_data.
    Runs in a background thread so it never blocks the response.
    """
    geo = get_ip_geo(ip_address)
    result = geo if geo else {'geo_error': 'private IP or lookup failed'}
    try:
        record = HoneypotAccess.objects.get(id=record_id)
        record.additional_data['geo'] = result
        record.save(update_fields=['additional_data'])
    except Exception as e:
        logger.warning(f"Failed to save geo data for honeypot record {record_id}: {e}")

# How long to ban an IP after honeypot access (seconds)
HONEYPOT_BAN_DURATION = 24 * 60 * 60  # 24 hours


def get_client_ip(request):
    """Get the client's IP address, respecting BEHIND_CLOUDFLARE setting."""
    return _get_client_ip(request) or 'unknown'


def log_and_block_honeypot_access(request, endpoint):
    """
    Log honeypot access and block the IP.
    Returns an HttpResponse.
    """
    ip_address = get_client_ip(request)

    # Fast path: honeypot-ban cache key set on first hit (24h TTL).
    ban_key = f'honeypot_ban_{ip_address}'
    if cache.get(ban_key):
        return get_fake_response(endpoint)

    # Slower path: cache expired but IP may still be in the DB blacklist.
    # InputSanitizationMiddleware maintains ip_blacklisted_{ip}; check it
    # before creating a new log record to avoid log spam on repeat hits.
    db_ban_key = f'ip_blacklisted_{ip_address}'
    db_ban_cached = cache.get(db_ban_key)
    if db_ban_cached is None:
        # Cache miss — do a DB lookup (cheap indexed query)
        try:
            from src.models import IPBlacklist
            if IPBlacklist.objects.filter(ip_address=ip_address, is_active=True).exists():
                # Re-warm both cache keys so subsequent requests are fast
                cache.set(ban_key, True, HONEYPOT_BAN_DURATION)
                cache.set(db_ban_key, True, 300)
                return get_fake_response(endpoint)
        except Exception as e:
            logger.warning(f"Honeypot DB blacklist check failed for {ip_address}: {e}")
    elif db_ban_cached:
        # Already cached as blacklisted — short-circuit without a new log record
        cache.set(ban_key, True, HONEYPOT_BAN_DURATION)
        return get_fake_response(endpoint)

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

    # Kick off geolocation lookup in background (non-blocking)
    threading.Thread(
        target=lookup_geo,
        args=(ip_address, honeypot_record.id),
        daemon=True,
    ).start()

    # Ban the IP in cache (fast path for repeat honeypot requests)
    ban_key = f'honeypot_ban_{ip_address}'
    cache.set(ban_key, True, HONEYPOT_BAN_DURATION)

    # Also persist to IPBlacklist DB so InputSanitizationMiddleware blocks this IP
    # on ALL endpoints, not just honeypot URLs, and so the ban survives cache flushes.
    try:
        from src.models import IPBlacklist
        if not IPBlacklist.objects.filter(ip_address=ip_address, is_active=True).exists():
            IPBlacklist.objects.create(
                ip_address=ip_address,
                reason=f'Honeypot trigger: {endpoint}',
                added_by=None,  # auto-added, no admin user
            )
        # Invalidate the middleware's cached blacklist result so it re-checks immediately
        cache.delete(f'ip_blacklisted_{ip_address}')
    except Exception as e:
        logger.error(f"Failed to add {ip_address} to IPBlacklist: {e}")

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

    # Determine if this hit warrants immediate escalation.
    # Routine scanner hits just get logged; the daily digest covers those.
    escalate = False
    escalation_reason = ''

    # Escalate if this IP has hit multiple distinct honeypots within the last hour
    from django.utils import timezone as tz
    recent_distinct = (
        HoneypotAccess.objects
        .filter(ip_address=ip_address, accessed_at__gte=tz.now() - tz.timedelta(hours=1))
        .values('endpoint')
        .distinct()
        .count()
    )
    if recent_distinct >= 3:
        escalate = True
        escalation_reason = f"IP hit {recent_distinct} distinct honeypot endpoints in the last hour — coordinated recon."

    # Escalate POST requests that include form field data (credential probing)
    if not escalate and request.method == 'POST' and request.POST:
        escalate = True
        escalation_reason = f"POST request with form data submitted to honeypot ({endpoint}). Fields: {list(request.POST.keys())}"

    # Send security alert (immediate email only if escalated, otherwise logs-only)
    alert_honeypot_triggered(endpoint, ip_address, user_agent, escalate=escalate, escalation_reason=escalation_reason)

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
    elif '.git' in endpoint:
        # Fake git config file
        return HttpResponse(
            "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n",
            content_type='text/plain',
            status=200
        )
    elif '.env' in endpoint or 'credentials' in endpoint or '.htaccess' in endpoint:
        # Already handled above or fake file
        return HttpResponse(
            "# Configuration file\n",
            content_type='text/plain',
            status=200
        )
    elif 'server-status' in endpoint or 'server-info' in endpoint:
        # Fake Apache server status
        return HttpResponse(
            '<html><body><h1>Apache Server Status</h1><p>Server Version: Apache/2.4.41</p></body></html>',
            content_type='text/html',
            status=200
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


@csrf_exempt
def honeypot_git(request, path=''):
    """Fake .git directory endpoint — probing for exposed git repos."""
    return log_and_block_honeypot_access(request, f'/.git/{path}' if path else '/.git/config')


@csrf_exempt
def honeypot_php_admin(request):
    """Fake admin.php/login.php — generic PHP admin panel probe."""
    return log_and_block_honeypot_access(request, request.path)


@csrf_exempt
def honeypot_wp_content(request, path=''):
    """Fake WordPress content/includes directory."""
    return log_and_block_honeypot_access(request, request.path)


@csrf_exempt
def honeypot_joomla(request, path=''):
    """Fake Joomla administrator panel."""
    return log_and_block_honeypot_access(request, f'/administrator/{path}' if path else '/administrator/')


@csrf_exempt
def honeypot_htaccess(request):
    """Fake .htaccess file — Apache config probe."""
    return log_and_block_honeypot_access(request, '/.htaccess')


@csrf_exempt
def honeypot_aws(request, path=''):
    """Fake AWS credentials probe."""
    return log_and_block_honeypot_access(request, request.path)


@csrf_exempt
def honeypot_server_status(request):
    """Fake Apache server-status endpoint."""
    return log_and_block_honeypot_access(request, '/server-status')
