"""
Kai commendations — v3.28.9 (corrects v3.28.8, which built this feature
under the wrong name — "accommodation request" — after a wording mistake;
see that changelog and this one for the correction).

Requested by Mason: a way to submit something to the Kai Committee that is
NOT a disciplinary report — a commendation, recognizing a member for doing
something well — with a toggle between the two forms on the submission
page and the same committee-admin-editable custom fields the disciplinary
report already has. The submitter selects WHO they're commending.

⚠️ DELIBERATELY A SEPARATE MODEL FROM KaiReport, NOT A `report_type` FLAG
ON IT. A commendation has no accused. Every piece of KaiReport's machinery
beyond title/description/status exists because there IS an adversarial
pair of parties: `targeted_to` (as an accused), `deliberation_outcome`,
`KaiRecusal` (the bylaws' "only the accused must recuse"), `KaiAppeal`
(only the accused may appeal a decision), `is_party`/`recusal_reason`
(submitter vs. accused vs. both), `case_number` alongside
"KAI-2026-NNN" case-file semantics, the whole identity-redaction
apparatus built around "the accused must never learn who reported them."
None of that has a meaning for "Jane organized the whole philanthropy
event and deserves recognition." Reusing KaiReport would mean either
dragging all of that in for a submission it doesn't apply to, or
scattering `if report_type == 'commendation':` branches through code
whose whole design assumes an adversarial pair of parties. A separate
model with its own, much smaller, workflow is the shape that matches
what this actually is.

Unlike KaiReport, `KaiCommendation.commended_member` IS a required field
(the whole point — someone must be able to select who they're
commending), but it is NOT "the accused." Nothing here recuses the
commended member from anything, nothing withholds identity from them by
design (see the visibility note below), and there is no equivalent of
KaiRecusal/KaiAppeal for this population.

What IS reused, deliberately: the KaiFormField dynamic-custom-field system
(discriminated by `form_type`, see that model) and the Kai committee
PERMISSION system (`KaiMemberPermission` / `_get_kai_access`,
src/view/kai_reports.py) — whoever a chair has granted
`can_view_report_list`/`can_view_report_details`/etc. sees commendations
too, with the same per-member granularity as discipline cases.

**Visibility (Mason, 09-02-26): Kai committee only.** A commendation is
not shown to the chapter automatically and is not shown to the commended
member automatically — the committee reviews it the same way it reviews
everything else routed through this dashboard. There is deliberately no
honoree-facing view built yet; if the committee wants to relay a
commendation to the person it's about, that happens outside the app
today. Building an in-app "share with the honoree" view is a natural
follow-up but is new scope beyond what was asked for this release.

**Submitter anonymity (Mason, 09-02-26): the submitter's choice.**
`is_submitter_anonymous` is a checkbox the submitter sets at submission
time. Because there is no honoree-facing view yet, this field's only
present effect is informational — it's shown on the committee's detail
page as an instruction ("the submitter asked to remain anonymous") for
whenever the committee does relay the commendation, in-app or out. It is
NOT a redaction mechanism the way `can_view_submitter_identity` is for
KaiReport: nothing in this module hides the submitter FROM THE
COMMITTEE — a committee member with `can_view_submitter_identity` always
sees who submitted a commendation, the same as for discipline cases.
"""
from django.conf import settings
from django.db import models

from src.storage import DualLocationStorage, uuid_upload_path


def kai_commendation_attachment_path(instance, filename):
    """`kai_commendations/<uuid>.<ext>` — see `uuid_upload_path` in src/storage.py."""
    return uuid_upload_path('kai_commendations')(instance, filename)


def kai_commendation_response_file_path(instance, filename):
    """`kai_commendations/custom_fields/<uuid>.<ext>` — see `uuid_upload_path`."""
    return uuid_upload_path('kai_commendations/custom_fields')(instance, filename)


