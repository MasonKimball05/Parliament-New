from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.conf import settings
from src.storage import DualLocationStorage
from src.encrypted_fields import EncryptedCharField, EncryptedEmailField
import os

logger = logging.getLogger('function_calls')

def validate_profile_picture(file):
    """Validate profile picture file type and size"""
    # Allowed MIME types for profile pictures
    allowed_types = [
        'image/jpeg',
        'image/jpg',
        'image/png',
        'image/webp',
    ]

    # Max file size: 5 MB
    max_size = 5 * 1024 * 1024

    if file.size > max_size:
        raise ValidationError(f'Profile picture file size cannot exceed 5 MB. Current size: {file.size / (1024 * 1024):.2f} MB')

    # Check MIME type
    file_type = getattr(file, 'content_type', None)
    if file_type and file_type not in allowed_types:
        raise ValidationError(f'Invalid file type. Only JPEG, PNG, and WebP images are allowed.')

    # Additional check: verify file extension
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.webp']:
        raise ValidationError(f'Invalid file extension. Only .jpg, .jpeg, .png, and .webp are allowed.')

class ParliamentUserManager(BaseUserManager):
    def create_user(self, user_id, name, username, member_type, password=None):
        if not user_id:
            raise ValueError('Users must have an ID')
        if not username:
            raise ValueError('Users must have an username')
        user = self.model(user_id=user_id, name=name, member_type=member_type)
        user.username = name  # Set username as name by default
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, user_id, name, username, member_type, password):
        user = self.create_user(user_id, name, username, member_type, password)
        user.is_admin = True
        user.save(using=self._db)
        return user

class ActiveUserManager(ParliamentUserManager):
    def get_queryset(self):
        return super().get_queryset().filter(member_status='Active')

class Role(models.Model):
    # Hard-coded VP roles (ID, Code, Name)
    # These are the canonical source of truth for roles in the system
    DEFAULT_ROLES = [
        (1, 'President', 'President'),
        (2, 'EVP', 'Executive Vice President'),
        (3, 'VPB', 'Vice President of Brotherhood'),
        (4, 'VPRM', 'Vice President of Risk Management'),
        (5, 'VPE', 'Vice President of Education'),
        (6, 'VPR', 'Vice President of Recruitment'),
        (7, 'VPP', 'Vice President of Programming'),
        (8, 'VPF', 'Vice President of Finance'),
        (9, 'VPA', 'Vice President of Administration'),
    ]

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True)

    one_per_chapter = models.BooleanField(default=False)

    def __str__(self):
        return self.name

class ParliamentUser(AbstractBaseUser):
    MEMBER_TYPES = (
        ('Member', 'Member'),
        ('Chair', 'Chair'),
        ('Officer', 'Officer'),
        ('Advisor', 'Advisor'),
        ('Pledge', 'Pledge'),
    )
    MEMBER_STATUS = (
        ('Active', 'Active'),
        ('Inactive', 'Inactive'),
        ('Alumni', 'Alumni'),
    )

    user_id = models.CharField(max_length=30, unique=True, primary_key=True)
    name = models.CharField(max_length=100)
    preferred_name = models.CharField(max_length=50, blank=True, help_text='Optional: Preferred first name (will display as "Preferred LastName")')
    member_type = models.CharField(max_length=20, choices=MEMBER_TYPES)
    is_active = models.BooleanField(default=True)
    is_admin = models.BooleanField(default=False)
    username = models.CharField(max_length=100, unique=True, help_text='Username for login (not encrypted - needed for authentication lookups)')
    email = models.EmailField(max_length=254, blank=True, null=True, unique=True, help_text='Email address for password reset and notifications')
    phone_number = models.CharField(max_length=20, blank=True, help_text='Optional phone number for directory listing')
    profile_picture = models.ImageField(
        upload_to='profile_pictures/',
        blank=True,
        null=True,
        validators=[validate_profile_picture],
        help_text='Profile picture (JPEG, PNG, or WebP, max 5MB)'
    )
    profile_picture_removed_by_admin = models.BooleanField(
        default=False,
        help_text='Flag indicating if profile picture was removed by an admin'
    )
    anonymous_vote = models.BooleanField(default=False)
    allow_abstain = models.BooleanField(default=True)
    roles = models.ManyToManyField(Role, blank=True)

    member_status = models.CharField(max_length=20, choices=MEMBER_STATUS, default='Active')
    force_password_change = models.BooleanField(default=False, help_text='User must change password on next login')
    role_number = models.CharField(
        max_length=30,
        unique=True,
        blank=True,
        null=True,
        help_text='Member roll number assigned at initiation (unique identifier visible to members)'
    )

    objects = ParliamentUserManager()
    active = ActiveUserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['name', 'member_type', 'user_id']

    def __str__(self):
        return f'{self.name} ({self.member_type})'

    def has_perm(self, perm, obj=None):
        return self.is_admin

    def has_module_perms(self, app_label):
        return self.is_admin

    @property
    def is_staff(self):
        return self.is_admin

    @property
    def is_officer(self):
        """Check if user is an officer based on member_type"""
        return self.member_type == 'Officer' or self.is_admin

    @property
    def is_advisor(self):
        """Check if user is an advisor"""
        return self.member_type == 'Advisor'

    @property
    def is_pledge(self):
        """Check if user is a pledge"""
        return self.member_type == 'Pledge'

    @property
    def can_vote(self):
        """Check if user is allowed to vote (excludes pledges)"""
        return self.member_type in ['Member', 'Chair', 'Officer'] and not self.is_pledge

    @property
    def can_view_officer_pages(self):
        """Check if user can view officer pages (Officers, Chairs, and Advisors)"""
        return self.is_officer or self.is_advisor or self.member_type == 'Chair'

    @property
    def can_manage_events(self):
        """Check if user can create/manage events (Officers and Chairs)"""
        return self.is_officer or self.member_type == 'Chair'

    def get_display_name(self):
        """Returns preferred name + last name if preferred name is set, otherwise full name"""
        if self.preferred_name:
            # Split the full name to get the last name
            name_parts = self.name.split()
            if len(name_parts) > 1:
                last_name = name_parts[-1]
                return f"{self.preferred_name} {last_name}"
            else:
                # If no last name, just return preferred name
                return self.preferred_name
        return self.name

    def has_default_password(self):
        """
        Check if the user's password is still set to a default value.
        Default password pattern: first initial + last name + user_id (lowercase)
        e.g., "Adam C. Boggs" with user_id 69 -> "aboggs69"
        Returns True if password matches any default pattern, False otherwise.
        """
        import re

        # Pattern: first initial + last name + user_id
        if self.name:
            parts = self.name.strip().split()
            if len(parts) >= 1:
                first_initial = parts[0][0].lower() if parts[0] else ''
                last_name = parts[-1].lower() if len(parts) > 1 else parts[0].lower()
                # Remove special characters (periods, commas, etc.)
                clean_last = re.sub(r'[^a-z0-9]', '', last_name)
                base_pattern = first_initial + clean_last

                # Primary pattern: base + user_id (e.g., "aboggs69")
                if self.check_password(base_pattern + str(self.user_id)):
                    return True

                # Also check without user_id
                if self.check_password(base_pattern):
                    return True

                # With "1" suffix
                if self.check_password(base_pattern + '1'):
                    return True

        # User ID alone
        if self.user_id and self.check_password(str(self.user_id)):
            return True

        return False

    class Meta:
        ordering = ['user_id']


class UserPreferences(models.Model):
    """
    User preferences for customizing their Parliament experience
    """
    THEME_CHOICES = (
        ('light', 'Light'),
        ('dark', 'Dark'),
        ('auto', 'Auto (System)'),
    )

    user = models.OneToOneField(ParliamentUser, on_delete=models.CASCADE, related_name='preferences', primary_key=True)

    # Theme Preferences
    theme = models.CharField(max_length=10, choices=THEME_CHOICES, default='light')

    # Notification Preferences
    email_announcements = models.BooleanField(default=True, help_text='Receive email notifications for new announcements')
    email_legislation = models.BooleanField(default=True, help_text='Receive email notifications for new legislation')
    email_events = models.BooleanField(default=True, help_text='Receive email notifications for upcoming events')
    email_committee_updates = models.BooleanField(default=True, help_text='Receive email notifications for committee updates')

    # In-app Notification Preferences
    show_announcement_popups = models.BooleanField(default=True, help_text='Show in-app popups for announcements')

    # Display Preferences
    compact_view = models.BooleanField(default=False, help_text='Use compact view for lists and tables')

    # Menu Customization (users can select up to 7 items)
    show_vote_menu = models.BooleanField(default=True, help_text='Show Vote link in navigation menu')
    show_committees_menu = models.BooleanField(default=True, help_text='Show Committees link in navigation menu')
    show_chats_menu = models.BooleanField(default=False, help_text='Show Chats link in navigation menu')
    show_documents_menu = models.BooleanField(default=True, help_text='Show Documents link in navigation menu')
    show_announcements_menu = models.BooleanField(default=True, help_text='Show Announcements link in navigation menu')
    show_calendar_menu = models.BooleanField(default=True, help_text='Show Calendar link in navigation menu')
    show_legislation_menu = models.BooleanField(default=True, help_text='Show Legislation link in navigation menu')
    show_excuses_menu = models.BooleanField(default=False, help_text='Show My Excuses link in navigation menu')
    show_search_menu = models.BooleanField(default=True, help_text='Show Search link in navigation menu')
    show_roberts_rules_menu = models.BooleanField(default=False, help_text='Show Robert\'s Rules link in navigation menu')

    # In-App Notification Preferences
    notify_announcements = models.BooleanField(default=True, help_text='Receive in-app notifications for announcements')
    notify_legislation = models.BooleanField(default=True, help_text='Receive in-app notifications for legislation & voting')
    notify_events = models.BooleanField(default=True, help_text='Receive in-app notifications for new events')
    notify_slating = models.BooleanField(default=True, help_text='Receive in-app notifications for officer elections')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Preferences for {self.user.name}"

    class Meta:
        verbose_name = 'User Preferences'
        verbose_name_plural = 'User Preferences'


# Signal to auto-create UserPreferences when a user is created
@receiver(post_save, sender=ParliamentUser)
def create_user_preferences(sender, instance, created, **kwargs):
    if created:
        UserPreferences.objects.get_or_create(user=instance)


def validate_legislation_file(value):
    """Validates the file extension."""
    if not value.name.endswith('.pdf') and not value.name.endswith('.docx'):
        raise ValidationError('Only PDF and DOCX files are allowed.')

class Legislation(models.Model):
    VOTE_THRESHOLDS = [
        ('51', '51%'),
        ('60', '60%'),
        ('67', '67%'),
        ('75', '75%'),
        ('100', 'Unanimous'),
    ]

    required_percentage = models.CharField(max_length=10, choices=[
        ('51', '51%'),
        ('60', '60%'),
        ('67', '67%'),
        ('75', '75%'),
        ('100', 'Unanimous')
    ], default='51')

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('passed', 'Passed'),
        ('removed', 'Removed'),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField()
    document = models.FileField(upload_to='legislation_docs/', validators=[validate_legislation_file], storage=DualLocationStorage())
    posted_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    available_at = models.DateTimeField(help_text="When the document becomes visible for review")
    voting_starts_at = models.DateTimeField(null=True, blank=True, help_text="When voting opens (defaults to available_at if not set)")
    created_at = models.DateTimeField(auto_now_add=True)
    voting_ends_at = models.DateTimeField(null=True, blank=True, help_text="Optional: When voting should automatically close")
    voting_ended_at = models.DateTimeField(null=True, blank=True)
    passed = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    anonymous_vote = models.BooleanField(default=False)
    allow_abstain = models.BooleanField(default=True)
    voting_closed = models.BooleanField(default=False)
    vote_mode = models.CharField(
        max_length=20,
        choices=[('percentage', 'Percentage'), ('piecewise', 'Piecewise'), ('plurality', 'Plurality')],
        default='percentage',
    )

    required_number = models.PositiveIntegerField(null=True, blank=True)
    plurality_options = ArrayField(models.CharField(max_length=100), blank=True, null=True)  # Only for PostgreSQL

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    @property
    def required_yes_votes(self):
        if self.vote_mode == 'piecewise':
            return self.required_number or 0
        return None

    def is_available(self):
        from django.utils import timezone
        return timezone.now() >= self.available_at

    def voting_has_started(self):
        """Check if voting period has begun."""
        from django.utils import timezone
        # If voting_starts_at is set, use it; otherwise voting starts when available
        start_time = self.voting_starts_at or self.available_at
        return timezone.now() >= start_time

    def get_voting_start_time(self):
        """Get the effective voting start time."""
        return self.voting_starts_at or self.available_at

    def __str__(self):
        return self.title

    def set_passed(self):
        from collections import Counter

        total_votes = Vote.objects.filter(legislation=self)

        if self.vote_mode == 'plurality':
            vote_choices = [v.vote_choice for v in total_votes]
            vote_counts = Counter(vote_choices)
            if vote_counts:
                max_votes = max(vote_counts.values())
                winners = [option for option, count in vote_counts.items() if count == max_votes]
                self.passed = len(winners) == 1  # Only passes if there is a single clear winner
            else:
                self.passed = False
        elif self.vote_mode == 'piecewise':
            yes_votes = total_votes.filter(vote_choice='yes').count()
            self.passed = yes_votes >= self.required_yes_votes
        else:  # percentage
            total_votes = total_votes.exclude(vote_choice='abstain')
            total = total_votes.count()
            yes = total_votes.filter(vote_choice='yes').count()
            if total > 0:
                yes_pct = (yes / total) * 100
                self.passed = yes_pct >= float(self.required_percentage)
            else:
                self.passed = False

        self.save()

