import uuid
from django.core.validators import validate_ipv46_address
from django.db import models
from django.conf import settings
from django.utils import timezone
from src.encrypted_fields import EncryptedCharField, EncryptedEmailField
from src.models.singleton import SingletonRow


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
        ('watch_flag', 'Watch Flag Alert'),
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


class UserWatchFlag(models.Model):
    """
    Admin-placed watch flag on a specific user. When active, any successful login
    or repeated failed login attempts for that user trigger an immediate alert email
    to the site administrator and create a LoginAlert record.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='watch_flag',
        help_text='User being watched',
    )
    reason = models.TextField(
        help_text='Why this user is being watched',
    )
    notes = models.TextField(
        blank=True,
        help_text='Additional admin notes',
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Uncheck to disable alerts without removing the flag',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='watch_flags_created',
        help_text='Admin who placed this flag',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User Watch Flag'
        verbose_name_plural = 'User Watch Flags'

    def __str__(self):
        return f"Watch: {self.user.name} ({'active' if self.is_active else 'inactive'})"


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
    # ⚠️ v3.21.7 — TWO THINGS ABOUT THIS COLUMN, AND THE HELP TEXT USED TO BE
    # WRONG ABOUT BOTH.
    #
    # 1. It said "IP address or **CIDR range** to block". **No CIDR range has
    #    ever blocked anything.** Every consumer matches with
    #    `filter(ip_address=<the client's address>)` — exact string equality —
    #    so `10.0.0.0/8` is an entry that can never equal any client address.
    #    An admin who entered one got a ban that silently protected nobody,
    #    which is worse than no ban, because the list then reads as coverage.
    #    Fifth instance of the false-comment category CLAUDE.md tracks, and the
    #    first where the false statement was in a `help_text` a human reads
    #    while typing into the field it describes.
    #
    # 2. It is a `CharField` while the other ten IP columns in this schema are
    #    `GenericIPAddressField`. That asymmetry is exactly why the v3.21.7
    #    finding was invisible here: a forged `CF-Connecting-IP` was written
    #    into this column on **every** backend without complaint, creating a
    #    ban keyed on a string no real client can send. PostgreSQL would have
    #    refused it in an `inet` column, as it refused it in `HoneypotAccess`.
    #
    # `validators` makes the admin form and any `full_clean()` reject a
    # non-address — note it does NOT run on `objects.create()`, so it is a
    # guard on the human path, not on the code path. The code path is covered
    # by `get_client_ip` now returning an address or `None`.
    #
    # ⚠️ **The column is deliberately NOT converted to `GenericIPAddressField`
    # in this release**, and that is a decision rather than an omission. The
    # conversion is `ALTER COLUMN ... TYPE inet USING ip_address::inet`, which
    # **hard-fails mid-deploy** if any existing production row holds a value
    # Postgres cannot cast — and the whole point of this note is that we know
    # such rows can have been created. `manage.py preflight` now counts them;
    # convert once it reports zero.
    ip_address = models.CharField(
        max_length=45,
        unique=True,
        validators=[validate_ipv46_address],
        help_text='Single IP address to block. CIDR ranges are NOT supported — '
                  'matching is exact, so a range would block nothing.',
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


class HoneypotAccess(models.Model):
    """
    Log access attempts to honeypot/poison pill endpoints.
    These are fake admin URLs that real users would never access.
    Any access is suspicious and triggers immediate action.
    """
    ACTIONS = [
        ('blocked', 'IP Blocked'),
        ('alerted', 'Alert Sent'),
        ('logged', 'Logged Only'),
    ]

    endpoint = models.CharField(max_length=200, help_text='Honeypot URL accessed')
    # ⚠️ v3.21.7 — NULLABLE, and the alternative was worse. See the note on
    # `LoginLockout.ip_address` below, which is the same decision made once for
    # all three of these columns.
    ip_address = models.GenericIPAddressField(
        null=True, blank=True,
        help_text='Client address, or empty when none could be resolved. '
                  'A hit with no address is still evidence that the hit happened.',
    )
    user_agent = models.TextField(blank=True)
    referer = models.TextField(blank=True, help_text='Referring page if any')
    request_method = models.CharField(max_length=10, default='GET')
    request_body = models.TextField(blank=True, help_text='POST body if any (sanitized)')
    accessed_at = models.DateTimeField(auto_now_add=True)
    action_taken = models.CharField(max_length=50, choices=ACTIONS, default='blocked')
    additional_data = models.JSONField(default=dict, blank=True, help_text='Extra context data')

    class Meta:
        ordering = ['-accessed_at']
        verbose_name = 'Honeypot Access'
        verbose_name_plural = 'Honeypot Accesses'

    def __str__(self):
        return f"{self.ip_address} -> {self.endpoint} ({self.accessed_at.strftime('%Y-%m-%d %H:%M')})"


class SystemLockdown(SingletonRow, models.Model):
    """
    Emergency lockdown mode - blocks all logins except whitelisted IPs.
    Only one lockdown record should exist (singleton pattern).

    v3.19.11: the read/cache/sentinel machinery moved to `SingletonRow`
    (`src/models/singleton.py`) unchanged — v3.19.10 built it here and the
    landing page needed the identical thing, which is the signal that it was
    never really about lockdown. The reasoning that produced it is preserved in
    that module; the `⚠️` notes below are the parts specific to *this* model,
    which is a security control and therefore has a stricter invalidation
    requirement than a content row.
    """
    is_active = models.BooleanField(default=False)
    reason = models.TextField(help_text='Why lockdown was activated')
    whitelisted_ips = models.JSONField(
        default=list,
        blank=True,
        help_text='List of IPs that can still log in'
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    activated_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lockdowns_activated'
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivated_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='lockdowns_deactivated'
    )
    message = models.TextField(
        default='The system is currently in maintenance mode. Please try again later.',
        help_text='Message shown to blocked users'
    )

    class Meta:
        verbose_name = 'System Lockdown'
        verbose_name_plural = 'System Lockdowns'

    def __str__(self):
        status = 'ACTIVE' if self.is_active else 'Inactive'
        return f"System Lockdown - {status}"

    CACHE_KEY = 'system_lockdown_instance'
    CACHE_TTL = 300  # backstop only; correctness comes from invalidation

    # ⚠️ `get_instance()`, `invalidate_cache()` and the `save()` guard are
    # inherited from `SingletonRow` as of v3.19.11. The history that produced
    # them is kept below because it is the reasoning, not the code, that a
    # future reader needs — and because the requirement it states is stricter
    # here than for any other singleton: THIS ONE IS A SECURITY CONTROL.
    #
    #     v3.18.7: cached. `EmergencyLockdownMiddleware` calls get_instance()
    #     on EVERY request — authenticated or not, exempting only /static/,
    #     /media/, /health/ and /favicon.ico — so this was the widest
    #     per-request DB read in the application, wider than the 2FA
    #     middleware's (which at least only charges authenticated users). It
    #     reads a singleton row whose `is_active` is False essentially
    #     permanently.
    #
    #     ⚠️ CACHING A SECURITY CONTROL MEANS THE INVALIDATION IS THE
    #     LOAD-BEARING HALF AND THE TTL IS NOT A SUBSTITUTE FOR IT. A stale
    #     `is_active=False` means an activated lockdown does not take effect —
    #     a control failing open — and a five-minute delay defeats the word
    #     "emergency". Invalidation is the post_save/post_delete receiver at
    #     the bottom of this module rather than a `cache.delete` inside
    #     activate()/deactivate(), because those two are not the only writers:
    #     the admin changeform edits `is_active` directly.
    #
    #     v3.19.10 removed the `get_or_create`, because narrowing a race is not
    #     the same as not having it: this was a write on the read path, so the
    #     first request after a restore, a fresh install, or an admin deleting
    #     the row issued an INSERT while serving a GET, and the request path
    #     could not run against a read-only connection at all.
    #
    #     v3.19.11 moved all of it to `SingletonRow` after finding the same
    #     three lines on `LandingPageContent`, and added the half v3.19.10
    #     missed — see that module's `save()`, which is a fix for a live 500 on
    #     `manage_lockdown`'s whitelist and message actions.

    def activate(self, admin, reason, whitelisted_ips=None):
        """Activate emergency lockdown"""
        from django.utils import timezone
        self.is_active = True
        self.reason = reason
        self.whitelisted_ips = whitelisted_ips if whitelisted_ips is not None else []
        self.activated_at = timezone.now()
        self.activated_by = admin
        self.deactivated_at = None
        self.deactivated_by = None
        self.save()

    def deactivate(self, admin):
        """Deactivate emergency lockdown"""
        from django.utils import timezone
        self.is_active = False
        self.deactivated_at = timezone.now()
        self.deactivated_by = admin
        self.save()

    def is_ip_whitelisted(self, ip_address):
        """Check if an IP is whitelisted"""
        return ip_address in self.whitelisted_ips


class SecurityNotificationLog(models.Model):
    """
    Log all security notifications sent to admins.
    Helps track what alerts have been sent and when.
    """
    SEVERITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ]

    event_type = models.CharField(max_length=100, help_text='Type of security event')
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES)
    details = models.TextField(help_text='Full event details')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='security_notifications'
    )
    sent_at = models.DateTimeField(auto_now_add=True)
    email_sent_to = models.EmailField(blank=True)
    email_sent = models.BooleanField(default=False)
    email_error = models.TextField(blank=True, help_text='Error message if email failed')

    class Meta:
        ordering = ['-sent_at']
        verbose_name = 'Security Notification Log'
        verbose_name_plural = 'Security Notification Logs'

    def __str__(self):
        return f"[{self.severity.upper()}] {self.event_type} - {self.sent_at.strftime('%Y-%m-%d %H:%M')}"


class CSPViolation(models.Model):
    """
    Stores individual Content-Security-Policy violation reports sent by browsers
    to /csp-report/.  Records are grouped by (violated_directive, blocked_uri) in
    the admin UI so false positives can be dismissed as a unit.
    """
    violated_directive = models.CharField(max_length=200, db_index=True)
    blocked_uri        = models.CharField(max_length=500, db_index=True)
    document_uri       = models.CharField(max_length=500, blank=True)
    source_file        = models.CharField(max_length=500, blank=True)
    line_number        = models.CharField(max_length=20, blank=True)
    ip_address         = models.GenericIPAddressField(null=True, blank=True)
    created_at         = models.DateTimeField(auto_now_add=True, db_index=True)

    # False-positive management
    dismissed          = models.BooleanField(default=False, db_index=True)
    dismissed_at       = models.DateTimeField(null=True, blank=True)
    dismissed_by       = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='dismissed_csp_violations',
    )

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'CSP Violation'
        verbose_name_plural = 'CSP Violations'

    def __str__(self):
        return f"{self.violated_directive} — {self.blocked_uri} ({self.created_at:%Y-%m-%d})"


class LoginLockout(models.Model):
    """
    Persisted record of IP/username lockout events from the rate-limiting systems.
    Cache-only lockouts are transient; this model makes them visible in the admin UI.
    """
    SOURCE_CHOICES = [
        ('ip', 'IP-Based (login_view)'),
        ('middleware_ip', 'IP-Based (middleware)'),
        ('middleware_user', 'Username-Based (middleware)'),
    ]

    # ⚠️ v3.21.7 — NULLABLE. THIS IS THE NOTE FOR ALL THREE OF THESE COLUMNS
    # (`HoneypotAccess`, `LoginLockout`, `QuarantinedAccount`).
    #
    # Every one of them was `NOT NULL` `inet` and every one of them was written
    # `get_client_ip(request) or 'unknown'`. `'unknown'` is not an address:
    # PostgreSQL refuses it outright (`InvalidTextRepresentation`) and SQLite
    # stores it, which is why nothing noticed for months — see v3.21.6, which
    # found the same crossing in `ActivityLog` via 50 identical CI errors.
    #
    # v3.21.7 stopped `get_client_ip` from ever returning a non-address, which
    # closed the attacker-triggerable route. It could not close this one,
    # because `None` into a NOT NULL column is an `IntegrityError` — the same
    # failure with a different exception class. **Moving a failure is not
    # fixing it.** The column had to be able to say "no address".
    #
    # Nullable rather than a second reserved sentinel (e.g. RFC 5737
    # `192.0.2.0`) because a reader who does not know the convention reads a
    # sentinel as a real address, and these three tables are read during
    # incidents by people who will not have read this comment. NULL is the one
    # value every reader already has to think about.
    #
    # ⚠️ The rows are still worth writing without one. A lockout with no
    # address is a *username* lockout — `source='middleware_user'`, where the
    # address was always incidental — and before this change the write was
    # inside `except Exception: pass`, so it was silently dropped entirely.
    # A row with a NULL address is strictly more than no row at all.
    #
    # ⚠️ Readers must handle `None`. Three did not and were fixed in the same
    # release: the honeypot bulk-blacklist action and both lockout→blacklist /
    # →whitelist actions took the value straight into another NOT NULL column.
    # `src/test_null_ip_readers.py` covers them.
    ip_address = models.GenericIPAddressField(
        null=True, blank=True,
        help_text='IP address that was locked out, or empty for a '
                  'username-only lockout with no resolvable address.',
    )
    username = models.CharField(max_length=150, blank=True, help_text='Username locked out (if applicable)')
    source = models.CharField(max_length=30, choices=SOURCE_CHOICES, default='ip')
    locked_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(help_text='When the cache lockout expires')
    is_cleared = models.BooleanField(default=False, help_text='True if manually cleared by admin')
    cleared_at = models.DateTimeField(null=True, blank=True)
    cleared_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='lockout_clears',
    )

    class Meta:
        ordering = ['-locked_at']
        verbose_name = 'Login Lockout'
        verbose_name_plural = 'Login Lockouts'

    def __str__(self):
        return f"{self.ip_address} ({self.source}) locked at {self.locked_at:%Y-%m-%d %H:%M}"

    @property
    def is_active(self):
        from django.utils import timezone
        return not self.is_cleared and self.expires_at > timezone.now()


class QuarantinedAccount(models.Model):
    """
    Track accounts that have been quarantined due to suspicious activity.
    Quarantined accounts cannot log in until manually released by an admin.
    """
    user = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.CASCADE,
        related_name='quarantine_records'
    )
    # ⚠️ v3.21.7 — NULLABLE. Same decision as `LoginLockout.ip_address` above;
    # the reasoning is written out there. A quarantine whose triggering address
    # could not be resolved is still a quarantine.
    ip_address = models.GenericIPAddressField(
        null=True, blank=True,
        help_text='IP address that triggered quarantine, or empty if none '
                  'could be resolved.',
    )
    reason = models.TextField(help_text='Why this account was quarantined')
    quarantined_at = models.DateTimeField(auto_now_add=True)
    quarantined_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='quarantine_actions',
        help_text='Admin who quarantined (null if automatic)'
    )
    is_auto = models.BooleanField(default=True, help_text='True if auto-quarantined by system')
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='quarantine_releases'
    )
    release_notes = models.TextField(blank=True, help_text='Notes about why account was released')
    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Optional: quarantine auto-releases at this time. Leave blank for indefinite.'
    )

    class Meta:
        ordering = ['-quarantined_at']
        verbose_name = 'Quarantined Account'
        verbose_name_plural = 'Quarantined Accounts'

    def __str__(self):
        status = 'Active' if self.is_active else 'Released'
        return f"{self.user.name} - {status} ({self.quarantined_at.strftime('%Y-%m-%d')})"

    @property
    def is_active(self):
        """Check if quarantine is still active (not manually released and not expired)."""
        from django.utils import timezone
        if self.released_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return False
        return True

    @classmethod
    def quarantine_user(cls, user, ip_address, reason, admin=None, expires_at=None):
        """
        Quarantine a user account.
        Also sets is_quarantined flag on the user.

        Pass expires_at (timezone-aware datetime) for a time-limited quarantine
        that the nightly Celery task will auto-release when it expires.
        """
        user.is_quarantined = True
        user.save(update_fields=['is_quarantined'])

        # ⚠️ v3.21.7 — normalised HERE, because this classmethod is the single
        # entry point for creating a quarantine and a call site is exactly what
        # gets left out. Both callers (the attack middleware and the admin-v2
        # console) hand over whatever `get_client_ip(...) or 'unknown'` gave
        # them, and `'unknown'` is not an address. Same fix as `log_activity`'s
        # in v3.21.6, in the place this model actually has one.
        from src.utils.security_utils import ip_or_none

        return cls.objects.create(
            user=user,
            ip_address=ip_or_none(ip_address),
            reason=reason,
            quarantined_by=admin,
            is_auto=(admin is None),
            expires_at=expires_at,
        )

    def release(self, admin, notes=''):
        """Release this quarantine"""
        from django.utils import timezone
        self.released_at = timezone.now()
        self.released_by = admin
        self.release_notes = notes
        self.save()

        # Check if user has any other active quarantines
        active_quarantines = QuarantinedAccount.objects.filter(
            user=self.user,
            released_at__isnull=True
        ).exclude(pk=self.pk).exists()

        if not active_quarantines:
            self.user.is_quarantined = False
            self.user.save(update_fields=['is_quarantined'])


class EmailVerificationToken(models.Model):
    """
    Pending email address change requiring confirmation.

    Created when an authenticated user with an existing email submits a new
    address. The change is not applied until they click the link in the
    confirmation email sent to the new address. Only one pending token per user
    is kept — submitting again invalidates the previous one.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='email_verification_tokens',
    )
    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    new_email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Email Verification Token'
        verbose_name_plural = 'Email Verification Tokens'

    def __str__(self):
        return f"{self.user} → {self.new_email} ({'used' if self.used else 'pending'})"

    @property
    def is_valid(self):
        return not self.used and timezone.now() < self.expires_at


# ---------------------------------------------------------------------------
# Cache invalidation
# ---------------------------------------------------------------------------
#
# v3.18.7. `SystemLockdown.get_instance()` is cached (see the note there) and
# correctness comes from invalidating on write, not from the TTL — this is an
# emergency control, and a stale copy means it fails open for the length of the
# expiry.
#
# A post_save receiver rather than a `cache.delete` inside activate()/
# deactivate() for the same reason v3.17.3 moved the flag invalidation to
# signals: those two methods are not the only writers. `SystemLockdownAdmin`
# (admin.py:2130) lets an admin edit `is_active`, `whitelisted_ips` and
# `message` directly on the changeform, and that path calls save() without
# going anywhere near activate(). The signal covers both; a delete() inside the
# two methods would have covered one and looked complete.
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver


@receiver(post_save, sender=SystemLockdown)
@receiver(post_delete, sender=SystemLockdown)
def _invalidate_system_lockdown_cache(sender, instance, **kwargs):
    SystemLockdown.invalidate_cache()
