from django.db import models
from django.conf import settings
from django.utils import timezone


class WebAuthnCredential(models.Model):
    """
    Stores a registered passkey (WebAuthn credential) for a user.

    A user may have multiple credentials (e.g. phone + laptop). Each credential
    is identified by a unique credential_id issued by the authenticator.

    On successful passkey authentication the user is fully logged in and treated
    as 2FA-verified — no separate TOTP step required.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='webauthn_credentials',
    )
    # Raw bytes as returned by py_webauthn — typically 16–1024 bytes
    credential_id = models.BinaryField(unique=True)
    public_key = models.BinaryField()
    sign_count = models.PositiveIntegerField(default=0)
    # Human-readable name the user assigns at registration time (e.g. "iPhone 15")
    name = models.CharField(max_length=100, default='Passkey')
    # AAGUID identifies the authenticator model (informational only)
    aaguid = models.CharField(max_length=36, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['created_at']
        verbose_name = 'WebAuthn Credential'
        verbose_name_plural = 'WebAuthn Credentials'

    def __str__(self):
        return f'{self.user.username} — {self.name} ({self.created_at.date()})'

    def mark_used(self, new_sign_count):
        self.sign_count = new_sign_count
        self.last_used_at = timezone.now()
        self.save(update_fields=['sign_count', 'last_used_at'])
