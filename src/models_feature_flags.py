"""
Feature flag system for controlling site functionality
"""
from django.db import models


class FeatureFlag(models.Model):
    """
    Feature flags to enable/disable functionality across the site
    """
    CATEGORY_CHOICES = (
        ('core', 'Core Features'),
        ('voting', 'Voting & Legislation'),
        ('committees', 'Committees'),
        ('events', 'Events & Calendar'),
        ('communications', 'Communications'),
        ('documents', 'Documents'),
        ('admin', 'Admin Features'),
    )

    name = models.CharField(max_length=100, unique=True, help_text='Internal name for the feature')
    display_name = models.CharField(max_length=200, help_text='Display name shown in admin')
    description = models.TextField(help_text='Description of what this feature does')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='core')

    is_enabled = models.BooleanField(default=True, help_text='Whether this feature is currently enabled')

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_toggled_by = models.CharField(max_length=100, blank=True, help_text='User who last toggled this flag')
    last_toggled_at = models.DateTimeField(null=True, blank=True, help_text='When this flag was last toggled')

    class Meta:
        verbose_name = 'Feature Flag'
        verbose_name_plural = 'Feature Flags'
        ordering = ['category', 'display_name']

    def __str__(self):
        status = "✓" if self.is_enabled else "✗"
        return f"{status} {self.display_name}"

    # Flags that should default to DISABLED if they don't exist
    DISABLED_BY_DEFAULT = ['maintenance_mode']

    @classmethod
    def is_feature_enabled(cls, feature_name):
        """
        Check if a feature is enabled
        Usage: FeatureFlag.is_feature_enabled('voting_system')
        """
        try:
            flag = cls.objects.get(name=feature_name)
            return flag.is_enabled
        except cls.DoesNotExist:
            # Some flags should default to disabled for safety
            if feature_name in cls.DISABLED_BY_DEFAULT:
                return False
            # Default to enabled if flag doesn't exist
            return True


class PageToggle(models.Model):
    """
    Toggle entire pages/URLs on or off
    """
    url_name = models.CharField(max_length=100, unique=True, help_text='Django URL name (e.g., "home", "vote")')
    display_name = models.CharField(max_length=200, help_text='Display name for this page')
    description = models.TextField(blank=True, help_text='Description of this page')

    is_enabled = models.BooleanField(default=True, help_text='Whether this page is accessible')

    # Custom message when disabled
    disabled_message = models.TextField(
        default='This page is currently unavailable. Please check back later.',
        help_text='Message shown to users when page is disabled'
    )

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_toggled_by = models.CharField(max_length=100, blank=True)
    last_toggled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Page Toggle'
        verbose_name_plural = 'Page Toggles'
        ordering = ['display_name']

    def __str__(self):
        status = "✓" if self.is_enabled else "✗"
        return f"{status} {self.display_name} ({self.url_name})"

    @classmethod
    def is_page_enabled(cls, url_name):
        """
        Check if a page is enabled
        Usage: PageToggle.is_page_enabled('home')
        """
        try:
            toggle = cls.objects.get(url_name=url_name)
            return toggle.is_enabled
        except cls.DoesNotExist:
            # Default to enabled if toggle doesn't exist
            return True


