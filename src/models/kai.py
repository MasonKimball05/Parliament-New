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

    # ------------------------------------------------------------------
    # Tag vocabulary — SECURITY BOUNDARY, added 07-28-26 (v3.16.3).
    #
    # `tags` used to be free text. That made it a hole straight through the
    # Kai identity redaction: a chair could type a member's name into a tag,
    # and every reviewer holding only `can_view_report_list` could then search
    # it (`_kai_search_q` searches tags unconditionally), read it on the list
    # card, and export it in the CSV — the three surfaces v3.16.2/v3.16.3
    # spent two releases redacting `submitted_by` and `targeted_to` out of.
    #
    # Tags are now a closed vocabulary. Nothing free-form reaches this field,
    # so it carries no identity and stays safe to search and display at list
    # level. Two rules for whoever maintains this:
    #
    #   1. Every value added here is visible to ANY reviewer who can see the
    #      report list, regardless of their identity permissions. Never add a
    #      tag that names or describes a specific person.
    #   2. If this is ever loosened back to free text, `_kai_search_q` must
    #      start gating `tags__icontains`, and the list card and CSV must
    #      redact the tag chips. See the note in that function.
    #
    # Adding a tag is a code change on purpose — it forces rule 1 to be read.
    # ------------------------------------------------------------------
    TAG_CHOICES = [
        ('urgent', 'Urgent'),
        ('follow-up', 'Follow-Up Needed'),
        ('repeat-incident', 'Repeat Incident'),
        ('awaiting-response', 'Awaiting Response'),
        ('awaiting-hearing', 'Awaiting Hearing'),
        ('documentation-pending', 'Documentation Pending'),
        ('minor', 'Minor'),
        ('escalated', 'Escalated'),
        ('resolved-informally', 'Resolved Informally'),
        ('referred-out', 'Referred Out of Chapter'),
        ('policy-review', 'Prompts Policy Review'),
        ('no-action', 'No Action Required'),
    ]
    ALLOWED_TAGS = [value for value, _ in TAG_CHOICES]
    TAG_LABELS = dict(TAG_CHOICES)

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

    def get_tag_labels(self):
        """Tags as human-readable labels, for display. Unknown values pass through."""
        return [self.TAG_LABELS.get(t, t) for t in self.get_tags_list()]

    @classmethod
    def normalize_tags(cls, raw):
        """
        Split `raw` into (accepted, rejected) against the closed vocabulary.

        Accepts a list, or a comma-separated string (what the form posts).
        Matching is case- and separator-insensitive, so 'Follow Up',
        'follow_up' and 'FOLLOW-UP' all land on 'follow-up' — the vocabulary is
        the security boundary, not the typing.

        Order is preserved and duplicates are dropped. Anything not in the
        vocabulary is returned in `rejected` so the caller can tell the user
        rather than silently discarding it.
        """
        if raw is None:
            return [], []
        if isinstance(raw, str):
            parts = raw.split(',')
        else:
            parts = list(raw)

        lookup = {}
        for value in cls.ALLOWED_TAGS:
            lookup[value] = value
            lookup[value.replace('-', '')] = value
        for value, label in cls.TAG_CHOICES:
            lookup[label.lower()] = value
            lookup[label.lower().replace(' ', '')] = value

        accepted, rejected = [], []
        for part in parts:
            text = str(part).strip()
            if not text:
                continue
            key = text.lower()
            match = lookup.get(key) or lookup.get(key.replace(' ', '').replace('_', '').replace('-', ''))
            if match is None:
                rejected.append(text)
            elif match not in accepted:
                accepted.append(match)
        return accepted, rejected

    def clean(self):
        """
        Reject out-of-vocabulary tags at the model layer too.

        The view is the real gate (it can report rejections to the user), but
        `full_clean()` runs from the Django admin and from any future form, so
        this stops the boundary being bypassed by a surface that doesn't exist
        yet. Note `save()` does NOT call this — the management command
        `normalize_kai_tags` exists to clean up anything that got in before
        07-28-26 or via a raw `.save()`.
        """
        from django.core.exceptions import ValidationError
        super().clean()
        _, rejected = self.normalize_tags(self.tags)
        if rejected:
            raise ValidationError({
                'tags': (
                    'Not in the allowed tag vocabulary: '
                    + ', '.join(rejected)
                    + '. Tags are visible to every reviewer who can see the report '
                      'list, so they are restricted to a fixed list that contains no '
                      'personal information. Allowed: '
                    + ', '.join(self.ALLOWED_TAGS)
                )
            })

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
        help_text=(
            "Tags pre-selected for reports created from this template. Restricted to "
            "KaiReport.TAG_CHOICES — this feeds straight into KaiReport.tags, so a free-text "
            "value here would reopen the identity leak that vocabulary closes."
        )
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

    def clean(self):
        """
        `suggested_tags` feeds KaiReport.tags, so it obeys the same vocabulary.

        Without this the template editor would be a side door into the field
        the vocabulary exists to protect — see the TAG_CHOICES comment on
        KaiReport.
        """
        from django.core.exceptions import ValidationError
        super().clean()
        _, rejected = KaiReport.normalize_tags(self.suggested_tags)
        if rejected:
            raise ValidationError({
                'suggested_tags': (
                    'Not in the allowed tag vocabulary: ' + ', '.join(rejected)
                    + '. Allowed: ' + ', '.join(KaiReport.ALLOWED_TAGS)
                )
            })


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
