"""
Signal handlers for security monitoring and login tracking
"""
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.dispatch import receiver
from django.utils import timezone
import logging

from src.models import LoginHistory, LoginAlert, ParliamentUser, Role
from src.utils.security_utils import (
    get_client_ip,
    get_geolocation_from_ip,
    parse_device_info,
    analyze_login_risk,
    create_login_alert
)

logger = logging.getLogger('security')
security_log = logging.getLogger('admin_actions')
fn_log = logging.getLogger('function_calls')


@receiver(user_logged_in)
def log_successful_login(sender, request, user, **kwargs):
    """
    Log successful login and perform security analysis
    """
    try:
        # Get IP address. Coerce a missing IP to 'unknown' (same convention as
        # the security middleware): LoginHistory.ip_address is NOT NULL, and a
        # None here previously crashed geo lookup / the insert and silently
        # dropped tracking for the login (v3.15.2 fix).
        ip_address = get_client_ip(request) or 'unknown'

        # Get geolocation data — reuse the 24h-cached lookup already done by
        # run_post_auth_pipeline() (stashed on the request) instead of making
        # a second, uncached HTTP call to ip-api.com on every login. The
        # pipeline geo (geo_utils.get_ip_geo) uses lat/lon keys, so map it
        # onto the latitude/longitude shape this handler expects.
        pipeline_ctx = getattr(request, '_login_pipeline', None)
        pipeline_geo = (pipeline_ctx or {}).get('geo')
        if pipeline_geo:
            location_data = {
                'country': pipeline_geo.get('country', ''),
                'city': pipeline_geo.get('city', ''),
                'region': pipeline_geo.get('region', ''),
                'latitude': pipeline_geo.get('lat'),
                'longitude': pipeline_geo.get('lon'),
            }
        else:
            # Impersonation logins (no pipeline), private IPs, or failed
            # pipeline lookups fall back to the direct lookup.
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

        # --- Pipeline extras: only for logins that went through
        # run_post_auth_pipeline() (password/passkey). Impersonation logins
        # (login_as_view.py, admin.py) call Django's login() directly without
        # the pipeline, so request._login_pipeline is absent there and this
        # block is skipped — matching the pre-existing behavior for those
        # paths (they never got non-US alerts / watch-flag alerts before).
        # (pipeline_ctx fetched above, where its cached geo is reused.)
        if pipeline_ctx:
            _handle_pipeline_login_extras(user, ip_address, login_record, risk_analysis, pipeline_ctx)

        # Log pledge logins separately for easy officer review
        if getattr(user, 'is_pledge', False):
            try:
                from src.models import ActivityLog
                ActivityLog.log_activity(
                    action_type='pledge_login',
                    user=user,
                    description=f"Pledge {user.name} logged in",
                    ip_address=ip_address,
                    user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
                    object_type='ParliamentUser',
                    object_id=user.pk,
                    object_repr=user.name,
                )
            except Exception as log_err:
                logger.error(f"Failed to log pledge login for {user.name}: {log_err}")

    except Exception as e:
        logger.error(f"Error tracking login for user {user.name}: {str(e)}", exc_info=True)


def _handle_pipeline_login_extras(user, ip_address, login_record, risk_analysis, pipeline_ctx):
    """
    Extras formerly done in security_utils.run_post_auth_pipeline(), moved
    here so LoginHistory creation stays the single write path (see
    log_successful_login above). Covers:

    - Method-aware (password/passkey) success logging on the same
      admin_actions/function_calls channels the pipeline used to log on.
    - LoginAlert + direct user notification for non-US logins.
    - Watch-flag alert, now using the richer risk_analysis from
      analyze_login_risk() instead of the pipeline's old is_foreign-only
      risk factor list.

    Only called when the login went through run_post_auth_pipeline (password
    or passkey) — see the pipeline_ctx check at the call site.
    """
    method = pipeline_ctx.get('method', 'password')
    is_foreign = pipeline_ctx.get('is_foreign', False)
    geo = pipeline_ctx.get('geo')
    user_agent = pipeline_ctx.get('user_agent', '')

    # --- Success logging (method-aware) ---
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

    # --- LoginAlert + in-app notification for non-US logins ---
    if is_foreign and geo:
        location_str = ', '.join(filter(None, [geo.get('city'), geo.get('region'), geo.get('country')]))
        try:
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

    # --- Watch-flag alert ---
    try:
        watch_flag = getattr(user, 'watch_flag', None)
        if watch_flag and watch_flag.is_active:
            from src.models import IPBlacklist
            from src.security_notifications import send_watch_flag_alert
            send_watch_flag_alert(
                watched_user=user,
                event_type='success',
                ip_address=ip_address,
                geo=geo,
                user_agent=user_agent,
                is_whitelisted=False,
                is_blacklisted=IPBlacklist.objects.filter(ip_address=ip_address, is_active=True).exists(),
                is_rate_limited=False,
                risk_level=risk_analysis['risk_level'],
                risk_factors=risk_analysis['risk_factors'],
                is_foreign=is_foreign,
                watch_reason=watch_flag.reason,
                login_history=login_record,
            )
    except Exception as exc:
        security_log.error(f'Watch flag alert error [{method}]: {exc}')