class SiteSetting(models.Model):
    """
    Configurable site-wide settings (key-value pairs)
    """
    SETTING_TYPE_CHOICES = (
        ('string', 'String'),
        ('integer', 'Integer'),
        ('boolean', 'Boolean'),
        ('json', 'JSON'),
    )

    CATEGORY_CHOICES = (
        ('general', 'General'),
        ('chat', 'Chat Settings'),
        ('notifications', 'Notifications'),
        ('security', 'Security'),
        ('display', 'Display'),
    )

    key = models.CharField(max_length=100, unique=True, help_text='Setting key (internal name)')
    display_name = models.CharField(max_length=200, help_text='Display name shown in admin')
    description = models.TextField(blank=True, help_text='Description of what this setting does')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='general')
    setting_type = models.CharField(max_length=20, choices=SETTING_TYPE_CHOICES, default='string')

    value = models.TextField(help_text='Setting value')
    default_value = models.TextField(help_text='Default value if not set')

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_modified_by = models.CharField(max_length=100, blank=True)

    class Meta:
        verbose_name = 'Site Setting'
        verbose_name_plural = 'Site Settings'
        ordering = ['category', 'display_name']

    def __str__(self):
        return f"{self.display_name}: {self.value}"

    def get_value(self):
        """Get the typed value based on setting_type"""
        if self.setting_type == 'integer':
            try:
                return int(self.value)
            except (ValueError, TypeError):
                return int(self.default_value)
        elif self.setting_type == 'boolean':
            return self.value.lower() in ('true', '1', 'yes', 'on')
        elif self.setting_type == 'json':
            import json
            try:
                return json.loads(self.value)
            except (json.JSONDecodeError, TypeError):
                return json.loads(self.default_value)
        return self.value

    @classmethod
    def get_setting(cls, key, default=None):
        """
        Get a setting value by key
        Usage: SiteSetting.get_setting('chat_active_poll_interval', 3000)
        """
        try:
            setting = cls.objects.get(key=key)
            return setting.get_value()
        except cls.DoesNotExist:
            return default

    @classmethod
    def set_setting(cls, key, value, modified_by=''):
        """
        Set a setting value by key
        """
        try:
            setting = cls.objects.get(key=key)
            setting.value = str(value)
            setting.last_modified_by = modified_by
            setting.save()
            return True
        except cls.DoesNotExist:
            return False


