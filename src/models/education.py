"""
Education committee models — pledge task tracker and page access controls.

Owned by the committee with is_education_committee=True (code='EDUCATION').
"""
from django.db import models
from django.conf import settings


class PledgeTask(models.Model):
    """
    A task, quiz, or milestone that pledges must complete.

    Created and managed by the Education Committee (VPE + chairs).
    """
    TASK_TYPES = [
        ('task', 'Task'),
        ('quiz', 'Quiz'),
        ('milestone', 'Milestone'),
        ('reading', 'Reading'),
    ]
    PHASE_CHOICES = [
        ('all', 'All Phases'),
        ('1', 'Phase 1'),
        ('2', 'Phase 2'),
        ('3', 'Phase 3'),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    task_type = models.CharField(max_length=20, choices=TASK_TYPES, default='task')
    phase = models.CharField(
        max_length=10, choices=PHASE_CHOICES, default='all',
        help_text='Which pledge phase this task applies to',
    )
    due_date = models.DateField(null=True, blank=True)
    is_required = models.BooleanField(
        default=True,
        help_text='If True, task must be completed before initiation',
    )
    points = models.PositiveSmallIntegerField(
        default=0,
        help_text='Optional point value for gamification / progress tracking',
    )
    show_analysis_to_pledges = models.BooleanField(
        default=False,
        help_text='Off by default: the question-by-question breakdown is for educators. '
                  'Turn on to let pledges see it for this quiz too — useful after a '
                  'review session, when knowing which questions the class missed is the '
                  'point rather than who missed them.',
    )
    max_score = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text='Optional. If set, this task is SCORED out of this many points and a chair '
                  'records what each pledge earned (e.g. 50/60). Leave blank for pass/fail. '
                  'Separate from "points", which is progress/gamification and is the same for '
                  'everyone who completes the task.',
    )
    display_order = models.PositiveSmallIntegerField(
        default=0,
        help_text='Lower numbers shown first within the same phase',
    )
    assigned_to = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='assigned_pledge_tasks',
        limit_choices_to={'member_type': 'Pledge'},
        help_text='Specific pledges this task applies to. Leave empty to assign to all pledges.',
    )

    # ── Activation ────────────────────────────────────────────────────────
    ACTIVATION_MODES = [
        ('immediate', 'Immediately active'),
        ('manual',    'Manual — activate when ready'),
        ('timed',     'Timed — go live on a specific date'),
    ]
    activation_mode = models.CharField(
        max_length=20, choices=ACTIVATION_MODES, default='immediate',
        help_text='Controls when this task becomes visible to pledges.',
    )
    activates_at = models.DateTimeField(
        null=True, blank=True,
        help_text='For timed mode: the datetime when the task goes live for pledges.',
    )
    is_published = models.BooleanField(
        default=True,
        help_text='For manual mode: flip to True to make the task visible. '
                  'Auto-managed for immediate/timed modes.',
    )

    is_active = models.BooleanField(default=True, help_text='False = soft-deleted.')
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='created_pledge_tasks',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', 'due_date', 'title']
        verbose_name = 'Pledge Task'
        verbose_name_plural = 'Pledge Tasks'

    #: Completion statuses that mean "this pledge is finished with it".
    #: `waived` counts: a chair has decided he does not need to do it, so
    #: showing it as overdue would be nagging him about somebody else's
    #: decision.
    DONE_STATUSES = ('completed', 'waived')

    def is_overdue_for(self, completion):
        """
        Is this task overdue for the pledge whose `completion` this is?

        `completion` may be None — a pledge who has never been marked is the
        common case, and it is exactly the case that most needs flagging.

        ⚠️ Overdue is a property of a task AND a pledge, never of a task alone.
        v3.20.0 stored `due_date` and surfaced it nowhere, so a task three weeks
        late looked identical to one due tomorrow; the fix is not a badge on the
        task, because the same task is finished for one pledge and late for
        another.
        """
        from django.utils import timezone
        if not self.due_date:
            return False
        if completion and completion.status in self.DONE_STATUSES:
            return False
        return self.due_date < timezone.localdate()

    @property
    def is_scored(self):
        """
        True when a chair is expected to record a mark out of `max_score`.

        Deliberately independent of `task_type` (v3.20.0): a graded reading
        reflection or a milestone evaluation is scored the same way a quiz is,
        and tying scoring to one type would mean adding a branch every time a
        new type appears.
        """
        return bool(self.max_score)

    @property
    def is_live(self):
        """True if the task is currently visible to pledges."""
        from django.utils import timezone
        if not self.is_active:
            return False
        if self.activation_mode == 'immediate':
            return True
        if self.activation_mode == 'manual':
            return self.is_published
        if self.activation_mode == 'timed':
            return bool(self.activates_at and self.activates_at <= timezone.now())
        return False

    @property
    def activation_status_label(self):
        """Human-readable status for the education dashboard grid."""
        from django.utils import timezone
        if not self.is_active:
            return 'deleted'
        if self.activation_mode == 'immediate':
            return 'live'
        if self.activation_mode == 'manual':
            return 'live' if self.is_published else 'draft'
        if self.activation_mode == 'timed':
            if not self.activates_at:
                return 'no date set'
            if self.activates_at <= timezone.now():
                return 'live'
            return f'goes live {self.activates_at.strftime("%-m/%-d at %-I:%M %p")}'
        return 'unknown'

    def __str__(self):
        return f'[{self.get_phase_display()}] {self.title}'


