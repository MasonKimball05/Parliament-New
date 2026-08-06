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

    # Flags that should default to DISABLED if they don't exist.
    #
    # ⚠️ v3.19.1 — `cnb_foreword` IS LOAD-BEARING HERE, NOT TIDINESS.
    # Everything else in the C&B set (`cnb_constitution`, `cnb_bylaws`,
    # `cnb_appendix`) is governance already in force, so the normal fail-OPEN
    # default is correct: a database with no flag rows should show the
    # Constitution, not hide it. The Foreword is the opposite case — it is
    # UNPASSED text seeded ahead of the vote, and fail-open would publish it to
    # the whole chapter the moment anyone deployed without running
    # `seed_feature_flags`. This one line is what makes `GoverningDocument.enabled()`
    # fail closed. Do not remove it, and see that method for the other half.
    DISABLED_BY_DEFAULT = ['maintenance_mode', 'cnb_foreword']

    @classmethod
    def is_feature_enabled(cls, feature_name):
        """
        Check if a feature is enabled
        Usage: FeatureFlag.is_feature_enabled('voting_system')

        NOTE the fail-OPEN default below, and that it is the opposite of how
        templates behave: `{% if feature_flags.x %}` resolves a missing flag to
        '' (falsy), so an unseeded flag is invisible in a template and enabled
        in Python. Dev mode records which of the three branches produced each
        answer precisely because that asymmetry is invisible otherwise — it cost
        a day of debugging on the calendar Subscribe button (07-25-26).
        """
        from django.core.cache import cache
        from src.dev_mode import record_flag

        # v3.17.1: cached. This is called from @require_feature_flag on a large
        # fraction of views and repeatedly within a single view — the admin-v2
        # dashboard alone asked for 'push_notifications_enabled' five times per
        # page load, five identical uncached `objects.get`s. v3.17.3: invalidated
        # by post_save/post_delete (see the bottom of this module), so a toggle
        # OR a bulk delete in the admin takes effect at once; the TTL is only a
        # backstop for writes no signal sees (raw SQL, `queryset.update()`, a
        # restored dump).
        cache_key = cls._cache_key(feature_name)
        cached = cache.get(cache_key)
        if cached is not None:
            record_flag(feature_name, cached['result'], cached['source'] + ' (cached)')
            return cached['result']

        try:
            flag = cls.objects.get(name=feature_name)
            result, source = flag.is_enabled, 'db row'
        except cls.DoesNotExist:
            # Some flags should default to disabled for safety
            if feature_name in cls.DISABLED_BY_DEFAULT:
                result, source = False, 'no row → DISABLED_BY_DEFAULT'
            else:
                # Default to enabled if flag doesn't exist
                result, source = True, 'no row → fail-open default'

        cache.set(cache_key, {'result': result, 'source': source}, cls.CACHE_TTL)
        record_flag(feature_name, result, source)
        return result

    CACHE_TTL = 300  # seconds; correctness comes from invalidation, not expiry

    @classmethod
    def _cache_key(cls, feature_name):
        return f'feature_flag:{feature_name}'

    @classmethod
    def invalidate_cache(cls, feature_name=None):
        """
        Drop cached lookups. Called from the post_save/post_delete receivers at
        the bottom of this module; also useful from tests and from any
        management command that writes flags outside the ORM.
        """
        from django.core.cache import cache
        if feature_name is not None:
            cache.delete(cls._cache_key(feature_name))
            return
        cache.delete_many([cls._cache_key(n) for n in cls.objects.values_list('name', flat=True)])

    # v3.17.3: invalidation moved from save()/delete() overrides to post_save /
    # post_delete signals at the bottom of this module. See the note there —
    # `queryset.delete()`, which is what the Django admin's "Delete selected"
    # action calls, never invokes Model.delete(), so a flag deleted from the
    # changelist kept answering from cache on every worker until the TTL
    # expired. Signals fire for both paths.


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

    CACHE_TTL = 300  # correctness comes from invalidation, not expiry

    @classmethod
    def _cache_key(cls, url_name):
        return f'page_toggle:{url_name}'

    @classmethod
    def invalidate_cache(cls, url_name=None):
        from django.core.cache import cache
        if url_name is not None:
            cache.delete(cls._cache_key(url_name))
            return
        cache.delete_many([
            cls._cache_key(n) for n in cls.objects.values_list('url_name', flat=True)
        ])

    @classmethod
    def is_page_enabled(cls, url_name):
        """
        Check if a page is enabled
        Usage: PageToggle.is_page_enabled('home')

        v3.17.2: cached, for the same reason FeatureFlag.is_feature_enabled is.
        `@require_page_enabled` decorates a large number of views, so this was an
        uncached `objects.get` on essentially every page load. v3.17.3:
        invalidated by post_save/post_delete — see the bottom of this module.
        """
        from django.core.cache import cache

        cache_key = cls._cache_key(url_name)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached['result']

        try:
            toggle = cls.objects.get(url_name=url_name)
            result = toggle.is_enabled
        except cls.DoesNotExist:
            # Default to enabled if toggle doesn't exist
            result = True

        cache.set(cache_key, {'result': result}, cls.CACHE_TTL)
        return result

    # v3.17.3: invalidation moved to post_save / post_delete signals — see the
    # note at the bottom of this module.


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

    CACHE_TTL = 300  # correctness comes from invalidation, not expiry

    @classmethod
    def _cache_key(cls, key):
        return f'site_setting:{key}'

    @classmethod
    def invalidate_cache(cls, key=None):
        """
        Drop cached lookups. Called from the post_save/post_delete receivers at
        the bottom of this module, and directly from any path that writes rows
        without going through Model.save() — `_seed_site_settings`' bulk_create
        in admin_v2 is the one such caller today.
        """
        from django.core.cache import cache
        if key is not None:
            cache.delete(cls._cache_key(key))
            return
        cache.delete_many([cls._cache_key(k) for k in cls.objects.values_list('key', flat=True)])

    @classmethod
    def get_setting(cls, key, default=None):
        """
        Get a setting value by key
        Usage: SiteSetting.get_setting('chat_active_poll_interval', 3000)

        v3.18.7: cached, for exactly the reason `FeatureFlag.is_feature_enabled`
        was cached in v3.17.1 — this is a plain `objects.get` and one of its
        callers is `Enforce2FAMiddleware`, which runs on every authenticated
        request. The two classes have always answered the same shape of question
        (look up a row by key, return its value) and only one of them had a
        cache; that asymmetry was the whole of the 08-05 finding.

        ⚠️ The cache stores whether the ROW EXISTED, not the default. Callers
        pass different defaults for the same key, so caching a miss as its
        default would let the first caller's fallback leak into the second's.
        `found` is what keeps a stored None distinguishable from a missing row.
        """
        from django.core.cache import cache

        cache_key = cls._cache_key(key)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached['value'] if cached['found'] else default

        try:
            setting = cls.objects.get(key=key)
            payload = {'found': True, 'value': setting.get_value()}
        except cls.DoesNotExist:
            payload = {'found': False, 'value': None}

        cache.set(cache_key, payload, cls.CACHE_TTL)
        return payload['value'] if payload['found'] else default

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


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------
#
# v3.17.3. `FeatureFlag.is_feature_enabled` and `PageToggle.is_page_enabled`
# are cached (v3.17.1 / v3.17.2) and correctness comes from invalidating on
# write, not from the 300 s TTL. Until now that invalidation lived in `save()`
# and `delete()` overrides on each model — which covers the ORM's per-object
# path and misses the bulk one:
#
#     FeatureFlag.objects.filter(...).delete()   # never calls Model.delete()
#
# and that bulk path is exactly what the Django admin's "Delete selected"
# action uses. So deleting a flag from the changelist left every worker
# answering from a cache entry for a row that no longer existed, for up to five
# minutes, with no way to tell from the admin that anything was stale. Given
# this codebase's history with flags failing open in Python and closed in
# templates (07-25-26), a five-minute window where a *deleted* flag still gates
# is worth closing properly.
#
# post_save / post_delete fire for both paths, so the signals below are a
# superset of what the overrides did. `invalidate_cache()` remains a public
# classmethod for the cases signals still cannot see — raw SQL, a restored
# dump, `queryset.update()` — where the TTL is the only other backstop.
#
# `context_feature_flags` is the template-facing dict built by the
# feature_flags context processor; it has to be dropped alongside the
# per-flag entries or Python and templates disagree for the length of its TTL.
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver


def _drop_context_dict():
    from django.core.cache import cache
    cache.delete('context_feature_flags')


@receiver(post_save, sender=FeatureFlag)
@receiver(post_delete, sender=FeatureFlag)
def _invalidate_feature_flag_cache(sender, instance, **kwargs):
    FeatureFlag.invalidate_cache(instance.name)
    _drop_context_dict()


@receiver(post_save, sender=PageToggle)
@receiver(post_delete, sender=PageToggle)
def _invalidate_page_toggle_cache(sender, instance, **kwargs):
    PageToggle.invalidate_cache(instance.url_name)
    _drop_context_dict()


# v3.18.7: SiteSetting joins them. No `_drop_context_dict()` here — settings are
# not part of the `feature_flags` context dict, so there is nothing template-side
# to keep in step. `set_setting` goes through `save()`, and the admin's own edit
# path (`admin_v2.py:928`) does too, so both are covered. The one write that
# signals cannot see is `_seed_site_settings`' `bulk_create`, which calls
# `invalidate_cache()` itself.
@receiver(post_save, sender=SiteSetting)
@receiver(post_delete, sender=SiteSetting)
def _invalidate_site_setting_cache(sender, instance, **kwargs):
    SiteSetting.invalidate_cache(instance.key)
