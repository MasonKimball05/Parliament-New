"""
User-facing notification tasks: API token expiry alerts, push notifications,
event reminders, and the daily site health digest.
"""
from celery import shared_task
from django.utils import timezone
import logging

logger = logging.getLogger(__name__)


@shared_task(name='tasks.notify_expiring_api_tokens')
def notify_expiring_api_tokens():
    """
    Create in-app (and email) notifications for API tokens expiring within 7 days.
    Runs daily. Uses a cache key to avoid re-notifying the same token within the
    same 7-day window.
    """
    from datetime import timedelta
    from src.models import APIToken, Notification
    from django.core.cache import cache as _cache

    warning_cutoff = timezone.now() + timedelta(days=7)
    expiring = APIToken.objects.filter(
        status=APIToken.STATUS_ACTIVE,
        expires_at__isnull=False,
        expires_at__gt=timezone.now(),
        expires_at__lte=warning_cutoff,
    ).select_related('user')

    notified = 0
    for token in expiring:
        dedup_key = f'token_expiry_notif_{token.pk}'
        if _cache.get(dedup_key):
            continue

        days_left = max(0, (token.expires_at - timezone.now()).days)
        label = f'{days_left} day{"s" if days_left != 1 else ""}'
        expires_str = token.expires_at.strftime('%B %d, %Y')

        try:
            Notification.objects.create(
                recipient=token.user,
                notification_type='security',
                title=f'API token "{token.name}" expires in {label}',
                message=f'Your token will stop working on {expires_str}. Go to Preferences → API Tokens to request a renewal.',
                link='/preferences/#api-tokens',
                source_type='apitoken',
                source_id=token.pk,
            )
            _cache.delete(f'notif_count_{token.user.pk}')
            _cache.set(dedup_key, True, 86400 * 7)
            notified += 1
        except Exception as exc:
            logger.error(f'[tasks] notify_expiring_api_tokens: failed for token {token.pk}: {exc}')

    if notified:
        logger.info(f'[tasks] notify_expiring_api_tokens: sent {notified} expiry notification(s)')


