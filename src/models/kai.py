from django.db import models
from django.conf import settings
from src.storage import DualLocationStorage


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
    tags = models.JSONField(
        default=list,
        blank=True,
        help_text="List of tags (e.g., ['urgent', 'follow-up', 'academic'])"
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

    # Submitter Notification
    submitter_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the submitter was last notified of case outcome"
    )
    submitter_email_viewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the submitter viewed the outcome notification email (tracked via pixel)"
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
        return self.tags or []

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
        ('closure_requested', 'Closure Requested'),
        ('closure_approved', 'Closure Request Approved'),
        ('closure_denied', 'Closure Request Denied'),
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
    suggested_tags = models.JSONField(
        default=list,
        blank=True,
        help_text="List of suggested tags for this type of report"
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


class KaiFormField(models.Model):
    """
    Dynamic form field definition for Kai report forms.
    Kai chair can create custom fields without code changes.
    Similar to SlatingFormField for slating applications.
    """
    FIELD_TYPES = [
        ('text', 'Text (Single Line)'),
        ('textarea', 'Text Area (Multi-line)'),
        ('number', 'Number'),
        ('email', 'Email Address'),
        ('date', 'Date'),
        ('select', 'Dropdown Select'),
        ('multiselect', 'Multi-Select'),
        ('checkbox', 'Checkbox'),
        ('radio', 'Radio Buttons'),
        ('file', 'File Upload'),
        ('member_select', 'Member Selection'),
    ]

    # Field Definition
    field_name = models.CharField(
        max_length=100,
        unique=True,
        help_text='Internal field name (no spaces, used for form processing)'
    )
    label = models.CharField(max_length=200, help_text='Display label shown to users')
    field_type = models.CharField(max_length=30, choices=FIELD_TYPES)
    placeholder = models.CharField(max_length=200, blank=True)
    help_text = models.TextField(blank=True, help_text='Help text shown below the field')

    # Options (for select, multiselect, radio)
    options = models.JSONField(
        default=list,
        blank=True,
        help_text='List of options for select/radio/multiselect fields'
    )

    # Validation
    is_required = models.BooleanField(default=False)
    validation_rules = models.JSONField(
        default=list,
        blank=True,
        help_text='Validation rules (e.g., min_length, max_length)'
    )

    # File Upload Settings
    allowed_file_types = models.JSONField(
        default=list,
        blank=True,
        help_text='Allowed MIME types for file uploads'
    )
    max_file_size_mb = models.IntegerField(default=10)

    # Display Settings
    display_order = models.IntegerField(default=0)
    section = models.CharField(
        max_length=100,
        blank=True,
        help_text='Group fields into sections (e.g., "Report Details", "Supporting Information")'
    )
    is_active = models.BooleanField(default=True)

    # Built-in flag (cannot be deleted by users)
    is_builtin = models.BooleanField(
        default=False,
        help_text='Built-in fields cannot be removed (title, category, description)'
    )

    # Audit
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_kai_form_fields'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['section', 'display_order']
        verbose_name = 'Kai Form Field'
        verbose_name_plural = 'Kai Form Fields'

    def __str__(self):
        return f"{self.label} ({self.get_field_type_display()})"


class KaiReportFieldResponse(models.Model):
    """
    Stores individual custom field responses for a Kai report.
    Enables fully dynamic forms without schema changes.
    Built-in fields (title, category, description, etc.) are stored directly on KaiReport model.
    """
    report = models.ForeignKey(
        KaiReport,
        on_delete=models.CASCADE,
        related_name='custom_responses'
    )
    field = models.ForeignKey(
        KaiFormField,
        on_delete=models.CASCADE,
        related_name='responses'
    )

    # Multi-type storage for flexibility
    text_value = models.TextField(blank=True, null=True)
    number_value = models.DecimalField(max_digits=20, decimal_places=5, null=True, blank=True)
    json_value = models.JSONField(null=True, blank=True, help_text='For arrays like multiselect')
    file_value = models.FileField(
        upload_to='kai_reports/custom_fields/',
        null=True,
        blank=True,
        storage=DualLocationStorage()
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['report', 'field']
        verbose_name = 'Kai Report Field Response'
        verbose_name_plural = 'Kai Report Field Responses'

    def __str__(self):
        return f"{self.report.title} - {self.field.label}"

    def get_display_value(self):
        """Return appropriate value based on field type"""
        if self.field.field_type in ['select', 'radio', 'text', 'textarea', 'email', 'date']:
            return self.text_value or ''
        elif self.field.field_type in ['multiselect', 'checkbox']:
            return self.json_value or []
        elif self.field.field_type == 'number':
            return self.number_value
        elif self.field.field_type == 'file':
            return self.file_value.url if self.file_value else None
        elif self.field.field_type == 'member_select':
            return self.text_value or ''
        else:
            return self.text_value or ''


class KaiClosureRequest(models.Model):
    """
    Request from submitter or accused to close/drop the case.
    Requires Kai committee approval before the case is actually closed.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('approved', 'Approved'),
        ('denied', 'Denied'),
    ]

    REQUEST_TYPE_CHOICES = [
        ('closure', 'Request Closure'),
        ('drop', 'Drop/Withdraw Case'),
    ]

    report = models.ForeignKey(
        KaiReport,
        on_delete=models.CASCADE,
        related_name='closure_requests'
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='kai_closure_requests'
    )
    request_type = models.CharField(
        max_length=20,
        choices=REQUEST_TYPE_CHOICES,
        default='closure',
        help_text="Type of request: closure (archive case) or drop (withdraw complaint)"
    )
    reason = models.TextField(help_text="Reason for requesting closure/drop")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='kai_closure_reviews'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True, help_text="Chair's notes on the decision")

    class Meta:
        ordering = ['-requested_at']
        verbose_name = 'Kai Closure Request'
        verbose_name_plural = 'Kai Closure Requests'

    def __str__(self):
        type_label = 'Drop' if self.request_type == 'drop' else 'Closure'
        return f"{type_label} request for '{self.report.title}' by {self.requested_by.name}"


class KaiMemberPermission(models.Model):
    """
    Granular, additive permissions for a Kai committee member.

    By default, members see nothing — chairs explicitly grant each permission.
    All permissions are wiped automatically when the exec role tied to the
    committee (committee.role) changes holders.
    """

    committee = models.ForeignKey(
        'Committee',
        on_delete=models.CASCADE,
        related_name='kai_member_permissions',
        limit_choices_to={'is_kai_committee': True},
    )
    user = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.CASCADE,
        related_name='kai_permissions',
    )

    # Read access
    can_view_report_list = models.BooleanField(
        default=False, help_text='Can see the list of submitted reports'
    )
    can_view_report_details = models.BooleanField(
        default=False, help_text='Can open and read individual report details'
    )
    can_view_submitter_identity = models.BooleanField(
        default=False,
        help_text="Can see who submitted a report (otherwise submitter is shown as 'Anonymous')"
    )
    can_view_accused_identity = models.BooleanField(
        default=False,
        help_text="Can see who is named in a report (otherwise shown as 'Redacted')"
    )

    # Write access
    can_edit_open_cases = models.BooleanField(
        default=False, help_text='Can update status and deliberation stage on open cases'
    )
    can_add_activity = models.BooleanField(
        default=False, help_text='Can add notes and activity log entries to any case'
    )
    can_close_cases = models.BooleanField(
        default=False, help_text='Can archive or close a case'
    )

    granted_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='kai_permissions_granted',
    )
    granted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('committee', 'user')]
        verbose_name = 'Kai Member Permission'
        verbose_name_plural = 'Kai Member Permissions'

    def __str__(self):
        return f"{self.committee.name} — {self.user.name}"
