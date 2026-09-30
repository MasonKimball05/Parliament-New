"""
The Chapter table (multi-chapter phase 4, step 4a, 09-29-26).

Mason decided on tenancy A (shared schema, a `chapter` FK on ROOT models;
docs/MULTI_CHAPTER_PLAN.md, "Phase 4 plan"). This is the first step: the
chapter's identity gets a database row. Nothing points at it yet, and
`src.chapter.get_chapter()` still reads `settings.CHAPTER`. Switching the
source of truth to this table happens in step 4b, together with resolving the
chapter from the request, because that is when there can be more than one.

Until then this table must MATCH settings. `src.W007` warns when it drifts,
and `manage.py sync_chapter_from_settings` copies settings onto the default row.

The identity fields mirror `src.chapter.ChapterIdentity` one-to-one;
`test_chapter_model` pins that, so adding a field to one means adding it to both.
"""
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q


class ChapterManager(models.Manager):
    def default(self):
        """The chapter this deployment serves today (step 4a: exactly one)."""
        return self.filter(is_default=True).first()


class Chapter(models.Model):
    SEMESTERS = [('Fall', 'Fall'), ('Spring', 'Spring')]

    slug = models.SlugField(max_length=50, unique=True, help_text="e.g. 'alpha-mu'")
    is_default = models.BooleanField(
        default=False,
        help_text='The chapter this deployment serves. Exactly one row may have this (step 4a).')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    # --- identity: mirrors src.chapter.ChapterIdentity ------------------------
    fraternity = models.CharField(max_length=100)
    chapter_name = models.CharField(max_length=100)
    school = models.CharField(max_length=150)
    school_short = models.CharField(max_length=80)
    city = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    site_name = models.CharField(max_length=150)
    domain = models.CharField(max_length=253, unique=True)
    crest = models.CharField(max_length=200, help_text='Static path to the crest image, e.g. images/crest.png')
    primary_color = models.CharField(max_length=20)
    secondary_color = models.CharField(max_length=20)
    motto = models.CharField(max_length=200, blank=True)
    school_email_domain = models.CharField(max_length=253, blank=True)
    founding_year = models.PositiveSmallIntegerField()
    founding_semester = models.CharField(max_length=6, choices=SEMESTERS)
    lettering_anchor = models.CharField(max_length=40, blank=True)

    objects = ChapterManager()

    class Meta:
        ordering = ['chapter_name']
        constraints = [
            models.UniqueConstraint(fields=['is_default'], condition=Q(is_default=True),
                                    name='chapter_single_default'),
        ]

    def __str__(self):
        return f'{self.chapter_name} ({self.domain})'

    def clean(self):
        if self.lettering_anchor:
            from src.pledge_classes import parse_lettering_anchor
            try:
                parse_lettering_anchor(self.lettering_anchor)
            except ValueError as e:
                raise ValidationError({'lettering_anchor': str(e)})

    def identity(self):
        """This row as a `src.chapter.ChapterIdentity`."""
        from src.chapter import FIELD_NAMES, ChapterIdentity
        return ChapterIdentity(**{f: getattr(self, f) for f in FIELD_NAMES})

    @staticmethod
    def values_from_settings():
        """{field: value} for the identity fields, from settings.CHAPTER (via get_chapter's validation)."""
        from src.chapter import FIELD_NAMES, get_chapter
        ident = get_chapter()
        return {f: getattr(ident, f) for f in FIELD_NAMES}

    def drift_from_settings(self):
        """{field: (row value, settings value)} for every identity field that differs."""
        want = self.values_from_settings()
        return {f: (getattr(self, f), v) for f, v in want.items() if getattr(self, f) != v}
