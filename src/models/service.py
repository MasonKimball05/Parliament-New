from django.db import models
from django.utils import timezone
from src.storage import DualLocationStorage
from src.models.users import member_defer


class ServicePeriod(models.Model):
    """
    Defines a service hours period (e.g., "Fall 2026") with dates and requirements.
    VPP can configure whether submissions require approval for each period.
    """
    name = models.CharField(max_length=100, help_text="Period name (e.g., 'Fall 2026')")
    start_date = models.DateField(help_text="Period start date")
    end_date = models.DateField(help_text="Period end date")

    # Hour requirements
    default_hours_required = models.DecimalField(
        max_digits=5, decimal_places=2, default=10.00,
        help_text="Default hours required for all members"
    )

    # Approval settings
    requires_approval = models.BooleanField(
        default=True,
        help_text="If True, submissions require VPP approval before counting"
    )

    # Status
    is_active = models.BooleanField(default=True)

    # Audit
    created_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL, null=True,
        related_name='created_service_periods'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-start_date']
        verbose_name = 'Service Period'
        verbose_name_plural = 'Service Periods'

    def __str__(self):
        return f"{self.name} ({self.start_date} to {self.end_date})"

    def is_current(self):
        """Check if this period is currently active based on dates"""
        # v3.17.4: localdate(). start_date/end_date are calendar dates a human
        # typed; with a UTC "today" a period stopped being current at 19:00
        # Central on its own last day.
        today = timezone.localdate()
        return self.start_date <= today <= self.end_date and self.is_active

    def get_member_expected_hours(self, member):
        """
        Get expected hours for a specific member (override or default).

        ⚠️ ONE QUERY PER CALL — do not call this in a loop over members.
        `service_dashboard` did, which cost one `SELECT … LIMIT 21` per member
        on every page load (v3.18.6). For a roster, build the map once:

            expected = dict(period.member_expectations.values_list(
                'member_id', 'expected_hours'))
            hours = expected.get(member.pk, period.default_hours_required)

        Single-member callers (`service_user_dashboard`) are fine as-is.
        """
        try:
            override = self.member_expectations.get(member=member)
            return override.expected_hours
        except ServiceMemberExpectation.DoesNotExist:
            return self.default_hours_required


class ServiceMemberExpectation(models.Model):
    """
    Override expected hours for individual members.
    Allows VPP to set custom requirements per member per period.
    """
    period = models.ForeignKey(
        ServicePeriod, on_delete=models.CASCADE,
        related_name='member_expectations'
    )
    member = models.ForeignKey(
        'ParliamentUser', on_delete=models.CASCADE,
        related_name='service_expectations'
    )
    expected_hours = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text="Custom hours required for this member"
    )
    reason = models.TextField(blank=True, help_text="Reason for adjusted hours")

    created_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL, null=True,
        related_name='created_service_expectations'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['period', 'member']
        verbose_name = 'Service Member Expectation'
        verbose_name_plural = 'Service Member Expectations'

    def __str__(self):
        return f"{self.member.name} - {self.period.name}: {self.expected_hours} hrs"


