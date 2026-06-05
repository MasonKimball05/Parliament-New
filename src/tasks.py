"""
Celery tasks for Parliament.

All tasks are fire-and-forget unless noted. Periodic tasks are scheduled via
django-celery-beat and stored in the database (manageable from admin-v2).

Task groups:
  Email          — async wrappers around notifications.py send functions
  Vote           — scheduled open/close for chapter + committee legislation
  Announcements  — scheduled publish + email dispatch
  Housekeeping   — session/lockout/blacklist/push/token cleanup (daily + monthly)
  Daily Digest   — combined daily site health report (3:30 AM CST)
                   includes: system integrity audit + honeypot activity
"""
from celery import shared_task
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# EMAIL TASKS
# Thin async wrappers so email sends never block a gunicorn worker thread.
# =============================================================================

@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='tasks.send_announcement_email')
def send_announcement_email(self, announcement_id, initiated_by_id=None):
    """
    Send announcement notification emails asynchronously.
    Called from manage_announcements.py instead of calling send_announcement_notification() directly.
    """
    try:
        from src.models import Announcement, ParliamentUser
        from src.notifications import send_announcement_notification
        announcement = Announcement.objects.get(pk=announcement_id)
        initiated_by = ParliamentUser.objects.filter(pk=initiated_by_id).first() if initiated_by_id else None
        send_announcement_notification(announcement, initiated_by=initiated_by)
    except Announcement.DoesNotExist:
        logger.warning(f"[tasks] Announcement {announcement_id} no longer exists — skipping email")
    except Exception as exc:
        logger.error(f"[tasks] send_announcement_email failed for id={announcement_id}: {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, name='tasks.send_security_alert_task')
def send_security_alert_task(self, event_type, severity, details, ip_address=None, user_id=None, force_send=False):
    """
    Send a security alert email asynchronously.
    Replaces direct calls to security_notifications.send_security_alert() in hot paths
    (middleware, login view) so attacks don't add email latency to the blocked request.
    """
    try:
        from src.security_notifications import send_security_alert
        from src.models import ParliamentUser
        user = ParliamentUser.objects.filter(pk=user_id).first() if user_id else None
        send_security_alert(
            event_type=event_type,
            severity=severity,
            details=details,
            ip_address=ip_address,
            user=user,
            force_send=force_send,
        )
    except Exception as exc:
        logger.error(f"[tasks] send_security_alert_task failed ({event_type}): {exc}")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=120, name='tasks.send_pledge_welcome_task')
def send_pledge_welcome_task(self, user_id, temp_password):
    """Send pledge welcome email asynchronously after account creation."""
    try:
        from src.models import ParliamentUser
        from src.notifications import send_pledge_welcome_email
        user = ParliamentUser.objects.get(pk=user_id)
        send_pledge_welcome_email(user, temp_password)
    except Exception as exc:
        logger.error(f"[tasks] send_pledge_welcome_task failed for user_id={user_id}: {exc}")
        raise self.retry(exc=exc)


# =============================================================================
# VOTE TASKS
# Scheduled via Beat to run every minute and handle auto-open/close of votes
# that have passed their voting_starts_at / voting_ends_at timestamps.
# This replaces the current on-page-load approach in vote_view.py and
# committee/vote.py, so votes open and close on schedule even if no one loads
# the page.
# =============================================================================

@shared_task(name='tasks.auto_open_close_chapter_votes')
def auto_open_close_chapter_votes():
    """
    Open and close chapter legislation votes on schedule.

    - Opens legislation where voting_starts_at has passed but vote is not yet open
    - Closes legislation where voting_ends_at has passed and vote is still open
    """
    from src.models import Legislation, Vote
    now = timezone.now()
    opened = 0
    closed = 0

    # Auto-open: legislation with a voting_starts_at in the past that isn't open yet
    to_open = Legislation.objects.filter(
        voting_closed=False,
        voting_starts_at__isnull=False,
        voting_starts_at__lte=now,
        available_at__isnull=True,  # Not yet "available" (the field used as visible-from)
    ).exclude(status='removed').exclude(status='tabled')

    for leg in to_open:
        if not leg.available_at:
            leg.available_at = leg.voting_starts_at
            leg.save(update_fields=['available_at'])
            opened += 1
            logger.info(f"[tasks] Auto-opened voting on '{leg.title}' (id={leg.id})")

    # Auto-close: legislation past voting_ends_at that is still open
    to_close = Legislation.objects.filter(
        voting_closed=False,
        voting_ends_at__isnull=False,
        voting_ends_at__lte=now,
    )

    for leg in to_close:
        votes = Vote.objects.filter(legislation=leg)
        yes = votes.filter(vote_choice='yes').count()
        no = votes.filter(vote_choice='no').count()
        total = yes + no

        leg.voting_closed = True
        leg.voting_ended_at = leg.voting_ends_at

        if total > 0:
            if leg.vote_mode == 'piecewise':
                leg.passed = yes >= (leg.required_number or 0)
            elif leg.vote_mode == 'plurality':
                options = {opt: votes.filter(vote_choice=opt).count() for opt in (leg.plurality_options or [])}
                leg.passed = max(options.values()) > 0 if options else False
            else:
                yes_pct = (yes / total) * 100
                leg.passed = yes_pct >= int(leg.required_percentage)

            leg.status = 'passed' if leg.passed else 'failed'

        leg.save()
        closed += 1
        result = 'passed' if leg.passed else ('no result — no votes cast' if total == 0 else 'failed')
        logger.info(f"[tasks] Auto-closed voting on '{leg.title}' (id={leg.id}) — {result}")

    if opened or closed:
        logger.info(f"[tasks] auto_open_close_chapter_votes: opened={opened}, closed={closed}")


