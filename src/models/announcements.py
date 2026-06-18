from django.db import models
from src.models.users import ParliamentUser


class Announcement(models.Model):
    """Model for officer announcements and event notifications"""
    MEMBER_TYPES = (
        ('Member', 'Members'),
        ('Advisor', 'Advisors'),
        ('Pledge', 'Pledges'),
    )

    title = models.CharField(max_length=200)
    content = models.TextField()
    posted_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    posted_at = models.DateTimeField(auto_now_add=True)
    publish_at = models.DateTimeField(null=True, blank=True, help_text='Schedule when this announcement should be published. Leave blank to publish immediately.')
    event_date = models.DateTimeField(null=True, blank=True, help_text='Optional event/time')
    is_active = models.BooleanField(default=True, help_text='Uncheck to hide announcement')
    visible_to = models.JSONField(
        null=True,
        blank=True,
        help_text='Select which member types can see this announcement. Leave empty for all members.'
    )
    # Email scheduling fields
    send_email_on_publish = models.BooleanField(default=False, help_text='Send email notifications when this announcement is published')
    email_sent_at = models.DateTimeField(null=True, blank=True, help_text='When email notifications were sent')

    # Linked documents (chapter-published documents attached to this announcement)
    linked_documents = models.ManyToManyField(
        'CommitteeDocument',
        blank=True,
        related_name='announcement_links',
        help_text='Chapter documents linked to this announcement.',
    )

    class Meta:
        ordering = ['-posted_at']
        indexes = [
            # Covers the common filter: active announcements ordered by date
            models.Index(fields=['is_active', '-posted_at'], name='announcement_active_posted_idx'),
        ]

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

    def is_visible_to_user(self, user):
        """Check if user should be able to see this announcement"""
        if not self.is_published():
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

    def get_view_stats(self):
        """Get view statistics for this announcement"""
        views = self.views.all()
        site_views = views.filter(view_source='site').count()
        email_views = views.filter(view_source='email').count()
        total_views = views.count()

        # Get target audience count
        target_users = ParliamentUser.objects.filter(member_status='Active')
        if self.visible_to:
            # Filter by member type if specified
            visible_types = list(self.visible_to)
            # If "Member" is in visible_to, include Chair and Officer
            if 'Member' in visible_types:
                visible_types.extend(['Chair', 'Officer'])
            target_users = target_users.filter(member_type__in=visible_types)
        target_count = target_users.count()

        return {
            'site_views': site_views,
            'email_views': email_views,
            'total_views': total_views,
            'target_audience': target_count,
            'view_rate': (total_views / target_count * 100) if target_count > 0 else 0,
        }

    def get_viewers(self):
        """Get list of users who have viewed this announcement with source"""
        return self.views.select_related('user').order_by('-viewed_at')


class UserAnnouncementView(models.Model):
    """Track which announcements users have seen/dismissed"""
    VIEW_SOURCE_CHOICES = [
        ('site', 'Website'),
        ('email', 'Email'),
    ]

    user = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    announcement = models.ForeignKey(Announcement, on_delete=models.CASCADE, related_name='views')
    viewed_at = models.DateTimeField(auto_now_add=True)
    dismissed = models.BooleanField(default=False, help_text='User has dismissed this notification')
    view_source = models.CharField(
        max_length=10,
        choices=VIEW_SOURCE_CHOICES,
        default='site',
        help_text='Where the user viewed the announcement'
    )

    class Meta:
        unique_together = ('user', 'announcement')
        ordering = ['-viewed_at']

    def __str__(self):
        return f"{self.user.name} - {self.announcement.title} ({self.view_source})"


class AnnouncementPoll(models.Model):
    """A poll or survey attached to an announcement."""
    QUESTION_TYPE_CHOICES = [
        ('single', 'Single Choice'),
        ('multiple', 'Multiple Choice'),
        ('text', 'Text Response'),
    ]

    announcement = models.OneToOneField(
        Announcement,
        on_delete=models.CASCADE,
        related_name='poll',
        help_text='The announcement this poll is attached to.',
    )
    title = models.CharField(max_length=200, help_text='Poll title shown to respondents.')
    description = models.TextField(blank=True, help_text='Optional instructions for respondents.')
    is_anonymous = models.BooleanField(
        default=False,
        help_text='When enabled, individual responses are hidden from results — only aggregate counts are shown.',
    )
    is_open = models.BooleanField(default=True, help_text='Uncheck to stop accepting new responses.')
    closes_at = models.DateTimeField(null=True, blank=True, help_text='Automatically close at this date/time. Leave blank to close manually.')
    created_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL, null=True, related_name='polls_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Announcement Poll'
        verbose_name_plural = 'Announcement Polls'

    def __str__(self):
        return self.title

    def is_accepting_responses(self):
        from django.utils import timezone
        if not self.is_open:
            return False
        if self.closes_at and timezone.now() >= self.closes_at:
            return False
        return True

    def has_user_responded(self, user):
        return self.responses.filter(respondent=user).exists()

    def get_respondent_count(self):
        return self.responses.count()

    def get_non_respondents(self, member_status='Active'):
        """Return users visible to the announcement who haven't responded."""
        from django.utils import timezone
        announcement = self.announcement
        if announcement.visible_to:
            member_types = list(announcement.visible_to)
            if 'Member' in member_types:
                member_types.extend(['Chair', 'Officer'])
            eligible = ParliamentUser.objects.filter(
                member_status=member_status, member_type__in=member_types,
            )
        else:
            eligible = ParliamentUser.objects.filter(member_status=member_status)
        responded_ids = self.responses.values_list('respondent_id', flat=True)
        return eligible.exclude(pk__in=responded_ids)


