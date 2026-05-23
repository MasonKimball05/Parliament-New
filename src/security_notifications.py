"""
Security notification utilities for Parliament system.
Handles email alerts for critical security events like attack detection,
quarantine activation, and emergency lockdown.
"""
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from django.utils.timezone import localtime
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
Time: {localtime(timezone.now()).strftime('%Y-%m-%d %H:%M:%S %Z')}

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


def alert_honeypot_triggered(endpoint, ip_address, user_agent, escalate=False, escalation_reason=''):
    """
    Log a honeypot access attempt.

    By default (escalate=False) this only writes to the DB — no email is sent.
    The daily digest command (send_honeypot_digest) covers routine hits.

    Set escalate=True for genuinely serious activity (e.g. same IP hitting
    multiple honeypots, POST with credential-like payload). Those still fire
    an immediate critical email.
    """
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

    if escalate and escalation_reason:
        event_details += f"\n\nEscalation Reason:\n{escalation_reason}"

    return send_security_alert(
        event_type='HONEYPOT_TRIGGERED',
        severity='critical' if escalate else 'low',
        details=event_details,
        ip_address=ip_address,
        force_send=escalate,
    )


def send_honeypot_digest(since=None):
    """
    Send a daily digest email summarising honeypot activity.
    Called by the send_honeypot_digest management command.
    Returns True if an email was sent, False otherwise.
    """
    from src.models import HoneypotAccess
    from django.db.models import Count

    if since is None:
        since = timezone.now() - timezone.timedelta(hours=24)

    hits = HoneypotAccess.objects.filter(accessed_at__gte=since)
    total = hits.count()

    if total == 0:
        logger.info("Honeypot digest: no hits in last 24h, skipping email.")
        return False

    email_to = get_security_alert_email()
    if not email_to:
        logger.warning("Honeypot digest: no SECURITY_ALERT_EMAIL configured.")
        return False

    top_endpoints = (
        hits.values('endpoint')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )
    top_ips = (
        hits.values('ip_address')
        .annotate(count=Count('id'))
        .order_by('-count')[:5]
    )

    endpoint_lines = '\n'.join(
        f"  {e['endpoint']} — {e['count']} hit{'s' if e['count'] != 1 else ''}"
        for e in top_endpoints
    )
    ip_lines = '\n'.join(
        f"  {e['ip_address']} — {e['count']} hit{'s' if e['count'] != 1 else ''}"
        for e in top_ips
    )

    site_url = get_site_url()
    subject = f"[Parliament] Honeypot Daily Digest — {total} hit{'s' if total != 1 else ''} in the last 24h"
    message = f"""
Parliament Honeypot Daily Digest
{'=' * 60}

Period: Last 24 hours (since {localtime(since).strftime('%Y-%m-%d %H:%M %Z')})
Total hits: {total}

Top Targeted Endpoints:
{endpoint_lines}

Top Attacking IPs:
{ip_lines}

All attempts were automatically blocked.
View full logs: {site_url}/admin-v2/security/honeypot-logs/

---
Note: Only routine scanner/bot activity is included in this digest.
Serious threats (coordinated multi-honeypot attacks, POST credential
attempts from the same IP) are still emailed immediately.
    """.strip()

    try:
        from django.core.mail import send_mail
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email_to],
            fail_silently=False,
        )
        logger.info(f"Honeypot digest sent: {total} hits to {email_to}")
        return True
    except Exception as e:
        logger.error(f"Failed to send honeypot digest: {e}")
        return False


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
Time: {localtime(timezone.now()).strftime('%Y-%m-%d %H:%M:%S %Z')}

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


