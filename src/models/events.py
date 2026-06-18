from django.db import models
from src.models.users import ParliamentUser


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

    # Push notification reminder fields (up to 2 reminders per event)
    reminder_1_enabled = models.BooleanField(
        default=False,
        help_text='Send a push notification reminder to members before this event'
    )
    reminder_1_hours_before = models.PositiveIntegerField(
        default=24,
        help_text='How many hours before the event to send the first reminder'
    )
    reminder_1_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the first push reminder was dispatched (null = not yet sent)'
    )
    reminder_2_enabled = models.BooleanField(
        default=False,
        help_text='Send a second push notification reminder before this event'
    )
    reminder_2_hours_before = models.PositiveIntegerField(
        default=1,
        help_text='How many hours before the event to send the second reminder'
    )
    reminder_2_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the second push reminder was dispatched (null = not yet sent)'
    )

    class Meta:
        ordering = ['date_time']
        indexes = [
            # Covers the common filter: active, non-archived events by date
            models.Index(fields=['is_active', 'archived', 'date_time'], name='event_active_archived_date_idx'),
        ]

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
        now = timezone.now()
        attendance, created = Attendance.objects.get_or_create(
            event=self.event,
            user=self.user,
            attendance_type='event',
            defaults={
                'status': 'excused',
                'created_at': now,
                'marked_by': officer,
                'marked_at': now,
                'notes': f'Excused: {self.reason[:100]}'
            }
        )

        # Only update to 'excused' if the member wasn't already marked present/late.
        # If they actually attended despite submitting an excuse, keep the real status.
        if not created and attendance.status not in ('present', 'late', 'excused'):
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


class EventReminderLog(models.Model):
    """
    Log of each push notification reminder dispatch for an event.
    One record per reminder slot (1 or 2) per event send attempt.
    """
    event = models.ForeignKey(
        Event,
        on_delete=models.CASCADE,
        related_name='reminder_logs',
    )
    reminder_slot = models.PositiveSmallIntegerField(
        help_text='Which reminder slot triggered this log (1 or 2)'
    )

    # Counts
    users_eligible = models.IntegerField(default=0, help_text='Active users matched by visibility filter')
    users_subscribed = models.IntegerField(default=0, help_text='Eligible users with at least one push subscription')
    users_opted_out = models.IntegerField(default=0, help_text='Subscribed users who opted out of push_events')
    notifications_dispatched = models.IntegerField(default=0, help_text='Number of send_push_notification tasks queued')

    # Status
    STATUS_CHOICES = (
        ('dispatched', 'Dispatched'),
        ('skipped_flag', 'Skipped — Feature Flag Off'),
        ('skipped_setting', 'Skipped — Global Setting Off'),
        ('error', 'Error'),
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='dispatched')
    error_message = models.TextField(blank=True)

    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sent_at']
        verbose_name = 'Event Reminder Log'
        verbose_name_plural = 'Event Reminder Logs'

    def __str__(self):
        return f'Reminder {self.reminder_slot} for "{self.event.title}" — {self.sent_at:%Y-%m-%d %H:%M}'


class EventReminderRecipient(models.Model):
    """
    Per-user record for an EventReminderLog dispatch.
    """
    reminder_log = models.ForeignKey(
        EventReminderLog,
        on_delete=models.CASCADE,
        related_name='recipients',
    )
    user = models.ForeignKey(
        ParliamentUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='event_reminder_receipts',
    )

    # Snapshot of user info at send time
    user_name = models.CharField(max_length=255)
    user_member_type = models.CharField(max_length=50)

    STATUS_CHOICES = (
        ('dispatched', 'Dispatched'),
        ('skipped_no_subscription', 'Skipped — No Push Subscription'),
        ('skipped_opted_out', 'Skipped — Opted Out'),
        ('skipped_visibility', 'Skipped — Not in Visibility'),
    )
    status = models.CharField(max_length=30, choices=STATUS_CHOICES)

    class Meta:
        ordering = ['status', 'user_name']
        verbose_name = 'Reminder Recipient'
        verbose_name_plural = 'Reminder Recipients'

    def __str__(self):
        return f'{self.user_name} — {self.get_status_display()}'
