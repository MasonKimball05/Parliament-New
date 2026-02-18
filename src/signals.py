"""
Signal handlers for security monitoring and login tracking
"""
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver
from django.utils import timezone
import logging

from src.models import LoginHistory, LoginAlert
from src.utils.security_utils import (
    get_client_ip,
    get_geolocation_from_ip,
    parse_device_info,
    analyze_login_risk,
    create_login_alert
)

logger = logging.getLogger('security')


@receiver(user_logged_in)
def log_successful_login(sender, request, user, **kwargs):
    """
    Log successful login and perform security analysis
    """
    try:
        # Get IP address
        ip_address = get_client_ip(request)

        # Get geolocation data
        location_data = get_geolocation_from_ip(ip_address)

        # Parse user agent
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        device_info = parse_device_info(user_agent)

        # Analyze risk
        risk_analysis = analyze_login_risk(user, ip_address, location_data, device_info)

        # Create login history record
        login_record = LoginHistory.objects.create(
            user=user,
            status='success',
            ip_address=ip_address,
            country=location_data.get('country', ''),
            city=location_data.get('city', ''),
            region=location_data.get('region', ''),
            latitude=location_data.get('latitude'),
            longitude=location_data.get('longitude'),
            user_agent=user_agent[:500],
            device_type=device_info.get('device_type', ''),
            browser=device_info.get('browser', '')[:100],
            os=device_info.get('os', '')[:100],
            is_suspicious=risk_analysis['is_suspicious'],
            risk_level=risk_analysis['risk_level'],
            risk_factors=risk_analysis['risk_factors'],
            distance_from_last=risk_analysis.get('distance_from_last'),
            time_from_last=risk_analysis.get('time_from_last')
        )

        # Create alerts for high-risk logins
        if risk_analysis['is_suspicious']:
            # Determine alert type based on risk factors
            risk_factors = risk_analysis['risk_factors']
            alert_type = 'other'
            severity = risk_analysis['risk_level']

            if any('Impossible travel' in factor for factor in risk_factors):
                alert_type = 'impossible_travel'
                severity = 'critical'
            elif any('New login location' in factor for factor in risk_factors):
                alert_type = 'new_location'
            elif any('New device' in factor for factor in risk_factors):
                alert_type = 'new_device'

            # Create title and description
            title = f"Suspicious login: {user.name}"
            description = f"Login from {login_record.location_display}\n"
            description += f"IP: {ip_address}\n"
            description += f"Device: {device_info.get('device_type')} - {device_info.get('browser')}\n"
            description += f"\nRisk Factors:\n"
            for factor in risk_factors:
                description += f"- {factor}\n"

            create_login_alert(
                login_history=login_record,
                alert_type=alert_type,
                severity=severity,
                title=title,
                description=description
            )

        logger.info(f"Login tracked: {user.name} from {ip_address} ({location_data.get('city', 'Unknown')}) - Risk: {risk_analysis['risk_level']}")

    except Exception as e:
        logger.error(f"Error tracking login for user {user.name}: {str(e)}", exc_info=True)


@receiver(user_login_failed)
def log_failed_login(sender, credentials, request, **kwargs):
    """
    Log failed login attempts
    """
    try:
        # Get IP address
        ip_address = get_client_ip(request)

        # Get geolocation data
        location_data = get_geolocation_from_ip(ip_address)

        # Parse user agent
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        device_info = parse_device_info(user_agent)

        # Try to find the user from credentials
        username = credentials.get('username', '')

        # Import here to avoid circular import
        from src.models import ParliamentUser

        try:
            user = ParliamentUser.objects.get(username=username)

            # Create failed login record
            login_record = LoginHistory.objects.create(
                user=user,
                status='failed',
                ip_address=ip_address,
                country=location_data.get('country', ''),
                city=location_data.get('city', ''),
                region=location_data.get('region', ''),
                latitude=location_data.get('latitude'),
                longitude=location_data.get('longitude'),
                user_agent=user_agent[:500],
                device_type=device_info.get('device_type', ''),
                browser=device_info.get('browser', '')[:100],
                os=device_info.get('os', '')[:100],
                is_suspicious=True,
                risk_level='medium',
                risk_factors=['Failed login attempt']
            )

            # Check for multiple failed attempts
            recent_failures = LoginHistory.objects.filter(
                user=user,
                status='failed',
                timestamp__gte=timezone.now() - timezone.timedelta(minutes=15)
            ).count()

            if recent_failures >= 3:
                # Create alert for multiple failures
                create_login_alert(
                    login_history=login_record,
                    alert_type='multiple_failures',
                    severity='high' if recent_failures >= 5 else 'medium',
                    title=f"Multiple failed login attempts: {user.name}",
                    description=f"{recent_failures} failed login attempts in the last 15 minutes\n"
                                f"IP: {ip_address}\n"
                                f"Location: {login_record.location_display}\n"
                                f"Device: {device_info.get('device_type')} - {device_info.get('browser')}"
                )

            logger.warning(f"Failed login attempt for {username} from {ip_address} ({location_data.get('city', 'Unknown')})")

        except ParliamentUser.DoesNotExist:
            # Unknown username
            logger.warning(f"Failed login attempt for unknown user '{username}' from {ip_address}")

    except Exception as e:
        logger.error(f"Error logging failed login: {str(e)}", exc_info=True)


