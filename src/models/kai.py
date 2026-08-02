from django.db import IntegrityError, models, transaction
from django.db.models import Q
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

    # v3.18.0: who is HANDLING the case, as distinct from who reviewed it.
    # `reviewed_by` is only populated at review time, so before that a case had
    # no owner at all — on a five-person committee that is how a case sits for
    # three weeks. Set independently of status; cleared on nothing.
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='kai_reports_assigned',
        help_text='Committee member handling this case. Independent of reviewed_by.',
    )

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

    # ------------------------------------------------------------------
    # Case number — v3.18.0
    #
    # The UI addressed cases by primary key (`#{{ report.id }}`). Sequential
    # PKs leak total case volume and chronological ordering to anyone who sees
    # one number — the same join-key concern already recorded for SlatingVote
    # in docs/CONFIDENTIALITY_MATRIX.md. A per-year number also simply reads
    # better in minutes: "KAI-2026-014", not "#87".
    #
    # Assigned on first save and never reused. Nullable because existing rows
    # are backfilled by migration, and because a row must exist before we know
    # its year — but `save()` fills it immediately, so a null in practice means
    # a fixture built with `bulk_create`.
    # ------------------------------------------------------------------
    case_number = models.CharField(
        max_length=20,
        blank=True,
        default='',
        db_index=True,
        help_text="Per-year case identifier, e.g. KAI-2026-014. Assigned automatically.",
    )

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Kai Report'
        verbose_name_plural = 'Kai Reports'
        constraints = [
            # v3.18.1 — `next_case_number()` is a read-then-write, so two
            # submissions landing together could both read the same maximum.
            # Conditional because the field defaults to '' and is nullable in
            # practice for `bulk_create`d fixtures — many blanks are fine, two
            # identical real numbers are not. `save()` catches the
            # IntegrityError and recomputes once.
            models.UniqueConstraint(
                fields=['case_number'],
                condition=~Q(case_number=''),
                name='uniq_kai_report_case_number',
            ),
        ]

    def __str__(self):
        return f"{self.title} - {self.submitted_by.name} ({self.submitted_at.strftime('%Y-%m-%d')})"

    # ------------------------------------------------------------------
    # Recusal — v3.18.0. SEE THE MODEL BELOW AND `_case_access` IN THE VIEW.
    #
    # The chapter bylaws (Chapter on the Kai Committee, § vi, seeded in
    # `src/management/data/cnb_data.py`) require that "only the accused must
    # temporarily recuse their seat for their trial." Until v3.18.0 the app
    # implemented no part of that: `_get_kai_access()` takes a user and a
    # committee and never sees the report, so a Kai member who was the accused
    # in an open case could read the allegation, see the submitter's identity,
    # and — holding `can_close_cases` — close the case against themselves.
    #
    # These two predicates are the whole rule. Everything else is plumbing.
    # ------------------------------------------------------------------
    def is_party(self, user):
        """True if `user` is the accused or the submitter on this case."""
        if user is None or not getattr(user, 'pk', None):
            return False
        return user.pk in (self.submitted_by_id, self.targeted_to_id)

    def recusal_reason(self, user):
        """Why `user` is recused from this case, or None if they are not."""
        return self.recusal_reason_for_pk(getattr(user, 'pk', None))

    def recusal_reason_for_pk(self, user_pk):
        """
        `recusal_reason` by primary key, for callers holding only an id.

        Three outcomes, and the third is the one worth explaining:

        ``'accused'``
            Named in the case. Fully recused — the identity of whoever reported
            them and the allegation body are both withheld, and they cannot act.

        ``'submitter'``
            Filed it. Keeps sight of it, loses the power to decide it.

        ``'self'``
            **Both** — a self-report. v3.18.0: treating this as `'accused'` hid
            a member's own self-filed case from them, which protects nothing:
            the identity being withheld from them is *their own*, and they wrote
            the allegation. Nothing to hide, so nothing is hidden. What survives
            is the part that still means something — they cannot decide it.
        """
        if not user_pk:
            return None
        is_accused = user_pk == self.targeted_to_id
        is_submitter = user_pk == self.submitted_by_id
        if is_accused and is_submitter:
            return 'self'
        if is_accused:
            return 'accused'
        if is_submitter:
            return 'submitter'
        return None

    # ------------------------------------------------------------------
    # Case aging — v3.18.0. Nothing tracked how long a case had been open, so
    # a case could sit at `pending` indefinitely with no signal anywhere.
    # ------------------------------------------------------------------
    #: Days at `pending` after which the list flags a case as stale.
    STALE_AFTER_DAYS = 14

    @property
    def days_open(self):
        """Whole days since submission — for a closed case, until it closed."""
        from django.utils import timezone

        end = self.reviewed_at if self.reviewed_at else timezone.now()
        return max((end - self.submitted_at).days, 0)

    @property
    def is_stale(self):
        """Open, unreviewed, and older than STALE_AFTER_DAYS."""
        return self.status == 'pending' and self.days_open >= self.STALE_AFTER_DAYS

    @classmethod
    def next_case_number(cls, year):
        """
        The next unused `KAI-<year>-NNN`.

        Derived from the highest existing number *for that year* rather than
        from a count, so deleting a case does not cause the next one to reuse
        its number. Case numbers appear in minutes; reuse would be worse than
        a gap.
        """
        prefix = f'KAI-{year}-'
        existing = (
            cls.objects.filter(case_number__startswith=prefix)
            .values_list('case_number', flat=True)
        )
        highest = 0
        for number in existing:
            tail = number[len(prefix):]
            if tail.isdigit():
                highest = max(highest, int(tail))
        return f'{prefix}{highest + 1:03d}'

    def save(self, *args, **kwargs):
        # Assign the case number on first save. `submitted_at` is auto_now_add,
        # so it is not populated until after the INSERT — use the current year
        # for a new row, which is the same thing for every row that is not
        # being back-dated by a fixture.
        #
        # v3.18.1 — two fixes here, both small:
        #
        #   1. `update_fields`. `manage_kai_report`'s assign action calls
        #      `save(update_fields=['assigned_to'])`. If that row's number were
        #      blank, the branch below would compute one, set it on the
        #      instance, and then `super().save()` would write only
        #      `assigned_to` — the number silently lost and a query spent
        #      finding it. Add the field to `update_fields` when we assign one.
        #   2. `IntegrityError` retry. `next_case_number` is a read-then-write,
        #      so two concurrent submissions can both read the same maximum.
        #      The partial unique constraint in Meta now catches that; this
        #      recomputes once rather than 500ing. Case numbers go into the
        #      minutes, and two cases sharing one is worse than a gap.
        if not self.case_number:
            from django.utils import timezone

            year = (self.submitted_at or timezone.now()).year
            self.case_number = self.next_case_number(year)

            update_fields = kwargs.get('update_fields')
            if update_fields is not None and 'case_number' not in update_fields:
                kwargs['update_fields'] = list(update_fields) + ['case_number']

            try:
                with transaction.atomic():
                    return super().save(*args, **kwargs)
            except IntegrityError:
                # Someone took the number between our SELECT and our INSERT.
                self.case_number = self.next_case_number(year)
                return super().save(*args, **kwargs)

        super().save(*args, **kwargs)

    @property
    def display_number(self):
        """Case number if assigned, else the pk — templates should use this."""
        return self.case_number or f'#{self.pk}'

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
        # v3.18.0 — recusal / stand-ins (bylaws §§ vi-ix) and appeals (§ b.i)
        ('standin_appointed', 'Stand-In Appointed'),
        ('standin_removed', 'Stand-In Withdrawn'),
        ('appeal_filed', 'Appeal Filed'),
        ('appeal_decided', 'Appeal Decided'),
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