class Attendance(models.Model):
    """
    Attendance record for events and committee meetings
    """
    ATTENDANCE_TYPE_CHOICES = (
        ('event', 'Event Attendance'),
        ('committee', 'Committee Attendance'),
    )

    STATUS_CHOICES = (
        ('pending', 'Pending'),  # Not yet marked
        ('present', 'Present'),
        ('absent', 'Absent'),
        ('excused', 'Excused'),
        ('late', 'Late'),
    )

    # Type of attendance
    attendance_type = models.CharField(
        max_length=10,
        choices=ATTENDANCE_TYPE_CHOICES,
        default='event',
        help_text='Type of attendance (event or committee)'
    )

    # For event attendance
    event = models.ForeignKey(
        'Event',
        on_delete=models.CASCADE,
        related_name='attendance_records',
        null=True,
        blank=True,
        help_text='Event this attendance record is for (if event attendance)'
    )

    # For committee attendance
    committee = models.ForeignKey(
        'Committee',
        on_delete=models.CASCADE,
        related_name='attendance_records',
        null=True,
        blank=True,
        help_text='Committee this attendance record is for (if committee attendance)'
    )

    user = models.ForeignKey(
        ParliamentUser,
        on_delete=models.CASCADE,
        limit_choices_to={'member_status': 'Active'},
        related_name='attendance_records'
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='pending',
        help_text='Attendance status'
    )

    # Tracking fields
    marked_by = models.ForeignKey(
        ParliamentUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='marked_attendance',
        help_text='Officer/Chair who marked this attendance'
    )
    marked_at = models.DateTimeField(null=True, blank=True, help_text='When attendance was marked')
    created_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True, help_text='Additional notes about attendance')

    # Legacy field for backwards compatibility
    date = models.DateField(auto_now_add=True)
    present = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at', 'user__name']
        verbose_name = 'Attendance Record'
        verbose_name_plural = 'Attendance Records'
        indexes = [
            models.Index(fields=['event', 'status']),
            models.Index(fields=['committee', 'status']),
            models.Index(fields=['user', 'status']),
            models.Index(fields=['attendance_type', 'created_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'user'],
                condition=models.Q(event__isnull=False, attendance_type='event'),
                name='unique_event_user_attendance'
            ),
            models.UniqueConstraint(
                fields=['committee', 'user', 'date'],
                condition=models.Q(committee__isnull=False, attendance_type='committee'),
                name='unique_committee_user_date_attendance'
            )
        ]

    def __str__(self):
        if self.attendance_type == 'event' and self.event:
            return f"{self.user.name} - {self.event.title} - {self.get_status_display()}"
        elif self.attendance_type == 'committee' and self.committee:
            return f"{self.user.name} - {self.committee.name} - {self.get_status_display()}"
        return f"{self.user.name} - {self.get_status_display()}"

    def save(self, *args, **kwargs):
        # Update legacy 'present' field based on status
        self.present = self.status == 'present'
        super().save(*args, **kwargs)


class AttendanceExcuse(models.Model):
    """
    Excuse request for an event
    """
    STATUS_CHOICES = (
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('denied', 'Denied'),
        ('expired', 'Expired (past deadline)'),
    )

    event = models.ForeignKey(
        'Event',
        on_delete=models.CASCADE,
        related_name='excuse_requests',
        help_text='Event for which excuse is requested'
    )
    user = models.ForeignKey(
        ParliamentUser,
        on_delete=models.CASCADE,
        related_name='excuse_requests',
        help_text='Member requesting the excuse'
    )

    # Excuse details
    reason = models.TextField(help_text='Reason for absence')
    supporting_document = models.FileField(
        upload_to='excuse_documents/',
        null=True,
        blank=True,
        help_text='Optional supporting document (doctor note, etc.)'
    )

    # Status and review
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='pending'
    )
    reviewed_by = models.ForeignKey(
        ParliamentUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_excuses',
        help_text='Officer who reviewed this excuse'
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the excuse was reviewed'
    )
    review_notes = models.TextField(
        blank=True,
        help_text='Officer notes about the excuse decision'
    )

    # Timestamps
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-submitted_at']
        unique_together = ('event', 'user')
        verbose_name = 'Attendance Excuse'
        verbose_name_plural = 'Attendance Excuses'
        indexes = [
            models.Index(fields=['event', 'status']),
            models.Index(fields=['user', 'status']),
            models.Index(fields=['-submitted_at']),
        ]

    def __str__(self):
        return f"{self.user.name} - {self.event.title} - {self.get_status_display()}"

    def is_past_deadline(self):
        """Check if this excuse was submitted after the deadline"""
        from django.utils import timezone

        if not self.event.excuse_deadline:
            # If no deadline, check against event time
            return self.submitted_at > self.event.date_time

        return self.submitted_at > self.event.excuse_deadline

    def approve(self, officer, notes=''):
        """Approve the excuse and update attendance"""
        from django.utils import timezone

        self.status = 'approved'
        self.reviewed_by = officer
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        self.save()

        # Update or create attendance record
        attendance, created = Attendance.objects.get_or_create(
            event=self.event,
            user=self.user,
            attendance_type='event',
            defaults={
                'status': 'excused',
                'marked_by': officer,
                'marked_at': timezone.now(),
                'notes': f'Excused: {self.reason[:100]}'
            }
        )

        if not created and attendance.status != 'excused':
            attendance.status = 'excused'
            attendance.marked_by = officer
            attendance.marked_at = timezone.now()
            attendance.notes = f'Excused: {self.reason[:100]}'
            attendance.save()

    def deny(self, officer, notes=''):
        """Deny the excuse"""
        from django.utils import timezone

        self.status = 'denied'
        self.reviewed_by = officer
        self.reviewed_at = timezone.now()
        self.review_notes = notes
        self.save()


class Vote(models.Model):
    user = models.ForeignKey(ParliamentUser, on_delete=models.CASCADE, limit_choices_to={'member_status': 'Active'})
    legislation = models.ForeignKey(Legislation, on_delete=models.CASCADE)
    vote_choice = models.CharField(max_length=100)


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

class Committee(models.Model):
    # Hard-coded committees (ID, Code, Name)
    # These are the canonical source of truth for committees in the system
    DEFAULT_COMMITTEES = [
        (1, 'BYLAWS', 'Constitution and Bylaws Committee'),
        (2, 'RITUAL', 'Ritual Committee'),
        (3, 'EXEC', 'Executive Board'),
        (4, 'KAI', 'Kai Committee'),
        (5, 'BROTHER', 'Brotherhood Committee'),
        (6, 'RECRUIT', 'Recruitment Committee'),
        (7, 'EDUCATION', 'Education Committee'),
        (8, 'SOCIAL', ' Social Committee'),
        (9, 'FINANCE', 'Finance Committee'),
        (10, 'ADMIN', 'Administration Committee'),
        (11, 'PROGRAM', 'Programming Committee'),
        (12, 'SLATING', 'Slating Committee'),
    ]

    name = models.CharField(max_length=225, unique=True)
    chairs = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='chair_roles'
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='committees',
    )
    advisors = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='advisor_roles'
    )
    voting_members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='committee_voters'
    )
    code = models.CharField(
        max_length=255,
        help_text='Code used to identify committee (ex. RISK, FINANCE)',
        blank=True,
        null=True,
        unique=True,
    )

    allow_multiple_chairs = models.BooleanField(default=False)
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True, related_name="committees")
    created_at = models.DateTimeField(auto_now_add=True)
    committee_id = models.IntegerField(unique=True, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    # Special committee flags
    is_exec_board = models.BooleanField(default=False, help_text='If True, membership auto-syncs with exec role holders')
    is_slating_committee = models.BooleanField(default=False, help_text='If True, has special visibility rules')

    # Explicit admin for committees (used for Slating Committee)
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='admin_of_committees',
        help_text='Explicit admin (used for Slating Committee)'
    )

    def __str__(self):
        return f"{self.code} - {self.name}"

    def chair_list(self):
        return ", ".join([c.name for c in self.chairs.all()])
    chair_list.short_description = "Chairs"

    def is_chair(self, user):
        return self.chairs.filter(pk=user.pk).exists()

    def is_member(self, user):
        return self.members.filter(pk=user.pk).exists()

    def is_voter(self, user):
        return self.voting_members.filter(pk=user.pk).exists()

    def is_vp(self, user):
        """Check to see if the member is the Admin/VP of the committee"""
        if not self.role:
            return False
        return user.roles.filter(pk=self.role.id).exists()

    def get_vp(self):
        """Get the VP of the committee"""
        if not self.role:
            return None
        vps = ParliamentUser.objects.filter(roles=self.role)
        return vps.first() if vps.exists() else None

    def is_visible_to(self, user):
        """Check if this committee should be visible to the user."""
        if not self.is_slating_committee:
            return True  # Normal committees visible to all

        # Slating committee special rules:
        # - Admin always sees it
        # - Site admins always see it
        # - If has members/chairs, those people see it
        # - Otherwise, only admin sees it
        if self.admin == user:
            return True
        if user.is_admin:
            return True
        if self.members.exists() or self.chairs.exists():
            return self.is_member(user) or self.is_chair(user)
        return False


