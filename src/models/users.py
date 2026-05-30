import os
import logging
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from src.constants import MemberType, MemberStatus

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
    grants_admin = models.BooleanField(
        default=False,
        help_text='If True, users with this role will automatically receive admin privileges when officer admins are synced'
    )

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
        ('Removed', 'Removed'),
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
    has_default_password = models.BooleanField(default=False, help_text='Password is still the system-assigned default — set False when user changes it')
    is_quarantined = models.BooleanField(default=False, help_text='Account quarantined due to suspicious activity')
    email_flagged = models.BooleanField(default=False, help_text='Email address flagged as undeliverable — user prompted to update it')
    email_flagged_reason = models.TextField(blank=True, help_text='Reason the email address was flagged (e.g. delivery error message)')
    email_flagged_at = models.DateTimeField(null=True, blank=True, help_text='When the email address was flagged')
    role_number = models.CharField(
        max_length=30,
        unique=True,
        blank=True,
        null=True,
        help_text='Member roll number assigned at initiation (unique identifier visible to members)'
    )

    # Extended profile fields (all optional)
    about_me = models.TextField(blank=True, help_text='Short bio visible to other members')
    majors = models.JSONField(default=list, blank=True, help_text='List of major fields of study')
    minors = models.JSONField(default=list, blank=True, help_text='List of minor fields of study')
    concentrations = models.JSONField(default=list, blank=True, help_text='List of concentrations')
    big_brother = models.ForeignKey(
        'self',
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='little_brothers',
        help_text='Big brother in the fraternity'
    )
    pledge_class = models.CharField(max_length=30, blank=True, help_text='e.g. "Spring 2024"')
    pledge_class_greek = models.CharField(max_length=50, blank=True, help_text='Greek letter/name for the class (e.g. "Beta")')
    graduation_year = models.PositiveSmallIntegerField(null=True, blank=True)
    graduation_semester = models.CharField(
        max_length=6, blank=True,
        choices=[('Fall', 'Fall'), ('Spring', 'Spring')],
    )
    # Social handles (store handle only, not full URL)
    instagram = models.CharField(max_length=60, blank=True)
    twitter = models.CharField(max_length=60, blank=True)
    linkedin = models.CharField(max_length=100, blank=True)
    snapchat = models.CharField(max_length=60, blank=True)
    facebook = models.CharField(max_length=100, blank=True)
    other_email = models.EmailField(max_length=254, blank=True, null=True, help_text='Secondary contact email')
    # Custom social handles: list of {"platform": str, "handle": str} dicts
    custom_socials = models.JSONField(default=list, blank=True)
    # Initiation chapters: list of {"school": str, "chapter": str} dicts
    # If empty, defaults to display "Alpha Mu — Samford University"
    initiation_chapters = models.JSONField(default=list, blank=True)

    # House — fixed choices, assigned by officers/historian chair only
    HOUSE_CHOICES = [
        ('Smith',    'Smith'),
        ('Duncan',   'Duncan'),
        ('Knox',     'Knox'),
        ('Marshall', 'Marshall'),
        ('Linton',   'Linton'),
        ('Hardin',   'Hardin'),
        ('Ryan',     'Ryan'),
        ('Gordon',   'Gordon'),
    ]
    house = models.CharField(max_length=20, blank=True, choices=HOUSE_CHOICES,
                             help_text='Chapter house assignment — set by officers/historian chair')

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
        return self.member_type == MemberType.OFFICER or self.is_admin

    @property
    def is_advisor(self):
        """Check if user is an advisor"""
        return self.member_type == MemberType.ADVISOR

    @property
    def is_pledge(self):
        """Check if user is a pledge"""
        return self.member_type == MemberType.PLEDGE

    @property
    def can_vote(self):
        """Check if user is allowed to vote (excludes pledges)"""
        return self.member_type in MemberType.CAN_VOTE and not self.is_pledge

    @property
    def can_view_officer_pages(self):
        """Check if user can view officer pages (Officers, Chairs, and Advisors)"""
        return self.is_officer or self.is_advisor or self.member_type == MemberType.CHAIR

    @property
    def can_manage_events(self):
        """Check if user can create/manage events (Officers and Chairs)"""
        return self.is_officer or self.member_type == MemberType.CHAIR

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

    def check_is_default_password(self):
        """
        Bcrypt-based check — used only in the data migration to backfill
        has_default_password for existing users. Do not call at request time.
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


class RoleHistory(models.Model):
    """Tracks positions a member has held (officer, chair, etc.)."""
    SEMESTER_CHOICES = [
        ('Spring', 'Spring'),
        ('Fall', 'Fall'),
        ('Summer', 'Summer'),
    ]

    user = models.ForeignKey(ParliamentUser, on_delete=models.CASCADE, related_name='role_history')
    role_name = models.CharField(max_length=100, help_text='e.g. "President"')
    start_semester = models.CharField(max_length=20, help_text='e.g. "Spring 2026"')
    end_semester = models.CharField(max_length=20, blank=True, help_text='Leave blank if current')

    class Meta:
        ordering = ['-start_semester']

    def __str__(self):
        end = self.end_semester or 'present'
        return f'{self.user.name} — {self.role_name} ({self.start_semester}–{end})'


def _default_user_prefs():
    """Returns the full default preferences structure for a new user."""
    return {
        'email': {
            'announcements': True,
            'legislation': True,
            'events': True,
            'committee_updates': True,
        },
        'display': {
            'compact_view': False,
            'announcement_popups': True,
            'home_layout': 'modern',
            'landing_page': 'home',
        },
        'menu': {
            'vote': True,
            'committees': True,
            'chats': False,
            'documents': True,
            'announcements': True,
            'calendar': True,
            'legislation': True,
            'excuses': False,
            'search': True,
            'roberts_rules': False,
        },
        'notifications': {
            'announcements': True,
            'legislation': True,
            'events': True,
            'slating': True,
        },
        'push': {
            'announcements': True,
            'legislation': True,
            'events': True,
            'slating': True,
        },
    }


class UserPreferences(models.Model):
    """
    User preferences for customizing their Parliament experience.

    All boolean preferences are stored in a single ``prefs`` JSONField with the structure:
        {
            "email":         { "announcements": bool, "legislation": bool, "events": bool, "committee_updates": bool },
            "display":       { "compact_view": bool, "announcement_popups": bool },
            "menu":          { "vote": bool, "committees": bool, "chats": bool, "documents": bool,
                               "announcements": bool, "calendar": bool, "legislation": bool,
                               "excuses": bool, "search": bool, "roberts_rules": bool },
            "notifications": { "announcements": bool, "legislation": bool, "events": bool, "slating": bool },
        }

    Adding a new preference requires only a default value here and a UI change — no schema migration.
    Named properties expose the individual keys so existing code and templates don't need to change.
    """
    THEME_CHOICES = (
        ('light', 'Light'),
        ('dark', 'Dark'),
        ('auto', 'Auto (System)'),
    )

    user = models.OneToOneField(ParliamentUser, on_delete=models.CASCADE, related_name='preferences', primary_key=True)

    # Theme is kept as its own field (non-boolean, has choices)
    theme = models.CharField(max_length=10, choices=THEME_CHOICES, default='light')

    # All boolean preferences in a single JSON column
    prefs = models.JSONField(default=_default_user_prefs)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Preferences for {self.user.name}"

    class Meta:
        verbose_name = 'User Preferences'
        verbose_name_plural = 'User Preferences'

    @staticmethod
    def get_defaults():
        """Return a fresh copy of the default preferences dict."""
        return _default_user_prefs()

    def _pref(self, section, key, default):
        """Read a single preference value, falling back to default if not set."""
        return (self.prefs or {}).get(section, {}).get(key, default)

    # --- Email notification preferences ---
    @property
    def email_announcements(self):
        return self._pref('email', 'announcements', True)

    @property
    def email_legislation(self):
        return self._pref('email', 'legislation', True)

    @property
    def email_events(self):
        return self._pref('email', 'events', True)

    @property
    def email_committee_updates(self):
        return self._pref('email', 'committee_updates', True)

    # --- Display preferences ---
    @property
    def show_announcement_popups(self):
        return self._pref('display', 'announcement_popups', True)

    @property
    def compact_view(self):
        return self._pref('display', 'compact_view', False)

    @property
    def home_layout(self):
        return self._pref('display', 'home_layout', 'modern')

    @property
    def landing_page(self):
        return self._pref('display', 'landing_page', 'home')

    # --- Menu preferences ---
    @property
    def show_vote_menu(self):
        return self._pref('menu', 'vote', True)

    @property
    def show_committees_menu(self):
        return self._pref('menu', 'committees', True)

    @property
    def show_chats_menu(self):
        return self._pref('menu', 'chats', False)

    @property
    def show_documents_menu(self):
        return self._pref('menu', 'documents', True)

    @property
    def show_announcements_menu(self):
        return self._pref('menu', 'announcements', True)

    @property
    def show_calendar_menu(self):
        return self._pref('menu', 'calendar', True)

    @property
    def show_legislation_menu(self):
        return self._pref('menu', 'legislation', True)

    @property
    def show_excuses_menu(self):
        return self._pref('menu', 'excuses', False)

    @property
    def show_search_menu(self):
        return self._pref('menu', 'search', True)

    @property
    def show_roberts_rules_menu(self):
        return self._pref('menu', 'roberts_rules', False)

    # --- In-app notification preferences ---
    @property
    def notify_announcements(self):
        return self._pref('notifications', 'announcements', True)

    @property
    def notify_legislation(self):
        return self._pref('notifications', 'legislation', True)

    @property
    def notify_events(self):
        return self._pref('notifications', 'events', True)

    @property
    def notify_slating(self):
        return self._pref('notifications', 'slating', True)

    # --- Push notification preferences ---
    @property
    def push_announcements(self):
        return self._pref('push', 'announcements', True)

    @property
    def push_legislation(self):
        return self._pref('push', 'legislation', True)

    @property
    def push_events(self):
        return self._pref('push', 'events', True)

    @property
    def push_slating(self):
        return self._pref('push', 'slating', True)


# Signal to auto-create UserPreferences when a user is created
@receiver(post_save, sender=ParliamentUser)
def create_user_preferences(sender, instance, created, **kwargs):
    if created:
        UserPreferences.objects.get_or_create(user=instance)


class TwoFactorRequirement(models.Model):
    """
    Track 2FA requirements for individual members.
    Allows per-user overrides to the global 2FA policy.
    """
    REQUIREMENT_CHOICES = (
        ('required', 'Required'),
        ('exempt', 'Exempt'),
    )

    user = models.OneToOneField(
        'ParliamentUser',
        on_delete=models.CASCADE,
        related_name='two_factor_requirement'
    )
    requirement = models.CharField(
        max_length=20,
        choices=REQUIREMENT_CHOICES,
        help_text='Whether this user is required to have 2FA or is exempt'
    )
    reason = models.TextField(
        blank=True,
        help_text='Reason for this requirement/exemption'
    )
    set_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='two_factor_requirements_set',
        help_text='Admin who set this requirement'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Two-Factor Requirement'
        verbose_name_plural = 'Two-Factor Requirements'

    def __str__(self):
        return f"{self.user.name} - {self.get_requirement_display()}"


class UserSession(models.Model):
    """
    Track active user sessions for security monitoring.
    Allows users to view their active sessions and log out remotely.
    """
    user = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.CASCADE,
        related_name='sessions'
    )
    session_key = models.CharField(max_length=40, unique=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    device_type = models.CharField(max_length=50, blank=True)  # mobile, tablet, desktop
    browser = models.CharField(max_length=100, blank=True)
    operating_system = models.CharField(max_length=100, blank=True)
    location = models.CharField(max_length=200, blank=True)  # City, Country (if available)
    created_at = models.DateTimeField(auto_now_add=True)
    last_activity = models.DateTimeField(auto_now=True)
    is_current = models.BooleanField(default=False)  # Mark the current session

    class Meta:
        ordering = ['-last_activity']
        verbose_name = 'User Session'
        verbose_name_plural = 'User Sessions'

    def __str__(self):
        return f"{self.user.name} - {self.device_type} ({self.ip_address})"

    @classmethod
    def parse_user_agent(cls, user_agent_string):
        """Parse user agent string to extract device, browser, and OS info."""
        device_type = 'desktop'
        browser = 'Unknown'
        operating_system = 'Unknown'

        if not user_agent_string:
            return device_type, browser, operating_system

        ua_lower = user_agent_string.lower()

        # Detect device type
        if 'mobile' in ua_lower or 'android' in ua_lower and 'mobile' in ua_lower:
            device_type = 'mobile'
        elif 'tablet' in ua_lower or 'ipad' in ua_lower:
            device_type = 'tablet'

        # Detect browser
        if 'edg/' in ua_lower or 'edge/' in ua_lower:
            browser = 'Microsoft Edge'
        elif 'chrome/' in ua_lower and 'safari/' in ua_lower:
            browser = 'Chrome'
        elif 'firefox/' in ua_lower:
            browser = 'Firefox'
        elif 'safari/' in ua_lower and 'chrome/' not in ua_lower:
            browser = 'Safari'
        elif 'opera' in ua_lower or 'opr/' in ua_lower:
            browser = 'Opera'
        elif 'msie' in ua_lower or 'trident/' in ua_lower:
            browser = 'Internet Explorer'

        # Detect OS
        if 'windows nt 10' in ua_lower:
            operating_system = 'Windows 10/11'
        elif 'windows nt' in ua_lower:
            operating_system = 'Windows'
        elif 'mac os x' in ua_lower:
            operating_system = 'macOS'
        elif 'iphone' in ua_lower:
            operating_system = 'iOS'
        elif 'ipad' in ua_lower:
            operating_system = 'iPadOS'
        elif 'android' in ua_lower:
            operating_system = 'Android'
        elif 'linux' in ua_lower:
            operating_system = 'Linux'

        return device_type, browser, operating_system

    @classmethod
    def create_or_update_session(cls, user, request):
        """Create or update a session record for the user."""
        session_key = request.session.session_key
        if not session_key:
            request.session.create()
            session_key = request.session.session_key

        # Get IP address
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip_address = x_forwarded_for.split(',')[-1].strip()
        else:
            ip_address = request.META.get('REMOTE_ADDR')

        user_agent = request.META.get('HTTP_USER_AGENT', '')[:500]
        device_type, browser, operating_system = cls.parse_user_agent(user_agent)

        session, created = cls.objects.update_or_create(
            session_key=session_key,
            defaults={
                'user': user,
                'ip_address': ip_address,
                'user_agent': user_agent,
                'device_type': device_type,
                'browser': browser,
                'operating_system': operating_system,
            }
        )

        return session

    @classmethod
    def cleanup_expired_sessions(cls):
        """Remove session records for expired Django sessions."""
        from django.conf import settings
        from django.utils import timezone
        from datetime import timedelta

        session_engine = getattr(settings, 'SESSION_ENGINE', 'django.contrib.sessions.backends.db')

        if session_engine == 'django.contrib.sessions.backends.db':
            # DB sessions: efficiently batch-query the session table
            from django.contrib.sessions.models import Session
            valid_keys = Session.objects.filter(
                expire_date__gt=timezone.now()
            ).values_list('session_key', flat=True)
            deleted_count, _ = cls.objects.exclude(
                session_key__in=list(valid_keys)
            ).delete()
        else:
            # Cache/Redis sessions: the DB session table is empty so we can't
            # use it. Fall back to expiring records older than SESSION_COOKIE_AGE.
            session_age = getattr(settings, 'SESSION_COOKIE_AGE', 1209600)
            cutoff = timezone.now() - timedelta(seconds=session_age)
            deleted_count, _ = cls.objects.filter(last_activity__lt=cutoff).delete()

        return deleted_count
