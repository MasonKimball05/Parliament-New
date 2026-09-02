from django.db import models
from src.models.users import ParliamentUser
from src.models.users import member_defer


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

    # ------------------------------------------------------------------
    # Target audience snapshot — v3.28.6.
    #
    # `get_view_stats()` used to compute "target audience" by re-running
    # `target_member_types()` + an Active-member filter against the CURRENT
    # roster, every time it was called. That is wrong the moment membership
    # changes: an announcement posted `visible_to=['Pledge']` shows a
    # shrinking, eventually-zero denominator as that pledge class initiates,
    # because there are no longer any *current* Active pledges to count —
    # even though the announcement was seen by every pledge who mattered at
    # the time. Reported live 09-02-26 as an announcement reading "5 of 0
    # members (500%)".
    #
    # Fixed by freezing the audience once, the first time anything needs to
    # know it (a real member viewing it, or an officer opening its stats) —
    # see `ensure_target_audience_snapshot()`. A list of user pks rather
    # than just a count, so `announcement_stats`'s "who hasn't viewed" list
    # can be computed against the same frozen population instead of
    # separately re-deriving it from the current roster.
    #
    # ⚠️ Known, accepted limitation: this cannot recover PAST audiences for
    # announcements posted before this field existed — there is no
    # historical record of who held which member_type on a given date. The
    # backfill migration (0027) does the best available thing (snapshots
    # against the CURRENT roster at migration time), which fixes the
    # display for most existing announcements but cannot un-lose data for
    # one already fully churned over, like the pledge-class example above.
    # Going forward, every announcement gets an accurate snapshot.
    # ------------------------------------------------------------------
    target_audience_snapshot = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'User IDs of members eligible to view this announcement, frozen the '
            'first time it is viewed or its stats are checked after publishing. '
            'Denominator for view-rate stats — see ensure_target_audience_snapshot().'
        ),
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

    @classmethod
    def annotate_view_stats(cls, queryset):
        """
        Annotate the three view counts so `get_view_stats()` needs no queries.

        v3.17.4: `get_view_stats()` ran four queries — three counts plus the
        target-audience count — and `manage_announcements` calls it once per row.
        At 25 announcements a page that was ~100 queries for numbers that are two
        aggregates. Dev mode reported the two `view_source` counts as a single
        50× group.

        All three counts share one join to `views`, so conditional aggregation
        gets them in one pass and no `distinct` is needed.

        v3.28.6: all three are now also filtered on `counted_in_target=True` —
        a plain boolean column, so this stays a single-pass conditional
        aggregate rather than needing a per-row correlated subquery against
        each announcement's own `target_audience_snapshot`. See
        `UserAnnouncementView.counted_in_target` for where that boolean is
        decided.
        """
        from django.db.models import Count, Q

        in_target = Q(views__counted_in_target=True)
        return queryset.annotate(
            _site_views=Count('views', filter=in_target & Q(views__view_source='site')),
            _email_views=Count('views', filter=in_target & Q(views__view_source='email')),
            _total_views=Count('views', filter=in_target),
        )

    @staticmethod
    def active_counts_by_member_type():
        """
        ``{member_type: active_member_count}`` in one query.

        The target audience depends on each announcement's `visible_to`, so the
        old code ran a differently-filtered COUNT per announcement — which is why
        the panel showed it as three separate shapes (11×, 8×, 6×) rather than
        one. Counting each type once and summing in Python gives the same numbers
        for any combination.
        """
        from django.db.models import Count

        return dict(
            ParliamentUser.objects.filter(member_status='Active')
            .values_list('member_type')
            .annotate(n=Count('user_id'))
        )

    def target_member_types(self):
        """
        The member types this announcement is aimed at, or None for everyone.

        Extracted so the per-object and batched paths cannot drift — this is the
        rule `is_visible_to_user`, `confirm_announcement_email` and the stats all
        depend on: selecting "Member" also includes Chair and Officer.
        """
        if not self.visible_to:
            return None
        types = set(self.visible_to)
        if 'Member' in types:
            types |= {'Chair', 'Officer'}
        return types

    @staticmethod
    def _active_user_ids_by_type():
        """``{member_type: [user_id, ...]}`` for every Active member — one query.

        Same shape as `active_counts_by_member_type()` above (built for the
        same reason — v3.17.4 — one query instead of one per announcement),
        just holding ids instead of counts, since a snapshot needs to know
        WHO, not just how many.
        """
        result = {}
        for row in (
            ParliamentUser.objects.filter(member_status='Active')
            .values('member_type', 'user_id')
        ):
            result.setdefault(row['member_type'], []).append(row['user_id'])
        return result

    def _snapshot_from_map(self, active_by_type):
        """The target-audience id list, computed from an already-fetched
        `_active_user_ids_by_type()` map rather than a query of its own."""
        visible_types = self.target_member_types()
        if visible_types is None:
            ids = {uid for ids in active_by_type.values() for uid in ids}
        else:
            ids = {uid for t in visible_types for uid in active_by_type.get(t, [])}
        return sorted(ids)

    def compute_target_audience_ids(self):
        """
        User ids currently eligible to view this announcement: Active status
        AND a matching member_type (or everyone, if `visible_to` is empty).

        This is the "live" computation — right for deciding who to show the
        announcement to on the site RIGHT NOW. It is deliberately NOT what
        `get_view_stats()` uses for its denominator once a snapshot exists —
        see `target_audience_snapshot` and `ensure_target_audience_snapshot()`.
        Single-object convenience wrapper around `_snapshot_from_map()`; for
        more than one announcement, use `ensure_target_audience_snapshots()`
        (batched) rather than calling this in a loop.
        """
        return self._snapshot_from_map(self._active_user_ids_by_type())

    def ensure_target_audience_snapshot(self):
        """
        Freeze `target_audience_snapshot` the first time it's needed, if this
        announcement is actually published yet. A no-op (no query, no write)
        on every call after the first for a given announcement.

        Not published yet: nothing to freeze — a draft's audience isn't real
        until it goes out, so this deliberately leaves the snapshot empty
        rather than freezing a guess that publish_at (or an edit before then)
        could still change.

        ⚠️ Single-object. Calling this in a loop over several announcements
        is the N+1 `annotate_view_stats()` was built to avoid one field over
        — use `Announcement.ensure_target_audience_snapshots(list_of_them)`
        for a page of more than one, which this delegates to anyway.
        """
        type(self).ensure_target_audience_snapshots([self])
        return self.target_audience_snapshot

    @classmethod
    def ensure_target_audience_snapshots(cls, announcements):
        """
        Batched form of `ensure_target_audience_snapshot()`: freezes every
        published-but-unsnapshotted announcement in `announcements` in ONE
        query for the roster plus one `bulk_update`, rather than one of each
        per announcement — the same shape `annotate_view_stats()` /
        `active_counts_by_member_type()` already use for this page (v3.17.4).

        `announcements` is a list/queryset the caller already has in hand;
        this never fetches announcements itself, only the roster needed to
        compute snapshots for the ones that need one. Safe to call with an
        empty or all-already-snapshotted list — costs nothing beyond the one
        `pending` check per object in that case.
        """
        pending = [a for a in announcements if not a.target_audience_snapshot and a.is_published()]
        if not pending:
            return
        active_by_type = cls._active_user_ids_by_type()
        for announcement in pending:
            announcement.target_audience_snapshot = announcement._snapshot_from_map(active_by_type)
        cls.objects.bulk_update(pending, ['target_audience_snapshot'])

    def is_in_target_audience(self, user):
        """
        Whether `user` was part of the frozen audience this announcement was
        published to — the single rule for "does this view count toward the
        stats." Every `UserAnnouncementView` creation site sets
        `counted_in_target` from this (see `src/tests/announcements/
        test_view_stats_snapshot.py::EveryCreationSiteSetsCountedInTargetTests`
        for the enumeration).

        Before a snapshot exists (announcement not yet published), nobody is
        "in" it — matches `ensure_target_audience_snapshot()` leaving an
        unpublished announcement's snapshot empty.
        """
        user_pk = getattr(user, 'pk', None)
        return bool(user_pk) and str(user_pk) in self.target_audience_snapshot

    def get_view_stats(self):
        """
        Get view statistics for this announcement.

        Uses values annotated by `annotate_view_stats()` when present, so a
        list page costs nothing per row beyond what's already loaded. Falls
        back to querying for a single object (`announcement_stats` still
        calls it that way).

        v3.28.6: both the numerator (views) and denominator (target
        audience) are now read from the frozen `target_audience_snapshot`
        rather than recomputed against the current roster — see that field's
        docstring. `ensure_target_audience_snapshot()` is called here too
        (in addition to the view-creation call sites) so opening the stats
        page for a just-published, not-yet-viewed announcement still freezes
        something sensible rather than showing an empty snapshot.
        """
        self.ensure_target_audience_snapshot()

        annotated = hasattr(self, '_total_views')
        if annotated:
            site_views = self._site_views
            email_views = self._email_views
            total_views = self._total_views
        else:
            views = self.views.filter(counted_in_target=True)
            site_views = views.filter(view_source='site').count()
            email_views = views.filter(view_source='email').count()
            total_views = views.count()

        target_count = len(self.target_audience_snapshot)

        return {
            'site_views': site_views,
            'email_views': email_views,
            'total_views': total_views,
            'target_audience': target_count,
            'view_rate': (total_views / target_count * 100) if target_count > 0 else 0,
        }

    def get_viewers(self):
        """Get list of users who have viewed this announcement with source"""
        return self.views.select_related('user').defer(*member_defer('user')).order_by('-viewed_at')


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
    # v3.28.6 — see Announcement.target_audience_snapshot and
    # Announcement.is_in_target_audience(). Every creation site must set this
    # explicitly from that method; default=True only covers a row created
    # some other way (e.g. by hand in the shell), where "count it" is the
    # safer failure than silently under-counting a real view.
    counted_in_target = models.BooleanField(
        default=True,
        help_text=(
            "Whether this view counts toward the announcement's view-rate stats — "
            'false for a member who could technically open the page (e.g. an '
            "alumnus who still has an account, or a wrong-member-type viewer) "
            "but wasn't part of who the announcement was actually published to."
        ),
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


class AnnouncementPollEmbed(models.Model):
    """
    A stable, UNAUTHENTICATED bearer link for embedding a poll's live QR
    code into something that fetches it with no session — a PowerPoint
    linked picture, Google Slides "Insert image by URL", OBS, etc. v3.28.7.

    Same shape as `EventCheckinEmbed` (src/models/events.py), and simpler in
    one respect: an event's QR encodes a rotating, time-boxed
    `EventCheckinWindow` token that changes every time an officer opens a
    new window, so that model exists specifically to give a slide a STABLE
    url pointing at whatever window happens to be open right now. A poll has
    no equivalent rotation — `take_poll`'s URL is just the announcement's
    permanent id — so this embed's only job is rendering that same QR as a
    fetchable image without requiring a login, not tracking anything that
    changes underneath it.

    ⚠️ WHY THIS IS SAFE TO BE ANONYMOUS: like the event embed, this token
    only lets the holder FETCH AN IMAGE — an image of a QR code that, when
    scanned, sends the scanner to `take_poll`'s own login-gated page. It
    grants no ability to read responses, open or close the poll, or do
    anything `take_poll` itself doesn't already require a real login for.
    Deliberately NOT registered in `/admin/`, same reason as
    `EventCheckinEmbed`/`CalendarSubscription`: `token` is a bearer
    credential, and an editable admin field is a way to leak it.

    One per poll (`OneToOneField`), created on first "Get embed link" click
    and stable after that — the whole point is pasting it into a slide
    once. `revoke` + a fresh token exists for the case where a link needs to
    stop working (e.g. it leaked somewhere unintended).
    """
    poll = models.OneToOneField(
        AnnouncementPoll, on_delete=models.CASCADE, related_name='qr_embed',
    )
    token = models.CharField(max_length=43, unique=True, editable=False)
    created_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL, null=True,
        related_name='created_poll_qr_embeds',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Poll QR Embed Link'
        verbose_name_plural = 'Poll QR Embed Links'

    def __str__(self):
        return f'QR embed link for {self.poll.title}'

    def is_active(self):
        return self.revoked_at is None

    @classmethod
    def get_or_create_for(cls, poll, created_by):
        """The stable embed link for `poll` — created on first use, reused
        after that. Regenerates the token if the existing one was revoked,
        so clicking "Get embed link" again after a revoke issues a working
        replacement rather than silently handing back a dead one. Mirrors
        `EventCheckinEmbed.get_or_create_for`."""
        import secrets

        embed, created = cls.objects.get_or_create(
            poll=poll,
            defaults={'token': secrets.token_urlsafe(32), 'created_by': created_by},
        )
        if not created and embed.revoked_at is not None:
            embed.token = secrets.token_urlsafe(32)
            embed.revoked_at = None
            embed.created_by = created_by
            embed.save(update_fields=['token', 'revoked_at', 'created_by'])
        return embed

    def revoke(self):
        from django.utils import timezone
        self.revoked_at = timezone.now()
        self.save(update_fields=['revoked_at'])


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
