from django.db import models
from django.conf import settings


class GoverningDocument(models.Model):
    DOCUMENT_TYPES = [
        ('foreword', 'Foreword'),
        ('constitution', 'Constitution'),
        ('bylaws', 'Bylaws'),
        ('appendix', 'Appendix'),
    ]

    #: v3.19.1 — the feature flag that shows each document to members.
    #: One flag per document so any of them can be pulled without a deploy.
    #: See `enabled()` below, and `FeatureFlag.DISABLED_BY_DEFAULT` for why
    #: `cnb_foreword` is the one that must fail CLOSED.
    FLAG_FOR_DOC_TYPE = {
        'foreword': 'cnb_foreword',
        'constitution': 'cnb_constitution',
        'bylaws': 'cnb_bylaws',
        'appendix': 'cnb_appendix',
    }

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

    display_order = models.PositiveIntegerField(
        default=0,
        help_text=(
            'Order this document appears in the viewer and table of contents. '
            'Foreword 0, Constitution 10, Bylaws 20, Appendix 30 — gaps left so '
            'a document can be inserted without renumbering.'
        ),
    )

    class Meta:
        # ⚠️ v3.19.1 — THIS ORDERING DID NOT EXIST BEFORE, AND ITS ABSENCE WAS
        # ABOUT TO BECOME VISIBLE. Every query in view/officer/cnb.py is a bare
        # `.all()`, and with no Meta.ordering the database is free to return
        # rows in any order it likes. It happened to look right because the
        # three documents were seeded in reading order and rarely updated —
        # Postgres returns unmodified rows roughly in insertion order, until an
        # UPDATE moves one, at which point the viewer silently reorders.
        #
        # A Foreword makes that latent bug certain rather than likely: it is
        # created LAST (it is the new row) and must render FIRST. Without an
        # explicit order it would have appeared after the Appendix, and the
        # cause would have looked like a template problem.
        ordering = ['display_order', 'doc_type']
        verbose_name = 'Governing Document'
        verbose_name_plural = 'Governing Documents'

    def __str__(self):
        return self.get_doc_type_display()

    @property
    def is_prose_only(self):
        """
        True for a document whose text lives entirely in `preamble` with no
        Articles — the Foreword is the only one today.

        Derived rather than stored: a flag would be one more thing to keep in
        step with reality, and reality here is simply "does it have articles".
        Templates use this to label the prose block correctly, since calling a
        Foreword a "Preamble" is the sort of wrong word nobody fixes later.
        """
        return not self.articles.exists()

    @classmethod
    def enabled_doc_types(cls):
        """
        The doc_types whose feature flag is currently on.

        ⚠️ READ `FeatureFlag.is_feature_enabled`'s DOCSTRING BEFORE CHANGING
        THIS. Flags fail OPEN in Python — a missing row returns True — unless
        the name is in `DISABLED_BY_DEFAULT`. That default is right for the
        three documents already in force (a DB with no flag rows should show
        the Constitution, not hide it) and catastrophically wrong for the
        Foreword, which is unpassed governance: an unseeded flag would PUBLISH
        it. `cnb_foreword` is therefore listed in `DISABLED_BY_DEFAULT`, which
        is the only thing making this fail closed. Do not remove it there
        without changing the logic here.

        v3.19.7 — resolved in ONE query instead of one per document. This loop
        was the `5x src_featureflag` that `test_url_smoke` and
        `test_detail_route_smoke` had been failing on since v3.19.1, on
        `/constitution-bylaws/` and `/cnb/resolutions/new/`. `resolve_many`
        applies the same defaults and writes the same cache entries, so the
        paragraph above is unaffected: `cnb_foreword` still resolves through
        `DISABLED_BY_DEFAULT` when it has no row, and that is asserted in
        `src/test_cnb_foreword.py`.
        """
        from src.models_feature_flags import FeatureFlag

        enabled = FeatureFlag.resolve_many(cls.FLAG_FOR_DOC_TYPE.values())

        return [
            doc_type
            for doc_type, flag in cls.FLAG_FOR_DOC_TYPE.items()
            if enabled[flag]
        ]

    @classmethod
    def enabled(cls):
        """
        Documents members are allowed to see. **Member-facing surfaces only.**

        Officer management (`/officers/cnb/*`) deliberately does NOT use this —
        you have to be able to edit a document before you turn it on, which is
        the entire workflow the Foreword exists for. `manage_document` and the
        CNB dashboard therefore keep querying every row.

        A doc_type with no entry in `FLAG_FOR_DOC_TYPE` is treated as visible,
        so adding a fifth document type without a flag fails toward the old
        behaviour rather than vanishing with no error.
        """
        known = set(cls.FLAG_FOR_DOC_TYPE)
        allowed = set(cls.enabled_doc_types())
        return cls.objects.exclude(
            models.Q(doc_type__in=known) & ~models.Q(doc_type__in=allowed)
        )


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