class CommitteePermissions(models.Model):
    committee = models.ForeignKey(Committee, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    can_view_docs = models.BooleanField(default=False)
    can_upload_docs = models.BooleanField(default=False)
    can_vote = models.BooleanField(default=False)
    can_manage_members = models.BooleanField(default=False)
    can_view_results = models.BooleanField(default=True)
    can_take_minutes = models.BooleanField(default=False, help_text='Designated secretary: can create/edit committee minutes')


class CommitteeLegislation(models.Model):
    VOTE_THRESHOLDS = [
        ('51', '51%'),
        ('60', '60%'),
        ('67', '67%'),
        ('75', '75%'),
        ('100', 'Unanimous'),
    ]

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('passed', 'Passed'),
        ('removed', 'Removed'),
    ]

    committee = models.ForeignKey(Committee, on_delete=models.CASCADE, related_name='legislation')
    title = models.CharField(max_length=200)
    description = models.TextField()
    document = models.FileField(upload_to='committee_legislation/', validators=[validate_legislation_file], null=True,
                                blank=True, storage=DualLocationStorage())
    posted_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    available_at = models.DateTimeField(help_text="When the document becomes visible for review")
    voting_starts_at = models.DateTimeField(null=True, blank=True, help_text="When voting opens (defaults to available_at if not set)")
    voting_ends_at = models.DateTimeField(null=True, blank=True, help_text="Optional: When voting should automatically close")
    created_at = models.DateTimeField(auto_now_add=True)
    voting_ended_at = models.DateTimeField(null=True, blank=True)

    anonymous_vote = models.BooleanField(default=False)
    allow_abstain = models.BooleanField(default=True)
    voting_closed = models.BooleanField(default=False)

    vote_mode = models.CharField(
        max_length=20,
        choices=[('percentage', 'Percentage'), ('piecewise', 'Piecewise'), ('plurality', 'Plurality')],
        default='percentage',
    )

    required_percentage = models.CharField(max_length=10, choices=VOTE_THRESHOLDS, default='51')
    required_number = models.PositiveIntegerField(null=True, blank=True)
    plurality_options = ArrayField(models.CharField(max_length=100), blank=True, null=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    passed = models.BooleanField(default=False)

    # Track if this was pushed to chapter
    pushed_to_chapter = models.BooleanField(default=False)
    chapter_legislation = models.ForeignKey(Legislation, on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name='committee_source')

    def is_available(self):
        from django.utils import timezone
        return timezone.now() >= self.available_at

    def voting_has_started(self):
        """Check if voting period has begun."""
        from django.utils import timezone
        start_time = self.voting_starts_at or self.available_at
        return timezone.now() >= start_time

    def get_voting_start_time(self):
        """Get the effective voting start time."""
        return self.voting_starts_at or self.available_at

    def __str__(self):
        return f"{self.committee.code} - {self.title}"

    def set_passed(self):
        from collections import Counter

        total_votes = CommitteeVote.objects.filter(legislation=self)

        if self.vote_mode == 'plurality':
            vote_choices = [v.vote_choice for v in total_votes]
            vote_counts = Counter(vote_choices)
            if vote_counts:
                max_votes = max(vote_counts.values())
                winners = [option for option, count in vote_counts.items() if count == max_votes]
                self.passed = len(winners) == 1
            else:
                self.passed = False
        elif self.vote_mode == 'piecewise':
            yes_votes = total_votes.filter(vote_choice='yes').count()
            self.passed = yes_votes >= self.required_number
        else:  # percentage
            total_votes = total_votes.exclude(vote_choice='abstain')
            total = total_votes.count()
            yes = total_votes.filter(vote_choice='yes').count()
            if total > 0:
                yes_pct = (yes / total) * 100
                self.passed = yes_pct >= float(self.required_percentage)
            else:
                self.passed = False

        if self.passed:
            self.status = 'passed'
        else:
            self.status = 'removed'
        self.save()


class CommitteeVote(models.Model):
    user = models.ForeignKey(ParliamentUser, on_delete=models.CASCADE, limit_choices_to={'member_status': 'Active'})
    legislation = models.ForeignKey(CommitteeLegislation, on_delete=models.CASCADE)
    vote_choice = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'legislation')


class CommitteeMinutes(models.Model):
    committee = models.ForeignKey(Committee, on_delete=models.CASCADE, related_name='minutes')
    title = models.CharField(max_length=200)
    date = models.DateField()
    content = models.TextField(blank=True)
    document = models.FileField(upload_to='committee_minutes/', null=True, blank=True, storage=DualLocationStorage())
    posted_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']
        verbose_name_plural = "Committee Minutes"

    def __str__(self):
        return f"{self.committee.code} - {self.title} ({self.date})"


class ChapterFolder(models.Model):
    """Custom folders for organizing chapter documents"""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, related_name='created_folders')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class DocumentTag(models.Model):
    """Tags for categorizing and organizing documents"""
    name = models.CharField(max_length=50, unique=True)
    color = models.CharField(
        max_length=20,
        default='gray',
        help_text='Badge color for the tag (e.g., blue, green, red, yellow, purple, pink)'
    )
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CommitteeDocument(models.Model):
    DOCUMENT_TYPES = [
        ('general', 'General Document'),
        ('minutes', 'Meeting Minutes'),
        ('agenda', 'Meeting Agenda'),
        ('report', 'Report'),
        ('policy', 'Policy Document'),
    ]

    VISIBILITY_CHOICES = [
        ('all_members', 'All Chapter Members'),
        ('committee_only', 'Committee Members Only'),
        ('chairs_only', 'Committee Chairs Only'),
        ('officers_only', 'Officers Only'),
        ('custom', 'Custom Users'),
    ]

    committee = models.ForeignKey(Committee, on_delete=models.CASCADE, null=True, blank=True, related_name='documents')
    title = models.CharField(max_length=200)
    document = models.FileField(upload_to='committee_documents/', storage=DualLocationStorage())
    uploaded_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    description = models.TextField(blank=True)
    published_to_chapter = models.BooleanField(default=False)
    chapter_folder = models.ForeignKey(ChapterFolder, on_delete=models.SET_NULL, null=True, blank=True, related_name='documents', help_text='Optional custom folder for chapter documents')
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES, default='general')
    meeting_date = models.DateField(null=True, blank=True, help_text='For minutes and agendas')

    # Enhanced document management features
    tags = models.ManyToManyField(DocumentTag, blank=True, related_name='documents')
    version_number = models.IntegerField(default=1, help_text='Current version number')
    is_latest_version = models.BooleanField(default=True, help_text='Whether this is the latest version')

    # Visibility controls
    visibility = models.CharField(
        max_length=20,
        choices=VISIBILITY_CHOICES,
        default='committee_only',
        help_text='Control who can view this document'
    )
    custom_viewers = models.ManyToManyField(
        'ParliamentUser',
        blank=True,
        related_name='viewable_documents',
        help_text='Specific users who can view this document (only applies when visibility is set to Custom)'
    )

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        if self.committee:
            return f"{self.committee.code} - {self.title}"
        return f"Chapter - {self.title}"

    def get_version_string(self):
        """Return formatted version string like 'v1.0'"""
        return f"v{self.version_number}.0"

    def can_user_view(self, user):
        """Check if a user has permission to view this document"""
        # Documents published to chapter are visible to all members
        if self.published_to_chapter:
            return True

        # Admins and the uploader can always view
        if user.is_admin or user == self.uploaded_by:
            return True

        # Check based on visibility setting
        if self.visibility == 'all_members':
            return True
        elif self.visibility == 'committee_only':
            if not self.committee:
                return True  # Chapter-level docs with committee_only treated as all_members
            return user in self.committee.members.all()
        elif self.visibility == 'chairs_only':
            if not self.committee:
                return user.is_officer
            return user in self.committee.chairs.all()
        elif self.visibility == 'officers_only':
            return user.member_type == 'Officer' or user.is_officer
        elif self.visibility == 'custom':
            return user in self.custom_viewers.all()

        return False


class DocumentVersion(models.Model):
    """Track document version history"""
    document = models.ForeignKey(CommitteeDocument, on_delete=models.CASCADE, related_name='versions')
    version_number = models.IntegerField()
    file = models.FileField(upload_to='document_versions/', storage=DualLocationStorage())
    uploaded_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    change_notes = models.TextField(blank=True, help_text='Description of changes in this version')
    file_size = models.BigIntegerField(null=True, blank=True, help_text='File size in bytes')

    class Meta:
        ordering = ['-version_number']
        unique_together = ['document', 'version_number']

    def __str__(self):
        return f"{self.document.title} - v{self.version_number}"

    def get_file_size_display(self):
        """Return human-readable file size"""
        if not self.file_size:
            return 'Unknown'
        size = self.file_size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"


class Announcement(models.Model):
    """Model for officer announcements and event notifications"""
    MEMBER_TYPES = (
        ('Member', 'Members'),
        ('Advisor', 'Advisors'),
        ('Pledge', 'Pledges'),
    )

    title = models.CharField(max_length=200)
    content = models.TextField()
    posted_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    posted_at = models.DateTimeField(auto_now_add=True)
    publish_at = models.DateTimeField(null=True, blank=True, help_text='Schedule when this announcement should be published. Leave blank to publish immediately.')
    event_date = models.DateTimeField(null=True, blank=True, help_text='Optional event/time')
    is_active = models.BooleanField(default=True, help_text='Uncheck to hide announcement')
    visible_to = models.JSONField(
        null=True,
        blank=True,
        help_text='Select which member types can see this announcement. Leave empty for all members.'
    )

    class Meta:
        ordering = ['-posted_at']

    def __str__(self):
        return f"{self.title} - {self.posted_at.strftime('%Y-%m-%d')}"

    def is_published(self):
        """Check if announcement should be visible based on publish_at date"""
        from django.utils import timezone
        if not self.is_active:
            return False
        if self.publish_at is None:
            return True
        return timezone.now() >= self.publish_at

    def is_visible_to_user(self, user):
        """Check if user should be able to see this announcement"""
        if not self.is_published():
            return False
        # If visible_to is None or empty, show to all users
        if not self.visible_to:
            return True
        # Check if user's member_type is directly in the list
        if user.member_type in self.visible_to:
            return True
        # If "Member" is selected, also include Chair and Officer
        if 'Member' in self.visible_to and user.member_type in ['Chair', 'Officer']:
            return True
        return False

    def get_view_stats(self):
        """Get view statistics for this announcement"""
        views = self.views.all()
        site_views = views.filter(view_source='site').count()
        email_views = views.filter(view_source='email').count()
        total_views = views.count()

        # Get target audience count
        target_users = ParliamentUser.objects.filter(member_status='Active')
        if self.visible_to:
            # Filter by member type if specified
            visible_types = list(self.visible_to)
            # If "Member" is in visible_to, include Chair and Officer
            if 'Member' in visible_types:
                visible_types.extend(['Chair', 'Officer'])
            target_users = target_users.filter(member_type__in=visible_types)
        target_count = target_users.count()

        return {
            'site_views': site_views,
            'email_views': email_views,
            'total_views': total_views,
            'target_audience': target_count,
            'view_rate': (total_views / target_count * 100) if target_count > 0 else 0,
        }

    def get_viewers(self):
        """Get list of users who have viewed this announcement with source"""
        return self.views.select_related('user').order_by('-viewed_at')


class UserAnnouncementView(models.Model):
    """Track which announcements users have seen/dismissed"""
    VIEW_SOURCE_CHOICES = [
        ('site', 'Website'),
        ('email', 'Email'),
    ]

    user = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name='views')
    viewed_at = models.DateTimeField(auto_now_add=True)
    dismissed = models.BooleanField(default=False, help_text='User has dismissed this notification')
    view_source = models.CharField(
        max_length=10,
        choices=VIEW_SOURCE_CHOICES,
        default='site',
        help_text='Where the user viewed the announcement'
    )

    class Meta:
        unique_together = ('user', 'announcement')
        ordering = ['-viewed_at']

    def __str__(self):
        return f"{self.user.name} - {self.announcement.title} ({self.view_source})"