class KaiCommendation(models.Model):
    """A member's submission to the Kai Committee commending another member."""

    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('acknowledged', 'Acknowledged'),
        ('archived', 'Archived'),
    ]

    title = models.CharField(max_length=255, help_text='Brief summary of what this commendation is for')
    description = models.TextField(help_text='What did they do? Be specific.')

    commended_member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='kai_commendations_received',
        help_text='The member being commended.',
        # null=True at the DB level only (see migration 0030) — `blank`
        # is deliberately left at its default False, so the submission
        # ModelForm still requires this. A row that predates the rename
        # from "accommodation request" has no honoree recorded anywhere
        # and there's no way to reconstruct one; NOT NULL would have
        # made that migration either fail or invent a wrong answer.
        null=True,
    )
    is_submitter_anonymous = models.BooleanField(
        default=False,
        help_text=(
            "If checked, the submitter has asked not to be named if the "
            "committee relays this commendation to the person it's about."
        ),
    )

    attachment = models.FileField(
        upload_to=kai_commendation_attachment_path,
        storage=DualLocationStorage(),
        blank=True,
        null=True,
        help_text='Optional supporting file (e.g. a photo, a screenshot of positive feedback)',
    )

    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='kai_commendations_submitted',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='kai_commendations_assigned',
        help_text='Committee member handling this commendation.',
    )
    committee_notes = models.TextField(
        blank=True,
        help_text='Internal committee notes — not shown outside the committee.',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='kai_commendations_reviewed',
    )

    # ------------------------------------------------------------------
    # Commendation number — mirrors KaiReport.case_number (v3.18.0), same
    # reasoning: a sequential pk leaks total volume and ordering, and
    # "COM-2026-014" reads better in committee notes than "#87". A
    # per-year, per-prefix counter rather than sharing KaiReport's, so the
    # two populations don't interleave and one being deleted doesn't
    # change the other's numbering.
    # ------------------------------------------------------------------
    commendation_number = models.CharField(
        max_length=20,
        blank=True,
        default='',
        db_index=True,
        help_text='Per-year identifier, e.g. COM-2026-014. Assigned automatically.',
    )

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Kai Commendation'
        verbose_name_plural = 'Kai Commendations'
        constraints = [
            models.UniqueConstraint(
                fields=['commendation_number'],
                condition=~models.Q(commendation_number=''),
                name='uniq_kai_commendation_number',
            ),
        ]

    def __str__(self):
        # commended_member is nullable at the DB level only (rows from
        # before the accommodation->commendation rename have none) — see
        # the field's own comment.
        honoree = self.commended_member.name if self.commended_member_id else 'no member specified'
        return f'{self.title} - {honoree} ({self.submitted_at.strftime("%Y-%m-%d")})'

    @property
    def display_number(self):
        return self.commendation_number or f'#{self.pk}'

    @classmethod
    def next_commendation_number(cls, year):
        """The next unused `COM-<year>-NNN`. See KaiReport.next_case_number —
        identical reasoning, kept as a separate counter (see class docstring)."""
        prefix = f'COM-{year}-'
        existing = (
            cls.objects.filter(commendation_number__startswith=prefix)
            .values_list('commendation_number', flat=True)
        )
        highest = 0
        for number in existing:
            tail = number[len(prefix):]
            if tail.isdigit():
                highest = max(highest, int(tail))
        return f'{prefix}{highest + 1:03d}'

    #: See KaiReport.CASE_NUMBER_MAX_ATTEMPTS — identical reasoning.
    COMMENDATION_NUMBER_MAX_ATTEMPTS = 5

    @staticmethod
    def _is_commendation_number_collision(exc):
        message = str(exc).lower()
        return 'uniq_kai_commendation_number' in message or 'commendation_number' in message

    def save(self, *args, **kwargs):
        # Assign the commendation number on first save. Same read-then-write-
        # with-retry shape as KaiReport.save() (v3.18.1/v3.18.2) — see there
        # for why a bounded retry loop is needed rather than a single attempt.
        from django.db import IntegrityError, transaction
        from django.utils import timezone

        if not self.commendation_number:
            year = (self.submitted_at or timezone.now()).year

            update_fields = kwargs.get('update_fields')
            if update_fields is not None and 'commendation_number' not in update_fields:
                kwargs['update_fields'] = list(update_fields) + ['commendation_number']

            candidate = self.next_commendation_number(year)
            prefix = f'COM-{year}-'
            for _attempt in range(self.COMMENDATION_NUMBER_MAX_ATTEMPTS):
                self.commendation_number = candidate
                try:
                    with transaction.atomic():
                        return super().save(*args, **kwargs)
                except IntegrityError as exc:
                    if not self._is_commendation_number_collision(exc):
                        raise
                    tail = candidate[len(prefix):]
                    counter = int(tail) if tail.isdigit() else 0
                    candidate = f'{prefix}{counter + 1:03d}'
            raise IntegrityError(
                f'Could not allocate a unique Kai commendation number for {year} '
                f'after {self.COMMENDATION_NUMBER_MAX_ATTEMPTS} attempts.'
            )

        super().save(*args, **kwargs)

    def mark_reviewed(self, reviewer, status):
        from django.utils import timezone
        assert status in ('acknowledged', 'archived'), status
        self.status = status
        self.reviewed_by = reviewer
        self.reviewed_at = timezone.now()
        self.save(update_fields=['status', 'reviewed_by', 'reviewed_at'])