# ============================================================================
# Executive Board Committee Auto-Sync
# ============================================================================

from django.db.models.signals import m2m_changed

# Executive role codes that grant EXEC committee membership
EXEC_ROLE_CODES = ['President', 'EVP', 'VPB', 'VPR', 'VPE', 'VPP', 'VPF', 'VPA', 'VPRM']


def sync_exec_committee():
    """
    Manually sync Executive Board committee membership.
    Call this function to ensure EXEC committee is in sync with role holders.
    """
    from src.models import Committee, Role, ParliamentUser

    try:
        exec_committee = Committee.objects.get(is_exec_board=True)
    except Committee.DoesNotExist:
        logger.warning("No committee with is_exec_board=True found")
        return

    # Get all executive roles
    exec_roles = Role.objects.filter(code__in=EXEC_ROLE_CODES)

    # Get all users with any exec role
    users_with_exec_roles = ParliamentUser.objects.filter(roles__in=exec_roles).distinct()

    # Sync membership
    exec_committee.members.set(users_with_exec_roles)
    logger.info(f"Synced EXEC committee membership: {users_with_exec_roles.count()} members")

    # Sync chairs (President and EVP)
    pres_evp_roles = Role.objects.filter(code__in=['President', 'EVP'])
    chairs = ParliamentUser.objects.filter(roles__in=pres_evp_roles).distinct()
    exec_committee.chairs.set(chairs)
    logger.info(f"Synced EXEC committee chairs: {chairs.count()} chairs")

    # Set EVP as admin
    evp_role = Role.objects.filter(code='EVP').first()
    if evp_role:
        evp_user = ParliamentUser.objects.filter(roles=evp_role).first()
        if evp_user and exec_committee.admin != evp_user:
            exec_committee.admin = evp_user
            exec_committee.save(update_fields=['admin'])
            logger.info(f"Set EXEC committee admin to EVP: {evp_user.name}")


def setup_slating_committee_admin():
    """
    Set up the Slating Committee with President as default admin.
    """
    from src.models import Committee, Role, ParliamentUser

    try:
        slating_committee = Committee.objects.get(is_slating_committee=True)
    except Committee.DoesNotExist:
        logger.warning("No committee with is_slating_committee=True found")
        return

    # If no admin set, set President as default admin
    if not slating_committee.admin:
        pres_role = Role.objects.filter(code='President').first()
        if pres_role:
            president = ParliamentUser.objects.filter(roles=pres_role).first()
            if president:
                slating_committee.admin = president
                slating_committee.save(update_fields=['admin'])
                logger.info(f"Set Slating Committee admin to President: {president.name}")


@receiver(m2m_changed)
def sync_exec_committee_on_role_change(sender, instance, action, pk_set, model, **kwargs):
    """
    When user roles change, sync Executive Board membership.
    This is connected to ParliamentUser.roles.through via m2m_changed signal.
    """
    from src.models import ParliamentUser, Role

    # Check if this is the ParliamentUser.roles.through model
    if sender != ParliamentUser.roles.through:
        return

    if action not in ['post_add', 'post_remove', 'post_clear']:
        return

    # Check if any of the changed roles are exec roles
    if action in ['post_add', 'post_remove'] and pk_set:
        changed_roles = Role.objects.filter(pk__in=pk_set)
        exec_role_codes = set(changed_roles.values_list('code', flat=True))
        if not exec_role_codes.intersection(set(EXEC_ROLE_CODES)):
            return  # No exec roles changed

    # Sync the EXEC committee
    try:
        sync_exec_committee()
    except Exception as e:
        logger.error(f"Error syncing EXEC committee: {e}")