class KaiRecusal(models.Model):
    """
    A committee member stepping back from a case they are a party to.

    v3.18.0 — WHY THIS EXISTS
    -------------------------
    The chapter bylaws (§ vi, seeded in `src/management/data/cnb_data.py`):

        "Should members of the Kai Committee be recused from their duties, the
         head of Kai shall appoint suitable replacement(s) for the position.
         However, should the offenses be separate from each other, then their
         trials remain separated and only the accused must temporarily recuse
         their seat for their trial."

    Until v3.18.0 the app implemented none of it. `_get_kai_access()` takes a
    user and a committee and never sees the report, so a Kai member who was the
    accused could read the allegation against themselves, see who reported them,
    and — holding `can_close_cases` — close it.

    **Enforcement does not depend on a row in this table.** `KaiReport.is_party()`
    is the rule and it is computed from the case itself, so recusal cannot be
    defeated by failing to create a record. This model exists for the *other*
    half of § vi: recording that a seat was vacated and who filled it, so the
    minutes can show the committee was properly constituted when it decided.
    """

    REASON_CHOICES = [
        # Computed from the case — never chosen by hand. See `_case_access`.
        ('accused', 'Named in the case'),
        ('submitter', 'Submitted the case'),
        # Chosen by the head of Kai. v3.18.0: a seat also needs filling when
        # its holder is simply not available — travelling, ill, or standing
        # back from one case — which the automatic rules cannot detect.
        ('unavailable', 'Unavailable for this case'),
        ('conflict', 'Declared conflict of interest'),
        ('other', 'Other'),
    ]

    #: Reasons the head of Kai may record by hand. `accused` and `submitter`
    #: are derived from the case itself, so offering them here would let
    #: someone assert a relationship the data contradicts.
    MANUAL_REASONS = ('unavailable', 'conflict', 'other')

    report = models.ForeignKey(
        'KaiReport',
        on_delete=models.CASCADE,
        related_name='recusals',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='kai_recusals',
    )
    reason = models.CharField(max_length=20, choices=REASON_CHOICES)
    notes = models.TextField(
        blank=True,
        help_text='Optional context. Visible to the committee, not to the parties.',
    )
    #: § vi — "the head of Kai shall appoint suitable replacement(s)".
    replacement = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='kai_recusal_replacements',
        help_text='Member appointed to fill the recused seat for this case.',
    )
    #: Permissions the stand-in holds **for this case only**, snapshotted from
    #: the recused member at the moment of appointment.
    #:
    #: A SNAPSHOT, not a live lookup, deliberately. If the recused member's own
    #: `KaiMemberPermission` row is later widened or narrowed — or deleted when
    #: they roll off the committee — the stand-in's authority on a case that may
    #: already be decided must not silently move with it. What the minutes
    #: record is what the stand-in had.
    granted_permissions = models.JSONField(
        default=dict,
        blank=True,
        help_text='Snapshot of the replaced seat\'s permissions, taken at appointment.',
    )
    appointed_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='kai_recusals_recorded',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = ['report', 'user']
        verbose_name = 'Kai Recusal'
        verbose_name_plural = 'Kai Recusals'

    def __str__(self):
        return f"{self.user.name} recused from {self.report.display_number} ({self.reason})"

    # ------------------------------------------------------------------
    # Stand-ins — v3.18.0
    #
    # Recusal on its own only *removes*. Bylaws §§ vi–ix also require the seat
    # to be *filled*, and a stand-in with no `KaiMemberPermission` row can see
    # nothing — so the appointment has to carry an access grant with it or it
    # is ceremonial.
    # ------------------------------------------------------------------

    #: Who may stand in. Mason 07-31-26: "anyone active or advisor".
    #:
    #: ⚠️ CORRECTED 07-31-26: the first cut required `member_status='Active'`
    #: AND a non-pledge type, which silently dropped **advisors**, since an
    #: advisor is commonly carried at Alumni status rather than Active. The
    #: codebase already had the right predicate for this in two places —
    #: `committee_detail.py:70` and `committee_home.py:73` both build their
    #: eligible-advisor list as `Q(member_status=ACTIVE) | Q(member_type=ADVISOR)`.
    #: Same rule here; see `eligible_standins`.
    #:
    #: Pledges are excluded in every case — they cannot sit on a judicial body.
    EXCLUDED_STANDIN_TYPES = ('Pledge',)

    @classmethod
    def eligible_standins(cls, report):
        """
        Members who may be appointed to a vacated seat on `report`.

        Excludes, in order of importance:

        1. **The parties to the case.** Appointing the accused or the submitter
           would hand them the access recusal exists to withdraw — and
           `_case_access` would recuse them again immediately, so the seat would
           be silently empty. Fail loudly by not offering them.
        2. Anyone already recused on this case.
        3. Anyone already standing in on this case.
        4. Pledges, and anyone not `Active`.
        """
        from src.models.users import ParliamentUser

        taken = set(
            cls.objects.filter(report=report)
            .values_list('user_id', flat=True)
        ) | set(
            cls.objects.filter(report=report, replacement__isnull=False)
            .values_list('replacement_id', flat=True)
        )
        parties = {report.submitted_by_id, report.targeted_to_id}
        excluded = {pk for pk in (taken | parties) if pk}

        from django.db.models import Q

        from src.constants import MemberStatus, MemberType

        return (
            ParliamentUser.objects
            .filter(Q(member_status=MemberStatus.ACTIVE) | Q(member_type=MemberType.ADVISOR))
            .exclude(member_type__in=cls.EXCLUDED_STANDIN_TYPES)
            .exclude(pk__in=excluded)
            .order_by('name')
        )

    @classmethod
    def standin_grant(cls, report, user):
        """
        The permission snapshot `user` holds as a stand-in on `report`, or None.

        Returns the dict stored at appointment. An appointment with no
        `granted_permissions` (a row created before this field existed, or one
        written by hand) grants nothing rather than everything — fail closed.
        """
        if not getattr(user, 'pk', None):
            return None
        row = cls.objects.filter(report=report, replacement=user).first()
        if row is None:
            return None
        return row.granted_permissions or {}

    def is_eligible_replacement(self, candidate):
        """Whether `candidate` may be appointed to this recusal's seat."""
        return self.eligible_standins(self.report).filter(pk=candidate.pk).exists()