class Event(models.Model):
    """Model for calendar events - officers and chairs can create, all members can view"""
    MEMBER_TYPES = (
        ('Member', 'Members'),
        ('Advisor', 'Advisors'),
        ('Pledge', 'Pledges'),
    )

    title = models.CharField(max_length=200)
    description = models.TextField()
    date_time = models.DateTimeField(help_text='Date and time of the event')
    location = models.CharField(max_length=300, blank=True, help_text='Event location (physical or virtual)')
    created_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, help_text='Uncheck to hide event from calendar')
    archived = models.BooleanField(default=False, help_text='Events older than 1 year are automatically archived')
    visible_to = models.JSONField(
        null=True,
        blank=True,
        help_text='Select which member types can see this event. Leave empty for all members.'
    )

    # Recurring event fields
    RECURRENCE_CHOICES = [
        ('none', 'Does not repeat'),
        ('daily', 'Daily'),
        ('weekly', 'Weekly'),
        ('biweekly', 'Every 2 weeks'),
        ('monthly', 'Monthly'),
        ('custom', 'Custom'),
    ]

    is_recurring = models.BooleanField(
        default=False,
        help_text='Check if this event repeats'
    )
    recurrence_type = models.CharField(
        max_length=20,
        choices=RECURRENCE_CHOICES,
        default='none',
        help_text='How often the event repeats'
    )
    recurrence_interval = models.PositiveIntegerField(
        default=1,
        help_text='For custom recurrence: repeat every X days/weeks/months'
    )
    recurrence_unit = models.CharField(
        max_length=10,
        choices=[('days', 'Day(s)'), ('weeks', 'Week(s)'), ('months', 'Month(s)')],
        default='weeks',
        help_text='Unit for custom recurrence interval'
    )
    recurrence_days = models.JSONField(
        null=True,
        blank=True,
        help_text='Days of week for weekly recurrence (0=Monday, 6=Sunday)'
    )
    recurrence_end_date = models.DateField(
        null=True,
        blank=True,
        help_text='Date when recurring events stop (leave blank for indefinite)'
    )
    parent_event = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='recurring_instances',
        help_text='Parent event for recurring event instances'
    )

    # Attendance tracking fields
    requires_attendance = models.BooleanField(
        default=True,
        help_text='Check if this event requires attendance tracking (chapter meetings, etc.)'
    )
    allow_excuses = models.BooleanField(
        default=True,
        help_text='Allow members to submit excuse requests for this event'
    )
    excuse_deadline = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Deadline for submitting excuses (leave empty to allow until event time)'
    )
    attendance_finalized = models.BooleanField(
        default=False,
        help_text='Mark as true when attendance has been finalized by an officer'
    )
    finalized_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='finalized_events',
        help_text='Officer who finalized attendance'
    )
    finalized_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When attendance was finalized'
    )

    class Meta:
        ordering = ['date_time']

    def __str__(self):
        return f"{self.title} - {self.date_time.strftime('%Y-%m-%d %H:%M')}"

    def is_upcoming(self):
        """Check if event is in the future"""
        from django.utils import timezone
        return self.date_time > timezone.now()

    def is_visible_to_user(self, user):
        """Check if user should be able to see this event"""
        if not self.is_active:
            return False
        # If visible_to is None or empty, show to all users
        if not self.visible_to:
            return True
        # Check if user's member_type is directly in the list
        if user.member_type in self.visible_to:
            return True
        # If "Member" is selected, also include Chair and Officer
        if 'Member' in self.visible_to and user.member_type in ['Chair', 'Officer']:
            return True
        return False

    def can_submit_excuse(self):
        """Check if excuses can still be submitted for this event"""
        from django.utils import timezone

        if not self.allow_excuses:
            return False

        if self.attendance_finalized:
            return False

        # Check excuse deadline
        if self.excuse_deadline:
            return timezone.now() < self.excuse_deadline

        # If no deadline set, allow until event time
        return timezone.now() < self.date_time

    def get_attendance_stats(self):
        """Get attendance statistics for this event"""
        from django.db.models import Count, Q

        if not self.requires_attendance:
            return None

        total_members = ParliamentUser.objects.filter(member_status='Active').count()

        attendance_records = self.attendance_records.all()
        present_count = attendance_records.filter(status='present').count()
        absent_count = attendance_records.filter(status='absent').count()
        excused_count = attendance_records.filter(status='excused').count()
        pending_count = attendance_records.filter(status='pending').count()

        return {
            'total_members': total_members,
            'present': present_count,
            'absent': absent_count,
            'excused': excused_count,
            'pending': pending_count,
            'unmarked': total_members - attendance_records.count(),
            'attendance_rate': (present_count / total_members * 100) if total_members > 0 else 0
        }


class ChatChannel(models.Model):
    """Represents a chat channel - committee or custom"""

    CHANNEL_TYPES = [
        ('committee', 'Committee Chat'),
        ('custom', 'Custom Channel'),
        ('direct', 'Direct Message'),  # Future: DMs between users
    ]

    ACCESS_TYPES = [
        ('open', 'All Members'),           # Anyone can access
        ('committee', 'Committee Members'), # Tied to committee
        ('restricted', 'Restricted'),      # Custom permissions
    ]

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    channel_type = models.CharField(max_length=20, choices=CHANNEL_TYPES, default='custom')
    access_type = models.CharField(max_length=20, choices=ACCESS_TYPES, default='restricted')

    # Link to committee (for committee chats)
    committee = models.ForeignKey(
        'Committee',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='chat_channel'
    )

    created_by = models.ForeignKey('ParliamentUser', on_delete=models.SET_NULL, null=True, related_name='created_channels')
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    # Icon/color for customization
    icon = models.CharField(max_length=10, default='💬')
    color = models.CharField(max_length=7, default='#003DA5')  # Hex color

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def has_access(self, user, admin_override=False):
        """Check if user has access to this channel"""
        if not self.is_active:
            return False

        # Admin override for "View All Channels" mode
        if admin_override and user.is_admin:
            return True

        if self.access_type == 'open':
            return True

        if self.access_type == 'committee' and self.committee:
            # Check if user is a committee member first
            if self.committee.is_member(user):
                return True
            # Check if user has guest permission with can_read=True
            return ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_read=True
            ).exists()

        if self.access_type == 'restricted':
            # Check custom permissions - must have can_read=True
            return ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_read=True
            ).exists() or ChatChannelPermission.objects.filter(
                channel=self,
                member_type=user.member_type,
                can_read=True
            ).exists() or (
                ChatChannelPermission.objects.filter(
                    channel=self,
                    chairs_only=True,
                    can_read=True
                ).exists() and user.chair_roles.exists()
            ) or (
                ChatChannelPermission.objects.filter(
                    channel=self,
                    officers_only=True,
                    can_read=True
                ).exists() and user.is_officer
            )

        return False

    def get_unread_count(self, user):
        """Get unread message count for a user"""
        try:
            receipt = ChatReadReceipt.objects.get(user=user, channel=self)
            if not receipt.last_read_message:
                return self.messages.filter(is_deleted=False).count()

            return self.messages.filter(
                created_at__gt=receipt.last_read_message.created_at,
                is_deleted=False
            ).count()
        except ChatReadReceipt.DoesNotExist:
            return self.messages.filter(is_deleted=False).count()

    def can_read(self, user):
        """Check if user can read messages in this channel"""
        if not self.is_active:
            return False

        # Admins always have access
        if user.is_admin:
            return True

        # Committee members always have read access
        if self.committee and self.committee.is_member(user):
            return True

        # Check if user has specific permission
        if self.access_type == 'open':
            return True

        # For committee and restricted channels, check guest permissions
        if self.access_type in ['committee', 'restricted']:
            # Check for explicit permission with can_read=True
            perm = ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_read=True
            ).first()
            return perm is not None

        return False

    def can_write(self, user):
        """Check if user can send messages in this channel"""
        if not self.is_active:
            return False

        # Admins always have access
        if user.is_admin:
            return True

        # Committee members always have write access
        if self.committee and self.committee.is_member(user):
            return True

        # Check if user has specific permission
        if self.access_type == 'open':
            return True

        # For committee and restricted channels, check guest permissions
        if self.access_type in ['committee', 'restricted']:
            # Check for explicit permission with can_write=True
            perm = ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_write=True
            ).first()
            return perm is not None

        return False

    def can_delete_messages(self, user):
        """Check if user can delete their own messages in this channel"""
        if not self.is_active:
            return False

        # Admins always have access
        if user.is_admin:
            return True

        # Chairs can always delete
        if self.committee and self.committee.is_chair(user):
            return True

        # Committee members always have delete access
        if self.committee and self.committee.is_member(user):
            return True

        # Check if user has specific permission
        if self.access_type == 'open':
            return True

        # For committee and restricted channels, check guest permissions
        if self.access_type in ['committee', 'restricted']:
            # Check for explicit permission with can_delete=True
            perm = ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_delete=True
            ).first()
            return perm is not None

        return False


class ChatChannelPermission(models.Model):
    """Defines who has access to a restricted channel"""

    MEMBER_TYPES = [
        ('Member', 'Member'),
        ('Chair', 'Chair'),
        ('Officer', 'Officer'),
        ('Advisor', 'Advisor'),
        ('Pledge', 'Pledge'),
    ]

    channel = models.ForeignKey(ChatChannel, on_delete=models.CASCADE, related_name='permissions')

    # Specific user access (nullable)
    user = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='channel_permissions'
    )

    # Role-based access (nullable)
    member_type = models.CharField(max_length=50, choices=MEMBER_TYPES, null=True, blank=True)

    # Chair-only access
    chairs_only = models.BooleanField(default=False, help_text='Only committee chairs can access')

    # Officer-only access
    officers_only = models.BooleanField(default=False, help_text='Only officers can access')

    # Read/Write permissions for guest users (non-committee members)
    can_read = models.BooleanField(default=True, help_text='User can read messages in this channel')
    can_write = models.BooleanField(default=True, help_text='User can send messages in this channel')
    can_delete = models.BooleanField(default=False, help_text='User can delete their own messages in this channel')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['channel', 'user'],
                name='unique_channel_user',
                condition=models.Q(user__isnull=False)
            ),
            models.UniqueConstraint(
                fields=['channel', 'member_type'],
                name='unique_channel_member_type',
                condition=models.Q(member_type__isnull=False)
            ),
        ]

    def __str__(self):
        if self.user:
            return f"{self.channel.name} - {self.user.name}"
        if self.member_type:
            return f"{self.channel.name} - {self.member_type}"
        if self.chairs_only:
            return f"{self.channel.name} - Chairs Only"
        if self.officers_only:
            return f"{self.channel.name} - Officers Only"
        return f"{self.channel.name} - Permission"


class ChatMessage(models.Model):
    """Chat messages - now linked to channels"""
    # New channel-based system
    channel = models.ForeignKey(ChatChannel, on_delete=models.CASCADE, related_name='messages', null=True, blank=True)

    # Legacy committee field (will be deprecated after migration)
    committee = models.ForeignKey(Committee, on_delete=models.CASCADE, related_name='chat_messages', null=True, blank=True)

    sender = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, related_name='sent_messages')
    message = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False, help_text='Soft delete - show "Message deleted"')

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['channel', '-created_at']),
            models.Index(fields=['committee', '-created_at']),  # Legacy index
        ]

    def __str__(self):
        if self.channel:
            return f"{self.sender.name} in {self.channel.name}: {self.message[:50]}"
        elif self.committee:
            return f"{self.sender.name} in {self.committee.code}: {self.message[:50]}"
        return f"{self.sender.name}: {self.message[:50]}"


class ChatReadReceipt(models.Model):
    """Track last read message per user per channel"""
    user = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, related_name='chat_receipts')

    # New channel-based system
    channel = models.ForeignKey(ChatChannel, on_delete=models.CASCADE, related_name='read_receipts', null=True, blank=True)

    # Legacy committee field (will be deprecated after migration)
    committee = models.ForeignKey(Committee, on_delete=models.CASCADE, related_name='read_receipts', null=True, blank=True)

    last_read_message = models.ForeignKey(ChatMessage, on_delete=models.SET_NULL, null=True, blank=True)
    last_read_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'channel'],
                name='unique_user_channel',
                condition=models.Q(channel__isnull=False)
            ),
            models.UniqueConstraint(
                fields=['user', 'committee'],
                name='unique_user_committee',
                condition=models.Q(committee__isnull=False)
            ),
        ]

    def __str__(self):
        if self.channel:
            return f"{self.user.name} - {self.channel.name}"
        elif self.committee:
            return f"{self.user.name} - {self.committee.code}"
        return f"{self.user.name}"

    def get_unread_count(self):
        """Get number of unread messages in this channel/committee"""
        if self.channel:
            if not self.last_read_message:
                return self.channel.messages.filter(is_deleted=False).count()

            return self.channel.messages.filter(
                created_at__gt=self.last_read_message.created_at,
                is_deleted=False
            ).count()
        elif self.committee:
            # Legacy support
            if not self.last_read_message:
                return self.committee.chat_messages.filter(is_deleted=False).count()

            return self.committee.chat_messages.filter(
                created_at__gt=self.last_read_message.created_at,
                is_deleted=False
            ).count()
        return 0