class ScheduledMaintenance(models.Model):
    """
    Schedule planned maintenance windows with user notifications
    """
    title = models.CharField(
        max_length=200,
        default='Scheduled Maintenance',
        help_text='Title shown in the warning banner'
    )
    message = models.TextField(
        default='We will be performing scheduled maintenance. The site may be temporarily unavailable.',
        help_text='Message shown to users before maintenance starts'
    )
    scheduled_start = models.DateTimeField(
        help_text='When maintenance should automatically begin'
    )
    estimated_duration_minutes = models.PositiveIntegerField(
        default=30,
        help_text='Estimated duration in minutes (shown to users)'
    )
    notify_email = models.EmailField(
        blank=True,
        help_text='Email address to notify when maintenance starts (leave blank to skip)'
    )

    # Status tracking
    is_active = models.BooleanField(
        default=True,
        help_text='Whether this scheduled maintenance is active (uncheck to cancel)'
    )
    maintenance_started = models.BooleanField(
        default=False,
        help_text='Whether maintenance has been triggered'
    )
    started_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When maintenance actually started'
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When maintenance was completed'
    )
    email_sent = models.BooleanField(
        default=False,
        help_text='Whether the notification email was sent'
    )

    # Metadata
    created_by = models.ForeignKey(
        'src.ParliamentUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='scheduled_maintenances'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Scheduled Maintenance'
        verbose_name_plural = 'Scheduled Maintenances'
        ordering = ['-scheduled_start']

    def __str__(self):
        from django.utils import timezone
        status = ""
        if self.completed_at:
            status = "✓ Completed"
        elif self.maintenance_started:
            status = "🔧 In Progress"
        elif not self.is_active:
            status = "✗ Cancelled"
        elif self.scheduled_start <= timezone.now():
            status = "⏰ Pending Start"
        else:
            status = "📅 Scheduled"
        from django.utils.timezone import localtime
        return f"{status} - {self.title} ({localtime(self.scheduled_start).strftime('%Y-%m-%d %H:%M %Z')})"

    @property
    def time_until_start(self):
        """Returns human-readable time until maintenance starts"""
        from django.utils import timezone
        if self.maintenance_started or self.completed_at:
            return None
        delta = self.scheduled_start - timezone.now()
        if delta.total_seconds() <= 0:
            return "Starting soon"

        hours, remainder = divmod(delta.total_seconds(), 3600)
        minutes, _ = divmod(remainder, 60)

        if hours > 24:
            days = int(hours // 24)
            return f"{days} day{'s' if days != 1 else ''}"
        elif hours > 0:
            return f"{int(hours)}h {int(minutes)}m"
        else:
            return f"{int(minutes)} minute{'s' if minutes != 1 else ''}"

    @property
    def estimated_end_time(self):
        """Calculate estimated end time"""
        from datetime import timedelta
        return self.scheduled_start + timedelta(minutes=self.estimated_duration_minutes)

    @classmethod
    def get_upcoming_maintenance(cls):
        """Get the next active scheduled maintenance that hasn't started yet"""
        from django.utils import timezone
        return cls.objects.filter(
            is_active=True,
            maintenance_started=False,
            completed_at__isnull=True,
            scheduled_start__gt=timezone.now() - timezone.timedelta(hours=1)  # Include recently passed
        ).order_by('scheduled_start').first()

    @classmethod
    def get_pending_maintenance(cls):
        """Get maintenance that should start now"""
        from django.utils import timezone
        return cls.objects.filter(
            is_active=True,
            maintenance_started=False,
            completed_at__isnull=True,
            scheduled_start__lte=timezone.now()
        ).order_by('scheduled_start').first()

    def start_maintenance(self):
        """Start the maintenance - enable maintenance mode and send notification"""
        from django.utils import timezone
        from django.core.cache import cache

        # Enable maintenance mode flag
        flag, created = FeatureFlag.objects.get_or_create(
            name='maintenance_mode',
            defaults={
                'display_name': 'Maintenance Mode',
                'description': 'Put site in maintenance mode - blocks all non-admin users',
                'category': 'admin',
                'is_enabled': True,
            }
        )
        if not created:
            flag.is_enabled = True
            flag.last_toggled_by = 'Scheduled Maintenance'
            flag.last_toggled_at = timezone.now()
            flag.save()

        # Set maintenance start time in cache
        cache.set('maintenance_mode_started_at', timezone.now(), 86400)
        cache.set('maintenance_blocked_count', 0, 86400)

        # Update this record
        self.maintenance_started = True
        self.started_at = timezone.now()
        self.save()

        # Send notification email
        if self.notify_email and not self.email_sent:
            self._send_start_notification()

        return True

    def _send_start_notification(self):
        """Send email notification that maintenance has started"""
        from django.core.mail import send_mail
        from django.conf import settings
        from django.utils.timezone import localtime

        try:
            subject = f"[Parliament] Maintenance Started: {self.title}"
            message = f"""
Scheduled maintenance has automatically started.

Title: {self.title}
Started at: {localtime(self.started_at).strftime('%Y-%m-%d %H:%M:%S %Z') if self.started_at else 'Now'}
Estimated duration: {self.estimated_duration_minutes} minutes

Message shown to users:
{self.message}

---
To end maintenance mode, go to the Django admin and disable the maintenance_mode feature flag,
or mark this scheduled maintenance as completed.

This is an automated message from Parliament.
            """

            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[self.notify_email],
                fail_silently=True,
            )
            self.email_sent = True
            self.save(update_fields=['email_sent'])
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Failed to send maintenance notification email: {e}")

    def complete_maintenance(self):
        """Mark maintenance as complete and disable maintenance mode"""
        from django.utils import timezone
        from django.core.cache import cache

        # Disable maintenance mode
        try:
            flag = FeatureFlag.objects.get(name='maintenance_mode')
            flag.is_enabled = False
            flag.last_toggled_by = 'Scheduled Maintenance (Completed)'
            flag.last_toggled_at = timezone.now()
            flag.save()
        except FeatureFlag.DoesNotExist:
            pass

        # Clear cache
        cache.delete('maintenance_mode_started_at')
        cache.delete('maintenance_blocked_count')

        # Update record
        self.completed_at = timezone.now()
        self.save()