class _SectionsInTheDocument(models.Manager):
    """Sections that are part of the document: everything not struck by a resolution."""

    def get_queryset(self):
        return super().get_queryset().filter(removed_at__isnull=True)


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

    #: 09-25-26 — when this section first existed in the app. NULL = it
    #: predates tracking (everything seeded before this release). Used by the
    #: "as of a date" view to hide sections added after that date.
    created_at = models.DateTimeField(null=True, blank=True, editable=False)

    # ── Struck from the document by a resolution (v3.44.0, 10-02-26) ─────────
    #
    # Mason: removing the VPA / VPRM sections left holes (§ 1, § 2, § 4). A
    # whole-section deletion that passes now takes the section OUT of the
    # document and the sections after it move up one (`cnb_structure.close_gap`).
    #
    # ⚠️ `objects` (the default manager) HIDES removed sections, on purpose:
    # every surface that lists a document — the viewer, the manager, the PDF,
    # the amendment and maker dropdowns, the cross-reference checker,
    # `article.sections` — goes through it, so a removed section drops out of
    # all of them without each having to remember. Use `Section.all_objects`
    # where history matters (section history, the as-of-a-date view).
    # Foreign keys TO a section (a passed amendment's) still resolve; Django
    # follows those with the base manager.
    #
    # This is NOT the IFC "suspend" switch (`is_active`): a suspended section
    # stays in the document, keeps its number and can be switched back on.
    removed_at = models.DateTimeField(null=True, blank=True, editable=False)
    #: The number it had. `number` itself is changed to a placeholder so the
    #: next section can take it ((article, number) is unique).
    former_number = models.CharField(max_length=20, blank=True, editable=False)

    objects = _SectionsInTheDocument()
    all_objects = models.Manager()

    class Meta:
        ordering = ['article', 'display_order']
        unique_together = ('article', 'number')
        verbose_name = 'Section'

    def save(self, *args, **kwargs):
        if self._state.adding and self.created_at is None:
            from django.utils import timezone
            self.created_at = timezone.now()
        super().save(*args, **kwargs)

    def record_revision(self, source, by=None, resolution=None, note=''):
        """
        Save the text that is ABOUT TO BE REPLACED as a SectionRevision
        (09-25-26). Call immediately before overwriting content/title/is_active.
        Placeholder text from the seeder is not history and is skipped.
        """
        if not self.pk or (self.content or '').startswith('PLACEHOLDER'):
            return None
        from django.utils import timezone
        return SectionRevision.objects.create(
            section=self, title=self.title or '', content=self.content or '',
            was_active=self.is_active, replaced_at=timezone.now(),
            replaced_by=by, source=source, resolution=resolution, note=note[:300],
        )

    @property
    def full_identifier(self):
        """Returns a human-readable ID like 'Constitution Art. III § 2'"""
        doc = self.article.document.get_doc_type_display()
        if self.removed_at:
            return f'{doc} Art. {self.article.number} § {self.former_number} (removed)'
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

    #: Lines that already carry their own resolving wording — not prefixed
    #: again, so text typed before 09-24-26 (when the prefix was written by
    #: hand on the second clause) renders unchanged instead of doubled.
    _RESOLVED_OPENERS = ('therefore', 'be it further resolved', 'be it resolved', 'resolved')

    @property
    def resolved_clauses(self):
        """
        `resolved_text` split into one clause per non-blank line, each as
        `{'prefix': str, 'text': str}` (`prefix` is '' when the line already
        opens with its own "Therefore…"/"Resolved…" wording).

        ⚠️ 09-24-26 — Mason: two resolved clauses rendered on ONE line in the
        Preview PDF. Both templates put the whole field inside a single <p>,
        so the newline between clauses collapsed to a space. Whereas clauses
        have always been split per line; this makes resolved clauses match.
        """
        clauses = []
        for line in (self.resolved_text or '').splitlines():
            text = line.strip()
            if not text:
                continue
            if text.lower().startswith(self._RESOLVED_OPENERS):
                prefix = ''
            elif not clauses:
                prefix = 'Therefore, be it resolved,'
            else:
                prefix = 'Therefore, be it further resolved,'
            clauses.append({'prefix': prefix, 'text': text})
        return clauses

    def apply_amendments(self, applied_by):
        """
        When a resolution passes: update each targeted section with the proposed text.
        Clears any existing amendment protection on those sections.
        - change/addition: replaces section content with proposed_text
        - deletion: clears content and suspends the section
        Called inside a transaction — caller is responsible for saving self.
        """
        from django.utils import timezone
        from src.cnb_structure import close_gap

        amendments = list(self.amendments.select_related('section__article__document'))
        # v3.43.0/v3.44.0 — every citation is recorded FIRST, from the document
        # as the chapter voted on it. Removing § 2 renumbers § 4; the amendment
        # to "§ 4" must still say § 4. (See `identifier_snapshot`.)
        cited = {a.pk: a.section.full_identifier for a in amendments}

        def finish(amendment):
            amendment.applied = True
            amendment.identifier_snapshot = cited[amendment.pk]
            amendment.save(update_fields=['applied', 'identifier_snapshot'])

        # Text changes first. `update_fields` throughout: each amendment holds
        # its own copy of its section, read before any renumbering, and a full
        # save would write that stale `number` back.
        removals = []
        for amendment in amendments:
            if amendment.is_whole_section_removal:
                removals.append(amendment)
                continue
            section = amendment.section
            # 09-25-26 — keep the outgoing text (SectionRevision).
            section.record_revision('resolution', by=applied_by, resolution=self)
            # change, addition, or partial deletion — proposed_text is the full updated section
            section.content = amendment.proposed_text
            section.amendment_protected = False
            section.protected_until = None
            section.protection_note = ''
            section.save(update_fields=['content', 'amendment_protected', 'protected_until', 'protection_note'])
            finish(amendment)

        # Then whole-section removals, one at a time, each read fresh: an
        # earlier removal in the same article has already moved it.
        for amendment in removals:
            section = amendment.section
            section.refresh_from_db()
            section.record_revision('resolution', by=applied_by, resolution=self)
            section.content = ''
            section.is_active = False
            section.deactivation_reason = f'Deleted by resolution: {self.title}'
            section.deactivated_by = applied_by
            section.deactivated_at = timezone.now()
            section.amendment_protected = False
            section.protected_until = None
            section.protection_note = ''
            section.save(update_fields=['content', 'is_active', 'deactivation_reason', 'deactivated_by',
                                        'deactivated_at', 'amendment_protected', 'protected_until',
                                        'protection_note'])
            # v3.44.0 — take it out of the document; later sections move up.
            close_gap(section)
            finish(amendment)

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

    #: v3.43.0 — the section's identifier ("Constitution Art. III § 3") at the
    #: moment the resolution passed. A later resolution can renumber articles
    #: and sections; a passed resolution must keep citing the numbers it was
    #: written and voted on with. Blank until applied.
    identifier_snapshot = models.CharField(max_length=120, blank=True)

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

    @property
    def is_whole_section_removal(self):
        return self.amendment_type == 'deletion' and not self.scope_note and not self.proposed_text

    @property
    def renumbering_note(self):
        """For a draft whole-section removal: what moves up when it passes ('' otherwise)."""
        if self.applied or not self.is_whole_section_removal:
            return ''
        from src.cnb_structure import describe_removal
        return describe_removal(self.section)

    @property
    def display_identifier(self):
        """What to call the section when showing this amendment (see `identifier_snapshot`)."""
        return self.identifier_snapshot or self.section.full_identifier

    def __str__(self):
        return f'{self.resolution.title} → {self.section}'