def send_watch_flag_alert(watched_user, event_type, ip_address, geo, user_agent,
                          is_whitelisted, is_blacklisted, is_rate_limited,
                          risk_level, risk_factors, is_foreign, watch_reason,
                          failed_attempts=None, login_history=None):
    """
    Send a watch flag alert email and create a LoginAlert for the flagged user.

    Args:
        watched_user:    The ParliamentUser being watched
        event_type:      'success' or 'failed'
        ip_address:      Plain-text IP string
        geo:             Dict from is_foreign_ip (may be None)
        user_agent:      Raw user agent string
        is_whitelisted:  bool
        is_blacklisted:  bool
        is_rate_limited: bool
        risk_level:      'low'|'medium'|'high'|'critical'
        risk_factors:    list of strings
        is_foreign:      bool
        watch_reason:    The reason stored on the UserWatchFlag
        failed_attempts: int — number of failed attempts (for failed-login triggers)
        login_history:   LoginHistory instance to link to the alert (may be None)
    """
    from src.models import LoginAlert, UserSession
    from django.core.mail import EmailMultiAlternatives
    from django.template.loader import render_to_string

    email_to = get_security_alert_email()
    site_url = get_site_url()
    timestamp = localtime(timezone.now()).strftime('%Y-%m-%d %H:%M:%S %Z')

    trigger_label = (
        'Successful login' if event_type == 'success'
        else f'Repeated failed login ({failed_attempts} attempts)'
    )

    # Parse device info from user agent
    device_type, browser, os = UserSession.parse_user_agent(user_agent)

    location_parts = []
    if geo:
        location_parts = [geo.get('city', ''), geo.get('region', ''), geo.get('country', '')]
    location = ', '.join(p for p in location_parts if p) or 'Unknown'

    context = {
        'watched_user': watched_user,
        'event_type': event_type,
        'trigger_label': trigger_label,
        'timestamp': timestamp,
        'watch_reason': watch_reason,
        'ip_address': ip_address,
        'location': location,
        'isp': geo.get('isp', '') if geo else '',
        'lat': geo.get('lat') if geo else None,
        'lon': geo.get('lon') if geo else None,
        'is_whitelisted': is_whitelisted,
        'is_blacklisted': is_blacklisted,
        'is_rate_limited': is_rate_limited,
        'user_agent': user_agent,
        'browser': browser,
        'os': os,
        'device_type': device_type,
        'risk_level': risk_level,
        'risk_factors': risk_factors,
        'is_foreign': is_foreign,
        'failed_attempts': failed_attempts,
        'site_url': site_url,
    }

    # Create LoginAlert record
    try:
        severity = risk_level if risk_level in ('low', 'medium', 'high', 'critical') else 'medium'
        if event_type == 'failed':
            severity = 'high'
        description = (
            f'{trigger_label} for watched user {watched_user.name} ({watched_user.username}).\n\n'
            f'IP: {ip_address}\n'
            f'Location: {location}\n'
            f'ISP: {geo.get("isp", "Unknown") if geo else "Unknown"}\n'
            f'Device: {device_type} / {browser} / {os}\n'
            f'Whitelisted: {is_whitelisted} | Blacklisted: {is_blacklisted} | Rate limited: {is_rate_limited}\n'
            f'Risk: {risk_level} — {", ".join(risk_factors) if risk_factors else "none"}\n'
            f'Watch reason: {watch_reason}'
        )
        LoginAlert.objects.create(
            user=watched_user,
            login_history=login_history,
            alert_type='other',
            severity=severity,
            status='new',
            title=f'Watch flag: {trigger_label} — {watched_user.name}',
            description=description,
        )
    except Exception as e:
        logger.error(f"Failed to create LoginAlert for watch flag: {e}")

    # Send email
    if not email_to:
        logger.warning("WATCH FLAG: No alert email configured — skipping email")
        return

    try:
        subject = f"[WATCH FLAG] {trigger_label} — {watched_user.name} ({watched_user.username})"
        html_body = render_to_string('emails/watch_flag_alert.html', context)
        plain_body = (
            f"{trigger_label} for watched user {watched_user.name} ({watched_user.username})\n"
            f"Time: {timestamp}\n"
            f"IP: {ip_address} | Location: {location}\n"
            f"Risk: {risk_level}\n"
            f"Watch reason: {watch_reason}\n\n"
            f"View in admin: {site_url}/admin-v2/users/{watched_user.user_id}/login-security/"
        )
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[email_to],
        )
        msg.attach_alternative(html_body, "text/html")
        msg.send()
        logger.info(f"Watch flag alert email sent for {watched_user.username} ({event_type})")
    except Exception as e:
        logger.error(f"Failed to send watch flag alert email: {e}")


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