class KaiAppeal(models.Model):
    """
    An appeal against a Kai decision.

    v3.18.0 — WHY THE TEN-DAY WINDOW IS A CONSTANT AND NOT A SETTING
    ----------------------------------------------------------------
    The chapter bylaws (§ b.i):

        "Kai Committee decisions can be appealed first to the chapter, then to
         the District Chief, and then to the Board of Trustees and the General
         Convention if needed. As outlined in the General Fraternities'
         Constitution all Kai Committee appeals must be made within 10 days
         from the date of notice of a decision."

    Ten days is set by the **General Fraternity's** Constitution, not by this
    chapter, so it is not a chapter-configurable value and is deliberately not a
    SiteSetting. If the General Fraternity changes it, that is a code change,
    which is the same reasoning as `KaiReport.TAG_CHOICES`.

    The window is anchored on `KaiReport.accused_notified_at`, which is exactly
    "the date of notice of a decision" and was already being populated. A case
    that has never notified the accused has **no** open appeal window — the
    clock cannot start before notice.
    """

    APPEAL_WINDOW_DAYS = 10

    LEVEL_CHOICES = [
        ('chapter', 'The Chapter'),
        ('district_chief', 'District Chief'),
        ('board', 'Board of Trustees'),
        ('convention', 'General Convention'),
    ]

    STATUS_CHOICES = [
        ('filed', 'Filed'),
        ('under_review', 'Under Review'),
        ('upheld', 'Decision Upheld'),
        ('overturned', 'Decision Overturned'),
        ('modified', 'Decision Modified'),
        ('withdrawn', 'Withdrawn'),
    ]

    report = models.ForeignKey(
        'KaiReport',
        on_delete=models.CASCADE,
        related_name='appeals',
    )
    filed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='kai_appeals_filed',
    )
    filed_at = models.DateTimeField(auto_now_add=True)
    level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default='chapter')
    grounds = models.TextField(help_text='The basis on which the decision is being appealed.')

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='filed')
    outcome_notes = models.TextField(blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='kai_appeals_decided',
    )
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-filed_at']
        verbose_name = 'Kai Appeal'
        verbose_name_plural = 'Kai Appeals'

    def __str__(self):
        return f"Appeal on {self.report.display_number} by {self.filed_by.name} ({self.level})"

    # -- the window ----------------------------------------------------------

    @staticmethod
    def window_closes_at(report):
        """When the appeal window shuts, or None if it never opened."""
        from datetime import timedelta

        if not report.accused_notified_at:
            return None
        return report.accused_notified_at + timedelta(days=KaiAppeal.APPEAL_WINDOW_DAYS)

    @staticmethod
    def window_is_open(report):
        from django.utils import timezone

        closes = KaiAppeal.window_closes_at(report)
        return closes is not None and timezone.now() < closes

    @staticmethod
    def days_remaining(report):
        """
        Whole days left to appeal, or None if the window never opened.

        Rounds **up**, because a right that expires in six hours has one day
        left, not zero — telling someone "0 days remaining" while they can
        still act is the wrong error to make with a deadline.
        """
        import math

        from django.utils import timezone

        closes = KaiAppeal.window_closes_at(report)
        if closes is None:
            return None
        remaining = (closes - timezone.now()).total_seconds()
        return max(math.ceil(remaining / 86400), 0)

    @classmethod
    def can_file(cls, report, user):
        """
        `(allowed, reason)` — whether `user` may file an appeal on `report`.

        Only the accused may appeal, and only inside the window. Reason strings
        are user-facing.
        """
        if report.targeted_to_id != getattr(user, 'pk', None):
            return False, 'Only the member named in a case may appeal its decision.'
        if not report.accused_notified_at:
            return False, 'No decision has been issued on this case yet.'
        if not cls.window_is_open(report):
            return False, (
                f'The {cls.APPEAL_WINDOW_DAYS}-day appeal window for this case has closed.'
            )
        if cls.objects.filter(report=report, filed_by=user).exclude(status='withdrawn').exists():
            return False, 'You have already filed an appeal on this case.'
        return True, ''
