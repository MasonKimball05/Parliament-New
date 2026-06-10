"""
API token and access logging models for Parliament's REST API.

APIToken replaces DRF's built-in Token model and adds:
  - Scope-based access control
  - Approval workflow (pending → active or rejected)
  - Full audit trail (approved_by, revoked_by, revoke_reason)
  - Expiry support
  - Access logging via APIAccessLog

APIAccessLog records every authenticated API request for audit purposes.
Logs older than 90 days are pruned by the cleanup_api_access_logs celery task.
"""
import secrets
from django.db import models
from django.utils import timezone


DEFINED_SCOPES = [
    ('members:read', 'Read member directory'),
    ('events:read', 'Read events and calendar'),
    ('legislation:read', 'Read legislation and votes'),
    ('committees:read', 'Read committee information'),
    ('attendance:read', 'Read your attendance records'),
]

# Module-level constant for convenience — also exposed as APIToken.ALL_SCOPE_KEYS
ALL_SCOPE_KEYS = [s[0] for s in DEFINED_SCOPES]


class APIToken(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_ACTIVE = 'active'
    STATUS_REVOKED = 'revoked'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        ('pending', 'Pending Approval'),
        ('active', 'Active'),
        ('revoked', 'Revoked'),
        ('rejected', 'Rejected'),
    ]

    # Keep ALL_SCOPE_KEYS as a class attribute so callers can do APIToken.ALL_SCOPE_KEYS
    ALL_SCOPE_KEYS = [s[0] for s in DEFINED_SCOPES]

    user = models.ForeignKey(
        'src.ParliamentUser',
        on_delete=models.CASCADE,
        related_name='api_tokens',
    )
    key = models.CharField(max_length=64, unique=True, editable=False)
    name = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    scopes = models.JSONField(default=list)  # list of scope key strings
    request_note = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)  # null = no expiry

    approved_by = models.ForeignKey(
        'src.ParliamentUser',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='approved_api_tokens',
    )
    approved_at = models.DateTimeField(null=True, blank=True)

    revoked_by = models.ForeignKey(
        'src.ParliamentUser',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='revoked_api_tokens',
    )
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoke_reason = models.TextField(blank=True)

    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        app_label = 'src'

    @classmethod
    def generate_key(cls):
        """Generate a 64-char hex key (256-bit entropy)."""
        return secrets.token_hex(32)

    def is_valid(self):
        """Return True if the token is active and not expired."""
        if self.status != self.STATUS_ACTIVE:
            return False
        if self.expires_at and self.expires_at < timezone.now():
            return False
        return True

    def has_scope(self, scope):
        """Return True if the given scope key is in this token's scope list."""
        return scope in (self.scopes or [])

    def __str__(self):
        return f"{self.user.username} — {self.name} ({self.status})"


class APIAccessLog(models.Model):
    """
    Records every authenticated API request.
    The token FK is nullable so logs survive token deletion.
    token_key_prefix stores the first 8 chars for identification after deletion.
    """
    token = models.ForeignKey(
        APIToken,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='access_logs',
    )
    token_key_prefix = models.CharField(max_length=8, blank=True)  # first 8 chars
    user = models.ForeignKey(
        'src.ParliamentUser',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='api_access_logs',
    )
    username = models.CharField(max_length=150, blank=True)  # denormalized
    timestamp = models.DateTimeField(auto_now_add=True)
    endpoint = models.CharField(max_length=255)
    method = models.CharField(max_length=10)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    response_status = models.PositiveSmallIntegerField()
    scopes_used = models.JSONField(default=list)
    query_params = models.JSONField(default=dict, blank=True)
    response_summary = models.JSONField(default=dict, blank=True)
    # response_summary structure: {"count": N, "sample": ["Name 1", "Name 2", ...]}

    class Meta:
        ordering = ['-timestamp']
        app_label = 'src'
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['user']),
            models.Index(fields=['timestamp']),
            models.Index(fields=['token_key_prefix']),
        ]

    def __str__(self):
        return f"{self.username} {self.method} {self.endpoint} {self.response_status} @ {self.timestamp}"
