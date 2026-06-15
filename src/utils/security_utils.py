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
        # Using ip-api.com (free, no API key required, 45 req/min limit)
        response = requests.get(
            f'http://ip-api.com/json/{ip_address}',
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

        # Check for new IP (use unsliced queryset)
        known_ips = previous_logins_base.filter(ip_address=ip_address)
        if not known_ips.exists():
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
    Run the standard post-authentication security pipeline, shared by the password
    and passkey login paths.

    Covers:
    - IP blacklist check (deny before login() if blocked)
    - Geo / foreign-IP detection
    - Session flag injection (login_geo, login_geo_suspicious, etc.)
    - LoginHistory creation
    - LoginAlert creation for non-US logins
    - Watch-flag alert

    Call this BEFORE calling login() on the request. If the first return value is
    not None, the IP is blocked and the view should return that error response
    immediately without proceeding with login.

    Returns:
        (error_response, None)    — IP is actively blacklisted; return error_response
        (None, context_dict)      — pipeline passed; context_dict has 'is_foreign' and 'login_record'
    """
    from src.models import IPBlacklist, UserSession, LoginHistory, LoginAlert
    from src.geo_utils import is_foreign_ip
    from src.security_notifications import send_watch_flag_alert

    security_log = logging.getLogger('admin_actions')
    fn_log = logging.getLogger('function_calls')

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
    risk_factors = ['non_us_location'] if is_foreign else []

    request.session['login_geo'] = geo
    request.session['login_geo_suspicious'] = is_foreign
    if geo:
        request.session['login_geo_country'] = geo.get('country', '')
        request.session['login_geo_city'] = geo.get('city', '')

    # --- LoginHistory ---
    login_record = None
    try:
        login_record = LoginHistory.objects.create(
            user=user,
            status='success',
            ip_address=ip_address,
            country=geo.get('country', '') if geo else '',
            city=geo.get('city', '') if geo else '',
            region=geo.get('region', '') if geo else '',
            latitude=geo.get('lat') if geo else None,
            longitude=geo.get('lon') if geo else None,
            user_agent=user_agent,
            is_suspicious=is_foreign,
            risk_level='medium' if is_foreign else 'low',
            risk_factors=risk_factors,
            alert_created=is_foreign,
        )
    except Exception as exc:
        security_log.warning(f'Failed to create LoginHistory: {exc}')

    # --- LoginAlert + in-app notification for non-US logins ---
    if is_foreign and geo:
        try:
            location_str = ', '.join(filter(None, [geo.get('city'), geo.get('region'), geo.get('country')]))
            LoginAlert.objects.create(
                user=user,
                login_history=login_record,
                alert_type='new_location',
                severity='medium',
                status='new',
                title=f'Non-US login [{method}]: {user.name} from {geo.get("country", "Unknown")}',
                description=(
                    f'{user.name} logged in via {method} from outside the United States.\n\n'
                    f'Location: {location_str}\n'
                    f'IP: {ip_address}\n'
                    f'ISP: {geo.get("isp", "Unknown")}\n'
                    f'Coordinates: {geo.get("lat")}, {geo.get("lon")}\n\n'
                    f'The user has been flagged for this session. Sensitive data exports '
                    f'are restricted until they log in from a US IP address.'
                ),
            )
        except Exception as exc:
            security_log.warning(f'Failed to create LoginAlert: {exc}')

        # Notify the user directly (in-app + email if they have one)
        try:
            from src.security_notifications import notify_user_security_event
            notify_user_security_event(
                user,
                subject=f'New login from {geo.get("country", "outside the US")}',
                body=(
                    f'Your account was accessed from {location_str or geo.get("country", "an international location")}. '
                    f'If this was you logging in while traveling, no action is needed. '
                    f'If you don\'t recognize this login, contact an officer immediately and change your password.'
                ),
                ip_address=ip_address,
            )
        except Exception as exc:
            security_log.warning(f'Failed to send non-US login user notification: {exc}')

    # --- Logging ---
    fn_log.info(
        f'Successful login [{method}]: {user.name} ({user.member_type}) (user_id={user.user_id}) '
        f'from IP {ip_address}'
        + (f" [{geo.get('city')}, {geo.get('country')}]" if geo else '')
    )
    if is_foreign:
        security_log.warning(
            f'LOGIN SUCCESS (NON-US) [{method}]: User {user.username!r} (ID: {user.user_id}) '
            f'from IP {ip_address} - Location: {geo.get("city", "?")}, {geo.get("country", "?")} '
            f'(ISP: {geo.get("isp", "?")}). Session flagged as suspicious.'
        )
    else:
        security_log.info(
            f'LOGIN SUCCESS [{method}]: User {user.username!r} (ID: {user.user_id}) from IP {ip_address}'
        )

    # --- Watch-flag alert ---
    try:
        watch_flag = getattr(user, 'watch_flag', None)
        if watch_flag and watch_flag.is_active:
            from src.models import IPBlacklist as _IPB
            send_watch_flag_alert(
                watched_user=user,
                event_type='success',
                ip_address=ip_address,
                geo=geo,
                user_agent=user_agent,
                is_whitelisted=False,
                is_blacklisted=_IPB.objects.filter(ip_address=ip_address, is_active=True).exists(),
                is_rate_limited=False,
                risk_level='medium' if is_foreign else 'low',
                risk_factors=risk_factors,
                is_foreign=is_foreign,
                watch_reason=watch_flag.reason,
                login_history=login_record,
            )
    except Exception as exc:
        security_log.error(f'Watch flag alert error [{method}]: {exc}')

    return None, {'is_foreign': is_foreign, 'login_record': login_record}


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
