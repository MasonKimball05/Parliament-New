from django.db import models
from django.conf import settings


class GoverningDocument(models.Model):
    DOCUMENT_TYPES = [
        ('constitution', 'Constitution'),
        ('bylaws', 'Bylaws'),
        ('appendix', 'Appendix'),
    ]

    doc_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES, unique=True)
    title = models.CharField(
        max_length=200,
        help_text='Full title, e.g. "Constitution of Alpha Mu Chapter of Beta Theta Pi"'
    )
    preamble = models.TextField(
        blank=True,
        help_text='Preamble text shown before Article I'
    )
    last_reviewed = models.DateField(
        null=True, blank=True,
        help_text='Date this document was last formally reviewed'
    )
    amendment_protection_weeks = models.PositiveIntegerField(
        default=15,
        help_text=(
            'Number of chapter periods (school weeks) a section is protected from new '
            'amendment resolutions after a failed amendment. '
            'Constitution default: 15. Bylaws default: 10.'
        )
    )

    class Meta:
        verbose_name = 'Governing Document'
        verbose_name_plural = 'Governing Documents'

    def __str__(self):
        return self.get_doc_type_display()


class Article(models.Model):
    document = models.ForeignKey(
        GoverningDocument, on_delete=models.CASCADE, related_name='articles'
    )
    number = models.CharField(
        max_length=20,
        help_text='Article number — Roman numeral, e.g. "I", "II", "III"'
    )
    title = models.CharField(max_length=200, help_text='e.g. "Name and Purpose"')
    display_order = models.PositiveIntegerField(default=0)

    # Officer/IFC deactivation
    is_active = models.BooleanField(
        default=True,
        help_text='Uncheck to suspend this article per IFC or governing body ruling'
    )
    deactivation_reason = models.TextField(
        blank=True,
        help_text='Required when deactivating — explain the ruling or reason'
    )
    deactivated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='deactivated_articles'
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['document', 'display_order']
        unique_together = ('document', 'number')
        verbose_name = 'Article'

    def __str__(self):
        return f'{self.document.get_doc_type_display()} Article {self.number} — {self.title}'


class Section(models.Model):
    article = models.ForeignKey(
        Article, on_delete=models.CASCADE, related_name='sections'
    )
    number = models.CharField(
        max_length=20,
        help_text='Section number, e.g. "1", "2", "1a"'
    )
    title = models.CharField(
        max_length=200, blank=True,
        help_text='Optional section heading'
    )
    content = models.TextField(help_text='The full text of this section')
    display_order = models.PositiveIntegerField(default=0)

    # Officer/IFC deactivation
    is_active = models.BooleanField(
        default=True,
        help_text='Uncheck to suspend this section per IFC or governing body ruling'
    )
    deactivation_reason = models.TextField(blank=True)
    deactivated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='deactivated_sections'
    )
    deactivated_at = models.DateTimeField(null=True, blank=True)

    # Partial suspensions — specific numbered sub-items suspended without suspending the whole section
    # Each entry: {"ref": "3.a.i", "reason": "...", "suspended_at": "YYYY-MM-DD", "suspended_by_name": "..."}
    partial_suspensions = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            'Partial suspensions on specific sub-items within this section. '
            'Each entry: {"ref": "3.a.i", "reason": "...", "suspended_at": "YYYY-MM-DD", "suspended_by_name": "..."}'
        )
    )

    # Failed amendment protection
    # If a resolution to amend this section fails, it becomes protected for a period.
    # While protected, no new amendment to this section may be introduced.
    amendment_protected = models.BooleanField(
        default=False,
        help_text='True if this section is protected from new amendments (following a failed amendment)'
    )
    protected_until = models.DateField(
        null=True, blank=True,
        help_text='Protection expires on this date — set automatically when an amendment resolution fails'
    )
    protection_note = models.TextField(
        blank=True,
        help_text='Auto-filled: which resolution triggered the protection and when'
    )

    class Meta:
        ordering = ['article', 'display_order']
        unique_together = ('article', 'number')
        verbose_name = 'Section'

    @property
    def full_identifier(self):
        """Returns a human-readable ID like 'Constitution Art. III § 2'"""
        doc = self.article.document.get_doc_type_display()
        return f'{doc} Art. {self.article.number} § {self.number}'

    def __str__(self):
        return self.full_identifier


