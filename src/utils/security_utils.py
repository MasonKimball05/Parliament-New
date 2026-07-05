"""
Security utilities for login tracking, geolocation, and anomaly detection
"""
import requests
import logging
from datetime import datetime, timedelta
from math import radians, cos, sin, asin, sqrt
from user_agents import parse as parse_user_agent
from django.utils import timezone
from django.utils.timezone import localtime
from django.conf import settings
from django.http import JsonResponse

logger = logging.getLogger('security')


def get_client_ip(request):
    """
    Get the client's IP address from the request.

    When BEHIND_CLOUDFLARE=True in settings, Cloudflare sits in front of nginx
    and sets the CF-Connecting-IP header to the real visitor IP. We use that
    directly because it is set by Cloudflare and cannot be forged by the visitor.

    Otherwise we sit behind a single nginx proxy. nginx appends the real client
    IP as the RIGHTMOST entry in X-Forwarded-For via $proxy_add_x_forwarded_for.
    Taking the rightmost value prevents spoofing: an attacker can forge leading
    XFF entries, but nginx always appends the actual socket IP.
    """
    if getattr(settings, 'BEHIND_CLOUDFLARE', False):
        cf_ip = request.META.get('HTTP_CF_CONNECTING_IP')
        if cf_ip:
            return cf_ip.strip()

    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[-1].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def get_geolocation_from_ip(ip_address):
    """
    Get geolocation data from an IP address using a free IP geolocation service

    Returns:
        dict: Dictionary with country, city, region, latitude, longitude
              Returns empty dict if lookup fails
    """
    # Skip private/local IPs
    if ip_address in ['127.0.0.1', 'localhost'] or ip_address.startswith('192.168.') or ip_address.startswith('10.'):
        return {
            'country': 'Local Network',
            'city': 'Local',
            'region': '',
            'latitude': None,
            'longitude': None
        }

    try:
        # Using ip-api.com (free, no API key required, 45 req/min limit).
        # Base URL is configurable (GEO_API_BASE_URL) so an HTTPS endpoint can
        # be used in production instead of sending lookups over cleartext HTTP.
        base_url = getattr(settings, 'GEO_API_BASE_URL', 'http://ip-api.com/json/')
        response = requests.get(
            f'{base_url}{ip_address}',
            timeout=3
        )

        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'success':
                return {
                    'country': data.get('country', ''),
                    'city': data.get('city', ''),
                    'region': data.get('regionName', ''),
                    'latitude': data.get('lat'),
                    'longitude': data.get('lon')
                }
    except Exception as e:
        logger.error(f"Failed to get geolocation for IP {ip_address}: {str(e)}")

    return {
        'country': '',
        'city': '',
        'region': '',
        'latitude': None,
        'longitude': None
    }


def parse_device_info(user_agent_string):
    """
    Parse user agent string to extract device, browser, and OS information

    Returns:
        dict: Dictionary with device_type, browser, os
    """
    try:
        ua = parse_user_agent(user_agent_string)

        # Determine device type
        if ua.is_mobile:
            device_type = 'mobile'
        elif ua.is_tablet:
            device_type = 'tablet'
        elif ua.is_pc:
            device_type = 'desktop'
        elif ua.is_bot:
            device_type = 'bot'
        else:
            device_type = 'unknown'

        # Get browser info
        browser = f"{ua.browser.family}"
        if ua.browser.version_string:
            browser += f" {ua.browser.version_string}"

        # Get OS info
        os = f"{ua.os.family}"
        if ua.os.version_string:
            os += f" {ua.os.version_string}"

        return {
            'device_type': device_type,
            'browser': browser,
            'os': os
        }
    except Exception as e:
        logger.error(f"Failed to parse user agent: {str(e)}")
        return {
            'device_type': 'unknown',
            'browser': 'Unknown',
            'os': 'Unknown'
        }