class ResolutionStructureChange(models.Model):
    """
    A structural change a resolution proposes: a NEW article or section, or a
    new NAME for one (v3.43.0, 10-02-26).

    Mason: "add an article + section maker to the edit page. Currently you have
    to just put it straight in as text in the body ... if someone wants to put
    in a new article 4 it moves the existing article 4 to 5, 5 to 6, etc."
    and "a way to edit the name of articles + sections too."

    `ResolutionAmendment` can only replace the TEXT of a section that already
    exists. This is everything else. Like an amendment it is a proposal:
    nothing in the live document changes until the resolution is marked
    passed, when `src.cnb_structure.apply_structure_changes` inserts,
    renumbers and renames.

    Position is stored as "before this article/section" (an object, not a
    number), so it stays right if another change renumbers things first.
    Null = at the end.
    """
    KIND_CHOICES = [
        ('new_article', 'New article'),
        ('new_section', 'New section'),
        ('rename_article', 'Rename an article'),
        ('rename_section', 'Rename a section'),
    ]

    resolution = models.ForeignKey(Resolution, on_delete=models.CASCADE, related_name='structure_changes')
    kind = models.CharField(max_length=20, choices=KIND_CHOICES)

    #: new_article: the document it goes into.
    document = models.ForeignKey(GoverningDocument, on_delete=models.CASCADE, null=True, blank=True, related_name='+')
    #: new_section: the existing article it goes into. rename_article: the article.
    article = models.ForeignKey(Article, on_delete=models.CASCADE, null=True, blank=True, related_name='+')
    #: new_section: instead of `article`, a new article proposed by this same resolution.
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='children')
    #: rename_section: the section.
    section = models.ForeignKey(Section, on_delete=models.CASCADE, null=True, blank=True, related_name='+')

    #: Where a new article / section goes. Null = at the end.
    before_article = models.ForeignKey(Article, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    before_section = models.ForeignKey(Section, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    title = models.CharField(max_length=200, blank=True, help_text='The new title.')
    content = models.TextField(blank=True, help_text='new_section: the full text of the new section.')
    #: rename_*: the title when this was drafted, so reviewers see old and new.
    old_title = models.CharField(max_length=200, blank=True)

    applied = models.BooleanField(default=False)
    #: Set when applied: what it became ("Constitution Art. IV"), and the rows.
    result_label = models.CharField(max_length=160, blank=True)
    result_article = models.ForeignKey(Article, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    result_section = models.ForeignKey(Section, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['added_at', 'pk']
        verbose_name = 'Resolution Structure Change'

    def __str__(self):
        return f'{self.resolution.title}: {self.get_kind_display()}'


class SectionRevision(models.Model):
    """
    A past version of a Section: the text that was in force UNTIL `replaced_at`
    (09-25-26).

    WHY: "what did Bylaws III §2 say last spring, and what changed it?" had no
    answer. `ResolutionAmendment.original_text_snapshot` is taken when an
    amendment is DRAFTED (not when it is applied), and direct edits in the C&B
    manager and forced imports overwrote text with no record at all. Precedent
    depends on knowing what the rule WAS — the same reasoning that keeps Kai
    records.

    Written by `Section.record_revision()` immediately before every overwrite:
    `Resolution.apply_amendments` (source='resolution'), `edit_section`
    ('direct_edit') and `seed_cnb_documents` ('import'). Migration 0053
    backfilled one row per already-applied amendment from its snapshot
    ('backfill' — approximate, see `note`).

    Append-only. Nothing in the app edits or deletes these.
    """
    SOURCE_CHOICES = [
        ('resolution', 'Resolution passed'),
        ('direct_edit', 'Direct edit in the C&B manager'),
        ('import', 'Document import'),
        ('backfill', 'Reconstructed from an earlier resolution'),
    ]

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name='revisions')
    title = models.CharField(max_length=255, blank=True)
    content = models.TextField(blank=True)
    was_active = models.BooleanField(default=True)
    replaced_at = models.DateTimeField(db_index=True)
    replaced_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES)
    resolution = models.ForeignKey(
        'Resolution', on_delete=models.SET_NULL, null=True, blank=True, related_name='section_revisions',
    )
    note = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ['-replaced_at', '-pk']
        verbose_name = 'Section Revision'
        verbose_name_plural = 'Section Revisions'

    def __str__(self):
        return f'{self.section} (until {self.replaced_at:%Y-%m-%d})'


class ResolutionCollaborator(models.Model):
    """
    Grants a member access to a resolution beyond the default member read access.
    - viewer: can always view this resolution (useful when resolution is pre-publication draft)
    - editor: can edit the resolution's text and add/remove amendments while it is
      draft or pending. Changing status and managing collaborators stay with CNB
      permission holders (Mason, 10-02-26, v3.41.1).
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


class ResolutionNote(models.Model):
    """
    A sticky note on a resolution for the people working on it — never part
    of the resolution text, never on the print/PDF view.

    09-24-26 — Mason: "a way for people who are working on a resolution to
    make notes ... off to the side ... without that being added to the
    resolution ... it says who put what note (or edited a note) like a sticky
    note ... where there can be multiple to work through ... like Word's
    comments but not tied to a part of the page."

    WHO: the working group — CNB permission holders plus anyone listed as a
    `ResolutionCollaborator` (viewer or editor). See `user_can_use_notes`.
    Resolutions themselves are readable by every member; notes are not.

    Everyone in the working group sees every note and may add, edit or tick
    one off; each of those records who and when. Deleting is limited to the
    note's author or a CNB holder, so one collaborator cannot quietly remove
    another's note — ticking it off as done is the ordinary way to clear it.
    """
    COLOR_CHOICES = [
        ('yellow', 'Yellow'),
        ('blue', 'Blue'),
        ('green', 'Green'),
        ('purple', 'Purple'),
    ]
    MAX_LENGTH = 2000

    #: Optional pin: which part of the resolution a note sits beside, as a
    #: sticky note in the margin on tablet/desktop (09-24-26, Mason's
    #: follow-up). Blank = not pinned, lives only in the notes list/drawer.
    #: These are page regions, not text offsets — they survive any edit to
    #: the resolution's wording.
    ANCHOR_CHOICES = [
        ('header', 'Header'),
        ('preamble', 'Section I — Preamble'),
        ('resolved', 'Resolved clauses'),
        ('body', 'Section II — Body'),
        ('conclusion', 'Section III — Conclusion'),
        ('amendments', 'Tracked amendments'),
    ]

    resolution = models.ForeignKey(
        Resolution, on_delete=models.CASCADE, related_name='notes'
    )
    anchor = models.CharField(max_length=20, choices=ANCHOR_CHOICES, blank=True, default='')
    body = models.TextField(max_length=MAX_LENGTH)
    color = models.CharField(max_length=10, choices=COLOR_CHOICES, default='yellow')

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='resolution_notes_created',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='resolution_notes_edited',
    )
    edited_at = models.DateTimeField(null=True, blank=True)

    is_done = models.BooleanField(default=False)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='resolution_notes_done',
    )
    done_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # Open notes first, newest first within each group.
        ordering = ['is_done', '-created_at']
        verbose_name = 'Resolution Note'
        verbose_name_plural = 'Resolution Notes'

    def __str__(self):
        return f'Note on "{self.resolution.title}" by {self.created_by or "deleted user"}'

    @staticmethod
    def user_can_use_notes(user, resolution):
        """CNB holders and any collaborator on this resolution."""
        if not user.is_authenticated:
            return False
        if user.has_cnb_permission:
            return True
        return resolution.collaborators.filter(user=user).exists()

    def user_can_delete(self, user):
        return user.has_cnb_permission or (self.created_by_id is not None and self.created_by_id == user.pk)
