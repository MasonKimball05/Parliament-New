import os
import logging
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.core.exceptions import ValidationError
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from django.db.models.functions import Lower
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
    def create_user(self, user_id, name, username, member_type, password=None,
                    **extra_fields):
        """
        Create a member.

        v3.17.3 fixed two things here, both long-standing:

        1. **`username` was accepted and then thrown away.** The body read
           `user.username = name`, so the caller's username became the member's
           display name. `username` is `unique=True` and is what login looks up,
           so two members called "John Smith" collided on creation and anyone
           made this way could not log in with the credential the caller thought
           they had set. `create_superuser` inherited it, which meant
           `manage.py createsuperuser` produced a superuser whose login was
           their full name. The argument is now honoured; passing an empty
           username still raises, as before.

        2. **No `**extra_fields`.** Every other Django user manager takes them,
           and callers reasonably expect `create_user(..., is_admin=True)` to
           work — all 12 tests in `src/test_page_visits_filter.py` were red for
           exactly this reason, which is why the module has been failing since
           it was written.
        """
        if not user_id:
            raise ValueError('Users must have an ID')
        if not username:
            raise ValueError('Users must have an username')
        user = self.model(
            user_id=user_id, name=name, username=username,
            member_type=member_type, **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, user_id, name, username, member_type, password,
                         **extra_fields):
        extra_fields.setdefault('is_admin', True)
        return self.create_user(
            user_id, name, username, member_type, password, **extra_fields,
        )


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
        (10, 'CNB', 'Constitution & Bylaws Chair'),
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

    # ------------------------------------------------------------------
    # ParliamentUser is a WIDE table — see MEMBER_DISPLAY_FIELDS below.
    # ------------------------------------------------------------------
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
    onboarding_complete = models.BooleanField(default=False, help_text='True once the user has completed the first-login onboarding wizard')
    onboarding_data = models.JSONField(default=dict, blank=True, help_text='Tracks onboarding progress: pages_visited list, checklist_dismissed flag, skipped_profile_items list')
    is_quarantined = models.BooleanField(default=False, help_text='Account quarantined due to suspicious activity')
    email_flagged = models.BooleanField(default=False, help_text='Email address flagged as undeliverable — user prompted to update it')
    email_flagged_reason = models.TextField(blank=True, help_text='Reason the email address was flagged (e.g. delivery error message)')
    email_flagged_at = models.DateTimeField(null=True, blank=True, help_text='When the email address was flagged')
    backup_codes_acknowledged = models.BooleanField(
        default=False,
        help_text='True after the user has viewed their backup codes on the reveal page. Reset when codes are regenerated.'
    )
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

    @property
    def has_cnb_permission(self):
        """Check if user can manage the Constitution & Bylaws builder.
        Admins always have access; otherwise requires the CNB role."""
        if self.is_admin:
            return True
        return self.roles.filter(code='CNB').exists()

    @property
    def can_access_kai(self):
        """Check if user is a chair of the Kai (conduct) committee"""
        try:
            from src.models.committees import Committee
            kai = Committee.objects.get(is_kai_committee=True)
            return kai.is_chair(self)
        except Exception:
            return False

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

    def save(self, *args, **kwargs):
        if self.email:
            self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['user_id']
        constraints = [
            models.UniqueConstraint(Lower('email'), name='uniq_parliament_user_email_lower'),
        ]


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


#: The columns a page needs to *show who someone is* — a name, an avatar, a
#: badge. Nothing else.
#:
#: v3.17.1. ParliamentUser carries the entire member profile in one table: a
#: bio, five JSON fields (majors, minors, concentrations, custom_socials,
#: initiation_chapters), six social handles, house assignment, graduation
#: details. That is ~43 columns. Any `select_related('user')` without a matching
#: `.only()` drags all of it across the wire — for every joined row — on pages
#: that render a single name.
#:
#: Use it like this::
#:
#:     Attendance.objects.select_related('user').only(
#:         'id', 'created_at',
#:         *(f'user__{f}' for f in MEMBER_DISPLAY_FIELDS),
#:     )
#:
#: The profile fields are only genuinely needed by `profile`, `directory`,
#: `house_map`, the chat member card and the admin-v2 profile editor. Splitting
#: them into a `MemberProfile` one-to-one would make that structural rather than
#: a convention — worth doing, but it is a migration touching every profile
#: read, so it deserves its own release rather than riding along with a perf
#: pass. Until then, this constant is the convention.
MEMBER_DISPLAY_FIELDS = (
    'user_id',
    'name',
    'preferred_name',
    'member_type',
    'member_status',
    'profile_picture',
    'profile_picture_removed_by_admin',
)

