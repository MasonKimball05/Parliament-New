import logging
from django.db import models
from django.conf import settings
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

logger = logging.getLogger('function_calls')


class ActivityLog(models.Model):
    """
    Comprehensive activity logging for audit trails and security monitoring
    """
    ACTION_CATEGORIES = (
        ('auth', 'Authentication'),
        ('legislation', 'Legislation'),
        ('vote', 'Voting'),
        ('committee', 'Committee'),
        ('document', 'Document'),
        ('announcement', 'Announcement'),
        ('event', 'Event'),
        ('user', 'User Management'),
        ('settings', 'Settings'),
        ('kai', 'Kai Committee'),
        ('other', 'Other'),
    )

    ACTION_TYPES = (
        # Authentication
        ('login', 'User Login'),
        ('logout', 'User Logout'),
        ('login_failed', 'Failed Login Attempt'),
        ('password_changed', 'Password Changed'),
        ('password_reset', 'Password Reset'),

        # Legislation
        ('legislation_created', 'Legislation Created'),
        ('legislation_edited', 'Legislation Edited'),
        ('legislation_deleted', 'Legislation Deleted'),
        ('legislation_reopened', 'Legislation Reopened'),
        ('vote_ended', 'Vote Ended'),

        # Voting
        ('vote_cast', 'Vote Cast'),
        ('vote_changed', 'Vote Changed'),

        # Committee
        ('committee_member_added', 'Committee Member Added'),
        ('committee_member_removed', 'Committee Member Removed'),
        ('committee_vote_created', 'Committee Vote Created'),
        ('committee_document_uploaded', 'Committee Document Uploaded'),
        ('committee_document_deleted', 'Committee Document Deleted'),
        ('committee_document_published', 'Committee Document Published to Chapter'),

        # Documents
        ('document_uploaded', 'Document Uploaded'),
        ('document_downloaded', 'Document Downloaded'),
        ('document_deleted', 'Document Deleted'),
        ('document_viewed', 'Document Viewed'),

        # Announcements
        ('announcement_created', 'Announcement Created'),
        ('announcement_edited', 'Announcement Edited'),
        ('announcement_deleted', 'Announcement Deleted'),
        ('announcement_toggled', 'Announcement Status Toggled'),

        # Events
        ('event_created', 'Event Created'),
        ('event_edited', 'Event Edited'),
        ('event_deleted', 'Event Deleted'),
        ('attendance_taken', 'Attendance Taken'),

        # User Management
        ('user_created', 'User Created'),
        ('user_edited', 'User Profile Edited'),
        ('user_role_changed', 'User Role Changed'),
        ('login_as_user', 'Admin Logged In As User'),
        ('profile_updated', 'Profile Updated'),
        ('profile_picture_changed', 'Profile Picture Changed'),

        # Settings
        ('preferences_updated', 'Preferences Updated'),
        ('settings_changed', 'System Settings Changed'),

        # Kai Committee
        ('kai_action', 'Kai Report Action'),

        # Pledge
        ('pledge_login', 'Pledge Login'),
        ('pledge_password_changed', 'Pledge Password Changed'),

        # Other
        ('other', 'Other Action'),
        ('bug_report_submitted', 'Bug Report Submitted'),
        ('email_sent', 'Email Sent'),
    )

    # Core fields
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='activity_logs',
        help_text='User who performed the action (null for system actions)'
    ) # Yeehaw God bless America
    action_category = models.CharField(max_length=20, choices=ACTION_CATEGORIES)
    action_type = models.CharField(max_length=50, choices=ACTION_TYPES)
    description = models.TextField(help_text='Human-readable description of the action')

    # Context fields
    ip_address = models.GenericIPAddressField(null=True, blank=True, help_text='IP address of the user')
    user_agent = models.CharField(max_length=500, blank=True, help_text='Browser/device information')

    # Related objects (optional)
    object_type = models.CharField(max_length=100, blank=True, help_text='Type of object affected (e.g., Legislation, User)')
    # v3.17.3: was an IntegerField, and that was a latent login outage.
    #
    # Most models here have integer primary keys, but ParliamentUser's pk is
    # `user_id`, a CharField. ~17 call sites log a user pk into this column —
    # including `signals.log_successful_login`, which runs on EVERY login — so
    # any member whose user_id is not purely numeric raised
    # `ValueError: Field 'object_id' expected a number but got 'ab-12'`,
    # inside the login signal, breaking the login itself. The chapter's ids
    # happen to be numeric today, which is the only reason this has not fired
    # in production; it is one non-numeric member id away from doing so, and it
    # is what kept 8 tests in test_pledge_permissions red.
    #
    # A CharField holds both kinds of pk. Nothing filters or joins on this
    # column — it is read only by the admin detail page and the activity-log
    # CSV export — so widening it is display-compatible: integers render the
    # same as before.
    object_id = models.CharField(
        max_length=64, null=True, blank=True,
        help_text='ID of the affected object (string — user pks are not integers)',
    )
    object_repr = models.CharField(max_length=500, blank=True, help_text='String representation of the affected object')

    # Additional data
    metadata = models.JSONField(null=True, blank=True, help_text='Additional data about the action (JSON)')

    # Timestamp
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Activity Log'
        verbose_name_plural = 'Activity Logs'
        indexes = [
            models.Index(fields=['-timestamp']),
            models.Index(fields=['user', '-timestamp']),
            models.Index(fields=['action_category', '-timestamp']),
            models.Index(fields=['action_type', '-timestamp']),
        ]

    def __str__(self):
        user_name = self.user.name if self.user else 'System'
        return f"{user_name} - {self.get_action_type_display()} - {self.timestamp.strftime('%Y-%m-%d %H:%M')}"

    @classmethod
    def log_activity(cls, action_type, user=None, description='', ip_address=None, user_agent='',
                     object_type='', object_id=None, object_repr='', metadata=None, request=None):
        """
        Helper method to create an activity log entry

        Usage:
            ActivityLog.log_activity(
                action_type='login',
                user=request.user,
                description='User logged in successfully',
                request=request
            )
        """
        # Determine category from action_type
        category_map = {
            'login': 'auth', 'logout': 'auth', 'login_failed': 'auth',
            'password_changed': 'auth', 'password_reset': 'auth',
            'legislation_created': 'legislation', 'legislation_edited': 'legislation',
            'legislation_deleted': 'legislation', 'legislation_reopened': 'legislation',
            'vote_ended': 'legislation', 'vote_cast': 'vote', 'vote_changed': 'vote',
            'committee_member_added': 'committee', 'committee_member_removed': 'committee',
            'committee_vote_created': 'committee', 'committee_document_uploaded': 'committee',
            'committee_document_deleted': 'committee', 'committee_document_published': 'committee',
            'document_uploaded': 'document', 'document_downloaded': 'document',
            'document_deleted': 'document', 'document_viewed': 'document',
            'announcement_created': 'announcement', 'announcement_edited': 'announcement',
            'announcement_deleted': 'announcement', 'announcement_toggled': 'announcement',
            'event_created': 'event', 'event_edited': 'event',
            'event_deleted': 'event', 'attendance_taken': 'event',
            'user_created': 'user', 'user_edited': 'user',
            'user_role_changed': 'user', 'login_as_user': 'user',
            'profile_updated': 'user', 'profile_picture_changed': 'user',
            'preferences_updated': 'settings', 'settings_changed': 'settings',
            'bug_report_submitted': 'other', 'email_sent': 'other',
            'kai_action': 'kai',
        }
        action_category = category_map.get(action_type, 'other')

        # Extract IP and user agent from request if provided
        if request:
            if not ip_address:
                # Check X-Forwarded-For header first (for requests behind proxy/load balancer)
                x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
                if x_forwarded_for:
                    # Take the rightmost IP — nginx appends the real client IP there.
                    ip_address = x_forwarded_for.split(',')[-1].strip()
                else:
                    ip_address = request.META.get('REMOTE_ADDR')
            if not user_agent:
                user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]

        return cls.objects.create(
            user=user,
            action_category=action_category,
            action_type=action_type,
            description=description,
            ip_address=ip_address,
            user_agent=user_agent,
            object_type=object_type,
            object_id=object_id,
            object_repr=object_repr,
            metadata=metadata
        )


