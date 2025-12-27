from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.core.exceptions import ValidationError
import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.conf import settings
from src.storage import DualLocationStorage

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
    username = models.CharField(max_length=100, unique=True)
    email = models.EmailField(max_length=254, blank=True, null=True, unique=True, help_text='Email address for password reset and notifications')
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
    user = models.ForeignKey(ParliamentUser, on_delete=models.CASCADE, limit_choices_to={'member_status': 'Active'})
    date = models.DateField(auto_now_add=True)
    present = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

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
        (8, 'RISK', 'Risk Management Committee'),
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


class CommitteeDocument(models.Model):
    DOCUMENT_TYPES = [
        ('general', 'General Document'),
        ('minutes', 'Meeting Minutes'),
        ('agenda', 'Meeting Agenda'),
        ('report', 'Report'),
        ('policy', 'Policy Document'),
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

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f"{self.committee.code} - {self.title}"


class Announcement(models.Model):
    """Model for officer announcements and event notifications"""
    title = models.CharField(max_length=200)
    content = models.TextField()
    posted_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    posted_at = models.DateTimeField(auto_now_add=True)
    publish_at = models.DateTimeField(null=True, blank=True, help_text='Schedule when this announcement should be published. Leave blank to publish immediately.')
    event_date = models.DateTimeField(null=True, blank=True, help_text='Optional event date/time')
    is_active = models.BooleanField(default=True, help_text='Uncheck to hide announcement')

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


class Event(models.Model):
    """Model for calendar events - officers can create, all members can view"""
    title = models.CharField(max_length=200)
    description = models.TextField()
    date_time = models.DateTimeField(help_text='Date and time of the event')
    location = models.CharField(max_length=300, blank=True, help_text='Event location (physical or virtual)')
    created_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True, help_text='Uncheck to hide event from calendar')
    archived = models.BooleanField(default=False, help_text='Events older than 1 year are automatically archived')

    class Meta:
        ordering = ['date_time']

    def __str__(self):
        return f"{self.title} - {self.date_time.strftime('%Y-%m-%d %H:%M')}"

    def is_upcoming(self):
        """Check if event is in the future"""
        from django.utils import timezone
        return self.date_time > timezone.now()


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

    # Report Details
    title = models.CharField(max_length=255, help_text="Brief title for the report")
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