#: The heavy profile columns — the complement of MEMBER_DISPLAY_FIELDS, for use
#: with `.defer()`.
#:
#: `.only()` is the right tool on a ParliamentUser queryset. On a *related*
#: queryset it is the wrong one: `Legislation.objects.select_related('posted_by')
#: .only('posted_by__name', ...)` forces you to enumerate every Legislation field
#: the page touches too, which is long and breaks the moment a template reads one
#: more. Deferring the known-heavy related columns says the same thing without
#: that fragility::
#:
#:     Legislation.objects.select_related('posted_by').defer(
#:         *(f'posted_by__{f}' for f in MEMBER_PROFILE_FIELDS)
#:     )
#:
#: Keep this and MEMBER_DISPLAY_FIELDS disjoint — `test_dev_mode` asserts it.
MEMBER_PROFILE_FIELDS = (
    'about_me',
    'majors',
    'minors',
    'concentrations',
    'custom_socials',
    'initiation_chapters',
    'instagram',
    'twitter',
    'linkedin',
    'snapchat',
    'facebook',
    'other_email',
    'house',
    'pledge_class_greek',
    'onboarding_data',
)


#: Columns that only mean something for the person who is *logged in*, and are
#: never rendered about a third party.
#:
#: v3.17.3 (second pass). `member_defer` originally dropped only the profile
#: columns, which left every joined member still carrying ~29 — among them
#: `password`. That is the argon2/pbkdf2 hash, and it was being selected into
#: the result set of essentially every list page on the site: the home page,
#: activity logs, committee rosters, the calendar feed. It is never rendered,
#: so this was not a disclosure — but a password hash has no business travelling
#: from the database to the application on a page that prints someone's name,
#: and for a codebase being handed to a successor "we don't select it unless we
#: need it" is a cheaper rule to keep than "we select it but never print it".
#:
#: ⚠️ These are deferred on **joins only**. They must NEVER be added to
#: `DeferredProfileModelBackend.DEFERRED_FIELDS`: `request.user.password` backs
#: `get_session_auth_hash()`, which Django checks on every authenticated
#: request, so deferring it there would add a query per request at best and
#: break session validation at worst. `DEFERRED_FIELDS` is derived from
#: MEMBER_PROFILE_FIELDS alone, and a test asserts these stay out of it.
#:
#: Verified against every template before adding: none is dereferenced off a
#: joined member. `last_login` and `has_default_password` do appear in
#: admin-v2, but on querysets of ParliamentUser itself, which never go through
#: member_defer(). `role_number` is NOT here — 32 templates render it.
MEMBER_ACCOUNT_FIELDS = (
    'password',
    'last_login',
    'force_password_change',
    'has_default_password',
    'backup_codes_acknowledged',
    'onboarding_complete',
)


def member_defer(*relations):
    """
    Field names to hand to ``.defer()`` to strip the profile columns off one or
    more joined-member relations.

    ::

        Attendance.objects.select_related('user', 'marked_by')
                          .defer(*member_defer('user', 'marked_by'))

    v3.17.3. This exists because the sweep it was written for touched ~120 call
    sites, and at that volume the difference between ``.only()`` and
    ``.defer()`` stops being a stylistic one:

    * ``.only()`` is a **whitelist**. Omit a column some template happens to
      read and you get a fresh query per row — you have re-created the N+1 you
      were removing, silently, on a page you may not have opened.
    * ``.defer()`` is a **blacklist** of columns we have specifically
      established are not used outside the profile/directory/house-map/chat-card
      surfaces. Getting it wrong on a page that *does* read one costs a single
      extra query on that page and nothing else.

    So `MEMBER_DISPLAY_FIELDS` + ``.only()`` stays the right tool when you have
    read the template and know exactly what it renders (the legislation pages
    do this). For a broad sweep, defer is the one that cannot fail closed.
    """
    return tuple(
        f'{relation}__{field}'
        for relation in relations
        for field in MEMBER_PROFILE_FIELDS + MEMBER_ACCOUNT_FIELDS
    )


def member_prefetch(lookup, to_attr=None):
    """
    A ``Prefetch`` for a member relation with the profile columns deferred.

    A plain ``prefetch_related('members')`` always selects every column; an
    explicit ``Prefetch`` queryset is the only way to narrow one. Deferring
    rather than ``.only()``-ing, for the reason in ``member_defer``.
    """
    from django.db.models import Prefetch

    queryset = ParliamentUser.objects.defer(
        *(MEMBER_PROFILE_FIELDS + MEMBER_ACCOUNT_FIELDS))
    if to_attr:
        return Prefetch(lookup, queryset=queryset, to_attr=to_attr)
    return Prefetch(lookup, queryset=queryset)


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
            'cnb': False,
            'resolutions': False,
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
    def show_cnb_menu(self):
        return self._pref('menu', 'cnb', False)

    @property
    def show_resolutions_menu(self):
        return self._pref('menu', 'resolutions', False)

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

    @property
    def push_chat(self):
        return self._pref('push', 'chat', True)


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

        # Get IP address.
        # v3.18.8: was an inline X-Forwarded-For parse that ignored
        # BEHIND_CLOUDFLARE, so every UserSession row stored the Cloudflare edge
        # rather than the member. That fed the Active Sessions panel AND the
        # session-fingerprint warning's "Stored IP / Current IP" pair, which is
        # the line someone reads when deciding whether a session was stolen.
        # See the note in models/activity.py.
        from src.utils.security_utils import get_client_ip
        ip_address = get_client_ip(request)

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
