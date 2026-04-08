"""
Security notification utilities for Parliament system.
Handles email alerts for critical security events like attack detection,
quarantine activation, and emergency lockdown.
"""
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
import logging

logger = logging.getLogger('admin_actions')


def get_security_alert_email():
    """Get the security alert email address from settings."""
    return getattr(settings, 'SECURITY_ALERT_EMAIL', getattr(settings, 'DEFAULT_FROM_EMAIL', None))


def get_site_url():
    """Get the site URL from settings"""
    return getattr(settings, 'SITE_URL', 'https://am-parliament.org').rstrip('/')


def send_security_alert(event_type, severity, details, ip_address=None, user=None, force_send=False):
    """
    Send a security alert email to configured admin email.

    Only sends for 'critical' severity unless force_send=True.
    All alerts are logged to SecurityNotificationLog regardless of email.

    Args:
        event_type: Type of security event (e.g., 'ATTACK_BLOCKED', 'ACCOUNT_QUARANTINED')
        severity: 'low', 'medium', 'high', or 'critical'
        details: Full description of the event
        ip_address: IP address involved (if applicable)
        user: ParliamentUser involved (if applicable)
        force_send: If True, send email regardless of severity

    Returns:
        SecurityNotificationLog instance
    """
    from src.models import SecurityNotificationLog

    email_to = get_security_alert_email()
    email_sent = False
    email_error = ''

    # Only send emails for critical events (or if forced)
    should_email = (severity == 'critical' or force_send) and email_to

    if should_email:
        try:
            site_url = get_site_url()
            subject = f"[SECURITY ALERT] {event_type}"

            message = f"""
Parliament Security Alert
{'=' * 60}

Event Type: {event_type}
Severity: {severity.upper()}
Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S %Z')}

IP Address: {ip_address or 'N/A'}
User: {user.name if user else 'N/A'} ({user.username if user else ''})

Details:
{'-' * 40}
{details}
{'-' * 40}

Action Required: Review in Admin-v2 Security Dashboard
{site_url}/admin-v2/security/

---
This is an automated security alert from Parliament.
            """.strip()

            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email_to],
                fail_silently=False
            )
            email_sent = True
            logger.info(f"Security alert email sent: {event_type} ({severity})")

        except Exception as e:
            email_error = str(e)
            logger.error(f"Failed to send security alert email: {e}")

    # Always log to database
    notification = SecurityNotificationLog.objects.create(
        event_type=event_type,
        severity=severity,
        details=details,
        ip_address=ip_address,
        user=user,
        email_sent_to=email_to or '',
        email_sent=email_sent,
        email_error=email_error
    )

    logger.warning(f"[SECURITY] {event_type} ({severity}): {details[:200]}...")

    return notification


def alert_attack_blocked(ip_address, attack_count, attack_type, details=''):
    """Send alert when multiple attack attempts are blocked from an IP."""
    event_details = f"""
Multiple attack attempts detected and blocked.

Attack Statistics:
- Total attempts: {attack_count}
- Latest attack type: {attack_type}
- Time window: Last 1 hour

{details}

The IP address has been temporarily blocked from accessing the system.
Consider adding this IP to the permanent blacklist if attacks continue.
    """.strip()

    return send_security_alert(
        event_type='ATTACK_BLOCKED',
        severity='critical',
        details=event_details,
        ip_address=ip_address
    )


def alert_failed_logins(ip_address, username, attempt_count):
    """Send alert when multiple failed login attempts are detected."""
    event_details = f"""
Excessive failed login attempts detected.

Login Attempt Details:
- Target username: {username}
- Total attempts: {attempt_count}
- Time window: Last 15 minutes

This may indicate a brute force attack or credential stuffing attempt.
The account and/or IP may have been temporarily locked.
    """.strip()

    return send_security_alert(
        event_type='FAILED_LOGIN_SPIKE',
        severity='critical',
        details=event_details,
        ip_address=ip_address
    )