class Resolution(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('pending', 'Pending Vote'),
        ('passed', 'Passed'),
        ('failed', 'Failed'),
        ('withdrawn', 'Withdrawn'),
    ]

    TYPE_CHOICES = [
        ('amendment', 'Constitutional/Bylaws Amendment'),
        ('general', 'General Resolution'),
        ('emergency', 'Emergency Resolution'),
    ]

    title = models.CharField(max_length=300, help_text='Short descriptive title — "On the [Subject]"')
    resolution_type = models.CharField(
        max_length=20, choices=TYPE_CHOICES, default='amendment'
    )
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default='draft'
    )

    # Header metadata
    authors = models.TextField(
        blank=True,
        help_text='Author(s) — free text, e.g. "Mason Kimball (αμ 73), Jonathan Hall (αμ 80)"'
    )
    sponsors = models.TextField(
        blank=True,
        help_text='Sponsor(s) — free text, e.g. "Executive Board" or a committee name'
    )

    # Section I — Preamble
    whereas_clauses = models.TextField(
        blank=True,
        help_text='WHEREAS clauses, one per line. Template prefixes each with "Whereas," and adds "; and"'
    )
    resolved_text = models.TextField(
        blank=True,
        help_text='THEREFORE, BE IT RESOLVED, — the primary resolved clause'
    )

    # Section II — Body of the Resolution
    resolution_body = models.TextField(
        blank=True,
        help_text=(
            'Section II — Body of the Resolution. Full text of proposed amendments/actions '
            'in numbered article format (e.g. Article 1, 1.1, 1.2…).'
        )
    )

    # Section III — Conclusion notes (numbered conclusion clauses before the certification block)
    additional_notes = models.TextField(
        blank=True,
        help_text=(
            'Section III — Conclusion Notes. Numbered clauses covering effective date, '
            'special notes, IFC compliance, etc. (e.g. "3.1 Effective Date and Threshold. …")'
        )
    )

    # Scheduling
    vote_date = models.DateField(
        null=True, blank=True,
        help_text='Date this resolution is scheduled to be voted on'
    )
    passed_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='authored_resolutions'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Resolution'

    def __str__(self):
        return f'{self.title} [{self.get_status_display()}]'

    def apply_amendments(self, applied_by):
        """
        When a resolution passes: update each targeted section with the proposed text.
        Clears any existing amendment protection on those sections.
        - change/addition: replaces section content with proposed_text
        - deletion: clears content and suspends the section
        Called inside a transaction — caller is responsible for saving self.
        """
        from django.utils import timezone
        for amendment in self.amendments.all():
            section = amendment.section
            whole_section_delete = (amendment.amendment_type == 'deletion' and not amendment.scope_note and not amendment.proposed_text)
            if whole_section_delete:
                # Whole-section deletion: clear content and suspend
                section.content = ''
                section.is_active = False
                section.deactivation_reason = f'Deleted by resolution: {self.title}'
                section.deactivated_by = applied_by
                section.deactivated_at = timezone.now()
            else:
                # change, addition, or partial deletion — proposed_text is the full updated section
                section.content = amendment.proposed_text
            section.amendment_protected = False
            section.protected_until = None
            section.protection_note = ''
            section.save()
            amendment.applied = True
            amendment.save()

    def apply_failure_protection(self):
        """
        When a resolution fails: lock impacted sections from new amendments.

        Protection period is taken from the section's document:
          - Constitution: amendment_protection_weeks (default 15 chapter periods)
          - Bylaws: amendment_protection_weeks (default 10 chapter periods)

        Each week is treated as 7 calendar days.
        Called inside a transaction — caller is responsible for saving self.
        """
        import datetime

        if not self.vote_date:
            from django.utils import timezone
            base_date = timezone.localdate()   # v3.17.4: calendar date, not UTC
        else:
            base_date = self.vote_date

        for amendment in self.amendments.select_related(
            'section__article__document'
        ).all():
            section = amendment.section
            weeks = section.article.document.amendment_protection_weeks
            expires = base_date + datetime.timedelta(weeks=weeks)
            doc_label = section.article.document.get_doc_type_display()
            note = (
                f'Amendment "{self.title}" failed on {base_date.isoformat()}. '
                f'{doc_label} protection period: {weeks} chapter periods '
                f'(until {expires.isoformat()}).'
            )
            section.amendment_protected = True
            section.protected_until = expires
            section.protection_note = note
            section.save()


