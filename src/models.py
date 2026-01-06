from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.core.exceptions import ValidationError
import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.conf import settings
from src.storage import DualLocationStorage
from src.encrypted_fields import EncryptedCharField, EncryptedEmailField

logger = logging.getLogger('function_calls')

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
    email = EncryptedEmailField(max_length=254, blank=True, null=True, unique=True, help_text='Encrypted email address for password reset and notifications')
    anonymous_vote = models.BooleanField(default=False)
    allow_abstain = models.BooleanField(default=True)
    roles = models.ManyToManyField(Role, blank=True)

    member_status = models.CharField(max_length=20, choices=MEMBER_STATUS, default='Active')
    force_password_change = models.BooleanField(default=False, help_text='User must change password on next login')

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
        """Check if user can view officer pages (Officers and Advisors)"""
        return self.is_officer or self.is_advisor

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
    available_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
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

class CommitteePermissions(models.Model):
    committee = models.ForeignKey(Committee, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    can_view_docs = models.BooleanField(default=False)
    can_upload_docs = models.BooleanField(default=False)
    can_vote = models.BooleanField(default=False)
    can_manage_members = models.BooleanField(default=False)
    can_view_results = models.BooleanField(default=True)


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
    available_at = models.DateTimeField()
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

    committee = models.ForeignKey(Committee, on_delete=models.CASCADE, related_name='documents')
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
        return f"{self.committee.code} - {self.title}"

    def get_version_string(self):
        """Return formatted version string like 'v1.0'"""
        return f"v{self.version_number}.0"

    def can_user_view(self, user):
        """Check if a user has permission to view this document"""
        # Admins and the uploader can always view
        if user.is_admin or user == self.uploaded_by:
            return True

        # Check based on visibility setting
        if self.visibility == 'all_members':
            return True
        elif self.visibility == 'committee_only':
            return user in self.committee.members.all()
        elif self.visibility == 'chairs_only':
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


class UserAnnouncementView(models.Model):
    """Track which announcements users have seen/dismissed"""
    user = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE)
    viewed_at = models.DateTimeField(auto_now_add=True)
    dismissed = models.BooleanField(default=False, help_text='User has dismissed this notification')

    class Meta:
        unique_together = ('user', 'announcement')
        ordering = ['-viewed_at']

    def __str__(self):
        return f"{self.user.name} - {self.announcement.title}"


class Event(models.Model):
    """Model for calendar events - officers can create, all members can view"""
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
            return self.committee.is_member(user)

        if self.access_type == 'restricted':
            # Check custom permissions
            return ChatChannelPermission.objects.filter(
                channel=self,
                user=user
            ).exists() or ChatChannelPermission.objects.filter(
                channel=self,
                member_type=user.member_type
            ).exists() or (
                ChatChannelPermission.objects.filter(
                    channel=self,
                    chairs_only=True
                ).exists() and user.chair_roles.exists()
            ) or (
                ChatChannelPermission.objects.filter(
                    channel=self,
                    officers_only=True
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

        # Committee members always have read access
        if self.committee and self.committee.is_member(user):
            return True

        # Admins always have access
        if user.is_admin:
            return True

        # Check if user has specific permission
        if self.access_type == 'open':
            return True

        if self.access_type == 'restricted':
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

        # Committee members always have write access
        if self.committee and self.committee.is_member(user):
            return True

        # Admins always have access
        if user.is_admin:
            return True

        # Check if user has specific permission
        if self.access_type == 'open':
            return True

        if self.access_type == 'restricted':
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

        # Committee members always have delete access
        if self.committee and self.committee.is_member(user):
            return True

        # Admins always have access
        if user.is_admin:
            return True

        # Chairs can always delete
        if self.committee and self.committee.is_chair(user):
            return True

        # Check if user has specific permission
        if self.access_type == 'open':
            return True

        if self.access_type == 'restricted':
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
        ('thrown_out', 'Case Thrown Out'),
        ('heard', 'Case Heard'),
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
        help_text='Login attempt that triggered this alert'
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


# Import feature flags models
from src.models_feature_flags import FeatureFlag, PageToggle