class AnnouncementPollQuestion(models.Model):
    """A single question within a poll."""
    QUESTION_TYPE_CHOICES = [
        ('single', 'Single Choice'),
        ('multiple', 'Multiple Choice'),
        ('text', 'Text Response'),
    ]

    poll = models.ForeignKey(AnnouncementPoll, on_delete=models.CASCADE, related_name='questions')
    text = models.CharField(max_length=500)
    question_type = models.CharField(max_length=20, choices=QUESTION_TYPE_CHOICES, default='single')
    order = models.PositiveIntegerField(default=0)
    is_required = models.BooleanField(default=True)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = 'Poll Question'

    def __str__(self):
        return self.text[:80]


class AnnouncementPollOption(models.Model):
    """A selectable option for a single/multiple-choice question."""
    question = models.ForeignKey(AnnouncementPollQuestion, on_delete=models.CASCADE, related_name='options')
    text = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']
        verbose_name = 'Poll Option'

    def __str__(self):
        return self.text


class AnnouncementPollResponse(models.Model):
    """One user's complete response to a poll."""
    poll = models.ForeignKey(AnnouncementPoll, on_delete=models.CASCADE, related_name='responses')
    respondent = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='poll_responses',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['poll', 'respondent']
        verbose_name = 'Poll Response'
        ordering = ['-submitted_at']

    def __str__(self):
        name = self.respondent.get_display_name() if self.respondent else 'Anonymous'
        return f"{name} → {self.poll.title}"


class AnnouncementPollAnswer(models.Model):
    """One answer to one question within a response."""
    response = models.ForeignKey(AnnouncementPollResponse, on_delete=models.CASCADE, related_name='answers')
    question = models.ForeignKey(AnnouncementPollQuestion, on_delete=models.CASCADE)
    selected_options = models.ManyToManyField(AnnouncementPollOption, blank=True)
    text_answer = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Poll Answer'
        unique_together = ['response', 'question']

    def __str__(self):
        return f"Answer to '{self.question.text[:40]}'"


class AnnouncementEmailLog(models.Model):
    """
    Detailed log of announcement email sends.
    Tracks the overall send attempt and metadata.
    """
    announcement = models.ForeignKey(
        Announcement,
        on_delete=models.CASCADE,
        related_name='email_logs'
    )
    initiated_by = models.ForeignKey(
        ParliamentUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='initiated_email_logs'
    )

    # Visibility settings at time of send
    visible_to_raw = models.JSONField(null=True, blank=True, help_text='Original visibility setting')
    expanded_member_types = models.JSONField(null=True, blank=True, help_text='Expanded member types targeted')

    # Counts
    total_active_users = models.IntegerField(default=0)
    users_matching_visibility = models.IntegerField(default=0)
    users_with_valid_email = models.IntegerField(default=0)
    emails_sent = models.IntegerField(default=0)
    emails_failed = models.IntegerField(default=0)

    # Status
    STATUS_CHOICES = (
        ('warming_up', 'Warming Up'),
        ('pending', 'Pending'),
        ('started', 'Started'),
        ('completed', 'Completed'),
        ('partial', 'Partial (Some Failed)'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled'),
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='started')
    error_message = models.TextField(blank=True)

    # Debug console log - captures step-by-step what happened
    console_log = models.TextField(blank=True, help_text='Detailed debug log of the send process')

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Announcement Email Log'
        verbose_name_plural = 'Announcement Email Logs'

    def __str__(self):
        return f"Email Log for '{self.announcement.title}' - {self.status}"


class AnnouncementEmailRecipient(models.Model):
    """
    Individual recipient record for an announcement email send.
    Tracks whether each user received the email and why/why not.
    """
    email_log = models.ForeignKey(
        AnnouncementEmailLog,
        on_delete=models.CASCADE,
        related_name='recipients'
    )
    user = models.ForeignKey(
        ParliamentUser,
        on_delete=models.SET_NULL,
        null=True,
        related_name='announcement_email_receipts'
    )

    # User info at time of send (in case user is deleted later)
    user_name = models.CharField(max_length=255)
    user_email = models.EmailField(blank=True)
    user_member_type = models.CharField(max_length=50)
    user_member_status = models.CharField(max_length=50)

    # Result
    STATUS_CHOICES = (
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('skipped_no_email', 'Skipped - No Email Address'),
        ('skipped_disabled', 'Skipped - Notifications Disabled'),
        ('skipped_visibility', 'Skipped - Not in Visibility'),
        ('skipped_inactive', 'Skipped - Inactive Member'),
        ('failed', 'Failed'),
    )
    status = models.CharField(max_length=30, choices=STATUS_CHOICES)
    error_message = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['status', 'user_name']
        verbose_name = 'Email Recipient'
        verbose_name_plural = 'Email Recipients'

    def __str__(self):
        return f"{self.user_name} - {self.get_status_display()}"
