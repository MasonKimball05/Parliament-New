"""
Calendar Subscription Model
Allows users to subscribe to their personal event calendar feed
"""
from django.db import models
from django.conf import settings
import secrets


class CalendarSubscription(models.Model):
    """
    Stores unique subscription tokens for each user's calendar feed.
    This allows users to subscribe to their event calendar in external apps
    (Google Calendar, Apple Calendar, Outlook, etc.)
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='calendar_subscription'
    )
    token = models.CharField(
        max_length=64,
        unique=True,
        db_index=True,
        help_text='Unique token for calendar subscription URL'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_accessed = models.DateTimeField(null=True, blank=True)
    access_count = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'calendar_subscriptions'
        verbose_name = 'Calendar Subscription'
        verbose_name_plural = 'Calendar Subscriptions'

    def __str__(self):
        return f'Calendar subscription for {self.user.get_display_name()}'

    @classmethod
    def get_or_create_for_user(cls, user):
        """Get existing subscription or create a new one"""
        subscription, created = cls.objects.get_or_create(
            user=user,
            defaults={'token': cls.generate_token()}
        )
        return subscription

    @classmethod
    def generate_token(cls):
        """Generate a secure random token"""
        return secrets.token_urlsafe(48)

    def regenerate_token(self):
        """Regenerate the subscription token (use when compromised)"""
        self.token = self.generate_token()
        self.save()
        return self.token

    def record_access(self):
        """Record that the feed was accessed"""
        from django.utils import timezone
        self.last_accessed = timezone.now()
        self.access_count += 1
        self.save(update_fields=['last_accessed', 'access_count'])
