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
    notes = models.TextField(
        blank=True,
        help_text='Optional notes from the reviewer (e.g. quiz score, observations)',
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
    submitted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('question', 'pledge')
        ordering = ['question__display_order']
        verbose_name = 'Pledge Quiz Answer'
        verbose_name_plural = 'Pledge Quiz Answers'

    def __str__(self):
        return f'{self.pledge} — Q{self.question.display_order} of {self.question.task.title}'
