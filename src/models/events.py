from datetime import timedelta, timezone as dt_timezone
from urllib.parse import urlencode

from django.db import models
# `timezone` is already taken by `datetime.timezone` above (aliased dt_timezone),
# so Django's is imported under a distinct alias. It is needed at module scope —
# not just inside methods, as elsewhere in this file — because `Attendance.date`
# uses `dj_timezone.localdate` as a field default.
from django.utils import timezone as dj_timezone
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

    # Sign-up tracking — for events with limited spots or where attendance is opt-in
    requires_signup = models.BooleanField(
        default=False,
        help_text='Members must sign up to attend. Signup list is visible to all members.',
    )
    max_signups = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Maximum number of sign-ups allowed. Leave blank for unlimited.',
    )
    signups_open = models.BooleanField(
        default=True,
        help_text='When False, new sign-ups are blocked (officer can close manually).',
    )
    allow_waitlist = models.BooleanField(
        default=False,
        help_text='When the event is full, members can join a waitlist and are auto-promoted when a slot opens.',
    )

    # RSVP announcement email
    rsvp_email_enabled = models.BooleanField(
        default=False,
        help_text='Send a chapter-wide announcement email when this sign-up event opens.',
    )
    rsvp_email_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the RSVP announcement email was dispatched (null = not yet sent).',
    )

    # Attendance tracking fields
    requires_attendance = models.BooleanField(
        default=False,
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
    reminder_1_email_enabled = models.BooleanField(
        default=False,
        help_text='Also send an email reminder (in addition to the push notification) for the first reminder'
    )
    reminder_1_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the first reminder was dispatched (null = not yet sent)'
    )
    reminder_2_enabled = models.BooleanField(
        default=False,
        help_text='Send a second push notification reminder before this event'
    )
    reminder_2_hours_before = models.PositiveIntegerField(
        default=1,
        help_text='How many hours before the event to send the second reminder'
    )
    reminder_2_email_enabled = models.BooleanField(
        default=False,
        help_text='Also send an email reminder (in addition to the push notification) for the second reminder'
    )
    reminder_2_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the second reminder was dispatched (null = not yet sent)'
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

    @property
    def google_calendar_url(self):
        """v3.15.0 QOL — 'Add to Google Calendar' quick-add link.

        Events have no end time; Google requires one, so we assume 1 hour
        (same assumption the iCal export makes). Times are sent as UTC
        (trailing Z) — Google renders them in the viewer's own calendar
        timezone. urlencode handles all escaping.
        """
        start = self.date_time.astimezone(dt_timezone.utc)
        end = start + timedelta(hours=1)
        fmt = '%Y%m%dT%H%M%SZ'
        return 'https://calendar.google.com/calendar/render?' + urlencode({
            'action': 'TEMPLATE',
            'text': self.title,
            'dates': f'{start.strftime(fmt)}/{end.strftime(fmt)}',
            'details': self.description or '',
            'location': self.location or '',
        })

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
        """
        Get attendance statistics for this event.

        ⚠️ v3.25.0 — THIS COSTS SIX QUERIES AND `event_attendance_list.html`
        CALLED IT FORTY TIMES. The page renders `past_events` (capped at 20)
        twice — once as a desktop table, once as mobile cards — and each row of
        each layout calls this method, so the two layouts were computing the
        same twenty answers twice over. Measured through the real endpoint:
        **271 queries**, of which 240 were this.

        Two changes, deliberately separate:

        * The result is **memoised on the instance**, which fixes the duplicate
          layout for every caller without any of them knowing. That alone is
          271 → 151.
        * `prime_attendance_stats()` below computes a whole page's worth in two
          queries and fills that cache. 271 → 33.

        The method still works standalone — the admin detail page calls it on
        one object and gets its six queries, which is correct for one object.
        """
        if not self.requires_attendance:
            return None

        cached = getattr(self, '_attendance_stats_cache', None)
        if cached is not None:
            return cached

        total_members = ParliamentUser.objects.filter(member_status='Active').count()

        attendance_records = self.attendance_records.all()
        present_count = attendance_records.filter(status='present').count()
        absent_count = attendance_records.filter(status='absent').count()
        excused_count = attendance_records.filter(status='excused').count()
        pending_count = attendance_records.filter(status='pending').count()

        stats = self._build_attendance_stats(
            total_members=total_members,
            marked=attendance_records.count(),
            present=present_count,
            absent=absent_count,
            excused=excused_count,
            pending=pending_count,
        )
        self._attendance_stats_cache = stats
        return stats

    @staticmethod
    def _build_attendance_stats(*, total_members, marked, present, absent,
                                excused, pending):
        """
        The shape of the dict, in one place.

        ⚠️ `unmarked` is `total_members - marked`, where `marked` is EVERY
        attendance row on the event and not the sum of the four buckets above
        it. `'late'` is a fifth status that no bucket counts, so the two are not
        the same number, and the bulk path below has to reproduce that or the
        fast page would quietly print different figures from the slow one.
        """
        return {
            'total_members': total_members,
            'present': present,
            'absent': absent,
            'excused': excused,
            'pending': pending,
            'unmarked': total_members - marked,
            'attendance_rate': (present / total_members * 100) if total_members > 0 else 0
        }

    @classmethod
    def prime_attendance_stats(cls, events):
        """
        Fill `get_attendance_stats()`'s cache for a whole page in two queries.

        Returns the events as a list, because the caller must render *these*
        instances — the cache lives on the object, so a queryset re-evaluated
        after this call would hand the template fresh instances with an empty
        cache and silently restore the N+1.

        ⚠️ `.order_by()` ON THE AGGREGATE IS LOAD-BEARING, NOT TIDINESS.
        `Attendance.Meta.ordering` is `['-created_at', 'user__name']`, and
        Django adds every ordering column to the `GROUP BY` — so without the
        clearing call this groups by event *and created_at* and returns one row
        per attendance record. That is a fast page with wrong numbers, which is
        worse than a slow one with right ones, and a query-count test cannot
        see it. Same trap as v3.18.6's service-hours aggregates.

        ⚠️ The filter is `event_id__in=…` and NOTHING ELSE, matching
        `self.attendance_records.all()` exactly. It is tempting to add
        `attendance_type='event'` here — committee rows have a null event so it
        would change nothing today — but making the fast path mean something
        slightly different from the slow one is how the two drift apart.
        """
        from django.db.models import Count, Q

        events = list(events)
        tracked = [e for e in events if e.requires_attendance]
        if not tracked:
            return events

        total_members = ParliamentUser.objects.filter(member_status='Active').count()

        rows = (
            Attendance.objects
            .filter(event_id__in=[e.pk for e in tracked])
            .values('event_id')
            .order_by()                      # see the warning above
            .annotate(
                marked=Count('id'),
                present=Count('id', filter=Q(status='present')),
                absent=Count('id', filter=Q(status='absent')),
                excused=Count('id', filter=Q(status='excused')),
                pending=Count('id', filter=Q(status='pending')),
            )
        )
        by_event = {row['event_id']: row for row in rows}

        empty = {'marked': 0, 'present': 0, 'absent': 0, 'excused': 0, 'pending': 0}
        for event in tracked:
            counts = by_event.get(event.pk, empty)
            event._attendance_stats_cache = cls._build_attendance_stats(
                total_members=total_members,
                marked=counts['marked'],
                present=counts['present'],
                absent=counts['absent'],
                excused=counts['excused'],
                pending=counts['pending'],
            )
        return events


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

    # Legacy field for backwards compatibility.
    #
    # v3.17.4 — this was `auto_now_add=True`, which was wrong in two ways that
    # combined into a nightly bug.
    #
    # 1. `auto_now_add` populates a DateField from `datetime.date.today()`, which
    #    is the SERVER-LOCAL date. Django sets `os.environ['TZ']` from
    #    `TIME_ZONE`, so that is the Central date. Meanwhile every caller looked
    #    the row up with `timezone.now().date()`, which is the UTC date. Those
    #    two disagree from 19:00 Central until midnight — i.e. for the whole of
    #    every evening meeting. `update_or_create(date=<utc today>)` therefore
    #    could not find the row it had itself written, so it inserted another
    #    one, stamped with the Central date, which the next request also missed.
    # 2. `auto_now_add` also makes the field non-editable, so the explicit
    #    `date=` those callers passed was silently DISCARDED on insert. That is
    #    why the mismatch could not be fixed in the callers alone: no value they
    #    passed could ever reach the column.
    #
    # A callable `default` keeps the auto-population but honours an explicit
    # value, so a lookup key and the row it creates finally agree. `localdate`
    # (not `now().date()`) is deliberate: every row already in the table was
    # written on the Central calendar by `date.today()`, so Central is the basis
    # that reads history correctly — and it is what a human means by the date of
    # a meeting. See `AttendanceDateBasisTests`.
    date = models.DateField(default=dj_timezone.localdate)
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
        # v3.13.3: update_or_create() saves with update_fields (Django ≥4.2),
        # which silently skipped this sync — the quick-attendance panel could
        # set status='present' while the legacy present bool stayed False.
        update_fields = kwargs.get('update_fields')
        if update_fields is not None and 'present' not in update_fields:
            kwargs['update_fields'] = list(update_fields) + ['present']
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
    Log of each reminder dispatch for an event (push, and optionally email).
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

    # Counts — push
    users_eligible = models.IntegerField(default=0, help_text='Active users matched by visibility filter')
    users_subscribed = models.IntegerField(default=0, help_text='Eligible users with at least one push subscription')
    users_opted_out = models.IntegerField(default=0, help_text='Subscribed users who opted out of push_events')
    notifications_dispatched = models.IntegerField(default=0, help_text='Number of send_push_notification tasks queued')

    # Counts — email (only meaningful when this slot had its email option enabled;
    # all zero, not an error, for a slot that was push-only)
    users_with_email = models.IntegerField(default=0, help_text='Eligible users with an email address on file')
    users_email_opted_out = models.IntegerField(default=0, help_text='Users with email who opted out of email_events')
    emails_dispatched = models.IntegerField(default=0, help_text='Number of reminder emails successfully sent')

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

    ``status`` is the push outcome and is always set. ``email_status`` is the
    email outcome and stays blank ('') for a slot that didn't have the email
    option enabled — blank means "not applicable", not "unknown" or "failed".
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

    EMAIL_STATUS_CHOICES = (
        ('dispatched', 'Dispatched'),
        ('skipped_opted_out', 'Skipped — Opted Out'),
        ('skipped_no_email', 'Skipped — No Email Address'),
        ('failed', 'Failed to Send'),
    )
    email_status = models.CharField(
        max_length=30, choices=EMAIL_STATUS_CHOICES, blank=True, default='',
        help_text="Blank when this reminder slot's email option was off",
    )

    class Meta:
        ordering = ['status', 'user_name']
        verbose_name = 'Reminder Recipient'
        verbose_name_plural = 'Reminder Recipients'

    def __str__(self):
        return f'{self.user_name} — {self.get_status_display()}'


