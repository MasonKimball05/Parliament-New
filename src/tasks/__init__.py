"""
Celery tasks for Parliament — split into submodules by domain.

  email.py         — async email wrappers (announcements, security alerts, general)
  votes.py         — vote auto-open/close + scheduled announcement dispatch
  cleanup.py       — nightly/monthly pruning of stale DB records
  notifications.py — user-facing push/in-app notifications + daily digest

All names are re-exported here so existing `from src.tasks import X` call sites
continue to work without modification. Celery's autodiscovery imports this package
and the submodule imports register every task with the worker.
"""
from src.tasks.email import (
    send_announcement_email,
    send_security_alert_task,
    send_email,
    send_pledge_welcome_task,
)
from src.tasks.votes import (
    auto_open_close_chapter_votes,
    auto_open_close_committee_votes,
    auto_open_close_slating_votes,
    publish_scheduled_announcements,
)
from src.tasks.cleanup import (
    cleanup_expired_sessions,
    prune_expired_login_lockouts,
    expire_stale_ip_blacklist_entries,
    prune_stale_push_subscriptions,
    cleanup_api_access_logs,
    prune_expired_chat_permissions,
)
from src.tasks.notifications import (
    notify_expiring_api_tokens,
    send_push_notification,
    send_event_reminder_pushes,
    send_service_event_email_reminders,
    send_daily_digest,
)

__all__ = [
    'send_announcement_email',
    'send_security_alert_task',
    'send_email',
    'send_pledge_welcome_task',
    'auto_open_close_chapter_votes',
    'auto_open_close_committee_votes',
    'auto_open_close_slating_votes',
    'publish_scheduled_announcements',
    'cleanup_expired_sessions',
    'prune_expired_login_lockouts',
    'expire_stale_ip_blacklist_entries',
    'prune_stale_push_subscriptions',
    'cleanup_api_access_logs',
    'prune_expired_chat_permissions',
    'notify_expiring_api_tokens',
    'send_push_notification',
    'send_event_reminder_pushes',
    'send_service_event_email_reminders',
    'send_daily_digest',
]