class PassedResolution(models.Model):
    """Model for tracking passed resolutions and their impact on Constitution/Bylaws"""

    BORDER_COLOR_CHOICES = [
        ('green', 'Green'),
        ('blue', 'Blue'),
        ('purple', 'Purple'),
        ('pink', 'Pink'),
        ('indigo', 'Indigo'),
        ('red', 'Red'),
        ('yellow', 'Yellow'),
    ]

    title = models.CharField(max_length=200, help_text='Title of the resolution')
    description = models.TextField(help_text='Brief description of what this resolution does')
    date_passed = models.DateField(help_text='Date this resolution was passed')

    # Link to legislation document
    legislation = models.ForeignKey(
        Legislation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text='Optional: Link to the actual legislation document'
    )

    # Alternative: Direct document upload
    document = models.FileField(
        upload_to='passed_resolutions/',
        null=True,
        blank=True,
        storage=DualLocationStorage(),
        help_text='Optional: Upload a document if not linked to legislation'
    )

    # Visual styling
    border_color = models.CharField(
        max_length=20,
        choices=BORDER_COLOR_CHOICES,
        default='green',
        help_text='Border color for the resolution card'
    )

    # Impact details
    impact_summary = models.TextField(
        blank=True,
        help_text='Brief summary of sections impacted (displayed in the card)'
    )

    # Display settings
    display_order = models.IntegerField(
        default=0,
        help_text='Order to display resolutions (lower numbers first)'
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Uncheck to hide this resolution from the page'
    )

    # Metadata
    created_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_resolutions'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', '-date_passed']

    def __str__(self):
        return f"{self.title} ({self.date_passed})"

    def get_document_url(self):
        """Get the URL to the resolution document"""
        if self.legislation:
            return self.legislation.document.url if self.legislation.document else None
        elif self.document:
            return self.document.url
        return None


class ResolutionSectionImpact(models.Model):
    """Track which sections of Constitution/Bylaws are impacted by a resolution"""

    SECTION_TYPE_CHOICES = [
        ('constitution', 'Constitution Article'),
        ('bylaws', 'Bylaws Article'),
        ('other', 'Other Document'),
    ]

    resolution = models.ForeignKey(
        PassedResolution,
        on_delete=models.CASCADE,
        related_name='section_impacts'
    )

    section_name = models.CharField(
        max_length=200,
        help_text='Display name for the section (e.g., "Constitution Article III (Leadership)")'
    )

    section_type = models.CharField(
        max_length=20,
        choices=SECTION_TYPE_CHOICES,
        default='constitution'
    )

    # URL/anchor to link to (e.g., "#const-leadership")
    section_anchor = models.CharField(
        max_length=100,
        blank=True,
        help_text='URL anchor/fragment (e.g., "#const-leadership")'
    )

    # Alternative: link to another page
    external_url = models.CharField(
        max_length=200,
        blank=True,
        help_text='Full URL to another page (e.g., officer duties detail page)'
    )

    display_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'section_name']

    def __str__(self):
        return f"{self.resolution.title} - {self.section_name}"

    def get_link_url(self):
        """Get the full URL for this section link"""
        if self.external_url:
            return self.external_url
        elif self.section_anchor:
            # Return just the anchor - template will handle the base URL
            return self.section_anchor
        return None


class KaiReport(models.Model):
    """Model for Kai Committee reports submitted by members"""

    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('reviewed', 'Reviewed'),
        ('archived', 'Archived'),
    ]

    DELIBERATION_CHOICES = [
        ('pending', 'Pending Deliberation'),
        ('under_investigation', 'Under Investigation'),
        ('scheduled', 'Scheduled for Hearing'),
        ('heard', 'Case Heard'),
        ('warning_issued', 'Warning Issued'),
        ('sanctions_applied', 'Sanctions Applied'),
        ('mediation', 'Informal Resolution / Mediation'),
        ('referred', 'Referred to Standards Board'),
        ('dismissed', 'Case Dismissed'),
        ('thrown_out', 'Case Thrown Out'),
    ]

    CATEGORY_CHOICES = [
        ('academic', 'Academic Misconduct'),
        ('behavioral', 'Behavioral Issues'),
        ('hazing', 'Hazing Concerns'),
        ('social', 'Social Conduct'),
        ('financial', 'Financial Issues'),
        ('other', 'Other'),
    ]

    # Report Details
    title = models.CharField(max_length=255, help_text="Brief title for the report")
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default='other',
        help_text="Category/type of the report"
    )
    description = models.TextField(help_text="Detailed description of the report")
    attachment = models.FileField(
        upload_to='kai_reports/',
        storage=DualLocationStorage(),
        blank=True,
        null=True,
        help_text="Optional file attachment"
    )

    # Submission Information
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='kai_reports_submitted'
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    targeted_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='kai_reports_targeted',
        help_text="Optional: Specific person this report is directed to"
    )

    # Status and Review
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='kai_reports_reviewed'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)

    # Tags and Notes
    tags = models.CharField(
        max_length=500,
        blank=True,
        help_text="Comma-separated tags (e.g., 'urgent, follow-up, academic')"
    )
    chair_notes = models.TextField(blank=True, help_text="Notes from the Kai chair")

    # Deliberation and Committee Decision
    deliberation_outcome = models.CharField(
        max_length=20,
        choices=DELIBERATION_CHOICES,
        default='pending',
        help_text="Outcome of the deliberation process"
    )
    committee_notes = models.TextField(
        blank=True,
        help_text="Committee notes about the hearing, sanctions applied, or other decisions"
    )
    closed_by_accused_request = models.BooleanField(
        default=False,
        help_text="Case closed at the request of the accused (only applicable when case was heard)"
    )

    # Accused Notification
    accused_notified = models.BooleanField(
        default=False,
        help_text="Whether the accused has been notified of the case"
    )
    accused_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the accused was notified"
    )
    accused_notification_message = models.TextField(
        blank=True,
        help_text="Custom message sent to the accused explaining what they are being reported for"
    )
    accused_email_viewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the accused viewed the notification email (tracked via pixel)"
    )

    # Related Reports
    related_reports = models.ManyToManyField(
        'self',
        blank=True,
        symmetrical=True,
        help_text="Link related reports (e.g., follow-ups, same incident)"
    )

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Kai Report'
        verbose_name_plural = 'Kai Reports'

    def __str__(self):
        return f"{self.title} - {self.submitted_by.name} ({self.submitted_at.strftime('%Y-%m-%d')})"

    def get_tags_list(self):
        """Return tags as a list"""
        if self.tags:
            return [tag.strip() for tag in self.tags.split(',')]
        return []

    def mark_as_reviewed(self, reviewer):
        """Mark the report as reviewed"""
        from django.utils import timezone
        self.status = 'reviewed'
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.save()


class KaiReportActivity(models.Model):
    """Activity log for tracking changes to Kai reports"""

    ACTION_CHOICES = [
        ('created', 'Report Created'),
        ('status_changed', 'Status Changed'),
        ('deliberation_updated', 'Deliberation Outcome Updated'),
        ('notes_updated', 'Chair Notes Updated'),
        ('tags_updated', 'Tags Updated'),
        ('committee_notes_updated', 'Committee Notes Updated'),
        ('minutes_closed', 'Minutes Closed'),
        ('archived', 'Report Archived'),
    ]

    report = models.ForeignKey(
        KaiReport,
        on_delete=models.CASCADE,
        related_name='activity_log'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    details = models.TextField(blank=True, help_text="Additional details about the action")
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Kai Report Activity'
        verbose_name_plural = 'Kai Report Activities'

    def __str__(self):
        user_name = self.user.name if self.user else 'System'
        return f"{user_name} - {self.get_action_display()} - {self.timestamp.strftime('%Y-%m-%d %H:%M')}"


class KaiReportTemplate(models.Model):
    """Templates for common types of Kai reports"""

    name = models.CharField(max_length=200, help_text="Template name (e.g., 'Academic Dishonesty Report')")
    description = models.TextField(help_text="Description of when to use this template")
    category = models.CharField(
        max_length=20,
        choices=KaiReport.CATEGORY_CHOICES,
        default='other',
        help_text="Default category for reports using this template"
    )
    title_template = models.CharField(
        max_length=300,
        help_text="Template for report title (can include placeholders like {member_name})"
    )
    description_template = models.TextField(
        help_text="Template for report description with guidelines and placeholders"
    )
    suggested_tags = models.CharField(
        max_length=500,
        blank=True,
        help_text="Comma-separated suggested tags for this type of report"
    )
    is_active = models.BooleanField(default=True, help_text="Whether this template is currently available")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_kai_templates'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'name']
        verbose_name = 'Kai Report Template'
        verbose_name_plural = 'Kai Report Templates'

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"


class Notification(models.Model):
    """In-app notifications for users"""
    NOTIFICATION_TYPES = (
        ('announcement', 'Announcement'),
        ('legislation_new', 'New Legislation'),
        ('vote_ended', 'Vote Ended'),
        ('event_new', 'New Event'),
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
        
        # Settings
        ('preferences_updated', 'Preferences Updated'),
        ('settings_changed', 'System Settings Changed'),
        
        # Other
        ('other', 'Other Action'),
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
    object_id = models.IntegerField(null=True, blank=True, help_text='ID of the affected object')
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
            'preferences_updated': 'settings', 'settings_changed': 'settings',
        }
        action_category = category_map.get(action_type, 'other')
        
        # Extract IP and user agent from request if provided
        if request:
            if not ip_address:
                # Check X-Forwarded-For header first (for requests behind proxy/load balancer)
                x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
                if x_forwarded_for:
                    # Take the first IP (client's real IP) from the comma-separated list
                    ip_address = x_forwarded_for.split(',')[0].strip()
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


class LoginHistory(models.Model):
    """
    Detailed login tracking for security monitoring and anomaly detection
    """
    LOGIN_STATUS_CHOICES = (
        ('success', 'Successful'),
        ('failed', 'Failed'),
        ('blocked', 'Blocked'),
    )

    RISK_LEVEL_CHOICES = (
        ('low', 'Low Risk'),
        ('medium', 'Medium Risk'),
        ('high', 'High Risk'),
        ('critical', 'Critical Risk'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='login_history',
        help_text='User who attempted to login'
    )
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    status = models.CharField(max_length=10, choices=LOGIN_STATUS_CHOICES, default='success')

    # IP and Location data
    ip_address = EncryptedCharField(max_length=45, help_text='Encrypted IP address of login attempt')
    country = models.CharField(max_length=100, blank=True, help_text='Country from IP geolocation')
    city = models.CharField(max_length=100, blank=True, help_text='City from IP geolocation')
    region = models.CharField(max_length=100, blank=True, help_text='State/Region from IP geolocation')
    latitude = models.FloatField(null=True, blank=True, help_text='Approximate latitude')
    longitude = models.FloatField(null=True, blank=True, help_text='Approximate longitude')

    # Device information
    user_agent = models.CharField(max_length=500, blank=True, help_text='Browser/device user agent string')
    device_type = models.CharField(max_length=50, blank=True, help_text='Device type (mobile, desktop, tablet)')
    browser = models.CharField(max_length=100, blank=True, help_text='Browser name and version')
    os = models.CharField(max_length=100, blank=True, help_text='Operating system')

    # Security analysis
    is_suspicious = models.BooleanField(default=False, help_text='Flagged as suspicious by automated detection')
    risk_level = models.CharField(max_length=10, choices=RISK_LEVEL_CHOICES, default='low')
    risk_factors = models.JSONField(default=list, blank=True, help_text='List of detected risk factors')

    # Distance calculations (for impossible travel detection)
    distance_from_last = models.FloatField(null=True, blank=True, help_text='Distance in km from previous login location')
    time_from_last = models.FloatField(null=True, blank=True, help_text='Time in hours from previous login')

    # Alert tracking
    alert_created = models.BooleanField(default=False, help_text='Whether an alert was created for this login')
    reviewed = models.BooleanField(default=False, help_text='Whether this login has been reviewed by an admin')
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_logins',
        help_text='Admin who reviewed this login'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, help_text='When this login was reviewed')
    notes = models.TextField(blank=True, help_text='Admin notes about this login')

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Login History'
        verbose_name_plural = 'Login Histories'
        indexes = [
            models.Index(fields=['-timestamp']),
            models.Index(fields=['user', '-timestamp']),
            models.Index(fields=['is_suspicious', '-timestamp']),
            models.Index(fields=['risk_level', '-timestamp']),
        ]

    def __str__(self):
        return f"{self.user.name} - {self.status} - {self.timestamp.strftime('%Y-%m-%d %H:%M')} from {self.city or 'Unknown'}"

    @property
    def location_display(self):
        """Human-readable location string"""
        parts = []
        if self.city:
            parts.append(self.city)
        if self.region:
            parts.append(self.region)
        if self.country:
            parts.append(self.country)
        return ', '.join(parts) if parts else f'IP: {self.ip_address}'

    @property
    def is_impossible_travel(self):
        """Check if this login represents impossible travel"""
        if self.distance_from_last and self.time_from_last:
            # If distance is more than 500km and time is less than 1 hour, flag as impossible
            if self.distance_from_last > 500 and self.time_from_last < 1:
                return True
            # Calculate average speed in km/h
            avg_speed = self.distance_from_last / self.time_from_last if self.time_from_last > 0 else 0
            # Flag if average speed exceeds 1000 km/h (typical commercial flight speed)
            return avg_speed > 1000
        return False


class LoginAlert(models.Model):
    """
    Security alerts for suspicious login activity
    """
    ALERT_TYPE_CHOICES = (
        ('impossible_travel', 'Impossible Travel'),
        ('new_location', 'New Location'),
        ('new_device', 'New Device'),
        ('multiple_failures', 'Multiple Failed Attempts'),
        ('unusual_time', 'Unusual Login Time'),
        ('vpn_detected', 'VPN/Proxy Detected'),
        ('other', 'Other Suspicious Activity'),
    )

    SEVERITY_CHOICES = (
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    )

    STATUS_CHOICES = (
        ('new', 'New'),
        ('investigating', 'Under Investigation'),
        ('resolved', 'Resolved'),
        ('false_positive', 'False Positive'),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='login_alerts',
        help_text='User whose account triggered the alert'
    )
    login_history = models.ForeignKey(
        LoginHistory,
        on_delete=models.CASCADE,
        related_name='alerts',
        null=True,
        blank=True,
        help_text='Login attempt that triggered this alert (optional for admin-initiated alerts)'
    )

    alert_type = models.CharField(max_length=30, choices=ALERT_TYPE_CHOICES)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default='medium')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='new')

    title = models.CharField(max_length=200, help_text='Alert title/summary')
    description = models.TextField(help_text='Detailed description of the security concern')

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # Admin review
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reviewed_alerts',
        help_text='Admin who reviewed this alert'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, help_text='When this alert was reviewed')
    resolution_notes = models.TextField(blank=True, help_text='Notes about alert resolution')

    # User notification
    user_notified = models.BooleanField(default=False, help_text='Whether the user was notified of this alert')
    notified_at = models.DateTimeField(null=True, blank=True, help_text='When the user was notified')

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Login Alert'
        verbose_name_plural = 'Login Alerts'
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['user', '-created_at']),
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['severity', '-created_at']),
        ]

    def __str__(self):
        return f"{self.get_alert_type_display()} - {self.user.name} - {self.get_severity_display()}"