def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great circle distance between two points on earth (in km)
    Uses the Haversine formula

    Args:
        lat1, lon1: Latitude and longitude of point 1
        lat2, lon2: Latitude and longitude of point 2

    Returns:
        float: Distance in kilometers
    """
    if None in (lat1, lon1, lat2, lon2):
        return None

    # Convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

    # Haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))

    # Radius of earth in kilometers
    r = 6371

    return c * r


def analyze_login_risk(user, ip_address, location_data, device_info):
    """
    Analyze a login attempt for security risks

    Args:
        user: ParliamentUser instance
        ip_address: IP address of login
        location_data: Dict with geolocation data
        device_info: Dict with device/browser info

    Returns:
        dict: {
            'is_suspicious': bool,
            'risk_level': str ('low', 'medium', 'high', 'critical'),
            'risk_factors': list of strings,
            'distance_from_last': float or None,
            'time_from_last': float or None
        }
    """
    from src.models import LoginHistory

    risk_factors = []
    risk_score = 0

    # Get previous successful logins (unsliced for filtering)
    previous_logins_base = LoginHistory.objects.filter(
        user=user,
        status='success'
    ).order_by('-timestamp')

    # Get last 10 for iteration
    previous_logins = previous_logins_base[:10]

    if previous_logins.exists():
        last_login = previous_logins.first()

        # Calculate time since last login
        time_diff = timezone.now() - last_login.timestamp
        time_hours = time_diff.total_seconds() / 3600

        # Calculate distance from last login (if we have coordinates)
        distance_km = None
        if (location_data.get('latitude') and location_data.get('longitude') and
            last_login.latitude and last_login.longitude):
            distance_km = calculate_distance(
                last_login.latitude,
                last_login.longitude,
                location_data['latitude'],
                location_data['longitude']
            )

            # Impossible travel detection
            if distance_km and time_hours > 0:
                avg_speed = distance_km / time_hours

                if avg_speed > 1000:  # Faster than commercial flight
                    risk_factors.append(f'Impossible travel: {distance_km:.0f}km in {time_hours:.1f}h ({avg_speed:.0f}km/h)')
                    risk_score += 50
                elif distance_km > 500 and time_hours < 1:
                    risk_factors.append(f'Suspicious travel: {distance_km:.0f}km in less than 1 hour')
                    risk_score += 30

        # Check if this is a new location (use unsliced queryset)
        known_locations = previous_logins_base.filter(
            city=location_data.get('city'),
            country=location_data.get('country')
        )
        if not known_locations.exists() and location_data.get('city'):
            risk_factors.append(f'New login location: {location_data.get("city")}, {location_data.get("country")}')
            risk_score += 10

        # Check if this is a new device/browser (use unsliced queryset)
        known_devices = previous_logins_base.filter(
            device_type=device_info.get('device_type'),
            browser__icontains=device_info.get('browser', '').split()[0]  # Check browser family
        )
        if not known_devices.exists():
            risk_factors.append(f'New device: {device_info.get("device_type")} with {device_info.get("browser")}')
            risk_score += 15

        # Check for new IP.
        # NOTE: LoginHistory.ip_address is an EncryptedCharField (Fernet). Its
        # ciphertext is non-deterministic, so a direct `.filter(ip_address=...)`
        # never matches and would flag every login as a new IP. Decrypt and
        # compare in Python over a bounded slice of recent logins instead.
        recent_ip_addresses = {
            login.ip_address for login in previous_logins_base[:100]
        }
        if ip_address not in recent_ip_addresses:
            risk_factors.append(f'New IP address: {ip_address}')
            risk_score += 5

        distance_from_last = distance_km
        time_from_last = time_hours
    else:
        # First login - moderate risk
        risk_factors.append('First login for this user')
        risk_score = 5
        distance_from_last = None
        time_from_last = None

    # Check for unusual login time (e.g., 2-6 AM)
    local_now = localtime(timezone.now())
    current_hour = local_now.hour
    if 2 <= current_hour <= 6:
        risk_factors.append(f'Unusual login time: {local_now.strftime("%H:%M %Z")}')
        risk_score += 5

    # Determine risk level based on score
    if risk_score >= 50:
        risk_level = 'critical'
        is_suspicious = True
    elif risk_score >= 30:
        risk_level = 'high'
        is_suspicious = True
    elif risk_score >= 15:
        risk_level = 'medium'
        is_suspicious = True
    else:
        risk_level = 'low'
        is_suspicious = False

    return {
        'is_suspicious': is_suspicious,
        'risk_level': risk_level,
        'risk_factors': risk_factors,
        'distance_from_last': distance_from_last,
        'time_from_last': time_from_last
    }


def run_post_auth_pipeline(request, user, ip_address, user_agent, method='password'):
    """
    Run the pre-login() security gate shared by the password and passkey login
    paths, and hand off context to the post-login signal handler.

    Covers:
    - IP blacklist check (deny before login() if blocked)
    - Geo / foreign-IP detection
    - Session flag injection (login_geo, login_geo_suspicious, etc.)

    LoginHistory creation, the non-US LoginAlert + user notification, the
    watch-flag alert, and success logging all happen exactly once — in
    signals.py's `log_successful_login`, fired by Django's `user_logged_in`
    signal when `login()` is called right after this function returns. This
    keeps a single write path instead of one here and one in the signal
    handler. This function stashes `request._login_pipeline` so that handler
    can reuse the already-computed geo/is_foreign data and tag its alerts/logs
    with the password-vs-passkey `method` label, instead of recomputing.

    Call this BEFORE calling login() on the request. If the first return value
    is not None, the IP is blocked and the view should return that error
    response immediately without proceeding with login.

    Returns:
        (error_response, None)        — IP is actively blacklisted; return error_response
        (None, {'is_foreign': bool})  — pipeline passed
    """
    from src.models import IPBlacklist
    from src.geo_utils import is_foreign_ip

    security_log = logging.getLogger('admin_actions')

    # --- Blacklist check ---
    # Redundant for the password-login path (login_view already checks before authenticate()),
    # but load-bearing for the passkey path (webauthn.py has no prior check). Keep it here.
    blacklist_entry = IPBlacklist.objects.filter(ip_address=ip_address, is_active=True).first()
    if blacklist_entry:
        if blacklist_entry.expires_at and blacklist_entry.expires_at < timezone.now():
            blacklist_entry.is_active = False
            blacklist_entry.save()
        else:
            blacklist_entry.block_count += 1
            blacklist_entry.last_blocked = timezone.now()
            blacklist_entry.save()
            security_log.warning(
                f'BLOCKED LOGIN [{method}]: Blacklisted IP {ip_address} attempted login '
                f'as {user.username}. Reason: {blacklist_entry.reason}'
            )
            return JsonResponse({'error': 'Access denied. Your IP address has been blocked.'}, status=403), None

    # --- Geo check ---
    is_foreign, geo = is_foreign_ip(ip_address)

    request.session['login_geo'] = geo
    request.session['login_geo_suspicious'] = is_foreign
    if geo:
        request.session['login_geo_country'] = geo.get('country', '')
        request.session['login_geo_city'] = geo.get('city', '')

    # Stash for signals.log_successful_login — avoids a second LoginHistory
    # write (was the bug: this function used to create one here, and the
    # user_logged_in signal handler created a second one for the same login)
    # and a second geo lookup, and lets the signal handler tag its
    # alerts/logs with the password-vs-passkey method.
    request._login_pipeline = {
        'method': method,
        'is_foreign': is_foreign,
        'geo': geo,
        'ip_address': ip_address,
        'user_agent': user_agent,
    }

    return None, {'is_foreign': is_foreign}


def create_login_alert(login_history, alert_type, severity, title, description):
    """
    Create a login security alert

    Args:
        login_history: LoginHistory instance
        alert_type: Type of alert (from LoginAlert.ALERT_TYPE_CHOICES)
        severity: Severity level (from LoginAlert.SEVERITY_CHOICES)
        title: Alert title
        description: Alert description

    Returns:
        LoginAlert instance
    """
    from src.models import LoginAlert

    alert = LoginAlert.objects.create(
        user=login_history.user,
        login_history=login_history,
        alert_type=alert_type,
        severity=severity,
        title=title,
        description=description
    )

    # Mark the login history as having an alert
    login_history.alert_created = True
    login_history.save(update_fields=['alert_created'])

    logger.warning(f"Security alert created: {title} for user {login_history.user.name}")

    return alert