class EventSignup(models.Model):
    """
    Tracks a member's sign-up for an event with requires_signup=True.

    Separate from attendance tracking — signing up means you intend to attend,
    not that you actually did. Officers still mark attendance independently.
    Cancelled signups are soft-deleted (is_cancelled=True) so the slot history is preserved.
    """
    event = models.ForeignKey(
        'Event',
        on_delete=models.CASCADE,
        related_name='signups',
    )
    user = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.CASCADE,
        related_name='event_signups',
    )
    signed_up_at = models.DateTimeField(auto_now_add=True)
    is_cancelled = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    # Waitlist support: NULL = confirmed signup; 1, 2, 3… = position in waitlist queue.
    # When a slot opens the member at position 1 is promoted (waitlist_position set to NULL).
    waitlist_position = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Null for confirmed sign-ups; 1-based position for waitlisted members.',
    )

    class Meta:
        unique_together = [['event', 'user']]
        ordering = ['signed_up_at']
        verbose_name = 'Event Signup'
        verbose_name_plural = 'Event Signups'

    def __str__(self):
        if self.is_cancelled:
            return f'{self.user.name} — {self.event.title} (cancelled)'
        if self.waitlist_position is not None:
            return f'{self.user.name} — {self.event.title} (waitlist #{self.waitlist_position})'
        return f'{self.user.name} — {self.event.title} (signed up)'