class IPWhitelist(models.Model):
    """
    IP addresses that are explicitly trusted and bypass security checks
    """
    ip_address = models.CharField(
        max_length=45,
        unique=True,
        help_text='IP address or CIDR range (e.g., 192.168.1.0/24)'
    )
    description = models.CharField(
        max_length=200,
        help_text='Description of why this IP is whitelisted (e.g., "Office Network")'
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='whitelisted_ips',
        help_text='Admin who added this IP to whitelist'
    )
    added_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, help_text='Whether this whitelist entry is currently active')

    class Meta:
        ordering = ['-added_at']
        verbose_name = 'IP Whitelist Entry'
        verbose_name_plural = 'IP Whitelist Entries'

    def __str__(self):
        return f"{self.ip_address} - {self.description}"


class IPBlacklist(models.Model):
    """
    IP addresses that are explicitly blocked from accessing the system
    """
    ip_address = models.CharField(
        max_length=45,
        unique=True,
        help_text='IP address or CIDR range to block'
    )
    reason = models.CharField(
        max_length=200,
        help_text='Reason for blocking this IP'
    )
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='blacklisted_ips',
        help_text='Admin who added this IP to blacklist'
    )
    added_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, help_text='Whether this blacklist entry is currently active')
    expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When this blacklist entry should automatically expire (optional)'
    )

    # Track blocks
    block_count = models.IntegerField(
        default=0,
        help_text='Number of times this IP has been blocked'
    )
    last_blocked = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Last time this IP was blocked'
    )

    class Meta:
        ordering = ['-added_at']
        verbose_name = 'IP Blacklist Entry'
        verbose_name_plural = 'IP Blacklist Entries'

    def __str__(self):
        return f"{self.ip_address} - {self.reason}"


class BugReport(models.Model):
    """
    Model for users to report bugs and issues they encounter
    """
    # Issue type choices
    ISSUE_TYPES = (
        ('ui', 'UI/Visual Issue'),
        ('functionality', 'Feature Not Working'),
        ('error_500', 'Server Error (500)'),
        ('error_404', 'Page Not Found (404)'),
        ('error_403', 'Permission Denied (403)'),
        ('performance', 'Slow/Performance Issue'),
        ('mobile', 'Mobile Display Issue'),
        ('accessibility', 'Accessibility Issue'),
        ('data', 'Incorrect Data Displayed'),
        ('other', 'Other'),
    )

    # Page choices - main areas of the application
    PAGE_CHOICES = (
        ('home', 'Home Page'),
        ('login', 'Login Page'),
        ('profile', 'Profile Page'),
        ('preferences', 'Preferences'),
        ('legislation', 'Legislation'),
        ('voting', 'Voting'),
        ('committees', 'Committees'),
        ('documents', 'Documents'),
        ('announcements', 'Announcements'),
        ('events', 'Events'),
        ('attendance', 'Attendance'),
        ('officer_home', 'Officer Dashboard'),
        ('admin', 'Admin Panel'),
        ('roberts_rules', "Robert's Rules"),
        ('constitution', 'Constitution & Bylaws'),
        ('other', 'Other Page'),
    )

    # Priority levels
    PRIORITY_CHOICES = (
        ('low', 'Low - Minor inconvenience'),
        ('medium', 'Medium - Affects usability'),
        ('high', 'High - Blocks functionality'),
        ('critical', 'Critical - System unusable'),
    )

    # Status for tracking
    STATUS_CHOICES = (
        ('new', 'New'),
        ('acknowledged', 'Acknowledged'),
        ('in_progress', 'In Progress'),
        ('resolved', 'Resolved'),
        ('wont_fix', "Won't Fix"),
        ('duplicate', 'Duplicate'),
    )

    # Required field
    description = models.TextField(
        help_text='Describe the issue you encountered in detail'
    )

    # Optional categorization fields
    issue_type = models.CharField(
        max_length=20,
        choices=ISSUE_TYPES,
        default='other',
        blank=True,
        help_text='What type of issue is this?'
    )

    page = models.CharField(
        max_length=50,
        choices=PAGE_CHOICES,
        blank=True,
        help_text='Which page did this occur on?'
    )

    page_url = models.CharField(
        max_length=500,
        blank=True,
        help_text='The URL where the issue occurred (auto-filled or manual)'
    )

    feature = models.CharField(
        max_length=200,
        blank=True,
        help_text='Which specific feature or section? (e.g., "Vote button", "Document upload")'
    )

    priority = models.CharField(
        max_length=20,
        choices=PRIORITY_CHOICES,
        default='medium',
        blank=True,
        help_text='How severe is this issue?'
    )

    # Reproduction info
    steps_to_reproduce = models.TextField(
        blank=True,
        help_text='Steps to reproduce the issue (optional)'
    )

    expected_behavior = models.TextField(
        blank=True,
        help_text='What did you expect to happen?'
    )

    actual_behavior = models.TextField(
        blank=True,
        help_text='What actually happened?'
    )

    # Technical info (auto-captured)
    browser_info = models.CharField(
        max_length=500,
        blank=True,
        help_text='Browser and device information'
    )

    # Screenshot
    screenshot = models.ImageField(
        upload_to='bug_reports/%Y/%m/',
        blank=True,
        null=True,
        help_text='Screenshot of the issue (optional)'
    )

    # Tracking fields
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='new'
    )

    admin_notes = models.TextField(
        blank=True,
        help_text='Internal notes for administrators'
    )

    # Relationships and timestamps
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bug_reports',
        help_text='User who submitted this report'
    )

    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the issue was resolved'
    )

    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='resolved_bugs',
        help_text='Admin who resolved this issue'
    )

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Bug Report'
        verbose_name_plural = 'Bug Reports'

    def __str__(self):
        return f"Bug #{self.id}: {self.get_issue_type_display()} - {self.description[:50]}"

    def mark_resolved(self, user):
        """Mark the bug as resolved"""
        from django.utils import timezone
        self.status = 'resolved'
        self.resolved_at = timezone.now()
        self.resolved_by = user
        self.save()


