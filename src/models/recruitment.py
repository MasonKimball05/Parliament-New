from django.db import models
from django.conf import settings
from django.utils import timezone


class RecruitmentCandidate(models.Model):
    """
    A prospective new member being tracked through the recruitment pipeline.

    Lives at the committee level (not per-event) so candidates persist across
    multiple events and the whole rush season. They can optionally be linked to
    a specific RecruitmentEvent where they were first encountered.
    """

    STATUS_CHOICES = [
        ('prospect', 'Prospect'),
        ('contacted', 'Contacted'),
        ('invited', 'Invited'),
        ('bid', 'Bid Offered'),
        ('accepted', 'Bid Accepted'),
        ('declined', 'Bid Declined'),
        ('rejected', 'Not Pursuing'),
    ]

    committee = models.ForeignKey(
        'Committee',
        on_delete=models.CASCADE,
        related_name='recruitment_candidates',
    )
    # Optional link to the event where this candidate was first met
    source_event = models.ForeignKey(
        'RecruitmentEvent',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='candidates',
        help_text='Event where this candidate was first encountered (optional)',
    )

    # Core info
    name = models.CharField(max_length=200)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='prospect',
    )

    # Assigned chapter member responsible for following up
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='assigned_candidates',
        help_text='Chapter member assigned to follow up with this candidate',
    )

    notes = models.TextField(blank=True)
    last_contacted = models.DateField(
        null=True, blank=True,
        help_text='Date of most recent contact with this candidate',
    )

    # Audit
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='added_candidates',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Recruitment Candidate'
        verbose_name_plural = 'Recruitment Candidates'

    def __str__(self):
        return f"{self.name} ({self.get_status_display()}) — {self.committee.code}"


class RecruitmentEvent(models.Model):
    EVENT_TYPE_CHOICES = [
        ('info_session', 'Info Session / Workshop'),
        ('interview', 'Interview'),
        ('rush_event', 'Rush Event'),
        ('deliberation', 'Voting / Deliberation'),
        ('other', 'Other'),
    ]

    VISIBILITY_CHOICES = [
        ('public', 'All chapter members'),
        ('committee_only', 'Recruitment committee only'),
    ]

    STATUS_CHOICES = [
        ('planned', 'Planned'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    NOTES_VISIBILITY_CHOICES = [
        ('public', 'Visible to all chapter members'),
        ('committee_only', 'Recruitment committee only'),
    ]

    # Core link to the shared Event (calendar entry)
    event = models.OneToOneField(
        'Event',
        on_delete=models.CASCADE,
        related_name='recruitment_event',
    )
    committee = models.ForeignKey(
        'Committee',
        on_delete=models.CASCADE,
        related_name='recruitment_events',
    )

    event_type = models.CharField(
        max_length=20,
        choices=EVENT_TYPE_CHOICES,
        default='other',
    )

    # Whether the calendar entry is visible to all members or committee only
    visibility = models.CharField(
        max_length=20,
        choices=VISIBILITY_CHOICES,
        default='public',
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='planned',
    )

    # Optional notes
    notes = models.TextField(blank=True)
    notes_visibility = models.CharField(
        max_length=20,
        choices=NOTES_VISIBILITY_CHOICES,
        default='committee_only',
    )

    # RSVP reminder — push + email to members who RSVPd 'going'
    rsvp_reminder_enabled = models.BooleanField(
        default=False,
        help_text='Send a push notification and email to everyone who RSVPd "going" before this event',
    )
    rsvp_reminder_hours_before = models.PositiveIntegerField(
        default=24,
        help_text='How many hours before the event to send the RSVP reminder',
    )
    rsvp_reminder_sent_at = models.DateTimeField(
        null=True, blank=True,
        help_text='Set when the reminder has been dispatched (null = not yet sent)',
    )

    # Audit
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_recruitment_events',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-event__date_time']
        verbose_name = 'Recruitment Event'
        verbose_name_plural = 'Recruitment Events'

    def __str__(self):
        return f"{self.get_event_type_display()} — {self.event.title}"

    @property
    def is_past(self):
        return self.event.date_time < timezone.now()


class RecruitmentEventRSVP(models.Model):
    RSVP_STATUS_CHOICES = [
        ('going', 'Going'),
        ('maybe', 'Maybe'),
        ('not_going', 'Not Going'),
    ]

    recruitment_event = models.ForeignKey(
        RecruitmentEvent,
        on_delete=models.CASCADE,
        related_name='rsvps',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='recruitment_rsvps',
    )
    status = models.CharField(
        max_length=20,
        choices=RSVP_STATUS_CHOICES,
        default='going',
    )
    checked_in = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['recruitment_event', 'user']
        verbose_name = 'Recruitment Event RSVP'
        verbose_name_plural = 'Recruitment Event RSVPs'

    def __str__(self):
        return f"{self.user.name} — {self.recruitment_event} ({self.status})"


class RecruitmentMemberPermission(models.Model):
    """
    Per-member permission overrides for the recruitment committee dashboard.
    Chairs always have full access; this table grants elevated access to regular members.
    """
    committee = models.ForeignKey(
        'Committee',
        on_delete=models.CASCADE,
        related_name='recruitment_permissions',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='recruitment_permissions',
    )

    # Can create, edit, and delete recruitment events
    can_manage_events = models.BooleanField(default=False)
    # Can view committee-only notes and candidate lists
    can_view_private = models.BooleanField(default=False)
    # Can mark attendees as checked-in
    can_take_attendance = models.BooleanField(default=False)

    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='granted_recruitment_permissions',
    )
    granted_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['committee', 'user']
        verbose_name = 'Recruitment Member Permission'
        verbose_name_plural = 'Recruitment Member Permissions'

    def __str__(self):
        return f"{self.user.name} — {self.committee.code}"