class EventCheckinWindow(models.Model):
    """
    A short, officer-opened window during which members can self-check-in to
    an event's attendance by scanning a QR code, instead of an officer marking
    every person by hand. v3.27.0 — closes the roadmap's "attendance QR
    self-check-in" item.

    ⚠️ DELIBERATELY TIME-BOXED, NOT A PERMANENT PER-EVENT CODE. A QR that never
    expires is just a photograph waiting to be shared into a group chat — it
    checks in whoever has the picture, not whoever is in the room. Requiring
    an officer to open a window BY HAND, and having it expire on its own a
    short time later (`WINDOW_MINUTES`), is the entire security model: the
    token is worthless outside a ~15-minute stretch that an officer chose to
    start, in person, at the actual event.

    ⚠️ ADDITIVE, NEVER A REPLACEMENT. `mark_event_attendance`
    (src/view/officer/event_attendance.py) is untouched by this model or by
    whether `qr_attendance_checkin` is enabled — an officer can always mark,
    correct, or override anyone by hand, whether or not a window is open, and
    a self-check-in through this model is just another way an `Attendance` row
    gets written, not a separate source of truth. A self-check-in never
    prevents an officer's own marking from being the one that sticks — an
    officer can always overwrite it afterward the same way they overwrite any
    other status, via `mark_event_attendance`.
    """
    WINDOW_MINUTES = 15

    event = models.ForeignKey(
        'Event', on_delete=models.CASCADE, related_name='checkin_windows',
    )
    #: `secrets.token_urlsafe` output, not a sequential id — see `open_for`.
    #: Guessable tokens would make the 15-minute expiry moot; an attacker who
    #: could enumerate them would not need to be anywhere near the event.
    token = models.CharField(max_length=43, unique=True, editable=False)
    opened_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL, null=True,
        related_name='opened_checkin_windows',
    )
    opened_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(editable=False)
    #: Lets an officer end a window early (e.g. attendance is clearly done and
    #: they don't want to leave a live QR displayed a moment longer than
    #: needed) without waiting out the rest of `WINDOW_MINUTES`.
    closed_early_at = models.DateTimeField(null=True, blank=True)
    closed_early_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='closed_checkin_windows',
    )

    class Meta:
        ordering = ['-opened_at']
        indexes = [models.Index(fields=['event', 'expires_at'])]
        verbose_name = 'Event Check-in Window'
        verbose_name_plural = 'Event Check-in Windows'

    def __str__(self):
        return f'{self.event.title} — window opened {self.opened_at:%Y-%m-%d %H:%M}'

    def is_open(self):
        if self.closed_early_at is not None:
            return False
        return dj_timezone.now() < self.expires_at

    def minutes_remaining(self):
        if not self.is_open():
            return 0
        remaining = (self.expires_at - dj_timezone.now()).total_seconds() / 60
        return max(0, round(remaining))

    @classmethod
    def open_for(cls, event, opened_by):
        """Start a new window for `event`. Does not close any existing one —
        see `get_open_window`, which only ever returns the most recent
        unexpired window regardless of how many past ones exist."""
        import secrets

        now = dj_timezone.now()
        return cls.objects.create(
            event=event,
            opened_by=opened_by,
            token=secrets.token_urlsafe(32),
            expires_at=now + timedelta(minutes=cls.WINDOW_MINUTES),
        )

    @classmethod
    def get_open_window(cls, event):
        """The currently open window for `event`, or None. `expires_at__gt`
        rather than filtering in Python — this is checked on every scan, so
        it needs to be one indexed query, not "fetch the latest row and ask
        it" (which would still need the same clock comparison anyway, just
        after a wasted round trip for an event with no window at all)."""
        return cls.objects.filter(
            event=event,
            expires_at__gt=dj_timezone.now(),
            closed_early_at__isnull=True,
        ).order_by('-opened_at').first()