class ChapterMinutes(models.Model):
    """
    Chapter meeting minutes with attendance tracking and embedded motions
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('finalized', 'Finalized'),
        ('published', 'Published'),
    ]

    VISIBILITY_CHOICES = [
        ('all_members', 'All Chapter Members'),
        ('officers_only', 'Officers Only'),
        ('custom', 'Custom Users'),
    ]

    title = models.CharField(max_length=200)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField(null=True, blank=True, help_text='Time the meeting was adjourned')
    committee = models.ForeignKey(
        Committee, on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='committee_minutes_sessions',
        help_text='If set, these are committee minutes; if null, chapter minutes'
    )
    event = models.ForeignKey(Event, on_delete=models.SET_NULL, null=True, blank=True, related_name='chapter_minutes')
    created_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, related_name='created_minutes')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    attendance_taken = models.BooleanField(default=False)
    attendance_data = models.JSONField(null=True, blank=True, help_text='Snapshot of attendance: [{user_id, name, status}, ...]')
    published_document = models.ForeignKey(CommitteeDocument, on_delete=models.SET_NULL, null=True, blank=True, related_name='source_minutes')
    publish_visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default='all_members')

    # Edit tracking for published minutes
    edited_after_publish = models.BooleanField(default=False)
    last_edit_at = models.DateTimeField(null=True, blank=True)
    last_edit_by = models.ForeignKey('ParliamentUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='edited_minutes')
    last_edit_reason = models.TextField(blank=True, help_text='Reason for editing after publication')

    class Meta:
        ordering = ['-date', '-start_time']
        verbose_name_plural = 'Chapter Minutes'

    def __str__(self):
        return f"{self.title} - {self.date}"


class MinutesSection(models.Model):
    """
    Ordered content blocks within chapter minutes (text, motion, header, or section_end)
    """
    SECTION_TYPES = [
        ('text', 'Text'),
        ('motion', 'Motion'),
        ('header', 'Section Header'),
        ('section_end', 'Section End'),
    ]

    minutes = models.ForeignKey(ChapterMinutes, on_delete=models.CASCADE, related_name='sections')
    section_type = models.CharField(max_length=20, choices=SECTION_TYPES)
    order = models.IntegerField(default=0)
    content = models.TextField(blank=True, help_text='Text content for text sections')
    title = models.CharField(max_length=200, blank=True, help_text='Title for section headers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.minutes.title} - Section {self.order} ({self.section_type})"


class MinutesMotion(models.Model):
    """
    A motion or vote recorded within chapter minutes
    """
    MOTION_TYPE_CHOICES = [
        ('custom', 'Custom Motion'),
        ('approve_prev_minutes', 'Approval of Previous Minutes'),
        ('approve_prev_minutes_uc', 'Approval of Previous Minutes by Unanimous Consent'),
        ('table_motion', 'Motion to Table'),
        ('call_question', 'Call the Question'),
        ('adjourn', 'Motion to Adjourn'),
        ('recess', 'Motion to Recess'),
        ('amend', 'Motion to Amend'),
        ('reconsider', 'Motion to Reconsider'),
        ('point_of_order', 'Point of Order'),
        ('other', 'Other'),
    ]

    VOTE_METHOD_CHOICES = [
        ('voice', 'Voice Vote'),
        ('show_of_hands', 'Show of Hands'),
        ('roll_call', 'Roll Call'),
        ('ballot', 'Ballot'),
        ('unanimous_consent', 'Unanimous Consent'),
        ('standing', 'Standing Vote'),
    ]

    RESULT_CHOICES = [
        ('passed', 'Passed'),
        ('failed', 'Failed'),
        ('tabled', 'Tabled'),
        ('withdrawn', 'Withdrawn'),
        ('referred', 'Referred to Committee'),
        ('no_vote', 'No Vote Taken'),
    ]

    CAUCUS_TYPE_CHOICES = [
        ('moderated', 'Moderated'),
        ('unmoderated', 'Unmoderated'),
    ]

    section = models.OneToOneField(MinutesSection, on_delete=models.CASCADE, related_name='motion')
    motion_type = models.CharField(max_length=30, choices=MOTION_TYPE_CHOICES, default='custom')
    motion_text = models.TextField()
    context_notes = models.TextField(blank=True, help_text='Notes relevant to this motion')
    author = models.ForeignKey('ParliamentUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='authored_motions')
    author_text = models.CharField(max_length=200, blank=True, help_text='Typed author name if not selected from dropdown')
    received_second = models.BooleanField(default=False)
    seconded_by_text = models.CharField(max_length=200, blank=True)
    vote_method = models.CharField(max_length=20, choices=VOTE_METHOD_CHOICES, default='voice')
    result = models.CharField(max_length=20, choices=RESULT_CHOICES, default='passed')
    votes_for = models.PositiveIntegerField(null=True, blank=True)
    votes_against = models.PositiveIntegerField(null=True, blank=True)
    votes_abstain = models.PositiveIntegerField(null=True, blank=True)
    caucus_held = models.BooleanField(default=False)
    caucus_duration = models.PositiveIntegerField(null=True, blank=True, help_text='Duration in minutes')
    caucus_type = models.CharField(max_length=15, choices=CAUCUS_TYPE_CHOICES, blank=True)
    speaker_time = models.PositiveIntegerField(null=True, blank=True, help_text='Seconds per speaker (moderated caucus)')

    def __str__(self):
        return f"{self.get_motion_type_display()} - {self.motion_text[:50]}"

    def get_author_display(self):
        if self.author:
            return self.author.get_display_name()
        return self.author_text or 'Unknown'


# ============================================================================
# SLATING SYSTEM MODELS
# ============================================================================
# Modular slating system for officer elections with dynamic form builder,
# configurable positions, secret ballot voting, and committee management.


class SlatingPeriod(models.Model):
    """
    Represents an election cycle with configurable dates, status, and settings.
    This is the main container for all slating-related data for a given election.
    """
    STATUS_CHOICES = [
        ('setup', 'Setup'),
        ('nominations_open', 'Nominations Open'),
        ('nominations_closed', 'Nominations Closed'),
        ('deliberation', 'Deliberation'),
        ('voting_open', 'Voting Open'),
        ('voting_closed', 'Voting Closed'),
        ('results_published', 'Results Published'),
        ('archived', 'Archived'),
    ]

    name = models.CharField(max_length=200, help_text='e.g., "Fall 2022 Officer Elections"')
    description = models.TextField(blank=True)
    academic_term = models.CharField(max_length=50, help_text='e.g., "Fall 2022"')
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='setup')

    # Configurable date windows
    nominations_open_at = models.DateTimeField(null=True, blank=True)
    nominations_close_at = models.DateTimeField(null=True, blank=True)
    deliberation_start_at = models.DateTimeField(null=True, blank=True)
    voting_open_at = models.DateTimeField(null=True, blank=True)
    voting_close_at = models.DateTimeField(null=True, blank=True)
    results_publish_at = models.DateTimeField(null=True, blank=True)

    # Voting configuration (CONFIGURABLE - not hardcoded 60%)
    required_approval_percentage = models.IntegerField(
        default=60,
        help_text='Percentage needed to approve slate (bylaws: 60%)'
    )
    max_slate_voting_attempts = models.IntegerField(
        default=3,
        help_text='Max full slate votes before individual position votes'
    )
    current_voting_attempt = models.IntegerField(default=0)
    allow_abstain = models.BooleanField(
        default=True,
        help_text='Allow members to abstain from voting'
    )

    # GPA configuration
    min_gpa_requirement = models.DecimalField(
        max_digits=3, decimal_places=2, default=2.50,
        help_text='Minimum GPA for Level 1 eligibility'
    )
    gpa_level_2_threshold = models.DecimalField(
        max_digits=3, decimal_places=2, default=0.20,
        help_text='How far below minimum for Level 2 (bylaws: 0.20)'
    )

    # Committee assignment
    slating_committee = models.ForeignKey(
        'Committee',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='slating_periods',
        help_text='Committee managing this election'
    )

    # Flexible settings as JSON
    extra_settings = models.JSONField(
        default=dict, blank=True,
        help_text='Additional configurable settings (JSON)'
    )

    # Metadata
    created_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True, related_name='created_slating_periods'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Admin transfer tracking (if President transfers admin to someone else)
    admin_transferred = models.BooleanField(default=False)
    admin_transfer_reason = models.TextField(blank=True, help_text='Reason for admin transfer (e.g., President is running)')
    admin_transferred_at = models.DateTimeField(null=True, blank=True)
    admin_transferred_from = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='transferred_slating_admin_from',
        help_text='Previous admin before transfer'
    )

    # Officer transition scheduling
    officer_transition_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the officer transition should take effect'
    )
    officer_transition_data = models.JSONField(
        default=dict, blank=True,
        help_text='Pending transition assignments (position_id -> member_id)'
    )
    officer_transition_completed = models.BooleanField(
        default=False,
        help_text='Whether the officer transition has been executed'
    )
    officer_transition_completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    def can_apply(self):
        """Check if nominations are currently open"""
        from django.utils import timezone
        now = timezone.now()
        return (
            self.status == 'nominations_open' and
            (not self.nominations_open_at or now >= self.nominations_open_at) and
            (not self.nominations_close_at or now < self.nominations_close_at)
        )

    def can_vote(self):
        """Check if voting is currently open"""
        from django.utils import timezone
        now = timezone.now()
        return (
            self.status == 'voting_open' and
            (not self.voting_open_at or now >= self.voting_open_at) and
            (not self.voting_close_at or now < self.voting_close_at)
        )

    def transition_officers(self, performed_by=None):
        """
        Transition officer roles based on election results.
        - Adds roles to newly elected officers
        - Removes roles from outgoing officers
        - Logs all changes

        Returns dict with 'added', 'removed', 'errors' lists.
        """
        if self.status != 'results_published':
            raise ValueError('Cannot transition officers until results are published')

        results = {'added': [], 'removed': [], 'errors': []}

        # Get winning candidates (from passed slate or individual votes)
        passed_slate = self.slates.filter(passed=True).first()
        if passed_slate:
            winners = passed_slate.candidates.all()
        else:
            # Individual voting - get candidates that passed individually
            winners = SlateCandidate.objects.filter(
                slate__period=self,
                individual_passed=True
            )

        for candidate in winners:
            position = candidate.position
            new_officer = candidate.application.applicant
            role = position.role

            if not role:
                results['errors'].append(f'No role linked to position {position.title}')
                continue

            try:
                # Add role to new officer
                new_officer.roles.add(role)
                results['added'].append({
                    'user': new_officer.name,
                    'role': role.name,
                    'position': position.title
                })

                # Remove role from old officers (exclude the new one)
                old_holders = ParliamentUser.objects.filter(roles=role).exclude(pk=new_officer.pk)
                for old_officer in old_holders:
                    old_officer.roles.remove(role)
                    results['removed'].append({
                        'user': old_officer.name,
                        'role': role.name
                    })

            except Exception as e:
                results['errors'].append(f'Error with {position.title}: {str(e)}')

        # Log the transition
        SlatingActivity.objects.create(
            period=self,
            user=performed_by,
            action='officers_transitioned',
            details=f"Added: {len(results['added'])}, Removed: {len(results['removed'])}, Errors: {len(results['errors'])}",
            metadata=results
        )

        return results


class SlatingPosition(models.Model):
    """
    Configurable position within a slating period.
    Linked to Role model but allows period-specific customization.
    """
    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='positions'
    )
    role = models.ForeignKey(
        'Role',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        help_text='Link to existing Role (optional)'
    )

    title = models.CharField(max_length=200)
    code = models.CharField(max_length=20)
    description = models.TextField(blank=True)

    # Position-specific requirements (override period defaults)
    min_gpa = models.DecimalField(
        max_digits=3, decimal_places=2, null=True, blank=True,
        help_text='Position-specific GPA requirement (overrides period default)'
    )
    requires_prior_experience = models.BooleanField(default=False)
    min_semesters_active = models.IntegerField(default=0)

    # Eligibility restrictions as JSON (flexible)
    eligible_member_types = models.JSONField(
        default=list, blank=True,
        help_text='List of member types eligible (empty = all)'
    )
    eligible_class_years = models.JSONField(
        default=list, blank=True,
        help_text='List of eligible class years (empty = all)'
    )
    custom_requirements = models.JSONField(
        default=dict, blank=True,
        help_text='Position-specific custom requirements'
    )

    display_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['display_order', 'title']
        unique_together = ['period', 'code']

    def __str__(self):
        return f"{self.title} ({self.period.name})"


class SlatingFormField(models.Model):
    """
    Dynamic form field definition for application forms.
    Chair/admin can create custom fields without code changes.
    This is the core of the configurable form system.
    """
    FIELD_TYPES = [
        ('text', 'Text (Single Line)'),
        ('textarea', 'Text Area (Multi-line)'),
        ('number', 'Number'),
        ('decimal', 'Decimal Number'),
        ('email', 'Email Address'),
        ('phone', 'Phone Number'),
        ('date', 'Date'),
        ('datetime', 'Date & Time'),
        ('select', 'Dropdown Select'),
        ('multiselect', 'Multi-Select'),
        ('checkbox', 'Checkbox'),
        ('radio', 'Radio Buttons'),
        ('file', 'File Upload'),
        ('image', 'Image Upload'),
        ('gpa', 'GPA Entry (with screenshot)'),
        ('position_preference', 'Position Preference Ranking'),
        ('member_select', 'Member Selection'),
    ]

    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='form_fields'
    )

    # Field Definition
    field_name = models.CharField(max_length=100, help_text='Internal field name (no spaces)')
    label = models.CharField(max_length=200, help_text='Display label')
    field_type = models.CharField(max_length=30, choices=FIELD_TYPES)
    placeholder = models.CharField(max_length=200, blank=True)
    help_text = models.TextField(blank=True)

    # Options (for select, multiselect, radio, checkbox)
    options = models.JSONField(
        default=list, blank=True,
        help_text='List of options for select/radio/checkbox fields'
    )

    # Validation
    is_required = models.BooleanField(default=False)
    validation_rules = models.JSONField(
        default=list, blank=True,
        help_text='List of validation rules with parameters'
    )

    # File Upload Settings
    allowed_file_types = models.JSONField(
        default=list, blank=True,
        help_text='Allowed MIME types for file uploads'
    )
    max_file_size_mb = models.IntegerField(default=10)

    # Display Settings
    display_order = models.IntegerField(default=0)
    section = models.CharField(
        max_length=100, blank=True,
        help_text='Group fields into sections'
    )
    show_in_review = models.BooleanField(
        default=True,
        help_text='Show this field in application review'
    )
    is_confidential = models.BooleanField(
        default=False,
        help_text='Only show to slating committee'
    )

    # Conditional Display
    depends_on_field = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        help_text='Only show if another field has specific value'
    )
    depends_on_value = models.JSONField(
        default=list, blank=True,
        help_text='Values that trigger this field to show'
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['section', 'display_order']
        unique_together = ['period', 'field_name']

    def __str__(self):
        return f"{self.label} ({self.field_type})"


class SlatingApplication(models.Model):
    """
    A candidate's application for a slating period.
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('under_review', 'Under Review'),
        ('interview_scheduled', 'Interview Scheduled'),
        ('interviewed', 'Interviewed'),
        ('recommended', 'Recommended'),
        ('not_recommended', 'Not Recommended'),
        ('withdrawn', 'Withdrawn'),
        ('slated', 'Slated'),
    ]

    GPA_LEVEL_CHOICES = [
        (1, 'Level 1 - Meets or exceeds minimum'),
        (2, 'Level 2 - Within 0.20 below minimum'),
        (3, 'Level 3 - Below Level 2'),
    ]

    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='applications'
    )
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='slating_applications'
    )

    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    # Position Preferences (tiered categories)
    position_preferences = models.JSONField(
        default=dict,
        help_text='Tiered position preferences: {first_choice: [], second_choice: [], third_choice: [], do_not_want: []}'
    )

    # GPA Information
    reported_gpa = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    gpa_verified = models.BooleanField(default=False)
    gpa_level = models.IntegerField(choices=GPA_LEVEL_CHOICES, null=True, blank=True)
    gpa_screenshot = models.FileField(
        upload_to='slating/gpa_screenshots/',
        null=True, blank=True,
        storage=DualLocationStorage()
    )

    # Timestamps
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Committee Review
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_slating_applications'
    )
    review_notes = models.TextField(blank=True, help_text='Confidential review notes')

    class Meta:
        ordering = ['-submitted_at', '-created_at']
        unique_together = ['period', 'applicant']

    def __str__(self):
        return f"{self.applicant.name} - {self.period.name}"

    def calculate_gpa_level(self):
        """Calculate GPA level based on period settings"""
        if not self.reported_gpa:
            return None

        min_gpa = self.period.min_gpa_requirement
        level_2_threshold = self.period.gpa_level_2_threshold

        if self.reported_gpa >= min_gpa:
            return 1
        elif self.reported_gpa >= (min_gpa - level_2_threshold):
            return 2
        else:
            return 3

    def get_preferred_positions(self):
        """
        Get all wanted positions in preference order (first_choice, second_choice, third_choice).
        Returns list of position IDs, excluding do_not_want.
        """
        prefs = self.position_preferences or {}

        # Handle legacy format (simple list)
        if isinstance(prefs, list):
            return prefs

        # New tiered format
        result = []
        for tier in ['first_choice', 'second_choice', 'third_choice']:
            result.extend(prefs.get(tier, []))
        return result

    def get_first_choice_positions(self):
        """Get first choice position IDs."""
        prefs = self.position_preferences or {}
        if isinstance(prefs, list):
            return prefs[:1] if prefs else []
        return prefs.get('first_choice', [])

    def get_position_tier(self, position_id):
        """
        Get the preference tier for a specific position.
        Returns 'first_choice', 'second_choice', 'third_choice', 'do_not_want', or None.
        """
        prefs = self.position_preferences or {}

        # Handle legacy format
        if isinstance(prefs, list):
            if position_id in prefs:
                idx = prefs.index(position_id)
                if idx == 0:
                    return 'first_choice'
                elif idx < len(prefs) // 2:
                    return 'second_choice'
                else:
                    return 'third_choice'
            return None

        # New tiered format
        for tier in ['first_choice', 'second_choice', 'third_choice', 'do_not_want']:
            if position_id in prefs.get(tier, []):
                return tier
        return None