@shared_task(name='tasks.auto_open_close_committee_votes')
def auto_open_close_committee_votes():
    """
    Close committee legislation votes on schedule (committee votes open manually).
    Mirrors the on-page-load auto-close in committee/vote.py but runs on a timer.
    """
    from src.models import CommitteeLegislation
    from src.view.committee.vote import get_vote_tally
    now = timezone.now()
    closed = 0

    to_close = CommitteeLegislation.objects.filter(
        voting_closed=False,
        voting_ends_at__isnull=False,
        voting_ends_at__lte=now,
    )

    for leg in to_close:
        tally = get_vote_tally(leg)
        total_votes = tally['total']

        leg.voting_closed = True
        leg.voting_ended_at = leg.voting_ends_at

        if total_votes > 0:
            if leg.vote_mode == 'plurality':
                options = {k: v for k, v in tally.items() if k != 'total'}
                leg.passed = max(options.values()) > 0 if options else False
                leg.status = 'passed' if leg.passed else 'draft'
            elif leg.vote_mode == 'piecewise':
                leg.passed = tally.get('yes', 0) >= (leg.required_number or 0)
                leg.status = 'passed' if leg.passed else 'draft'
            else:
                yes = tally.get('yes', 0)
                no = tally.get('no', 0)
                countable = yes + no
                if countable > 0:
                    yes_pct = (yes / countable) * 100
                    leg.passed = yes_pct >= int(leg.required_percentage)
                    leg.status = 'passed' if leg.passed else 'draft'

        leg.save()
        closed += 1
        logger.info(f"[tasks] Auto-closed committee vote on '{leg.title}' (id={leg.id})")

    if closed:
        logger.info(f"[tasks] auto_open_close_committee_votes: closed={closed}")


@shared_task(name='tasks.auto_open_close_slating_votes')
def auto_open_close_slating_votes():
    """
    Open and close slating period voting on schedule.
    SlatingPeriod has voting_open_at and voting_close_at fields.
    """
    from src.models import SlatingPeriod
    now = timezone.now()
    opened = 0
    closed = 0

    # Auto-open: voting_open_at has passed, status is still deliberation
    to_open = SlatingPeriod.objects.filter(
        status='deliberation',
        voting_open_at__isnull=False,
        voting_open_at__lte=now,
    )
    for period in to_open:
        period.status = 'voting_open'
        period.save(update_fields=['status'])
        opened += 1
        logger.info(f"[tasks] Auto-opened slating voting for period id={period.id}")

    # Auto-close: voting_close_at has passed, status is still voting_open
    to_close = SlatingPeriod.objects.filter(
        status='voting_open',
        voting_close_at__isnull=False,
        voting_close_at__lte=now,
    )
    for period in to_close:
        period.status = 'voting_closed'
        period.save(update_fields=['status'])
        closed += 1
        logger.info(f"[tasks] Auto-closed slating voting for period id={period.id}")

    if opened or closed:
        logger.info(f"[tasks] auto_open_close_slating_votes: opened={opened}, closed={closed}")


# =============================================================================
# ANNOUNCEMENT TASKS
# =============================================================================

@shared_task(name='tasks.publish_scheduled_announcements')
def publish_scheduled_announcements():
    """
    Dispatch email notifications for announcements whose publish_at time has
    arrived but whose send_email_on_publish flag is still set (email not yet sent).

    Replaces the cron-based process_scheduled_announcements management command.
    Uses the same email_sent_at / send_email_on_publish fields to avoid duplicates.
    """
    from src.models import Announcement
    from django.db import transaction
    now = timezone.now()

    pending = Announcement.objects.filter(
        is_active=True,
        send_email_on_publish=True,
        email_sent_at__isnull=True,
        publish_at__isnull=False,
        publish_at__lte=now,
    )

    for announcement in pending:
        # Claim the row atomically before queuing — prevents duplicate sends
        # if Beat fires the task twice in quick succession
        with transaction.atomic():
            claimed = Announcement.objects.select_for_update(skip_locked=True).filter(
                pk=announcement.pk,
                send_email_on_publish=True,
                email_sent_at__isnull=True,
            ).first()
            if not claimed:
                continue
            claimed.email_sent_at = now
            claimed.send_email_on_publish = False
            claimed.save(update_fields=['email_sent_at', 'send_email_on_publish'])

        send_announcement_email.delay(announcement.pk)
        logger.info(f"[tasks] Queued email for scheduled announcement id={announcement.pk} '{announcement.title}'")


# =============================================================================
# HOUSEKEEPING TASKS
# =============================================================================

