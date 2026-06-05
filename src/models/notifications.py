from django.db import models
from django.conf import settings
from src.models.users import ParliamentUser


class Notification(models.Model):
    """In-app notifications for users"""
    NOTIFICATION_TYPES = (
        ('announcement', 'Announcement'),
        ('legislation_new', 'New Legislation'),
        ('vote_ended', 'Vote Ended'),
        ('event_new', 'New Event'),
        ('chat_mention', 'Chat Mention'),
    )

    recipient = models.ForeignKey(ParliamentUser, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    title = models.CharField(max_length=255)
    message = models.TextField(blank=True)
    link = models.CharField(max_length=500, blank=True, help_text='URL to navigate to when clicked')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    # Generic reference to the source object
    source_type = models.CharField(max_length=50, blank=True, help_text='Model name of source object')
    source_id = models.IntegerField(null=True, blank=True, help_text='PK of source object')

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'is_read', '-created_at']),
        ]

    def __str__(self):
        return f"{self.notification_type}: {self.title} → {self.recipient.name}"


class NotificationSchedule(models.Model):
    """
    Configurable notification schedules for automated reminders.
    Allows officers to set up recurring notifications for events, votes, etc.
    """
    NOTIFICATION_TYPE_CHOICES = (
        ('event_reminder', 'Event Reminder'),
        ('vote_reminder', 'Vote Reminder'),
        ('attendance_reminder', 'Attendance Reminder'),
        ('dues_reminder', 'Dues Reminder'),
        ('custom', 'Custom Notification'),
    )

    TARGET_AUDIENCE_CHOICES = (
        ('all_active', 'All Active Members'),
        ('all_members', 'All Members (including Alumni)'),
        ('officers', 'Officers Only'),
        ('pledges', 'Pledges Only'),
        ('committee', 'Specific Committee'),
        ('custom', 'Custom Selection'),
    )

    name = models.CharField(max_length=100, help_text="Name for this notification schedule")
    notification_type = models.CharField(max_length=50, choices=NOTIFICATION_TYPE_CHOICES)
    description = models.TextField(blank=True, help_text="Description of this notification schedule")

    # Timing
    hours_before = models.IntegerField(
        default=24,
        help_text="Hours before the event/deadline to send notification"
    )
    send_at_time = models.TimeField(
        null=True,
        blank=True,
        help_text="Specific time of day to send (optional)"
    )

    # Delivery channels
    send_email = models.BooleanField(default=True, help_text="Send via email")
    send_in_app = models.BooleanField(default=True, help_text="Send as in-app notification")

    # Target audience
    target_audience = models.CharField(
        max_length=20,
        choices=TARGET_AUDIENCE_CHOICES,
        default='all_active'
    )
    target_committee = models.ForeignKey(
        'Committee',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notification_schedules',
        help_text="If target is 'committee', which committee"
    )

    # Message template
    message_template = models.TextField(
        help_text="Message template. Use {event_name}, {event_date}, {event_time}, {event_location} as placeholders"
    )
    email_subject_template = models.CharField(
        max_length=200,
        blank=True,
        help_text="Email subject template (if different from name)"
    )

    # Status
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_notification_schedules'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['notification_type', 'name']
        verbose_name = 'Notification Schedule'
        verbose_name_plural = 'Notification Schedules'

    def __str__(self):
        return f"{self.name} ({self.get_notification_type_display()})"


class NotificationLog(models.Model):
    """
    Log of sent notifications for tracking and analytics.
    """
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    )

    schedule = models.ForeignKey(
        NotificationSchedule,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='logs'
    )
    notification_type = models.CharField(max_length=50)
    title = models.CharField(max_length=255)
    message = models.TextField()

    # Delivery info
    sent_via_email = models.BooleanField(default=False)
    sent_via_in_app = models.BooleanField(default=False)
    recipient_count = models.IntegerField(default=0)
    successful_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)

    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True)

    # Related object
    related_object_type = models.CharField(max_length=50, blank=True)
    related_object_id = models.IntegerField(null=True, blank=True)

    # Timestamps
    scheduled_for = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Notification Log'
        verbose_name_plural = 'Notification Logs'
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['notification_type', '-created_at']),
        ]

    def __str__(self):
        return f"{self.title} - {self.status} ({self.created_at.strftime('%Y-%m-%d %H:%M')})"


class PushSubscription(models.Model):
    """
    Stores a Web Push subscription for one browser/device.
    One user can have multiple subscriptions (phone + laptop, etc.).
    Created by the subscribe endpoint; deleted on unsubscribe or when
    a push send returns a 410 Gone (subscription expired).
    """
    user = models.ForeignKey(ParliamentUser, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint = models.TextField(unique=True)
    p256dh = models.TextField()   # public key
    auth = models.TextField()     # auth secret
    user_agent = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Push Subscription'
        verbose_name_plural = 'Push Subscriptions'

    def __str__(self):
        return f"{self.user} — {self.endpoint[:60]}…"

    def as_subscription_info(self):
        """Return the dict shape pywebpush expects."""
        return {
            'endpoint': self.endpoint,
            'keys': {'p256dh': self.p256dh, 'auth': self.auth},
        }