class SlatingApplicationResponse(models.Model):
    """
    Stores individual field responses for an application.
    Enables fully dynamic forms without schema changes.
    """
    application = models.ForeignKey(
        SlatingApplication,
        on_delete=models.CASCADE,
        related_name='responses'
    )
    field = models.ForeignKey(
        SlatingFormField,
        on_delete=models.CASCADE,
        related_name='responses'
    )

    # Store all types as text/JSON for flexibility
    text_value = models.TextField(blank=True, null=True)
    number_value = models.DecimalField(max_digits=20, decimal_places=5, null=True, blank=True)
    json_value = models.JSONField(null=True, blank=True)  # For arrays, objects
    file_value = models.FileField(
        upload_to='slating/application_files/',
        null=True, blank=True,
        storage=DualLocationStorage()
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['application', 'field']

    def get_display_value(self):
        """Return appropriate value based on field type"""
        if self.field.field_type in ['select', 'radio']:
            return self.text_value
        elif self.field.field_type in ['multiselect', 'checkbox']:
            return self.json_value or []
        elif self.field.field_type in ['number', 'decimal', 'gpa']:
            return self.number_value
        elif self.field.field_type in ['file', 'image']:
            return self.file_value.url if self.file_value else None
        else:
            return self.text_value


class SlatingInterview(models.Model):
    """
    Interview tracking for slating applications.
    Notes are confidential and destroyed after approval.
    """
    RECOMMENDATION_CHOICES = [
        ('strongly_recommend', 'Strongly Recommend'),
        ('recommend', 'Recommend'),
        ('neutral', 'Neutral'),
        ('not_recommend', 'Do Not Recommend'),
        ('strongly_not_recommend', 'Strongly Do Not Recommend'),
    ]

    application = models.ForeignKey(
        SlatingApplication,
        on_delete=models.CASCADE,
        related_name='interviews'
    )

    # Scheduling
    scheduled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    location = models.CharField(max_length=200, blank=True)

    # Interview Panel
    interviewers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='conducted_slating_interviews'
    )

    # CONFIDENTIAL Notes (destroyed after minutes approval)
    notes = models.TextField(blank=True, help_text='CONFIDENTIAL interview notes')
    strengths = models.TextField(blank=True)
    concerns = models.TextField(blank=True)

    # Recommendation
    recommendation = models.CharField(
        max_length=25,
        choices=RECOMMENDATION_CHOICES,
        blank=True
    )
    recommended_positions = models.ManyToManyField(
        SlatingPosition,
        blank=True,
        related_name='recommended_candidates'
    )

    # Confidentiality tracking
    notes_destroyed = models.BooleanField(default=False)
    destroyed_at = models.DateTimeField(null=True, blank=True)
    destroyed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='destroyed_slating_notes'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-scheduled_at']

    def destroy_notes(self, user):
        """Destroy confidential notes after minutes approval"""
        from django.utils import timezone
        self.notes = '[DESTROYED - Slate Approved]'
        self.strengths = ''
        self.concerns = ''
        self.notes_destroyed = True
        self.destroyed_at = timezone.now()
        self.destroyed_by = user
        self.save()


class Slate(models.Model):
    """
    A complete slate for chapter voting.
    Multiple slates can exist (primary, alternatives) per period.
    """
    SLATE_TYPES = [
        ('primary', 'Primary Slate'),
        ('alternative', 'Alternative Slate'),
        ('draft', 'Draft Slate'),
    ]

    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='slates'
    )

    name = models.CharField(max_length=200, default='Primary Slate')
    slate_type = models.CharField(max_length=20, choices=SLATE_TYPES, default='primary')
    description = models.TextField(blank=True)

    # Approval tracking
    is_approved = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='approved_slates'
    )

    # Voting results
    total_votes = models.IntegerField(default=0)
    approval_votes = models.IntegerField(default=0)
    rejection_votes = models.IntegerField(default=0)
    abstain_votes = models.IntegerField(default=0)
    approval_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    passed = models.BooleanField(null=True)

    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_slates'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.period.name}"

    def calculate_results(self):
        """Calculate voting results from SlatingVote records"""
        from django.db.models import Count
        votes = self.votes.filter(voting_attempt=self.period.current_voting_attempt)
        self.total_votes = votes.count()
        self.approval_votes = votes.filter(vote_choice='approve').count()
        self.rejection_votes = votes.filter(vote_choice='reject').count()
        self.abstain_votes = votes.filter(vote_choice='abstain').count()

        # Calculate percentage (excluding abstentions)
        counted_votes = self.approval_votes + self.rejection_votes
        if counted_votes > 0:
            self.approval_percentage = (self.approval_votes / counted_votes) * 100
            self.passed = self.approval_percentage >= self.period.required_approval_percentage
        else:
            self.approval_percentage = None
            self.passed = None

        self.save()


class SlateCandidate(models.Model):
    """
    A candidate assigned to a position on a slate.
    """
    slate = models.ForeignKey(
        Slate,
        on_delete=models.CASCADE,
        related_name='candidates'
    )
    position = models.ForeignKey(
        SlatingPosition,
        on_delete=models.CASCADE,
        related_name='slate_assignments'
    )
    application = models.ForeignKey(
        SlatingApplication,
        on_delete=models.CASCADE,
        related_name='slate_assignments'
    )

    # For individual position voting (fallback)
    individual_votes_for = models.IntegerField(default=0)
    individual_votes_against = models.IntegerField(default=0)
    individual_passed = models.BooleanField(null=True)

    display_order = models.IntegerField(default=0)
    notes = models.TextField(blank=True, help_text='Public notes about this assignment')

    class Meta:
        ordering = ['display_order']
        unique_together = ['slate', 'position']  # One candidate per position per slate

    def __str__(self):
        return f"{self.application.applicant.name} for {self.position.title}"


class SlatingBallot(models.Model):
    """
    Tracks who has voted (but not how they voted) for audit.
    Implements secret ballot requirement.
    """
    VOTE_TYPE_CHOICES = [
        ('slate', 'Full Slate Vote'),
        ('individual', 'Individual Position Vote'),
    ]

    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='ballots'
    )
    voter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='slating_ballots'
    )

    # Track voting attempt
    voting_attempt = models.IntegerField(default=1)

    # Whether this is a full slate vote or individual position vote
    vote_type = models.CharField(max_length=20, choices=VOTE_TYPE_CHOICES, default='slate')

    # If individual vote, which position
    position = models.ForeignKey(
        SlatingPosition,
        on_delete=models.SET_NULL,
        null=True, blank=True
    )

    # Timestamp and verification
    voted_at = models.DateTimeField(auto_now_add=True)
    ballot_hash = models.CharField(
        max_length=64, unique=True,
        help_text='Hash for vote verification without revealing vote'
    )

    class Meta:
        # One ballot per voter per attempt per type
        unique_together = ['period', 'voter', 'voting_attempt', 'vote_type', 'position']

    def __str__(self):
        return f"Ballot: {self.voter.name} - {self.period.name} (Attempt {self.voting_attempt})"


class SlatingVote(models.Model):
    """
    Anonymous vote record. No direct link to voter.
    """
    VOTE_CHOICES = [
        ('approve', 'Approve'),
        ('reject', 'Reject'),
        ('abstain', 'Abstain'),
    ]

    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='votes'
    )

    # Slate-level vote
    slate = models.ForeignKey(
        Slate,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='votes'
    )

    # Or individual position vote
    slate_candidate = models.ForeignKey(
        SlateCandidate,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='votes'
    )

    voting_attempt = models.IntegerField(default=1)
    vote_choice = models.CharField(max_length=10, choices=VOTE_CHOICES)

    # For rejection votes - which positions are being rejected
    rejected_positions = models.JSONField(
        default=list,
        blank=True,
        help_text='List of position IDs that the voter objects to (required for reject votes)'
    )

    # Anonymous tracking
    vote_hash = models.CharField(max_length=64, unique=True)
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-voted_at']


class SlatingActivity(models.Model):
    """
    Activity log for slating actions.
    """
    ACTION_CHOICES = [
        ('period_created', 'Period Created'),
        ('period_status_changed', 'Period Status Changed'),
        ('position_added', 'Position Added'),
        ('position_modified', 'Position Modified'),
        ('form_field_added', 'Form Field Added'),
        ('form_field_modified', 'Form Field Modified'),
        ('application_submitted', 'Application Submitted'),
        ('application_reviewed', 'Application Reviewed'),
        ('interview_scheduled', 'Interview Scheduled'),
        ('interview_completed', 'Interview Completed'),
        ('slate_created', 'Slate Created'),
        ('slate_modified', 'Slate Modified'),
        ('voting_opened', 'Voting Opened'),
        ('vote_cast', 'Vote Cast'),  # Generic, no details about how
        ('voting_closed', 'Voting Closed'),
        ('results_published', 'Results Published'),
        ('notes_destroyed', 'Confidential Notes Destroyed'),
        ('admin_transferred', 'Admin Transferred'),
        ('officers_transitioned', 'Officers Transitioned'),
    ]

    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='activity_log'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    details = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name_plural = 'Slating Activities'

    def __str__(self):
        return f"{self.get_action_display()} - {self.period.name}"


# Import feature flags models
from src.models_feature_flags import FeatureFlag, PageToggle
