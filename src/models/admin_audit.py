from django.db import models
from django.conf import settings


class AdminActionLog(models.Model):
    """
    Audit trail for officer/admin actions that modify sensitive state.

    Covers: API token approve/deny/revoke/scope-edit, account unlock,
    role changes, manual blacklist edits, and any other high-stakes
    officer action wired up via log_admin_action().

    Separate from ActivityLog (which tracks member-facing events) and
    SecurityNotificationLog (which tracks automated security events).
    This table is specifically for human admin decisions.
    """

    ACTION_CHOICES = [
        ('token_approved',   'API Token Approved'),
        ('token_denied',     'API Token Denied'),
        ('token_revoked',    'API Token Revoked'),
        ('token_scopes_edited', 'API Token Scopes Edited'),
        ('account_unlocked', 'Account Unlocked'),
        ('role_changed',     'Role Changed'),
        ('blacklist_added',  'IP Blacklisted'),
        ('blacklist_removed','IP Blacklist Removed'),
        ('quarantine_set',   'Account Quarantined'),
        ('quarantine_lifted','Quarantine Lifted'),
        ('other',            'Other'),
    ]

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='admin_actions_taken',
        help_text='Officer/admin who performed the action',
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='admin_actions_received',
        help_text='User the action was performed on (if applicable)',
    )
    target_repr = models.CharField(
        max_length=255,
        blank=True,
        help_text='Human-readable description of the target (e.g. token name, IP address)',
    )
    detail = models.TextField(
        blank=True,
        help_text='Extra context: what changed, old→new values, reason',
    )
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['actor', '-timestamp']),
            models.Index(fields=['action', '-timestamp']),
            models.Index(fields=['target_user', '-timestamp']),
        ]
        verbose_name = 'Admin Action Log'
        verbose_name_plural = 'Admin Action Logs'

    def __str__(self):
        actor_name = self.actor.name if self.actor else '(deleted)'
        return f"{actor_name} → {self.get_action_display()} — {self.timestamp.strftime('%Y-%m-%d %H:%M')}"


def log_admin_action(actor, action, request=None, target_user=None, target_repr='', detail=''):
    """
    Create an AdminActionLog entry. Safe to call from any view — failures
    are caught and logged rather than bubbled up.

    Args:
        actor:        The ParliamentUser performing the action.
        action:       One of AdminActionLog.ACTION_CHOICES keys.
        request:      Django request (used to extract IP address).
        target_user:  The user affected, if any.
        target_repr:  Short string describing the target object (e.g. token name).
        detail:       Extra context — what changed, before/after values.
    """
    import logging
    logger = logging.getLogger('admin_actions')

    ip_address = None
    if request:
        try:
            from src.utils.security_utils import get_client_ip
            ip_address = get_client_ip(request)
        except Exception:
            pass

    try:
        AdminActionLog.objects.create(
            actor=actor,
            action=action,
            target_user=target_user,
            target_repr=target_repr,
            detail=detail,
            ip_address=ip_address,
        )
    except Exception as exc:
        logger.error(f"[admin_audit] Failed to write AdminActionLog: {exc}")