class ResolutionAmendment(models.Model):
    """
    A single amendment action within a resolution — targets one Section
    and proposes replacement text.
    """
    AMENDMENT_TYPE_CHOICES = [
        ('change', 'Change — rewording or replacing existing text'),
        ('addition', 'Addition — inserting new content into a section'),
        ('deletion', 'Deletion — removing content from a section'),
    ]

    resolution = models.ForeignKey(
        Resolution, on_delete=models.CASCADE, related_name='amendments'
    )
    section = models.ForeignKey(
        Section, on_delete=models.PROTECT, related_name='amendment_history'
    )

    amendment_type = models.CharField(
        max_length=20,
        choices=AMENDMENT_TYPE_CHOICES,
        default='change',
        help_text='What kind of change this amendment makes to the section',
    )

    # Optional: which specific clause/sub-item within the section is affected.
    # e.g. "3.a.i" or "the second sentence of § 4" — free text for human clarity.
    # The proposed_text always contains the full updated section text; this field
    # tells readers/voters which part is actually changing.
    scope_note = models.CharField(
        max_length=300,
        blank=True,
        help_text=(
            'Optional — which specific clause or sub-item is being changed, e.g. "§ 3.a.i" or '
            '"the definition in the second paragraph". Helps readers understand scope without '
            'reading the full diff.'
        ),
    )

    # Snapshot of the section text at the time this amendment was drafted.
    # Lets reviewers see exactly what was being replaced.
    original_text_snapshot = models.TextField(
        help_text='Auto-filled: text of the section when this amendment was created'
    )
    proposed_text = models.TextField(
        blank=True,
        help_text='The full updated section text (empty only when amendment_type=deletion of whole section)',
    )

    applied = models.BooleanField(
        default=False,
        help_text='True once the resolution passes and this text has been written to the section'
    )

    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('resolution', 'section')
        ordering = ['section__article__display_order', 'section__display_order']
        verbose_name = 'Resolution Amendment'

    def save(self, *args, **kwargs):
        # Auto-populate the snapshot from the current section text on first save
        if not self.pk and not self.original_text_snapshot:
            self.original_text_snapshot = self.section.content
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.resolution.title} → {self.section}'


class ResolutionCollaborator(models.Model):
    """
    Grants a member access to a resolution beyond the default member read access.
    - viewer: can always view this resolution (useful when resolution is pre-publication draft)
    - editor: can add/remove amendments, change status — same as CNB permission holder
    Only CNB permission holders can add or remove collaborators.
    """
    ROLE_CHOICES = [
        ('viewer', 'Viewer'),
        ('editor', 'Editor'),
    ]

    resolution = models.ForeignKey(
        Resolution, on_delete=models.CASCADE, related_name='collaborators'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='resolution_collaborations'
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='viewer')
    added_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='added_resolution_collaborators'
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('resolution', 'user')
        ordering = ['user__name']
        verbose_name = 'Resolution Collaborator'

    def __str__(self):
        return f'{self.user} on "{self.resolution.title}" ({self.role})'