class PledgePageRestriction(models.Model):
    """
    Controls which URL names (Django url_name) pledges are allowed to access.

    By default all pages that use @exclude_pledges are blocked. This model
    provides an opt-in per-phase allowlist: if a URL name has a row here with
    the pledge's current phase allowed, they can access it.

    Managed by the VPE / Education Committee chairs from the education dashboard.
    """
    PHASE_CHOICES = [
        ('all', 'All Phases'),
        ('1', 'Phase 1'),
        ('2', 'Phase 2'),
        ('3', 'Phase 3'),
    ]

    url_name = models.CharField(
        max_length=100,
        unique=True,
        help_text="Django URL name (e.g. 'directory', 'events_list') to allow pledges to access",
    )
    display_name = models.CharField(
        max_length=100,
        blank=True,
        help_text='Human-readable label shown in the VPE settings panel',
    )
    allowed_phases = models.JSONField(
        default=list,
        help_text='List of phase strings ("1", "2", "3", "all") that can access this page. Empty = blocked for all phases.',
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        on_delete=models.SET_NULL,
        related_name='pledge_page_restriction_updates',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_name', 'url_name']
        verbose_name = 'Pledge Page Restriction'
        verbose_name_plural = 'Pledge Page Restrictions'

    def __str__(self):
        return f'{self.display_name or self.url_name} → phases {self.allowed_phases}'

    @classmethod
    def is_allowed(cls, url_name, pledge_phase):
        """
        Return True if a pledge in the given phase can access this url_name.
        Default is OPEN — a restriction row must exist with the phase absent to block.

        Results are cached for 5 minutes per url_name so the decorator doesn't
        hit the DB on every request from pledge users.
        """
        from django.core.cache import cache
        cache_key = f'pledge_restriction_{url_name}'
        # Sentinel: '__open__' means no restriction row exists (open by default).
        cached = cache.get(cache_key)
        if cached is None:
            try:
                restriction = cls.objects.get(url_name=url_name)
                cached = restriction.allowed_phases or []
            except cls.DoesNotExist:
                cached = '__open__'
            cache.set(cache_key, cached, 300)  # 5-minute TTL

        if cached == '__open__':
            return True
        phases = cached
        if not phases:
            return False
        return 'all' in phases or str(pledge_phase) in phases

    @classmethod
    def invalidate_cache(cls, url_name):
        """Call after saving or deleting a restriction to bust the per-url cache."""
        from django.core.cache import cache
        cache.delete(f'pledge_restriction_{url_name}')


class PledgeTaskCompletion(models.Model):
    """
    Records when a specific pledge completes a PledgeTask.
    Marked by an education committee member or officer.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('completed', 'Completed'),
        ('incomplete', 'Incomplete'),
        ('waived', 'Waived'),
    ]

    task = models.ForeignKey(
        PledgeTask,
        on_delete=models.CASCADE,
        related_name='completions',
    )
    pledge = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='task_completions',
        limit_choices_to={'member_type': 'Pledge'},
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    score = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text='Points this pledge earned, out of the task\'s max_score (e.g. 50 of 60). '
                  'Only meaningful when the task has a max_score.',
    )
    notes = models.TextField(
        blank=True,
        help_text='Optional notes from the reviewer (e.g. what was missed, observations). '
                  'v3.20.0: scores now have their own field — use `score`, not this.',
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_pledge_completions',
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('task', 'pledge')
        ordering = ['task__display_order', 'task__title', 'pledge__name']
        verbose_name = 'Pledge Task Completion'
        verbose_name_plural = 'Pledge Task Completions'

    def __str__(self):
        return f'{self.pledge} — {self.task.title} ({self.status})'

    # ── Score display (v3.20.0) ───────────────────────────────────────────
    #
    # ⚠️ SCORING IS INFORMATIONAL AND DOES NOT DECIDE `status`. A chair still
    # marks completed/incomplete explicitly. That is a deliberate call: a
    # threshold that flips a pledge's standing means a mistyped score silently
    # changes whether he is eligible for initiation, and the person best placed
    # to catch that is the chair who just typed it.

    @property
    def has_score(self):
        """True when there is a score to show. `0` is a real score, so test for None."""
        return self.score is not None and bool(self.task.max_score)

    @property
    def score_display(self):
        """`50/60`, or an empty string when the task is not scored."""
        if not self.has_score:
            return ''
        return f'{self.score}/{self.task.max_score}'

    @property
    def score_percent(self):
        """Rounded percentage, or None. Guards a zero max_score rather than dividing by it."""
        if not self.has_score or not self.task.max_score:
            return None
        return round(self.score / self.task.max_score * 100)


class PledgeTaskQuestion(models.Model):
    """
    A question belonging to a quiz-type PledgeTask.

    Answers are free-text (short answer). Chairs review submissions and mark the
    associated PledgeTaskCompletion as completed or incomplete.
    """
    task = models.ForeignKey(
        PledgeTask,
        on_delete=models.CASCADE,
        related_name='questions',
        limit_choices_to={'task_type': 'quiz'},
    )
    question_text = models.TextField()
    answer_hint = models.TextField(
        blank=True,
        help_text='Optional model answer shown to chairs during grading (not shown to pledges)',
    )
    display_order = models.PositiveSmallIntegerField(
        default=0,
        help_text='Lower numbers shown first',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_order', 'created_at']
        verbose_name = 'Pledge Task Question'
        verbose_name_plural = 'Pledge Task Questions'

    def __str__(self):
        return f'Q{self.display_order}: {self.question_text[:60]}'


class PledgeQuizAnswer(models.Model):
    """
    A pledge's answer to a single PledgeTaskQuestion.

    All answers for a quiz (task + pledge pair) are submitted together.
    Submitting creates a PledgeTaskCompletion with status='pending' so
    the chair can review and mark it completed or incomplete.
    """
    question = models.ForeignKey(
        PledgeTaskQuestion,
        on_delete=models.CASCADE,
        related_name='answers',
    )
    pledge = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='quiz_answers',
        limit_choices_to={'member_type': 'Pledge'},
    )
    answer_text = models.TextField()
    #: v3.21.0 — per-question marking. `None` means a chair has not marked this
    #: answer yet, which is distinct from marking it wrong.
    #:
    #: ⚠️ WITHOUT THIS THERE IS NO ITEM ANALYSIS. Grading was whole-quiz only
    #: (a status, and since v3.20.0 a score), so "which question did everyone
    #: get wrong" was not a question the data could answer — the answers were
    #: free text and nothing recorded whether any of them was right. The
    #: analysis page is built on this field, so it is only as good as the
    #: marking a chair actually does.
    is_correct = models.BooleanField(null=True, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('question', 'pledge')
        ordering = ['question__display_order']
        verbose_name = 'Pledge Quiz Answer'
        verbose_name_plural = 'Pledge Quiz Answers'

    def __str__(self):
        return f'{self.pledge} — Q{self.question.display_order} of {self.question.task.title}'


class EducationMeeting(models.Model):
    """
    A pledge education meeting — date, time, location, homework and attendance.

    ⚠️ WHY THIS IS A SIDECAR ON `Event` AND NOT A STANDALONE MODEL (v3.20.0).
    It mirrors `RecruitmentEvent` exactly: the shared `Event` row owns the
    calendar entry (so a meeting appears on the chapter calendar, in the iCal
    feed and in event reminders for free), and this row owns everything that is
    specific to pledge education. One meeting, one calendar entry, no syncing.

    ⚠️ AND WHY ATTENDANCE IS A SEPARATE TABLE RATHER THAN `Attendance`.
    Mason's requirement is that attendance is *pledge-only* while the meeting
    itself is visible to the whole chapter. `Attendance` is the chapter-wide
    table: 49 call sites, unique constraints keyed on
    `attendance_type='event'`, and several queries that select on `event=` alone.
    Adding a third `attendance_type` there would make "pledges only" a property
    maintained by discipline across all of those, and this codebase has recorded
    seven releases of exactly that going wrong — a rule stated correctly, then
    one call site left outside it.

    `EducationMeetingAttendance` makes it true by construction instead: the
    table only ever contains pledges, so no chapter attendance query can see an
    education record even if someone forgets. Recruitment made the same call —
    it reuses `Event` and owns `RecruitmentEventRSVP`.
    """
    MEETING_TYPES = [
        ('meeting',  'Pledge Meeting'),
        ('study',    'Study Session'),
        ('ritual',   'Ritual Practice'),
        ('test',     'Test / Exam'),
        ('service',  'Service Event'),
        ('other',    'Other'),
    ]

    #: The calendar entry. Visible to the whole chapter by default — brothers
    #: should be able to see when the pledge class meets; only attendance is
    #: restricted.
    event = models.OneToOneField(
        'Event',
        on_delete=models.CASCADE,
        related_name='education_meeting',
    )
    committee = models.ForeignKey(
        'Committee',
        on_delete=models.CASCADE,
        related_name='education_meetings',
    )
    meeting_type = models.CharField(max_length=20, choices=MEETING_TYPES, default='meeting')

    #: Homework — real tasks, so a pledge sees it in My Tasks with a due date,
    #: a required flag and (if scored) a mark, rather than as prose he has to
    #: remember. A task may be homework for more than one meeting.
    homework = models.ManyToManyField(
        PledgeTask,
        blank=True,
        related_name='education_meetings',
        help_text='Tasks assigned as homework at this meeting.',
    )

    attendance_required = models.BooleanField(
        default=True,
        help_text='Whether pledges are expected at this meeting. Attendance can be '
                  'taken either way; this drives what the pledge is told.',
    )
    points = models.PositiveSmallIntegerField(
        default=0,
        help_text='Points a pledge earns for attending. Awarded for "present" only — '
                  'excused costs nothing but earns nothing.',
    )

    notes = models.TextField(
        blank=True,
        help_text='Chair-facing notes. Not shown to pledges.',
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_education_meetings',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-event__date_time']
        verbose_name = 'Education Meeting'
        verbose_name_plural = 'Education Meetings'

    def __str__(self):
        return f'{self.get_meeting_type_display()} — {self.event.title}'

    @property
    def is_past(self):
        from django.utils import timezone
        return self.event.date_time < timezone.now()


class EducationMeetingAttendance(models.Model):
    """
    One pledge's attendance at one education meeting.

    ⚠️ PLEDGE-ONLY BY CONSTRUCTION, not by convention — see the note on
    `EducationMeeting`. `limit_choices_to` covers the admin and forms; the
    views filter on `member_type='Pledge'` when building the roster; and
    `src/test_education_meetings.py` asserts that no chapter-wide attendance
    query can see these rows.
    """
    STATUS_CHOICES = [
        ('pending', 'Not marked'),
        ('present', 'Present'),
        ('excused', 'Excused'),
        ('late',    'Late'),
        ('absent',  'Absent'),
    ]

    #: Statuses that earn the meeting's points. `late` earns them too — a
    #: pledge who turned up late was there; docking points for it is a
    #: judgement the chair can make with `absent` if he wants to.
    EARNS_POINTS = ('present', 'late')

    meeting = models.ForeignKey(
        EducationMeeting,
        on_delete=models.CASCADE,
        related_name='attendance_records',
    )
    pledge = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='education_meeting_attendance',
        limit_choices_to={'member_type': 'Pledge'},
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    notes = models.TextField(blank=True)

    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='marked_education_attendance',
    )
    marked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('meeting', 'pledge')
        ordering = ['meeting', 'pledge__name']
        verbose_name = 'Education Meeting Attendance'
        verbose_name_plural = 'Education Meeting Attendance'
        indexes = [
            models.Index(fields=['meeting', 'status']),
            models.Index(fields=['pledge', 'status']),
        ]

    def __str__(self):
        return f'{self.pledge} — {self.meeting.event.title} ({self.get_status_display()})'

    @property
    def points_earned(self):
        return self.meeting.points if self.status in self.EARNS_POINTS else 0


class EducationAbsenceRequest(models.Model):
    """
    A pledge asking to be excused from an education meeting.

    ⚠️ WHY THIS IS NOT `AttendanceExcuse` (v3.21.0). That model exists and is
    the obvious reuse — but it hangs off `Event`, and its queue is read by
    OFFICERS for chapter events. An education absence filed there would go to
    the wrong reviewer and would mix pledge-education records into chapter ones.

    Same reasoning as `EducationMeetingAttendance`: education records stay in
    education tables, so "only pledges, only education chairs" is true by
    construction rather than by every query remembering to filter. The cost is a
    second excuse flow, which is a real cost — but the alternative couples the
    two populations, and this project has spent nine releases paying for that
    kind of coupling.

    Deliberately NO file upload. `AttendanceExcuse` takes a doctor's note, and
    that pulls in the whole private-upload surface v3.19.6/7 had to fix — an
    ownership-aware serving view, MIME validation, a `Content-Disposition`
    decision. A reason field answers the question a chair actually has; if
    documents are ever wanted, do it deliberately and reuse
    `serve_private_upload`.
    """
    STATUS_CHOICES = [
        ('pending',  'Pending review'),
        ('approved', 'Approved'),
        ('denied',   'Denied'),
    ]

    meeting = models.ForeignKey(
        EducationMeeting,
        on_delete=models.CASCADE,
        related_name='absence_requests',
    )
    pledge = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='education_absence_requests',
        limit_choices_to={'member_type': 'Pledge'},
    )
    reason = models.TextField(help_text='Why the pledge cannot attend.')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='reviewed_education_absences',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_note = models.TextField(blank=True, help_text='Optional note back to the pledge.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('meeting', 'pledge')
        ordering = ['-created_at']
        verbose_name = 'Education Absence Request'
        verbose_name_plural = 'Education Absence Requests'

    def __str__(self):
        return f'{self.pledge} — {self.meeting.event.title} ({self.status})'