@receiver(post_save)
def log_model_save(sender, instance, created, **kwargs):
    """Enhanced logging for model save events"""
    if sender.__module__.startswith('django.'):
        return

    action = 'CREATE' if created else 'UPDATE'
    model_name = sender.__name__

    # Build detailed log information
    details = {
        'model': model_name,
        'instance_id': str(instance.pk),
    }

    # Add model-specific details
    if hasattr(instance, 'title'):
        details['title'] = instance.title
    elif hasattr(instance, 'name'):
        details['name'] = instance.name

    # Get user information if available from thread-local storage or instance
    user_info = 'System'
    if hasattr(instance, 'posted_by'):
        user_info = str(instance.posted_by)
    elif hasattr(instance, 'uploaded_by'):
        user_info = str(instance.uploaded_by)

    # Format log entry
    from src.logging_utils import LogContext
    log_entry = LogContext.format_log_entry(
        user=user_info,
        action=action,
        resource_type=model_name,
        resource_id=instance.pk,
        details=details,
        status='success'
    )
    logger.info(log_entry)


@receiver(post_delete)
def log_model_delete(sender, instance, **kwargs):
    """Enhanced logging for model delete events"""
    if sender.__module__.startswith('django.'):
        return

    model_name = sender.__name__

    # Build detailed log information
    details = {
        'model': model_name,
        'instance_id': str(instance.pk),
    }

    if hasattr(instance, 'title'):
        details['title'] = instance.title
    elif hasattr(instance, 'name'):
        details['name'] = instance.name

    # Get user information if available
    user_info = 'System'
    if hasattr(instance, 'posted_by'):
        user_info = str(instance.posted_by)
    elif hasattr(instance, 'uploaded_by'):
        user_info = str(instance.uploaded_by)

    # Format log entry
    from src.logging_utils import LogContext
    log_entry = LogContext.format_log_entry(
        user=user_info,
        action='DELETE',
        resource_type=model_name,
        resource_id=instance.pk,
        details=details,
        status='success'
    )
    logger.info(log_entry)