@shared_task(name='tasks.cleanup_expired_sessions')
def cleanup_expired_sessions():
    """
    Remove expired UserSession records. Django's session engine handles its own
    expiry; this task cleans Parliament's UserSession tracking table.
    """
    try:
        from src.models import UserSession
        cutoff = timezone.now() - timezone.timedelta(days=30)
        deleted, _ = UserSession.objects.filter(last_activity__lt=cutoff).delete()
        if deleted:
            logger.info(f"[tasks] cleanup_expired_sessions: removed {deleted} stale UserSession records")
    except Exception as exc:
        logger.error(f"[tasks] cleanup_expired_sessions failed: {exc}")


@shared_task(name='tasks.prune_expired_login_lockouts')
def prune_expired_login_lockouts():
    """
    Delete LoginLockout records whose cache lockout has expired.

    LoginLockout rows are created for every IP/username lockout event so they
    show up in admin-v2. The cache entry that actually enforces the lockout
    expires automatically, but the DB row stays forever. This task prunes rows
    that are past their expires_at and were not manually cleared (cleared rows
    are worth keeping for audit history).
    Runs daily alongside session cleanup.
    """
    try:
        from src.models import LoginLockout
        cutoff = timezone.now()
        deleted, _ = LoginLockout.objects.filter(
            expires_at__lt=cutoff,
            is_cleared=False,
        ).delete()
        if deleted:
            logger.info(f"[tasks] prune_expired_login_lockouts: removed {deleted} expired LoginLockout records")
    except Exception as exc:
        logger.error(f"[tasks] prune_expired_login_lockouts failed: {exc}")


@shared_task(name='tasks.expire_stale_ip_blacklist_entries')
def expire_stale_ip_blacklist_entries():
    """
    Set is_active=False on IPBlacklist entries that have passed their expires_at.

    Entries with no expires_at are permanent and are left alone.
    Runs daily so the blacklist stays accurate without manual intervention.
    """
    try:
        from src.models import IPBlacklist
        now = timezone.now()
        updated = IPBlacklist.objects.filter(
            is_active=True,
            expires_at__lt=now,
        ).exclude(expires_at=None).update(is_active=False)
        if updated:
            logger.info(f"[tasks] expire_stale_ip_blacklist_entries: deactivated {updated} expired IPBlacklist entries")
    except Exception as exc:
        logger.error(f"[tasks] expire_stale_ip_blacklist_entries failed: {exc}")


@shared_task(name='tasks.prune_stale_push_subscriptions')
def prune_stale_push_subscriptions():
    """
    Delete PushSubscription records unused for 90+ days.

    Subscriptions that return 410 Gone are deleted immediately on send. This
    task catches the rest: subscriptions that are technically alive but haven't
    been used in 90 days are almost certainly from browsers where the user
    revoked permission or cleared site data. Pruning them keeps the table lean
    and prevents push tasks from attempting dead endpoints.
    Runs monthly (first of the month at 3:00 AM CST).
    """
    try:
        from src.models import PushSubscription
        cutoff = timezone.now() - timezone.timedelta(days=90)
        deleted, _ = PushSubscription.objects.filter(
            last_used_at__lt=cutoff,
        ).delete()
        if deleted:
            logger.info(f"[tasks] prune_stale_push_subscriptions: removed {deleted} stale PushSubscription records")
    except Exception as exc:
        logger.error(f"[tasks] prune_stale_push_subscriptions failed: {exc}")


@shared_task(name='tasks.prune_old_auth_tokens')
def prune_old_auth_tokens():
    """
    Delete DRF auth tokens that haven't been used in 90 days.

    DRF's Token model has no built-in expiry. Tokens accumulate for every user
    who has ever called /api/v1/auth/token/. This task removes tokens that
    haven't been seen in 90 days so stale credentials don't linger indefinitely.
    Token.created is the only timestamp available (DRF doesn't track last use),
    so we use that as the cutoff.
    Runs monthly alongside push subscription pruning.
    """
    try:
        from rest_framework.authtoken.models import Token
        cutoff = timezone.now() - timezone.timedelta(days=90)
        deleted, _ = Token.objects.filter(created__lt=cutoff).delete()
        if deleted:
            logger.info(f"[tasks] prune_old_auth_tokens: removed {deleted} old DRF auth tokens")
    except Exception as exc:
        logger.error(f"[tasks] prune_old_auth_tokens failed: {exc}")


@shared_task(name='tasks.prune_expired_chat_permissions')
def prune_expired_chat_permissions():
    """
    Delete ChatChannelPermission rows whose expires_at has passed.

    Guest permissions can have an optional expiry date. When that date passes
    the permission is functionally dead (can_* checks filter it out), but the
    row remains. This task prunes those rows nightly so the guest list stays
    clean and the DB doesn't accumulate stale entries.
    """
    try:
        from src.models import ChatChannelPermission
        deleted, _ = ChatChannelPermission.objects.filter(
            expires_at__isnull=False,
            expires_at__lte=timezone.now(),
        ).delete()
        if deleted:
            logger.info(f"[tasks] prune_expired_chat_permissions: removed {deleted} expired permission(s)")
    except Exception as exc:
        logger.error(f"[tasks] prune_expired_chat_permissions failed: {exc}")


