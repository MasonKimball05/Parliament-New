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