def alert_account_quarantined(user, ip_address, reason, is_auto=True):
    """Send alert when an account is quarantined."""
    auto_text = "automatically by the system" if is_auto else "manually by an administrator"

    event_details = f"""
A user account has been quarantined {auto_text}.

Account Details:
- Name: {user.name}
- Username: {user.username}
- Member Type: {user.member_type}

Quarantine Reason:
{reason}

The user will not be able to log in until the quarantine is released
by an administrator in Admin-v2 > Security > Quarantine Management.
    """.strip()

    return send_security_alert(
        event_type='ACCOUNT_QUARANTINED',
        severity='critical',
        details=event_details,
        ip_address=ip_address,
        user=user
    )


def alert_honeypot_triggered(endpoint, ip_address, user_agent):
    """Send alert when a honeypot/poison pill endpoint is accessed."""
    event_details = f"""
A honeypot (trap) endpoint was accessed. This is suspicious activity
that indicates potential scanning or attack reconnaissance.

Honeypot Details:
- Endpoint accessed: {endpoint}
- User Agent: {user_agent[:200] if user_agent else 'N/A'}

The IP address has been automatically blocked.
This type of access is typically from automated scanners or
attackers probing for vulnerabilities.
    """.strip()

    return send_security_alert(
        event_type='HONEYPOT_TRIGGERED',
        severity='critical',
        details=event_details,
        ip_address=ip_address
    )


def alert_lockdown_activated(admin, reason):
    """Send alert when emergency lockdown is activated."""
    event_details = f"""
EMERGENCY LOCKDOWN HAS BEEN ACTIVATED

Activated by: {admin.name} ({admin.username})
Reason: {reason}

All login attempts from non-whitelisted IPs will be blocked.
Users will see a maintenance message when trying to access the system.

To deactivate lockdown, visit Admin-v2 > Security > Emergency Lockdown
or access from a whitelisted IP address.
    """.strip()

    return send_security_alert(
        event_type='LOCKDOWN_ACTIVATED',
        severity='critical',
        details=event_details,
        user=admin,
        force_send=True  # Always send lockdown notifications
    )


def alert_lockdown_deactivated(admin):
    """Send alert when emergency lockdown is deactivated."""
    event_details = f"""
Emergency lockdown has been deactivated.

Deactivated by: {admin.name} ({admin.username})
Time: {timezone.now().strftime('%Y-%m-%d %H:%M:%S %Z')}

Normal operations have resumed. All users can now log in.
    """.strip()

    return send_security_alert(
        event_type='LOCKDOWN_DEACTIVATED',
        severity='high',
        details=event_details,
        user=admin,
        force_send=True  # Always send lockdown notifications
    )


def alert_impossible_travel(user, ip_address, last_location, new_location, time_diff_minutes):
    """Send alert when impossible travel is detected (login from geographically distant location too quickly)."""
    event_details = f"""
Impossible travel detected - user logged in from two distant locations
in an impossibly short time period.

User: {user.name} ({user.username})

Location Change:
- Previous: {last_location}
- Current: {new_location}
- Time between logins: {time_diff_minutes} minutes

This may indicate:
1. Compromised credentials being used from a different location
2. VPN usage (legitimate but worth noting)
3. Shared account access

Consider investigating and potentially requiring password change.
    """.strip()

    return send_security_alert(
        event_type='IMPOSSIBLE_TRAVEL',
        severity='critical',
        details=event_details,
        ip_address=ip_address,
        user=user
    )


def alert_ip_blacklisted(ip_address, reason, added_by=None):
    """Send alert when an IP is added to the blacklist."""
    by_text = f"by {added_by.name}" if added_by else "automatically"

    event_details = f"""
An IP address has been added to the blacklist {by_text}.

IP Address: {ip_address}
Reason: {reason}

All requests from this IP will be blocked until removed from the blacklist.
    """.strip()

    return send_security_alert(
        event_type='IP_BLACKLISTED',
        severity='high',
        details=event_details,
        ip_address=ip_address,
        user=added_by
    )