@receiver(user_login_failed)
def log_failed_login(sender, credentials, request, **kwargs):
    """
    Log failed login attempts
    """
    try:
        # Get IP address. Coerce a missing IP to 'unknown' (same convention as
        # the security middleware): LoginHistory.ip_address is NOT NULL, and a
        # None here previously crashed geo lookup / the insert and silently
        # dropped tracking for the login (v3.15.2 fix).
        ip_address = get_client_ip(request) or 'unknown'

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


def sync_member_type_for_officer_roles(user):
    """
    Update user's member_type based on whether they hold any officer roles
    (President or any Vice President). Called after role changes.

    - Gains an officer role → member_type set to 'Officer'
    - Loses all officer roles → member_type reverted to 'Member'
      (only if currently 'Officer' and not a system admin)
    """
    from src.models import Role

    officer_role_codes = set(EXEC_ROLE_CODES)
    has_officer_role = user.roles.filter(code__in=officer_role_codes).exists()

    if has_officer_role:
        if user.member_type != 'Officer':
            user.member_type = 'Officer'
            user.save(update_fields=['member_type'])
            logger.info(
                f"Auto-promoted {user.name} to Officer (assigned officer role)"
            )
    else:
        # Revert to Member if currently Officer (is_admin flag is unaffected)
        if user.member_type == 'Officer':
            user.member_type = 'Member'
            user.save(update_fields=['member_type'])
            logger.info(
                f"Auto-demoted {user.name} to Member (no officer roles remaining)"
            )


@receiver(m2m_changed, sender=ParliamentUser.roles.through)
def sync_exec_committee_on_role_change(sender, instance, action, pk_set, model, **kwargs):
    """
    When user roles change, sync Executive Board membership and member_type.
    """
    if action not in ['post_add', 'post_remove', 'post_clear']:
        return

    # Check if any of the changed roles are exec roles
    if action in ['post_add', 'post_remove'] and pk_set:
        changed_roles = Role.objects.filter(pk__in=pk_set)
        exec_role_codes = set(changed_roles.values_list('code', flat=True))
        if not exec_role_codes.intersection(set(EXEC_ROLE_CODES)):
            return  # No exec roles changed, skip both syncs

    # Sync member_type for the user whose roles changed
    try:
        sync_member_type_for_officer_roles(instance)
    except Exception as e:
        logger.error(f"Error syncing member_type for officer role change: {e}")

    # Sync the EXEC committee
    try:
        sync_exec_committee()
    except Exception as e:
        logger.error(f"Error syncing EXEC committee: {e}")

    # Reset Kai permissions if a role tied to a Kai committee changed hands
    if action in ['post_add', 'post_remove'] and pk_set:
        try:
            reset_kai_permissions_on_role_change(pk_set)
        except Exception as e:
            logger.error(f"Error resetting Kai permissions on role change: {e}")


# ============================================================================
# Kai Permission Reset on Exec Role Change
# ============================================================================

def reset_kai_permissions_on_role_change(changed_role_pks):
    """
    When a role changes hands, reset all KaiMemberPermission rows for any
    Kai committee whose .role FK matches the changed role.

    Also wipes user-specific ChatChannelPermission rows for the committee's
    chat channel — so guest access doesn't persist across exec transitions.

    The intent is: every time a new person takes the Kai chair exec position,
    they start with a clean slate and deliberately grant permissions to members
    they trust.
    """
    from src.models import Committee, KaiMemberPermission, ChatChannel, ChatChannelPermission

    # Find Kai committees whose linked exec role is among the changed roles
    kai_committees = Committee.objects.filter(
        is_kai_committee=True,
        role__pk__in=changed_role_pks,
    )

    for committee in kai_committees:
        # Wipe all member-level Kai permissions
        kai_deleted, _ = KaiMemberPermission.objects.filter(committee=committee).delete()
        if kai_deleted:
            logger.info(
                f"[signals] reset_kai_permissions: wiped {kai_deleted} KaiMemberPermission rows "
                f"for committee '{committee.name}' after role change"
            )

        # Wipe user-specific chat guest permissions for the committee channel
        try:
            channel = ChatChannel.objects.get(committee=committee, channel_type='committee')
            chat_deleted, _ = ChatChannelPermission.objects.filter(
                channel=channel,
                user__isnull=False,
            ).delete()
            if chat_deleted:
                logger.info(
                    f"[signals] reset_kai_permissions: wiped {chat_deleted} ChatChannelPermission rows "
                    f"for channel '{channel.name}' after role change"
                )
        except ChatChannel.DoesNotExist:
            pass