class EventCheckinEmbed(models.Model):
    """
    A stable, UNAUTHENTICATED bearer link for embedding the live QR check-in
    image into something that fetches it with no session at all — Google
    Slides' "Insert image by URL", a PowerPoint linked picture, OBS, etc.
    v3.27.0.

    Same shape of problem as `CalendarSubscription` (src/models_calendar_
    subscription.py), solved the same way: the officer-facing QR image view
    (`qr_checkin_image`) requires login, which a slide-deck fetch cannot
    provide, so there has to be SOME endpoint reachable with no session — and
    the only thing that can guard it is a long, unguessable token, not a
    login check. Deliberately NOT registered in `/admin/`, for the identical
    reason `CalendarSubscription` is not: `token` is a bearer credential, and
    an editable admin field is a way to leak it to anyone who can view that
    row.

    ⚠️ WHY THIS IS SAFE TO BE ANONYMOUS: this token does not grant the ability
    to check anyone in — it only lets the holder FETCH AN IMAGE. The actual
    security boundary is still `EventCheckinWindow`'s own token and 15-minute
    expiry, which this does not touch or extend. Someone with only this
    token, and no open window, gets a placeholder image. Someone with this
    token WHILE a window is open sees exactly what the projected slide is
    already showing the whole room — a photo of the screen would tell them
    the same thing. What this token is NOT is a substitute for the window
    being open; it is one more way to look at the same QR the window already
    controls, not a second way in.

    One per event (`OneToOneField`), created on first "Get embed link" click
    and stable after that so the same URL keeps working the next time the
    officer opens a window for the same event — the whole point is to paste
    it into a slide ONCE. `revoke` + a fresh `token` exists for the case where
    a link needs to stop working (e.g. it leaked somewhere unintended).
    """
    event = models.OneToOneField(
        'Event', on_delete=models.CASCADE, related_name='checkin_embed',
    )
    token = models.CharField(max_length=43, unique=True, editable=False)
    created_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL, null=True,
        related_name='created_checkin_embeds',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Event Check-in Embed Link'
        verbose_name_plural = 'Event Check-in Embed Links'

    def __str__(self):
        return f'Embed link for {self.event.title}'

    def is_active(self):
        return self.revoked_at is None

    @classmethod
    def get_or_create_for(cls, event, created_by):
        """The stable embed link for `event` — created on first use, reused
        after that. Regenerates the token if the existing one was revoked,
        so clicking "Get embed link" again after a revoke issues a working
        replacement rather than silently handing back a dead one."""
        import secrets

        embed, created = cls.objects.get_or_create(
            event=event,
            defaults={'token': secrets.token_urlsafe(32), 'created_by': created_by},
        )
        if not created and embed.revoked_at is not None:
            embed.token = secrets.token_urlsafe(32)
            embed.revoked_at = None
            embed.created_by = created_by
            embed.save(update_fields=['token', 'revoked_at', 'created_by'])
        return embed

    def revoke(self):
        self.revoked_at = dj_timezone.now()
        self.save(update_fields=['revoked_at'])