class ServiceHoursSubmission(models.Model):
    """
    Service hours submission by a member.
    Similar to KaiReport - tracks individual service hour entries.
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    # Core fields
    period = models.ForeignKey(
        ServicePeriod, on_delete=models.CASCADE,
        related_name='submissions'
    )
    submitted_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.CASCADE,
        related_name='service_hours_submitted'
    )

    # Hours details
    hours = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text="Number of service hours"
    )
    service_date = models.DateField(help_text="Date service was performed")
    organization = models.CharField(max_length=200, help_text="Organization or event name")
    description = models.TextField(help_text="Description of service performed")

    # Attachment
    attachment = models.FileField(
        upload_to='service_hours/',
        blank=True, null=True,
        storage=DualLocationStorage(),
        help_text="Optional proof/documentation (PDF, image, etc.)"
    )

    # Approval workflow
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    reviewed_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='service_hours_reviewed'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewer_notes = models.TextField(blank=True, help_text="Notes from VPP on approval/rejection")

    # Timestamps
    submitted_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Service Hours Submission'
        verbose_name_plural = 'Service Hours Submissions'

    def __str__(self):
        return f"{self.submitted_by.name} - {self.hours}hrs @ {self.organization}"

    def can_edit(self):
        """Returns True if submission can be edited (not yet approved)"""
        return self.status in ['draft', 'pending', 'rejected']

    def get_status_badge_class(self):
        """Return CSS classes for status badge"""
        return {
            'draft': 'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300',
            'pending': 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-200',
            'approved': 'bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-200',
            'rejected': 'bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-200',
        }.get(self.status, '')


class ServiceFormField(models.Model):
    """
    Dynamic form field for service hours submissions.
    Similar to KaiFormField - allows VPP to add custom fields.
    """
    FIELD_TYPES = [
        ('text', 'Text (Single Line)'),
        ('textarea', 'Text Area (Multi-line)'),
        ('number', 'Number'),
        ('date', 'Date'),
        ('select', 'Dropdown Select'),
        ('multiselect', 'Multi-Select'),
        ('checkbox', 'Checkbox'),
        ('radio', 'Radio Buttons'),
        ('file', 'File Upload'),
    ]

    field_name = models.CharField(
        max_length=100, unique=True,
        help_text="Internal field name (no spaces, lowercase)"
    )
    label = models.CharField(max_length=200, help_text="Display label for the field")
    field_type = models.CharField(max_length=30, choices=FIELD_TYPES)
    placeholder = models.CharField(max_length=200, blank=True)
    help_text = models.TextField(blank=True)
    options = models.JSONField(
        default=list, blank=True,
        help_text="Options for select/multiselect/radio fields"
    )

    is_required = models.BooleanField(default=False)
    validation_rules = models.JSONField(default=list, blank=True)

    # File field settings
    allowed_file_types = models.JSONField(
        default=list, blank=True,
        help_text="Allowed file extensions for file fields"
    )
    max_file_size_mb = models.IntegerField(default=10)

    # Display
    display_order = models.IntegerField(default=0)
    section = models.CharField(max_length=100, blank=True, help_text="Group fields by section")
    is_active = models.BooleanField(default=True)
    is_builtin = models.BooleanField(
        default=False,
        help_text="Built-in fields cannot be deleted"
    )

    # Audit
    created_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL,
        null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['section', 'display_order']
        verbose_name = 'Service Form Field'
        verbose_name_plural = 'Service Form Fields'

    def __str__(self):
        return f"{self.label} ({self.field_type})"


class ServiceFieldResponse(models.Model):
    """
    Stores custom field responses for service submissions.
    Similar to KaiReportFieldResponse - flexible storage for different field types.
    """
    submission = models.ForeignKey(
        ServiceHoursSubmission, on_delete=models.CASCADE,
        related_name='custom_responses'
    )
    field = models.ForeignKey(
        ServiceFormField, on_delete=models.CASCADE,
        related_name='responses'
    )

    # Flexible value storage
    text_value = models.TextField(blank=True, null=True)
    number_value = models.DecimalField(max_digits=20, decimal_places=5, null=True, blank=True)
    json_value = models.JSONField(null=True, blank=True)
    file_value = models.FileField(
        upload_to='service_hours/custom_fields/',
        null=True, blank=True,
        storage=DualLocationStorage()
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['submission', 'field']
        verbose_name = 'Service Field Response'
        verbose_name_plural = 'Service Field Responses'

    def __str__(self):
        return f"{self.submission} - {self.field.label}"

    def get_display_value(self):
        """Return the appropriate value based on field type"""
        if self.field.field_type in ['text', 'textarea', 'email', 'date', 'select', 'radio']:
            return self.text_value or ''
        elif self.field.field_type == 'number':
            return self.number_value
        elif self.field.field_type in ['multiselect', 'checkbox']:
            return self.json_value or []
        elif self.field.field_type == 'file':
            return self.file_value.url if self.file_value else None
        return self.text_value


class ServiceActivity(models.Model):
    """
    Activity log for service hours submissions.
    Similar to KaiReportActivity - tracks all actions for audit trail.
    """
    ACTION_CHOICES = [
        ('created', 'Submission Created'),
        ('updated', 'Submission Updated'),
        ('submitted', 'Submitted for Approval'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
        ('edited_after_rejection', 'Edited After Rejection'),
        ('resubmitted', 'Resubmitted After Rejection'),
    ]

    submission = models.ForeignKey(
        ServiceHoursSubmission, on_delete=models.CASCADE,
        related_name='activity_log'
    )
    user = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL,
        null=True, blank=True
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    details = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Service Activity'
        verbose_name_plural = 'Service Activities'

    def __str__(self):
        return f"{self.submission} - {self.get_action_display()}"


class ServiceHoursAdjustment(models.Model):
    """
    Manual hour adjustments made by VPP or admins.
    Used to grant or deduct hours directly without a submission.
    Requires a reason to be documented for transparency.
    """
    period = models.ForeignKey(
        ServicePeriod, on_delete=models.CASCADE,
        related_name='adjustments'
    )
    member = models.ForeignKey(
        'ParliamentUser', on_delete=models.CASCADE,
        related_name='service_adjustments'
    )

    # Adjustment amount (positive = grant hours, negative = deduct hours)
    hours = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text="Hours to add (positive) or remove (negative)"
    )

    # Required reason for transparency
    reason = models.TextField(
        help_text="Required explanation for this adjustment"
    )

    # Audit trail
    adjusted_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL,
        null=True, related_name='service_adjustments_made'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Service Hours Adjustment'
        verbose_name_plural = 'Service Hours Adjustments'

    def __str__(self):
        action = "granted" if self.hours > 0 else "deducted"
        return f"{abs(self.hours)} hrs {action} to {self.member.name} - {self.period.name}"


class ServiceEvent(models.Model):
    """
    A planned service event that lives on the chapter calendar and automatically
    awards hours to attendees when the VPP finalizes attendance.

    Wraps a standard Event (so it appears on the calendar) and adds:
      - hours_awarded: the hours every marked-present attendee receives
      - period: which ServicePeriod the hours count toward
      - email_reminder: optional custom-copy email sent N hours before the event
      - hours_applied: set to True once finalize has created the submissions
    """

    # The underlying calendar event (created alongside this record)
    event = models.OneToOneField(
        'Event',
        on_delete=models.CASCADE,
        related_name='service_event',
        help_text='Calendar event linked to this service event',
    )

    # Service-hours metadata
    period = models.ForeignKey(
        ServicePeriod,
        on_delete=models.PROTECT,
        related_name='service_events',
        help_text='Which service period hours count toward',
    )
    hours_awarded = models.DecimalField(
        max_digits=5, decimal_places=2,
        help_text='Hours credited to each member marked present',
    )

    # Email reminder (separate from the push-notification slots on Event)
    email_reminder_enabled = models.BooleanField(
        default=False,
        help_text='Send a custom email reminder before the event',
    )
    email_reminder_hours_before = models.PositiveIntegerField(
        default=24,
        help_text='How many hours before the event to send the email reminder',
    )
    email_reminder_subject = models.CharField(
        max_length=200, blank=True,
        help_text='Email subject line',
    )
    email_reminder_body = models.TextField(
        blank=True,
        help_text='Email body (plain text). You may use {event_title}, {event_date}, {event_location}, {hours} as placeholders.',
    )
    email_reminder_sent_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Set when the reminder email has been dispatched (null = not yet sent)',
    )

    # Optional per-member hours overrides set on the attendance page before finalizing.
    # Stored as {str(user_pk): "decimal_string"} — only populated when a member's hours
    # differ from hours_awarded. apply_hours() checks this before falling back to hours_awarded.
    member_hours_override = models.JSONField(
        default=dict,
        blank=True,
        help_text='Per-member hours overrides: {str(user_pk): "decimal_hours"}',
    )

    # Finalization state
    hours_applied = models.BooleanField(
        default=False,
        help_text='True once attendance has been finalized and submissions created',
    )

    # Audit
    created_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL,
        null=True, related_name='created_service_events',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-event__date_time']
        verbose_name = 'Service Event'
        verbose_name_plural = 'Service Events'

    def __str__(self):
        return f"{self.event.title} — {self.hours_awarded} hrs ({self.period.name})"

    def get_present_attendees(self):
        """Return Attendance queryset for members marked present at this event."""
        return self.event.attendance_records.filter(status='present').select_related('user').defer(*member_defer('user'))

    def get_hours_for_user(self, user_pk):
        """
        Return the hours this member should receive, respecting any per-member override.
        Falls back to hours_awarded when no override is set.
        """
        override = (self.member_hours_override or {}).get(str(user_pk))
        if override is not None:
            try:
                from decimal import Decimal
                return Decimal(str(override))
            except Exception:
                pass
        return self.hours_awarded

    def apply_hours(self, finalized_by):
        """
        Create pre-approved ServiceHoursSubmission records for every member
        marked present. Respects per-member hours overrides in member_hours_override.
        Safe to call once only (guarded by hours_applied).
        Returns the count of submissions created.
        """
        if self.hours_applied:
            return 0

        now = timezone.now()
        created = 0
        for record in self.get_present_attendees():
            hours = self.get_hours_for_user(record.user.pk)
            ServiceHoursSubmission.objects.create(
                period=self.period,
                submitted_by=record.user,
                hours=hours,
                service_date=self.event.date_time.date(),
                organization=self.event.title,
                description=(
                    f'Attended service event: {self.event.title}. '
                    f'Hours awarded automatically upon attendance finalization.'
                ),
                status='approved',
                reviewed_by=finalized_by,
                reviewed_at=now,
                reviewer_notes='Auto-approved via service event attendance finalization.',
            )
            created += 1

        self.hours_applied = True
        self.save(update_fields=['hours_applied'])
        return created