@shared_task(name='tasks.send_push_notification', bind=True, max_retries=2, default_retry_delay=30)
def send_push_notification(self, user_id, title, body, url='/home/', tag='parliament'):
    """
    Send a Web Push notification to all active subscriptions for a user.

    Payload shape matches service-worker.js: { title, body, url, tag }
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
                expired_ids.append(sub.pk)
                logger.info(f'[push] subscription expired for user {user_id}, removing')
            else:
                logger.warning(f'[push] WebPushException for user {user_id}: {exc} (status={status})')
        except Exception as exc:
            logger.error(f'[push] unexpected error for user {user_id}: {exc}')

    if expired_ids:
        PushSubscription.objects.filter(pk__in=expired_ids).delete()


@shared_task(name='tasks.send_event_reminder_pushes')
def send_event_reminder_pushes():
    """
    Send push notification reminders for upcoming events.

    Runs every 15 minutes via Celery Beat. Respects per-user push_events preference
    and per-event reminder slot configuration.
    """
    from src.models import ParliamentUser, PushSubscription, Event, EventReminderLog, EventReminderRecipient
    from src.models_feature_flags import FeatureFlag, SiteSetting
    from datetime import timedelta
    from django.db.models import Q

    now = timezone.now()

    master_flag = FeatureFlag.objects.filter(name='push_notifications_enabled').first()
    if master_flag and not master_flag.is_enabled:
        logger.debug('[event_reminders] push_notifications_enabled flag is OFF — skipping')
        return

    push_events_flag = FeatureFlag.objects.filter(name='push_events').first()
    if push_events_flag and not push_events_flag.is_enabled:
        logger.debug('[event_reminders] push_events flag is OFF — skipping')
        return

    reminders_enabled = SiteSetting.get_setting('event_reminders_enabled', True)
    if not reminders_enabled:
        logger.debug('[event_reminders] event_reminders_enabled setting is OFF — skipping')
        return

    candidates = Event.objects.filter(
        is_active=True,
        date_time__gt=now,
        date_time__lte=now + timedelta(days=7),
    ).filter(
        Q(reminder_1_enabled=True, reminder_1_sent_at__isnull=True) |
        Q(reminder_2_enabled=True, reminder_2_sent_at__isnull=True)
    )

    sent_count = 0

    for event in candidates:
        due_slots = []
        if (event.reminder_1_enabled and event.reminder_1_sent_at is None and
                now >= event.date_time - timedelta(hours=event.reminder_1_hours_before)):
            due_slots.append((1, 'reminder_1_sent_at'))
        if (event.reminder_2_enabled and event.reminder_2_sent_at is None and
                now >= event.date_time - timedelta(hours=event.reminder_2_hours_before)):
            due_slots.append((2, 'reminder_2_sent_at'))

        if not due_slots:
            continue

        all_active = ParliamentUser.objects.filter(member_status='Active', is_active=True).select_related('preferences')
        if event.visible_to:
            visible_types = set(event.visible_to)
            if 'Member' in visible_types:
                visible_types.update(['Chair', 'Officer'])
            eligible_users = all_active.filter(member_type__in=visible_types)
        else:
            eligible_users = all_active

        eligible_users = list(eligible_users)
        subscribed_user_ids = set(
            PushSubscription.objects.filter(user__in=eligible_users).values_list('user_id', flat=True)
        )

        event_url = f'/officer/manage-events/{event.pk}/attendance/'
        local_dt = timezone.localtime(event.date_time)
        time_str = local_dt.strftime('%a, %b %-d at %-I:%M %p')
        base_body = time_str + (f' — {event.location}' if event.location else '')

        for slot_num, sent_at_field in due_slots:
            title = f'Upcoming Event: {event.title}'
            tag = f'event_reminder_{slot_num}'

            recipient_rows = []
            dispatched = 0
            opted_out = 0

            for user in eligible_users:
                if user.pk not in subscribed_user_ids:
                    recipient_rows.append(EventReminderRecipient(
                        user=user,
                        user_name=user.name or user.username,
                        user_member_type=user.member_type or '',
                        status='skipped_no_subscription',
                    ))
                    continue

                try:
                    push_on = user.preferences.push_events
                except Exception:
                    push_on = True

                if not push_on:
                    opted_out += 1
                    recipient_rows.append(EventReminderRecipient(
                        user=user,
                        user_name=user.name or user.username,
                        user_member_type=user.member_type or '',
                        status='skipped_opted_out',
                    ))
                    continue

                send_push_notification.delay(user.pk, title, base_body, event_url, tag=tag)
                dispatched += 1
                recipient_rows.append(EventReminderRecipient(
                    user=user,
                    user_name=user.name or user.username,
                    user_member_type=user.member_type or '',
                    status='dispatched',
                ))

            reminder_log = EventReminderLog.objects.create(
                event=event,
                reminder_slot=slot_num,
                users_eligible=len(eligible_users),
                users_subscribed=len(subscribed_user_ids),
                users_opted_out=opted_out,
                notifications_dispatched=dispatched,
                status='dispatched',
            )
            for row in recipient_rows:
                row.reminder_log = reminder_log
            EventReminderRecipient.objects.bulk_create(recipient_rows)

            Event.objects.filter(pk=event.pk).update(**{sent_at_field: now})
            sent_count += 1
            logger.info(
                f'[event_reminders] Slot {slot_num} reminder sent for "{event.title}" '
                f'(id={event.pk}) — dispatched={dispatched}, opted_out={opted_out}'
            )

    if sent_count:
        logger.info(f'[event_reminders] send_event_reminder_pushes: dispatched {sent_count} reminder(s)')


@shared_task(name='tasks.send_daily_digest')
def send_daily_digest():
    """
    Daily site health digest. Runs every night at 3:30 AM CST via Celery Beat.

    Combines system integrity audit and honeypot activity into one report.
    Always sends — absence of the email is itself a signal that the task stopped running.
    The task never raises; failures in individual checks are caught independently.
    """
    from django.core.mail import send_mail
    from django.utils.timezone import localtime
    from src.security_notifications import get_security_alert_email, get_site_url

    digest_start = timezone.now()
    since = digest_start - timezone.timedelta(hours=24)

    findings = []
    ok_results = []
    errors = []

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

        no_password = ParliamentUser.objects.filter(is_active=True, member_status='Active', password='').count()
        if no_password:
            flag('high', 'Accounts', f"{no_password} active user(s) have no password set")
        else:
            ok('Accounts', 'All active users have a password')

        stale_force_pw = ParliamentUser.objects.filter(force_password_change=True, is_active=True)
        stale_force_pw_count = stale_force_pw.filter(last_login__lt=now - timezone.timedelta(days=14)).count()
        if stale_force_pw_count:
            flag('medium', 'Accounts', f"{stale_force_pw_count} user(s) have had force_password_change set for 14+ days without logging in")
        ok('Accounts', f"{stale_force_pw.count()} user(s) currently flagged force_password_change")

        inconsistent_pw_flags = ParliamentUser.objects.filter(has_default_password=True, force_password_change=False, is_active=True).count()
        if inconsistent_pw_flags:
            flag('medium', 'Accounts', f"{inconsistent_pw_flags} user(s) have has_default_password=True but force_password_change=False")

        inactive_active = ParliamentUser.objects.filter(
            member_status='Active', is_active=True,
            last_login__lt=now - timezone.timedelta(days=60),
        ).exclude(member_type='Advisor').count()
        if inactive_active > 5:
            flag('low', 'Accounts', f"{inactive_active} active non-advisor members haven't logged in for 60+ days")

        old_quarantine = ParliamentUser.objects.filter(is_quarantined=True, is_active=True)
        if old_quarantine.exists():
            names = ', '.join(old_quarantine.values_list('username', flat=True)[:5])
            flag('medium', 'Accounts', f"{old_quarantine.count()} user(s) currently quarantined: {names}")

        admins = list(ParliamentUser.objects.filter(is_admin=True, is_active=True).values_list('username', flat=True))
        ok('Accounts', f"Admin accounts ({len(admins)}): {', '.join(admins) or 'none'}")

        from src.models import ActivityLog
        recent_admin_grants = ActivityLog.objects.filter(
            action_type='other', description__icontains='admin',
            timestamp__gte=now - timezone.timedelta(days=7),
        ).count()
        if recent_admin_grants:
            flag('medium', 'Accounts', f"{recent_admin_grants} activity log entries mention 'admin' in the last 7 days — verify no unexpected privilege changes")

        new_accounts = ParliamentUser.objects.filter(date_joined__gte=now - timezone.timedelta(days=7)) if hasattr(ParliamentUser, 'date_joined') else []
        if not new_accounts:
            new_account_logs = ActivityLog.objects.filter(
                action_type='other', description__icontains='created',
                timestamp__gte=now - timezone.timedelta(days=7),
            ).count()
            if new_account_logs:
                flag('low', 'Accounts', f"{new_account_logs} account-creation activity log entries in the last 7 days — verify these are expected")
        else:
            count = new_accounts.count()
            if count:
                names = ', '.join(new_accounts.values_list('username', flat=True)[:5])
                flag('low', 'Accounts', f"{count} new user account(s) created in the last 7 days: {names}")

        stale_email_flagged = ParliamentUser.objects.filter(
            email_flagged=True, is_active=True,
            email_flagged_at__lt=now - timezone.timedelta(days=14),
        ).count()
        if stale_email_flagged:
            flag('medium', 'Accounts', f"{stale_email_flagged} user(s) have had a flagged/undeliverable email for 14+ days without updating it")

    except Exception as exc:
        errors.append(f"User account checks: {exc}")
        logger.error(f"[digest] User account checks failed: {exc}")

    # -------------------------------------------------------------------------
    # 2. 2FA DEVICE INTEGRITY
    # -------------------------------------------------------------------------
    try:
        from django_otp.plugins.otp_totp.models import TOTPDevice
        from django.db.models import Count

        multi_totp = TOTPDevice.objects.filter(confirmed=True).values('user').annotate(n=Count('id')).filter(n__gt=1)
        if multi_totp.exists():
            flag('high', '2FA', f"{multi_totp.count()} user(s) have multiple confirmed TOTP devices — possible enrollment issue")

        orphan_totp = TOTPDevice.objects.filter(confirmed=True).exclude(user__is_active=True).count()
        if orphan_totp:
            flag('low', '2FA', f"{orphan_totp} confirmed TOTP device(s) belong to inactive/removed users")

        from src.models import TwoFactorRequirement
        required_no_device = TwoFactorRequirement.objects.filter(requirement='required').exclude(
            user__in=TOTPDevice.objects.filter(confirmed=True).values('user')
        ).count()
        if required_no_device:
            flag('medium', '2FA', f"{required_no_device} user(s) have 2FA individually required but no confirmed TOTP device")

        from src.models import ParliamentUser as _PU
        admin_no_2fa = _PU.objects.filter(is_admin=True, is_active=True).exclude(pk__in=TOTPDevice.objects.filter(confirmed=True).values('user'))
        if admin_no_2fa.exists():
            names = ', '.join(admin_no_2fa.values_list('username', flat=True))
            flag('high', '2FA', f"{admin_no_2fa.count()} admin account(s) have no 2FA device: {names}")

        users_with_2fa = set(TOTPDevice.objects.filter(confirmed=True).values_list('user', flat=True))
        no_email_with_2fa = (
            _PU.objects.filter(is_active=True, member_status='Active', pk__in=users_with_2fa, email__isnull=True) |
            _PU.objects.filter(is_active=True, member_status='Active', pk__in=users_with_2fa, email='')
        )
        if no_email_with_2fa.count():
            flag('high', '2FA', f"{no_email_with_2fa.count()} user(s) have 2FA enabled but no email — self-service recovery unavailable")

        ok('2FA', 'Device integrity checks complete')

    except Exception as exc:
        errors.append(f"2FA device checks: {exc}")
        logger.error(f"[digest] 2FA device checks failed: {exc}")

    # -------------------------------------------------------------------------
    # 3. LEGISLATION / VOTE INTEGRITY
    # -------------------------------------------------------------------------
    try:
        from src.models import Legislation, Vote, Announcement
        from django.db.models import Count as _DCount

        if Legislation.objects.filter(status='active', voting_closed=True, is_active=True).count():
            flag('medium', 'Legislation', f"{Legislation.objects.filter(status='active', voting_closed=True, is_active=True).count()} legislation item(s) are status='active' but voting_closed=True")

        passed_mismatch = Legislation.objects.filter(passed=True, is_active=True).exclude(status='passed').count()
        if passed_mismatch:
            flag('medium', 'Legislation', f"{passed_mismatch} legislation item(s) have passed=True but status != 'passed'")

        orphan_votes = Vote.objects.filter(legislation__is_active=False).count()
        if orphan_votes:
            flag('low', 'Legislation', f"{orphan_votes} vote records belong to inactive legislation")

        now = timezone.now()
        stale_active = Legislation.objects.filter(status='active', voting_closed=False, is_active=True, voting_starts_at__lt=now - timezone.timedelta(days=30)).count()
        if stale_active:
            flag('low', 'Legislation', f"{stale_active} legislation item(s) have been open for voting for 30+ days")

        missed_close = Legislation.objects.filter(voting_closed=False, is_active=True, voting_ends_at__lt=now, voting_ends_at__isnull=False).count()
        if missed_close:
            flag('medium', 'Legislation', f"{missed_close} legislation item(s) passed their voting_ends_at but are still open — Celery Beat may have missed a close window")

        duplicate_votes = Vote.objects.values('user', 'legislation').annotate(n=_DCount('id')).filter(n__gt=1)
        if duplicate_votes.exists():
            flag('high', 'Legislation', f"{duplicate_votes.count()} user/legislation pair(s) have more than one Vote record — data integrity issue")

        stuck_announcements = Announcement.objects.filter(publish_at__lt=now, publish_at__isnull=False, is_active=False).count()
        if stuck_announcements:
            flag('medium', 'Announcements', f"{stuck_announcements} announcement(s) have a past publish_at but are not yet published")

        ok('Legislation', 'Vote and announcement integrity checks complete')

    except Exception as exc:
        errors.append(f"Legislation checks: {exc}")
        logger.error(f"[digest] Legislation checks failed: {exc}")

    # -------------------------------------------------------------------------
    # 4. SECURITY SYSTEMS
    # -------------------------------------------------------------------------
    try:
        from src.models import IPBlacklist, LoginLockout, ActivityLog, LoginHistory, SecurityNotificationLog, HoneypotAccess, UserWatchFlag, IPWhitelist
        from django.db.models import Count as _Count
        now = timezone.now()

        expired_blacklist = IPBlacklist.objects.filter(is_active=True, expires_at__lt=now).exclude(expires_at=None).count()
        if expired_blacklist:
            flag('low', 'Security', f"{expired_blacklist} IPBlacklist entry/entries are past expiry but still is_active=True")

        expired_lockouts = LoginLockout.objects.filter(expires_at__lt=now).count()
        if expired_lockouts > 50:
            flag('low', 'Security', f"{expired_lockouts} expired LoginLockout records in DB — consider pruning")

        top_attack_ip = (
            SecurityNotificationLog.objects
            .filter(event_type='ATTACK_BLOCKED', sent_at__gte=now - timezone.timedelta(days=7))
            .values('ip_address').annotate(n=_Count('id')).order_by('-n').first()
        )
        if top_attack_ip and top_attack_ip['n'] >= 20:
            flag('medium', 'Security', f"IP {top_attack_ip['ip_address']} triggered {top_attack_ip['n']} attack-blocked events in the last 7 days")

        spike = ActivityLog.objects.filter(timestamp__gte=now - timezone.timedelta(days=7)).values('user__username').annotate(n=_Count('id')).filter(n__gt=500).order_by('-n')
        for s in spike:
            flag('medium', 'Security', f"User '{s['user__username']}' generated {s['n']} activity log entries in 7 days — possible automation or abuse")

        total_logs = ActivityLog.objects.count()
        if total_logs > 100_000:
            flag('low', 'Maintenance', f"ActivityLog has {total_logs:,} records — consider running the audit log retention command")

        for model, label, threshold in [(LoginHistory, 'LoginHistory', 200_000), (SecurityNotificationLog, 'SecurityNotificationLog', 50_000), (HoneypotAccess, 'HoneypotAccess', 50_000)]:
            count = model.objects.count()
            if count > threshold:
                flag('low', 'Maintenance', f"{label} has {count:,} records (threshold {threshold:,}) — consider pruning old entries")

        stale_watch_list = list(
            UserWatchFlag.objects.filter(is_active=True, updated_at__lt=now - timezone.timedelta(days=30))
            .values_list('user__username', flat=True)[:5]
        )
        if stale_watch_list:
            # Count separately so we get the real total, not just the capped preview list
            stale_watch_count = UserWatchFlag.objects.filter(
                is_active=True, updated_at__lt=now - timezone.timedelta(days=30)
            ).count()
            names = ', '.join(stale_watch_list)
            flag('medium', 'Security', f"{stale_watch_count} active watch flag(s) haven't been reviewed/updated in 30+ days: {names}")

        old_whitelist_rows = list(
            IPWhitelist.objects.filter(is_active=True, added_at__lt=now - timezone.timedelta(days=180))
            .values('ip_address', 'description')[:5]
        )
        if old_whitelist_rows:
            old_whitelist_count = IPWhitelist.objects.filter(
                is_active=True, added_at__lt=now - timezone.timedelta(days=180)
            ).count()
            entries = ', '.join(f"{e['ip_address']} ({e['description'][:30]})" for e in old_whitelist_rows)
            flag('low', 'Security', f"{old_whitelist_count} IPWhitelist entry/entries are 6+ months old and may be stale: {entries}")

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

        ancient_sessions = UserSession.objects.filter(last_activity__lt=now - timezone.timedelta(days=45)).count()
        if ancient_sessions:
            flag('low', 'Sessions', f"{ancient_sessions} UserSession record(s) haven't been active in 45+ days")

        orphan_push = PushSubscription.objects.filter(user__is_active=False).count()
        if orphan_push:
            flag('low', 'Push', f"{orphan_push} PushSubscription(s) belong to inactive users")

        stale_push = PushSubscription.objects.filter(last_used_at__lt=now - timezone.timedelta(days=60)).count()
        if stale_push:
            flag('low', 'Push', f"{stale_push} PushSubscription(s) haven't been used in 60+ days")

        ok('Sessions/Push', 'Session and push integrity checks complete')

    except Exception as exc:
        errors.append(f"Session/push checks: {exc}")
        logger.error(f"[digest] Session/push checks failed: {exc}")

    # -------------------------------------------------------------------------
    # 6. CELERY BEAT HEALTH
    # -------------------------------------------------------------------------
    try:
        from django_celery_beat.models import PeriodicTask
        from django.utils.timezone import localtime as _localtime
        now = timezone.now()

        stale_tasks = PeriodicTask.objects.filter(enabled=True).exclude(last_run_at=None).filter(last_run_at__lt=now - timezone.timedelta(hours=48))
        for t in stale_tasks:
            flag('high', 'Celery Beat', f"Task '{t.name}' is enabled but last ran at {_localtime(t.last_run_at).strftime('%Y-%m-%d %H:%M %Z')} — Beat may be down")

        never_run = PeriodicTask.objects.filter(enabled=True, last_run_at=None).count()
        if never_run:
            flag('low', 'Celery Beat', f"{never_run} enabled periodic task(s) have never run — check Beat is running and schedules are seeded")

        ok('Celery Beat', 'Beat health checks complete')

    except Exception as exc:
        errors.append(f"Celery Beat checks: {exc}")
        logger.error(f"[digest] Celery Beat checks failed: {exc}")

    # -------------------------------------------------------------------------
    # HONEYPOT ACTIVITY
    # -------------------------------------------------------------------------
    honeypot_section = ''
    try:
        from src.models import HoneypotAccess, IPBlacklist, SecurityNotificationLog
        from django.db.models import Count as _HCount

        hp_hits = HoneypotAccess.objects.filter(accessed_at__gte=since)
        hp_total = hp_hits.count()
        top_endpoints = hp_hits.values('endpoint').annotate(count=_HCount('id')).order_by('-count')[:5]
        top_ips = hp_hits.values('ip_address').annotate(count=_HCount('id')).order_by('-count')[:5]
        top_ip_addresses = [e['ip_address'] for e in top_ips]
        blacklisted_ips = set(IPBlacklist.objects.filter(ip_address__in=top_ip_addresses, is_active=True).values_list('ip_address', flat=True))
        attack_blocks = SecurityNotificationLog.objects.filter(event_type='ATTACK_BLOCKED', sent_at__gte=since).count()

        endpoint_lines = '\n'.join(f"  {e['endpoint']} — {e['count']} hit{'s' if e['count'] != 1 else ''}" for e in top_endpoints) or '  (none)'
        ip_lines = '\n'.join(f"  {e['ip_address']} — {e['count']} hit{'s' if e['count'] != 1 else ''}  {'[BLACKLISTED]' if e['ip_address'] in blacklisted_ips else '[not blacklisted]'}" for e in top_ips) or '  (none)'

        honeypot_section = (
            f"\nHONEYPOT ACTIVITY (last 24h)\n-----------------------------\n"
            f"Total hits:          {hp_total}\nAttack-blocked:      {attack_blocks}\n"
            f"\nTop Targeted Endpoints:\n{endpoint_lines}\n\nTop Attacking IPs:\n{ip_lines}\n"
        )
    except Exception as exc:
        errors.append(f"Honeypot section: {exc}")
        logger.error(f"[digest] Honeypot section failed: {exc}")
        honeypot_section = '\nHONEYPOT ACTIVITY\n------------------\n  (error collecting data — see logs)\n'

    # -------------------------------------------------------------------------
    # COMPILE AND SEND
    # -------------------------------------------------------------------------
    from django.utils.timezone import localtime
    digest_duration = (timezone.now() - digest_start).total_seconds()
    site_url = get_site_url()
    email_to = get_security_alert_email()

    high   = [(s, c, m) for s, c, m in findings if s == 'high']
    medium = [(s, c, m) for s, c, m in findings if s == 'medium']
    low    = [(s, c, m) for s, c, m in findings if s == 'low']

    def _section(label, items):
        if not items:
            return ''
        return f"\n{label}\n{'-' * len(label)}\n" + '\n'.join(f"  [{s.upper()}] {c}: {m}" for s, c, m in items) + '\n'

    status_line = 'ALL CLEAR' if not findings else f"{len(high)} HIGH / {len(medium)} MEDIUM / {len(low)} LOW"
    subject = f"[Parliament] Daily Digest — {localtime(digest_start).strftime('%a %b %-d')} — {status_line}"
    ok_lines = '\n'.join(f"  [OK] {c}: {m}" for c, m in ok_results) or '  (no checks recorded)'

    body_parts = [
        f"Parliament Daily Site Digest\n{'=' * 60}",
        f"Date:     {localtime(digest_start).strftime('%Y-%m-%d')}",
        f"Run at:   {localtime(digest_start).strftime('%H:%M %Z')}",
        f"Duration: {digest_duration:.1f}s",
        f"Status:   {status_line}",
        honeypot_section,
        _section('HIGH SEVERITY', high),
        _section('MEDIUM SEVERITY', medium),
        _section('LOW SEVERITY / INFORMATIONAL', low),
        '' if findings else '\nNo issues found.\n',
        f"\nALL CHECK RESULTS\n-----------------\n{ok_lines}\n",
    ]
    if errors:
        body_parts.append(f"\nCHECKS THAT ERRORED\n-------------------\n" + '\n'.join(f"  {e}" for e in errors) + '\n')
    body_parts.append(f"\nFull logs and admin tools: {site_url}/admin-v2/security/")

    message = '\n'.join(p for p in body_parts if p)
    logger.info(f"[digest] Daily digest complete: {status_line} ({len(findings)} findings, {len(ok_results)} ok, {len(errors)} errors, {digest_duration:.1f}s)")

    if email_to:
        try:
            from django.conf import settings as _settings
            send_mail(subject=subject, message=message, from_email=_settings.DEFAULT_FROM_EMAIL, recipient_list=[email_to], fail_silently=False)
            logger.info(f"[digest] Report emailed to {email_to}")
        except Exception as exc:
            logger.error(f"[digest] Failed to email report: {exc}")
    else:
        logger.warning("[digest] No SECURITY_ALERT_EMAIL configured — daily digest not emailed")


@shared_task(name='tasks.send_service_event_email_reminders')
def send_service_event_email_reminders():
    """
    Send custom email reminders for upcoming service events.

    Runs every 15 minutes (same schedule as push reminders). For each ServiceEvent
    with email_reminder_enabled=True and email_reminder_sent_at=None, checks whether
    we are within the configured lead-time window and dispatches the email to all
    active, eligible members who have an email address.

    Supports placeholders in subject and body:
        {event_title}    — event title
        {event_date}     — formatted date/time
        {event_location} — location (empty string if not set)
        {hours}          — hours_awarded
    """
    from datetime import timedelta
    from django.conf import settings as _settings
    from django.core.mail import send_mail
    from src.models import ServiceEvent, ParliamentUser

    now = timezone.now()
    window_end = now + timedelta(days=7)

    candidates = (
        ServiceEvent.objects
        .filter(
            email_reminder_enabled=True,
            email_reminder_sent_at__isnull=True,
            event__is_active=True,
            event__date_time__gt=now,
            event__date_time__lte=window_end,
        )
        .select_related('event', 'period')
    )

    sent_events = 0

    for se in candidates:
        # Check whether we've reached the send window
        send_at = se.event.date_time - timedelta(hours=se.email_reminder_hours_before)
        if now < send_at:
            continue  # Not time yet

        event = se.event

        # Build placeholder context
        local_dt = timezone.localtime(event.date_time)
        context = {
            'event_title': event.title,
            'event_date': local_dt.strftime('%A, %B %-d at %-I:%M %p'),
            'event_location': event.location or '',
            'hours': str(se.hours_awarded),
        }

        try:
            subject = se.email_reminder_subject.format(**context)
            body = se.email_reminder_body.format(**context)
        except (KeyError, ValueError) as fmt_err:
            logger.warning(f"[service_event_reminders] format error for ServiceEvent {se.pk}: {fmt_err}")
            subject = se.email_reminder_subject
            body = se.email_reminder_body

        from_email = getattr(_settings, 'DEFAULT_FROM_EMAIL', None)
        if not from_email:
            logger.warning('[service_event_reminders] DEFAULT_FROM_EMAIL not configured — skipping')
            continue

        # Eligible recipients: active members with an email address
        # Respect visible_to if set on the event
        recipients = ParliamentUser.objects.filter(member_status='Active', is_active=True).exclude(email__isnull=True).exclude(email='')
        if event.visible_to:
            from django.db.models import Q as _Q
            visible_types = set(event.visible_to)
            if 'Member' in visible_types:
                visible_types.update(['Chair', 'Officer'])
            recipients = recipients.filter(member_type__in=visible_types)

        dispatched = 0
        for user in recipients:
            try:
                send_mail(
                    subject=subject,
                    message=body,
                    from_email=from_email,
                    recipient_list=[user.email],
                    fail_silently=False,
                )
                dispatched += 1
            except Exception as mail_exc:
                logger.warning(f"[service_event_reminders] failed to email {user.email} for event {se.pk}: {mail_exc}")

        if dispatched > 0:
            # Only mark sent when at least one email was delivered; if dispatched==0
            # (e.g. transient SMTP outage) leave sent_at=None so the next Celery tick retries.
            se.email_reminder_sent_at = now
            se.save(update_fields=['email_reminder_sent_at'])
            sent_events += 1
            logger.info(f"[service_event_reminders] ServiceEvent {se.pk} ({event.title}): emailed {dispatched} recipient(s)")
        else:
            logger.warning(f"[service_event_reminders] ServiceEvent {se.pk} ({event.title}): 0 emails delivered — will retry next tick")

    if sent_events:
        logger.info(f"[service_event_reminders] dispatched reminders for {sent_events} service event(s)")


@shared_task(name='tasks.send_recruitment_rsvp_reminders')
def send_recruitment_rsvp_reminders():
    """
    Send push + email reminders to members who RSVPd 'going' for upcoming recruitment events.

    Runs every 15 minutes. For each RecruitmentEvent with rsvp_reminder_enabled=True and
    rsvp_reminder_sent_at=None, checks whether we're within the configured lead-time window
    and dispatches both push and email reminders to confirmed RSVPs.

    Only marks sent_at when at least one recipient was successfully reached.
    """
    from datetime import timedelta
    from django.conf import settings as _settings
    from django.core.mail import send_mail
    from src.models import RecruitmentEvent, RecruitmentEventRSVP

    now = timezone.now()
    window_end = now + timedelta(days=7)

    candidates = (
        RecruitmentEvent.objects
        .filter(
            rsvp_reminder_enabled=True,
            rsvp_reminder_sent_at__isnull=True,
            event__is_active=True,
            event__date_time__gt=now,
            event__date_time__lte=window_end,
        )
        .select_related('event', 'committee')
    )

    sent_count = 0

    for re in candidates:
        send_at = re.event.date_time - timedelta(hours=re.rsvp_reminder_hours_before)
        if now < send_at:
            continue

        event = re.event
        local_dt = timezone.localtime(event.date_time)
        date_str = local_dt.strftime('%A, %B %-d at %-I:%M %p')
        location_str = event.location or 'TBD'

        going_rsvps = list(
            RecruitmentEventRSVP.objects
            .filter(recruitment_event=re, status='going')
            .select_related('user')
        )

        if not going_rsvps:
            # No one RSVPd — mark sent so we don't re-check every tick
            re.rsvp_reminder_sent_at = now
            re.save(update_fields=['rsvp_reminder_sent_at'])
            logger.info(f"[rsvp_reminders] RecruitmentEvent {re.pk}: no going RSVPs, skipped")
            sent_count += 1
            continue

        push_title = f"Reminder: {event.title}"
        push_body = f"Today at {date_str}" if local_dt.date() == now.date() else f"{date_str}"
        event_url = f"/committee/{re.committee.code}/recruitment/{re.pk}/"

        from_email = getattr(_settings, 'DEFAULT_FROM_EMAIL', None)
        email_subject = f"Reminder: {event.title} — {date_str}"
        email_body = (
            f"Hi,\n\n"
            f"This is a reminder that you RSVPd 'Going' to the following recruitment event:\n\n"
            f"  {event.title}\n"
            f"  {date_str}\n"
            f"  Location: {location_str}\n\n"
            f"See you there!\n\n"
            f"— {re.committee.name}"
        )

        dispatched_push = 0
        dispatched_email = 0

        for rsvp in going_rsvps:
            user = rsvp.user

            # Push notification
            try:
                send_push_notification.delay(
                    user.pk, push_title, push_body, event_url,
                    tag=f'recruitment-reminder-{re.pk}',
                )
                dispatched_push += 1
            except Exception as push_exc:
                logger.warning(f"[rsvp_reminders] push failed for user {user.pk}: {push_exc}")

            # Email notification
            if from_email and user.email:
                try:
                    send_mail(
                        subject=email_subject,
                        message=email_body,
                        from_email=from_email,
                        recipient_list=[user.email],
                        fail_silently=False,
                    )
                    dispatched_email += 1
                except Exception as mail_exc:
                    logger.warning(f"[rsvp_reminders] email failed for {user.email}: {mail_exc}")

        total = dispatched_push + dispatched_email
        if total > 0:
            re.rsvp_reminder_sent_at = now
            re.save(update_fields=['rsvp_reminder_sent_at'])
            sent_count += 1
            logger.info(
                f"[rsvp_reminders] RecruitmentEvent {re.pk} ({event.title}): "
                f"push={dispatched_push}, email={dispatched_email}"
            )
        else:
            logger.warning(
                f"[rsvp_reminders] RecruitmentEvent {re.pk} ({event.title}): "
                f"0 deliveries — will retry next tick"
            )

    if sent_count:
        logger.info(f"[rsvp_reminders] processed reminders for {sent_count} recruitment event(s)")


@shared_task(name='tasks.send_event_signup_announcements')
def send_event_signup_announcements():
    """
    Send a chapter-wide announcement email when a sign-up event opens.

    Runs every 15 minutes. For each Event with requires_signup=True,
    signups_open=True, rsvp_email_enabled=True, and rsvp_email_sent_at=None,
    emails all eligible members with event details and a one-click sign-up link.

    Respects event.visible_to. Only marks rsvp_email_sent_at when at least one
    email was delivered so transient SMTP failures retry on the next tick.
    """
    from django.conf import settings as _settings
    from django.core.mail import send_mail
    from django.urls import reverse
    from src.models import Event, ParliamentUser

    now = timezone.now()

    candidates = (
        Event.objects
        .filter(
            requires_signup=True,
            signups_open=True,
            rsvp_email_enabled=True,
            rsvp_email_sent_at__isnull=True,
            is_active=True,
            archived=False,
            date_time__gt=now,
        )
        .order_by('date_time')
    )

    from_email = getattr(_settings, 'DEFAULT_FROM_EMAIL', None)
    if not from_email:
        logger.warning('[signup_announcements] DEFAULT_FROM_EMAIL not configured — skipping')
        return

    sent_count = 0

    for event in candidates:
        local_dt = timezone.localtime(event.date_time)
        date_str = local_dt.strftime('%A, %B %-d at %-I:%M %p')

        try:
            signup_path = reverse('event_signup', kwargs={'event_id': event.pk})
            site_url = getattr(_settings, 'SITE_URL', '').rstrip('/')
            signup_url = f'{site_url}{signup_path}' if site_url else signup_path
        except Exception:
            signup_url = ''

        subject = f'Sign-ups open: {event.title}'
        lines = [
            f'{event.title}',
            f'When: {date_str}',
        ]
        if event.location:
            lines.append(f'Where: {event.location}')
        if event.description:
            lines.append(f'\n{event.description}')
        if event.max_signups:
            lines.append(f'\nSpots available: {event.max_signups}')
        if event.allow_waitlist:
            lines.append('(A waitlist is available if spots fill up.)')
        if signup_url:
            lines.append(f'\nSign up here: {signup_url}')
        body = '\n'.join(lines)

        recipients = (
            ParliamentUser.objects
            .filter(member_status='Active', is_active=True)
            .exclude(email__isnull=True)
            .exclude(email='')
            .exclude(member_type='Advisor')
        )
        if event.visible_to:
            visible_types = set(event.visible_to)
            if 'Member' in visible_types:
                visible_types.update(['Chair', 'Officer'])
            recipients = recipients.filter(member_type__in=visible_types)

        dispatched = 0
        for user in recipients:
            try:
                send_mail(
                    subject=subject,
                    message=body,
                    from_email=from_email,
                    recipient_list=[user.email],
                    fail_silently=False,
                )
                dispatched += 1
            except Exception as mail_exc:
                logger.warning(
                    f'[signup_announcements] failed to email {user.email} '
                    f'for event {event.pk}: {mail_exc}'
                )

        if dispatched > 0:
            event.rsvp_email_sent_at = now
            event.save(update_fields=['rsvp_email_sent_at'])
            sent_count += 1
            logger.info(
                f'[signup_announcements] Event {event.pk} ({event.title}): '
                f'emailed {dispatched} recipient(s)'
            )
        else:
            logger.warning(
                f'[signup_announcements] Event {event.pk} ({event.title}): '
                f'0 emails delivered — will retry next tick'
            )

    if sent_count:
        logger.info(f'[signup_announcements] announced {sent_count} event(s)')


@shared_task(name='tasks.send_weekly_chapter_digest')
def send_weekly_chapter_digest():
    """
    Send each active member a personalised weekly digest every Sunday morning.

    Sections (all optional — section is omitted if there's nothing to show):
      • Upcoming events     — next 7 days, filtered to events visible to that member
      • Open legislation    — chapter legislation currently accepting votes
      • Your service hours  — hours logged vs. required in the current active period

    Per-member personalisation means this loops over members to send individual
    emails, but the heavy DB work is batched upfront (single query per section).
    """
    from datetime import timedelta

    from django.conf import settings as _settings
    from django.core.mail import send_mail
    from django.urls import reverse

    from src.models import Event, Legislation, ParliamentUser, ServiceHoursSubmission, ServicePeriod
    from django.db.models import Sum

    from_email = getattr(_settings, 'DEFAULT_FROM_EMAIL', None)
    if not from_email:
        logger.warning('[weekly_digest] DEFAULT_FROM_EMAIL not configured — skipping')
        return

    now = timezone.now()

    # Idempotency guard — prevent duplicate sends if Celery retries or the task
    # is accidentally scheduled twice on the same Sunday.
    from django.core.cache import cache as _cache
    _digest_cache_key = f'weekly_digest_sent_{now.strftime("%Y-%W")}'
    if _cache.get(_digest_cache_key):
        logger.info('[weekly_digest] already sent this week — skipping duplicate run')
        return

    site_url = getattr(_settings, 'SITE_URL', '').rstrip('/')
    week_ahead = now + timedelta(days=7)

    # ── Recipients ────────────────────────────────────────────────────────────
    # All active non-pledge members with an email address.
    recipients = list(
        ParliamentUser.objects
        .filter(member_status='Active', is_active=True)
        .exclude(member_type__in=['Advisor', 'Pledge'])
        .exclude(email__isnull=True)
        .exclude(email='')
        .order_by('name')
    )
    if not recipients:
        logger.info('[weekly_digest] no eligible recipients — skipping')
        return

    # ── Upcoming events (next 7 days) ─────────────────────────────────────────
    upcoming_events = list(
        Event.objects
        .filter(is_active=True, archived=False, date_time__gte=now, date_time__lte=week_ahead)
        .exclude(recruitment_event__isnull=False)
        .order_by('date_time')
    )

    # ── Open legislation ──────────────────────────────────────────────────────
    open_legislation = list(
        Legislation.objects
        .filter(is_active=True, voting_closed=False)
        .order_by('created_at')
    )

    # ── Service hours — active period ─────────────────────────────────────────
    active_period = (
        ServicePeriod.objects
        .filter(is_active=True)
        .order_by('-start_date')
        .first()
    )
    hours_by_member = {}  # pk → float
    member_required = {}  # pk → float
    default_required = 0.0
    if active_period:
        default_required = float(active_period.default_hours_required or 0)
        # Per-member overrides in one query
        overrides = {
            o.member_id: float(o.expected_hours)
            for o in active_period.member_expectations.all()
        }
        # Approved hours per member in one query
        submissions = (
            ServiceHoursSubmission.objects
            .filter(period=active_period, status='approved')
            .values('submitted_by')
            .annotate(total=Sum('hours'))
        )
        hours_by_member = {s['submitted_by']: float(s['total']) for s in submissions}
        member_required = overrides  # will fall back to default_required for absent keys

    # ── Per-member email ──────────────────────────────────────────────────────
    sent_count = 0
    skip_count = 0

    for member in recipients:
        member_type = getattr(member, 'member_type', '')

        # Filter events visible to this member's type
        member_events = [
            e for e in upcoming_events
            if not e.visible_to or member_type in e.visible_to
            or (member_type in ('Officer', 'Chair') and 'Member' in (e.visible_to or []))
        ]

        # Skip members who have nothing to see
        if not member_events and not open_legislation and not active_period:
            skip_count += 1
            continue

        lines = [f'Hi {member.name},', '', "Here's your weekly Parliament digest.", '']

        # ── Upcoming events block ─────────────────────────────────────────────
        if member_events:
            lines.append('── UPCOMING EVENTS ─────────────────────────────')
            for event in member_events:
                local_dt = timezone.localtime(event.date_time)
                date_str = local_dt.strftime('%A, %B %-d at %-I:%M %p')
                lines.append(f'  • {event.title} — {date_str}')
                if event.location:
                    lines.append(f'    Where: {event.location}')
                if event.requires_signup:
                    try:
                        path = reverse('event_signup', kwargs={'event_id': event.pk})
                        lines.append(f'    Sign up: {site_url}{path}')
                    except Exception:
                        pass
            lines.append('')

        # ── Open legislation block ────────────────────────────────────────────
        if open_legislation:
            lines.append('── OPEN VOTES ───────────────────────────────────')
            for leg in open_legislation:
                try:
                    path = reverse('vote', kwargs={'legislation_id': leg.pk})
                    url = f'{site_url}{path}'
                except Exception:
                    url = ''
                entry = f'  • {leg.title}'
                if url:
                    entry += f' — {url}'
                lines.append(entry)
            lines.append('')

        # ── Service hours block ───────────────────────────────────────────────
        if active_period:
            hours_logged = hours_by_member.get(member.pk, 0.0)
            required = member_required.get(member.pk, default_required)
            if required:
                pct = round(hours_logged / required * 100)
                status = 'MET ✓' if hours_logged >= required else f'{pct}%'
                lines.append('── SERVICE HOURS ────────────────────────────────')
                lines.append(
                    f'  {hours_logged:.1f} / {required:.1f} h logged  ({status})'
                )
                if hours_logged < required:
                    remaining = required - hours_logged
                    lines.append(f'  {remaining:.1f} h remaining this period')
                lines.append('')

        lines += [
            '────────────────────────────────────────────────',
            f'View Parliament: {site_url}' if site_url else 'Log in to Parliament for details.',
            '',
            "You're receiving this because you're an active chapter member.",
        ]

        body = '\n'.join(lines)
        subject = f'Weekly Chapter Digest — {now.strftime("%B %-d, %Y")}'

        try:
            send_mail(
                subject=subject,
                message=body,
                from_email=from_email,
                recipient_list=[member.email],
                fail_silently=False,
            )
            sent_count += 1
        except Exception as mail_exc:
            logger.warning(f'[weekly_digest] failed to email {member.email}: {mail_exc}')

    # Mark this week's digest as sent so any duplicate Celery run is a no-op.
    _cache.set(_digest_cache_key, True, 60 * 60 * 60)  # 60 hours — covers the full Sunday window

    logger.info(
        f'[weekly_digest] sent={sent_count} skipped={skip_count} '
        f'(events={len(upcoming_events)} legislation={len(open_legislation)} '
        f'period={"yes" if active_period else "no"})'
    )