# ---------------------------------------------------------------------------
# Daily Digest — combined site health report
# ---------------------------------------------------------------------------

@shared_task(name='tasks.send_daily_digest')
def send_daily_digest():
    """
    Daily site health digest. Runs every night at 3 AM CST via Celery Beat.

    Combines two things previously separate:
      1. System integrity audit — checks for stale/inconsistent data,
         slow-burn anomalies, and edge cases real-time systems miss.
      2. Honeypot activity — hits, top endpoints, top IPs, blacklist status,
         and attack-blocked event count from the last 24 hours.

    Always sends, even on a clean run — absence of the email is itself a
    signal that the task stopped running. All check results (OK and flagged)
    are included so the email is a complete daily health report.

    The task never raises — failures in individual checks are caught so a
    broken check doesn't prevent the rest from running.
    """
    from django.core.mail import send_mail
    from django.utils.timezone import localtime
    from src.security_notifications import get_security_alert_email, get_site_url

    digest_start = timezone.now()
    since = digest_start - timezone.timedelta(hours=24)

    findings = []    # (severity, category, message) — flagged issues
    ok_results = []  # (category, message) — clean check results
    errors = []      # section names that threw an exception

    def flag(severity, category, message):
        findings.append((severity, category, message))
        logger.warning(f"[digest] [{severity.upper()}] {category}: {message}")

    def ok(category, message):
        ok_results.append((category, message))
        logger.info(f"[digest] [OK] {category}: {message}")

    # -------------------------------------------------------------------------
    # 1. USER ACCOUNT INTEGRITY
    # -------------------------------------------------------------------------
    try:
        from src.models import ParliamentUser
        now = timezone.now()

        # Active users with no password set (empty hash)
        no_password = ParliamentUser.objects.filter(
            is_active=True, member_status='Active', password=''
        ).count()
        if no_password:
            flag('high', 'Accounts', f"{no_password} active user(s) have no password set")
        else:
            ok('Accounts', 'All active users have a password')

        # Users with force_password_change stuck for >14 days
        stale_force_pw = ParliamentUser.objects.filter(
            force_password_change=True,
            is_active=True,
        )
        # No created_at on the flag itself — check last_login as proxy:
        # if they haven't logged in in 14 days and force_password_change is still True,
        # they may be stuck or the flag was set but never cleared
        stale_force_pw_count = stale_force_pw.filter(
            last_login__lt=now - timezone.timedelta(days=14)
        ).count()
        if stale_force_pw_count:
            flag('medium', 'Accounts', f"{stale_force_pw_count} user(s) have had force_password_change set for 14+ days without logging in")
        ok('Accounts', f"{stale_force_pw.count()} user(s) currently flagged force_password_change")

        # has_default_password=True but force_password_change=False (inconsistent flags)
        inconsistent_pw_flags = ParliamentUser.objects.filter(
            has_default_password=True, force_password_change=False, is_active=True
        ).count()
        if inconsistent_pw_flags:
            flag('medium', 'Accounts', f"{inconsistent_pw_flags} user(s) have has_default_password=True but force_password_change=False")

        # Active members not logged in for 60+ days
        inactive_active = ParliamentUser.objects.filter(
            member_status='Active',
            is_active=True,
            last_login__lt=now - timezone.timedelta(days=60),
        ).exclude(member_type='Advisor').count()
        if inactive_active > 5:
            flag('low', 'Accounts', f"{inactive_active} active non-advisor members haven't logged in for 60+ days")

        # Users quarantined for >7 days (may have been forgotten)
        old_quarantine = ParliamentUser.objects.filter(
            is_quarantined=True,
            is_active=True,
        )
        # No quarantine timestamp on user model — just count and flag if any exist
        if old_quarantine.exists():
            names = ', '.join(old_quarantine.values_list('username', flat=True)[:5])
            flag('medium', 'Accounts', f"{old_quarantine.count()} user(s) currently quarantined: {names}")

        # is_admin=True users — flag for review (shouldn't grow silently)
        admins = list(
            ParliamentUser.objects.filter(is_admin=True, is_active=True)
            .values_list('username', flat=True)
        )
        ok('Accounts', f"Admin accounts ({len(admins)}): {', '.join(admins) or 'none'}")

        # New admin accounts created in the last 7 days
        from src.models import ActivityLog
        recent_admin_grants = ActivityLog.objects.filter(
            action_type='other',
            description__icontains='admin',
            timestamp__gte=now - timezone.timedelta(days=7),
        ).count()
        if recent_admin_grants:
            flag('medium', 'Accounts', f"{recent_admin_grants} activity log entries mention 'admin' in the last 7 days — verify no unexpected privilege changes")

        # New accounts created in the last 7 days — informational
        new_accounts = ParliamentUser.objects.filter(
            date_joined__gte=now - timezone.timedelta(days=7)
        ) if hasattr(ParliamentUser, 'date_joined') else []
        # date_joined may not exist on AbstractBaseUser; fall back to ActivityLog
        if not new_accounts:
            new_account_logs = ActivityLog.objects.filter(
                action_type='other',
                description__icontains='created',
                timestamp__gte=now - timezone.timedelta(days=7),
            ).count()
            if new_account_logs:
                flag('low', 'Accounts', f"{new_account_logs} account-creation activity log entries in the last 7 days — verify these are expected")
        else:
            count = new_accounts.count()
            if count:
                names = ', '.join(new_accounts.values_list('username', flat=True)[:5])
                flag('low', 'Accounts', f"{count} new user account(s) created in the last 7 days: {names}")

        # Active users with email flagged as undeliverable for 14+ days
        stale_email_flagged = ParliamentUser.objects.filter(
            email_flagged=True,
            is_active=True,
            email_flagged_at__lt=now - timezone.timedelta(days=14),
        ).count()
        if stale_email_flagged:
            flag('medium', 'Accounts', f"{stale_email_flagged} user(s) have had a flagged/undeliverable email for 14+ days without updating it — notifications silently failing")

    except Exception as exc:
        errors.append(f"User account checks: {exc}")
        logger.error(f"[digest] User account checks failed: {exc}")

    # -------------------------------------------------------------------------
    # 2. 2FA DEVICE INTEGRITY
    # -------------------------------------------------------------------------
    try:
        from django_otp.plugins.otp_totp.models import TOTPDevice
        from django_otp.plugins.otp_static.models import StaticDevice

        # Users with multiple confirmed TOTP devices (should be exactly 1)
        from django.db.models import Count
        multi_totp = (
            TOTPDevice.objects
            .filter(confirmed=True)
            .values('user')
            .annotate(n=Count('id'))
            .filter(n__gt=1)
        )
        if multi_totp.exists():
            flag('high', '2FA', f"{multi_totp.count()} user(s) have multiple confirmed TOTP devices — possible enrollment issue")

        # Confirmed TOTP devices for users who are no longer active
        orphan_totp = TOTPDevice.objects.filter(
            confirmed=True,
        ).exclude(user__is_active=True).count()
        if orphan_totp:
            flag('low', '2FA', f"{orphan_totp} confirmed TOTP device(s) belong to inactive/removed users")

        # Active users who have 2FA required but no device
        from src.models import TwoFactorRequirement
        required_no_device = TwoFactorRequirement.objects.filter(
            requirement='required'
        ).exclude(
            user__in=TOTPDevice.objects.filter(confirmed=True).values('user')
        ).count()
        if required_no_device:
            flag('medium', '2FA', f"{required_no_device} user(s) have 2FA individually required but no confirmed TOTP device")

        # Admins with no confirmed TOTP device
        from src.models import ParliamentUser as _PU
        admin_no_2fa = _PU.objects.filter(
            is_admin=True, is_active=True
        ).exclude(
            pk__in=TOTPDevice.objects.filter(confirmed=True).values('user')
        )
        if admin_no_2fa.exists():
            names = ', '.join(admin_no_2fa.values_list('username', flat=True))
            flag('high', '2FA', f"{admin_no_2fa.count()} admin account(s) have no 2FA device: {names}")

        # Active users who require 2FA but have no email — locked out of recovery
        users_needing_2fa_pks = set(
            TOTPDevice.objects.filter(confirmed=True).values_list('user', flat=True)
        )
        no_email_with_2fa = _PU.objects.filter(
            is_active=True, member_status='Active',
            pk__in=users_needing_2fa_pks,
        ).filter(
            email__isnull=True
        ) | _PU.objects.filter(
            is_active=True, member_status='Active',
            pk__in=users_needing_2fa_pks,
            email='',
        )
        no_email_count = no_email_with_2fa.count()
        if no_email_count:
            flag('high', '2FA', f"{no_email_count} user(s) have 2FA enabled but no email address — self-service recovery is unavailable for them")

        ok('2FA', 'Device integrity checks complete')

    except Exception as exc:
        errors.append(f"2FA device checks: {exc}")
        logger.error(f"[digest] 2FA device checks failed: {exc}")

    # -------------------------------------------------------------------------
    # 3. LEGISLATION / VOTE INTEGRITY
    # -------------------------------------------------------------------------
    try:
        from src.models import Legislation, Vote

        # Legislation where status='active' but voting_closed=True (contradictory)
        contradictory = Legislation.objects.filter(
            status='active', voting_closed=True, is_active=True
        ).count()
        if contradictory:
            flag('medium', 'Legislation', f"{contradictory} legislation item(s) are status='active' but voting_closed=True")

        # Legislation marked passed=True but status != 'passed'
        passed_mismatch = Legislation.objects.filter(
            passed=True, is_active=True
        ).exclude(status='passed').count()
        if passed_mismatch:
            flag('medium', 'Legislation', f"{passed_mismatch} legislation item(s) have passed=True but status != 'passed'")

        # Active votes for inactive legislation
        orphan_votes = Vote.objects.filter(
            legislation__is_active=False
        ).count()
        if orphan_votes:
            flag('low', 'Legislation', f"{orphan_votes} vote records belong to inactive legislation")

        # Long-running active votes (open for >30 days)
        now = timezone.now()
        stale_active = Legislation.objects.filter(
            status='active',
            voting_closed=False,
            is_active=True,
            voting_starts_at__lt=now - timezone.timedelta(days=30),
        ).count()
        if stale_active:
            flag('low', 'Legislation', f"{stale_active} legislation item(s) have been open for voting for 30+ days")

        # voting_ends_at passed but voting_closed=False (Beat may have missed the window)
        missed_close = Legislation.objects.filter(
            voting_closed=False,
            is_active=True,
            voting_ends_at__lt=now,
            voting_ends_at__isnull=False,
        ).count()
        if missed_close:
            flag('medium', 'Legislation', f"{missed_close} legislation item(s) passed their voting_ends_at but are still open — Celery Beat may have missed a close window")

        # Duplicate votes: same user voted more than once on the same legislation
        from django.db.models import Count as _DCount
        duplicate_votes = (
            Vote.objects
            .values('user', 'legislation')
            .annotate(n=_DCount('id'))
            .filter(n__gt=1)
        )
        if duplicate_votes.exists():
            flag('high', 'Legislation', f"{duplicate_votes.count()} user/legislation pair(s) have more than one Vote record — data integrity issue")

        # Scheduled announcements stuck unpublished (publish_at in the past, not yet published)
        from src.models import Announcement
        stuck_announcements = Announcement.objects.filter(
            publish_at__lt=now,
            publish_at__isnull=False,
            is_active=False,
        ).count()
        if stuck_announcements:
            flag('medium', 'Announcements', f"{stuck_announcements} announcement(s) have a past publish_at but are not yet published — Beat may have missed a publish window")

        ok('Legislation', 'Vote and announcement integrity checks complete')

    except Exception as exc:
        errors.append(f"Legislation checks: {exc}")
        logger.error(f"[digest] Legislation checks failed: {exc}")

    # -------------------------------------------------------------------------
    # 4. SECURITY SYSTEMS
    # -------------------------------------------------------------------------
    try:
        from src.models import IPBlacklist, LoginLockout, QuarantinedAccount
        now = timezone.now()

        # IPBlacklist entries that are past their expiry but still marked active
        expired_blacklist = IPBlacklist.objects.filter(
            is_active=True,
            expires_at__lt=now,
        ).exclude(expires_at=None).count()
        if expired_blacklist:
            flag('low', 'Security', f"{expired_blacklist} IPBlacklist entry/entries are past expiry but still is_active=True")

        # DB LoginLockout records that are past expiry (cache clears automatically but DB doesn't)
        expired_lockouts = LoginLockout.objects.filter(
            expires_at__lt=now
        ).count()
        if expired_lockouts > 50:
            flag('low', 'Security', f"{expired_lockouts} expired LoginLockout records in DB — consider pruning")

        # High-volume attack blocks in the last 7 days from a single IP
        from src.models import SecurityNotificationLog
        from django.db.models import Count as _Count
        top_attack_ip = (
            SecurityNotificationLog.objects
            .filter(event_type='ATTACK_BLOCKED', sent_at__gte=now - timezone.timedelta(days=7))
            .values('ip_address')
            .annotate(n=_Count('id'))
            .order_by('-n')
            .first()
        )
        if top_attack_ip and top_attack_ip['n'] >= 20:
            flag('medium', 'Security', f"IP {top_attack_ip['ip_address']} triggered {top_attack_ip['n']} attack-blocked events in the last 7 days — consider permanent blacklist")

        # Unusual ActivityLog spike: any single user with >500 log entries in 7 days
        from src.models import ActivityLog
        spike = (
            ActivityLog.objects
            .filter(timestamp__gte=now - timezone.timedelta(days=7))
            .values('user__username')
            .annotate(n=_Count('id'))
            .filter(n__gt=500)
            .order_by('-n')
        )
        for s in spike:
            flag('medium', 'Security', f"User '{s['user__username']}' generated {s['n']} activity log entries in 7 days — possible automation or abuse")

        # ActivityLog table size warning
        total_logs = ActivityLog.objects.count()
        if total_logs > 100_000:
            flag('low', 'Maintenance', f"ActivityLog has {total_logs:,} records — consider running the audit log retention command")

        # Other table size warnings
        from src.models import LoginHistory, HoneypotAccess
        for model, label, threshold in [
            (LoginHistory, 'LoginHistory', 200_000),
            (SecurityNotificationLog, 'SecurityNotificationLog', 50_000),
            (HoneypotAccess, 'HoneypotAccess', 50_000),
        ]:
            count = model.objects.count()
            if count > threshold:
                flag('low', 'Maintenance', f"{label} has {count:,} records (threshold {threshold:,}) — consider pruning old entries")

        # Active watch flags not updated in 30+ days (may be stale/forgotten)
        from src.models import UserWatchFlag
        stale_watch = UserWatchFlag.objects.filter(
            is_active=True,
            updated_at__lt=now - timezone.timedelta(days=30),
        )
        if stale_watch.exists():
            names = ', '.join(stale_watch.values_list('user__username', flat=True)[:5])
            flag('medium', 'Security', f"{stale_watch.count()} active watch flag(s) haven't been reviewed/updated in 30+ days: {names}")

        # IPWhitelist entries older than 6 months — may be stale
        from src.models import IPWhitelist
        old_whitelist = IPWhitelist.objects.filter(
            is_active=True,
            added_at__lt=now - timezone.timedelta(days=180),
        )
        if old_whitelist.exists():
            entries = ', '.join(
                f"{e['ip_address']} ({e['description'][:30]})"
                for e in old_whitelist.values('ip_address', 'description')[:5]
            )
            flag('low', 'Security', f"{old_whitelist.count()} IPWhitelist entry/entries are 6+ months old and may be stale: {entries}")

        ok('Security', 'Security system checks complete')

    except Exception as exc:
        errors.append(f"Security system checks: {exc}")
        logger.error(f"[digest] Security system checks failed: {exc}")

    # -------------------------------------------------------------------------
    # 5. SESSION & PUSH INTEGRITY
    # -------------------------------------------------------------------------
    try:
        from src.models import UserSession, PushSubscription
        now = timezone.now()

        # Sessions active for >45 days (Django default session age is 2 weeks — very stale)
        ancient_sessions = UserSession.objects.filter(
            last_activity__lt=now - timezone.timedelta(days=45)
        ).count()
        if ancient_sessions:
            flag('low', 'Sessions', f"{ancient_sessions} UserSession record(s) haven't been active in 45+ days — cleanup task may not be running")

        # Push subscriptions for users who are no longer active
        orphan_push = PushSubscription.objects.filter(
            user__is_active=False
        ).count()
        if orphan_push:
            flag('low', 'Push', f"{orphan_push} PushSubscription(s) belong to inactive users")

        # Push subscriptions unused for 60+ days — endpoint likely expired
        stale_push = PushSubscription.objects.filter(
            last_used_at__lt=now - timezone.timedelta(days=60),
        ).count()
        if stale_push:
            flag('low', 'Push', f"{stale_push} PushSubscription(s) haven't been used in 60+ days — endpoints may be expired and worth pruning")

        ok('Sessions/Push', 'Session and push integrity checks complete')

    except Exception as exc:
        errors.append(f"Session/push checks: {exc}")
        logger.error(f"[digest] Session/push checks failed: {exc}")

    # -------------------------------------------------------------------------
    # 6. CELERY BEAT HEALTH
    # -------------------------------------------------------------------------
    try:
        from django_celery_beat.models import PeriodicTask

        # Tasks that are enabled but haven't run in >48 hours (beat may be down)
        now = timezone.now()
        stale_tasks = (
            PeriodicTask.objects
            .filter(enabled=True)
            .exclude(last_run_at=None)
            .filter(last_run_at__lt=now - timezone.timedelta(hours=48))
        )
        for t in stale_tasks:
            flag('high', 'Celery Beat', f"Task '{t.name}' is enabled but last ran at {localtime(t.last_run_at).strftime('%Y-%m-%d %H:%M %Z')} — Beat may be down")

        never_run = PeriodicTask.objects.filter(enabled=True, last_run_at=None).count()
        if never_run:
            flag('low', 'Celery Beat', f"{never_run} enabled periodic task(s) have never run — check Beat is running and schedules are seeded")

        ok('Celery Beat', 'Beat health checks complete')

    except Exception as exc:
        errors.append(f"Celery Beat checks: {exc}")
        logger.error(f"[digest] Celery Beat checks failed: {exc}")

    # -------------------------------------------------------------------------
    # HONEYPOT ACTIVITY (last 24 hours)
    # -------------------------------------------------------------------------
    honeypot_section = ''
    try:
        from src.models import HoneypotAccess, IPBlacklist, SecurityNotificationLog
        from django.db.models import Count as _HCount

        hp_hits = HoneypotAccess.objects.filter(accessed_at__gte=since)
        hp_total = hp_hits.count()

        top_endpoints = (
            hp_hits.values('endpoint')
            .annotate(count=_HCount('id'))
            .order_by('-count')[:5]
        )
        top_ips = (
            hp_hits.values('ip_address')
            .annotate(count=_HCount('id'))
            .order_by('-count')[:5]
        )
        top_ip_addresses = [e['ip_address'] for e in top_ips]
        blacklisted_ips = set(
            IPBlacklist.objects.filter(
                ip_address__in=top_ip_addresses,
                is_active=True,
            ).values_list('ip_address', flat=True)
        )

        attack_blocks = SecurityNotificationLog.objects.filter(
            event_type='ATTACK_BLOCKED',
            sent_at__gte=since,
        ).count()

        endpoint_lines = (
            '\n'.join(
                f"  {e['endpoint']} — {e['count']} hit{'s' if e['count'] != 1 else ''}"
                for e in top_endpoints
            ) or '  (none)'
        )
        ip_lines = (
            '\n'.join(
                f"  {e['ip_address']} — {e['count']} hit{'s' if e['count'] != 1 else ''}"
                f"  {'[BLACKLISTED]' if e['ip_address'] in blacklisted_ips else '[not blacklisted]'}"
                for e in top_ips
            ) or '  (none)'
        )

        honeypot_section = (
            f"\nHONEYPOT ACTIVITY (last 24h)\n"
            f"-----------------------------\n"
            f"Total hits:          {hp_total}\n"
            f"Attack-blocked:      {attack_blocks}\n"
            f"\nTop Targeted Endpoints:\n{endpoint_lines}\n"
            f"\nTop Attacking IPs:\n{ip_lines}\n"
        )

    except Exception as exc:
        errors.append(f"Honeypot section: {exc}")
        logger.error(f"[digest] Honeypot section failed: {exc}")
        honeypot_section = '\nHONEYPOT ACTIVITY\n------------------\n  (error collecting data — see logs)\n'

    # -------------------------------------------------------------------------
    # COMPILE AND EMAIL REPORT
    # -------------------------------------------------------------------------
    digest_duration = (timezone.now() - digest_start).total_seconds()
    site_url = get_site_url()
    email_to = get_security_alert_email()

    high   = [(s, c, m) for s, c, m in findings if s == 'high']
    medium = [(s, c, m) for s, c, m in findings if s == 'medium']
    low    = [(s, c, m) for s, c, m in findings if s == 'low']

    def _flagged_section(label, items):
        if not items:
            return ''
        lines = '\n'.join(f"  [{s.upper()}] {c}: {m}" for s, c, m in items)
        return f"\n{label}\n{'-' * len(label)}\n{lines}\n"

    status_line = 'ALL CLEAR' if not findings else f"{len(high)} HIGH / {len(medium)} MEDIUM / {len(low)} LOW"
    subject = f"[Parliament] Daily Digest — {localtime(digest_start).strftime('%a %b %-d')} — {status_line}"

    ok_lines = '\n'.join(f"  [OK] {c}: {m}" for c, m in ok_results) or '  (no checks recorded)'

    body_parts = [
        f"Parliament Daily Site Digest",
        f"{'=' * 60}",
        f"Date:     {localtime(digest_start).strftime('%Y-%m-%d')}",
        f"Run at:   {localtime(digest_start).strftime('%H:%M %Z')}",
        f"Duration: {digest_duration:.1f}s",
        f"Status:   {status_line}",
        honeypot_section,
    ]

    body_parts.append(_flagged_section('HIGH SEVERITY', high))
    body_parts.append(_flagged_section('MEDIUM SEVERITY', medium))
    body_parts.append(_flagged_section('LOW SEVERITY / INFORMATIONAL', low))

    if not findings:
        body_parts.append('\nNo issues found.\n')

    body_parts.append(f"\nALL CHECK RESULTS\n-----------------\n{ok_lines}\n")

    if errors:
        body_parts.append(
            f"\nCHECKS THAT ERRORED\n-------------------\n"
            + '\n'.join(f"  {e}" for e in errors)
            + '\n'
        )

    body_parts.append(f"\nFull logs and admin tools: {site_url}/admin-v2/security/")

    message = '\n'.join(p for p in body_parts if p)
    logger.info(f"[digest] Daily digest complete: {status_line} ({len(findings)} findings, {len(ok_results)} ok, {len(errors)} errors, {digest_duration:.1f}s)")

    if email_to:
        try:
            from django.conf import settings as _settings
            send_mail(
                subject=subject,
                message=message,
                from_email=_settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email_to],
                fail_silently=False,
            )
            logger.info(f"[digest] Report emailed to {email_to}")
        except Exception as exc:
            logger.error(f"[digest] Failed to email report: {exc}")
    else:
        logger.warning("[digest] No SECURITY_ALERT_EMAIL configured — daily digest not emailed")