class KaiCommendationActivity(models.Model):
    """Activity log for a commendation — mirrors KaiReportActivity."""

    ACTION_CHOICES = [
        ('created', 'Commendation Submitted'),
        ('status_changed', 'Status Changed'),
        ('assigned', 'Assigned'),
        ('notes_updated', 'Committee Notes Updated'),
        ('reviewed', 'Reviewed'),
    ]

    commendation = models.ForeignKey(
        KaiCommendation,
        on_delete=models.CASCADE,
        related_name='activity_log',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    details = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name = 'Kai Commendation Activity'
        verbose_name_plural = 'Kai Commendation Activities'

    def __str__(self):
        user_name = self.user.name if self.user else 'System'
        return f'{user_name} - {self.get_action_display()} - {self.timestamp.strftime("%Y-%m-%d %H:%M")}'


class KaiCommendationFieldResponse(models.Model):
    """
    Custom field responses for a commendation — mirrors
    KaiReportFieldResponse exactly, pointed at KaiCommendation instead of
    KaiReport. See KaiFormField's docstring for why the field DEFINITIONS
    are shared between both forms while the responses are not: a response
    belongs to one specific commendation or report, so there's nothing to
    gain from sharing that table, and keeping it separate means a bug in
    one form's response-handling code can't touch the other's data.
    """
    commendation = models.ForeignKey(
        KaiCommendation,
        on_delete=models.CASCADE,
        related_name='custom_responses',
    )
    field = models.ForeignKey(
        'KaiFormField',
        on_delete=models.CASCADE,
        related_name='commendation_responses',
    )

    text_value = models.TextField(blank=True, null=True)
    number_value = models.DecimalField(max_digits=20, decimal_places=5, null=True, blank=True)
    json_value = models.JSONField(null=True, blank=True)
    file_value = models.FileField(
        upload_to=kai_commendation_response_file_path,
        null=True,
        blank=True,
        storage=DualLocationStorage(),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['commendation', 'field']
        verbose_name = 'Kai Commendation Field Response'
        verbose_name_plural = 'Kai Commendation Field Responses'

    def __str__(self):
        return f'{self.commendation.title} - {self.field.label}'

    def get_display_value(self):
        """Mirrors KaiReportFieldResponse.get_display_value — see the
        v3.19.6 comment there on why a file field returns the basename, not
        the URL: this is rendered as text, and the raw storage path must
        never be printed to the page."""
        import os

        if self.field.field_type in ['select', 'radio', 'text', 'textarea', 'email', 'date']:
            return self.text_value or ''
        elif self.field.field_type in ['multiselect', 'checkbox']:
            return self.json_value or []
        elif self.field.field_type == 'number':
            return self.number_value
        elif self.field.field_type == 'file':
            return os.path.basename(self.file_value.name) if self.file_value else None
        elif self.field.field_type == 'member_select':
            return self.text_value or ''
        else:
            return self.text_value or ''