# ---------------------------------------------------------------------------
# Push Notifications
# ---------------------------------------------------------------------------

@shared_task(name='tasks.send_push_notification', bind=True, max_retries=2, default_retry_delay=30)
def send_push_notification(self, user_id, title, body, url='/home/', tag='parliament'):
    """
    Send a Web Push notification to all active subscriptions for a user.

    Payload shape matches service-worker.js:
      { title, body, url, tag }

    Subscriptions that return 410 Gone are automatically deleted (expired).
    Skips gracefully if VAPID keys are not configured.
    """
    from django.conf import settings
    from src.models import PushSubscription

    vapid_private = getattr(settings, 'VAPID_PRIVATE_KEY', None)
    vapid_claims = getattr(settings, 'VAPID_CLAIMS', None)
    if not vapid_private or not vapid_claims:
        logger.debug('[push] VAPID keys not configured — skipping push')
        return

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        logger.error('[push] pywebpush not installed')
        return

    subscriptions = PushSubscription.objects.filter(user_id=user_id)
    if not subscriptions.exists():
        return

    import json as _json
    payload = _json.dumps({'title': title, 'body': body, 'url': url, 'tag': tag})
    expired_ids = []

    for sub in subscriptions:
        try:
            webpush(
                subscription_info=sub.as_subscription_info(),
                data=payload,
                vapid_private_key=vapid_private,
                vapid_claims=vapid_claims,
            )
            PushSubscription.objects.filter(pk=sub.pk).update(last_used_at=timezone.now())
        except WebPushException as exc:
            response = getattr(exc, 'response', None)
            status = response.status_code if response is not None else None
            if status == 410:
                # Subscription has expired — remove it
                expired_ids.append(sub.pk)
                logger.info(f'[push] subscription expired for user {user_id}, removing')
            else:
                logger.warning(f'[push] WebPushException for user {user_id}: {exc} (status={status})')
        except Exception as exc:
            logger.error(f'[push] unexpected error for user {user_id}: {exc}')

    if expired_ids:
        PushSubscription.objects.filter(pk__in=expired_ids).delete()
