"""
Kai accommodation requests — v3.28.8.

Requested by Mason: a way to submit something to the Kai Committee that is
NOT a disciplinary report — a request for accommodation (medical,
religious, scheduling, or otherwise) — with a toggle between the two forms
on the submission page and the same committee-admin-editable custom fields
the disciplinary report already has.

⚠️ DELIBERATELY A SEPARATE MODEL FROM KaiReport, NOT A `report_type` FLAG
ON IT. An accommodation request has no accused. Every piece of KaiReport's
machinery beyond title/description/status exists because there IS one:
`targeted_to`, `deliberation_outcome`, `KaiRecusal` (the bylaws' "only the
accused must recuse"), `KaiAppeal` (only the accused may appeal a
decision), `is_party`/`recusal_reason` (submitter vs. accused vs. both),
`case_number` alongside "KAI-2026-NNN" case-file semantics, the whole
identity-redaction apparatus built around "the accused must never learn
who reported them." None of that has a meaning for "a member needs a
lighter meeting schedule during finals" or "please excuse me from an event
for a religious observance." Reusing KaiReport would mean either dragging
all of that in for a request it doesn't apply to, or scattering
`if report_type == 'accommodation':` branches through code whose whole
design assumes an adversarial pair of parties. A separate model with its
own, much smaller, workflow is the shape that matches what this actually
is: a request and a committee response, not a case.

What IS reused, deliberately: the KaiFormField dynamic-custom-field system
(discriminated by `form_type`, see that model) and the Kai committee
PERMISSION system (`KaiMemberPermission` / `_get_kai_access`,
src/view/kai_reports.py) — whoever a chair has granted
`can_view_report_list`/`can_view_report_details`/etc. sees accommodation
requests too, with the same per-member granularity as discipline cases.
That was a deliberate choice (Mason, 09-02-26): accommodation requests are
sensitive (often medical/disability/religious) and the Kai committee is
already the body this chapter trusts with sensitive personal information
under a permission system built for exactly that, rather than inventing a
second, parallel access-control surface for a second population to keep in
sync with the first.

Unlike KaiReport, the requester's identity is NOT withheld from anyone by
design the way an accused's is — there is no adverse party here to
withhold it from. `can_view_submitter_identity` still gates whether a
given committee member SEES who requested it (the same granular grant as
discipline cases, per the access decision above), but there is no
equivalent of "the accused must never learn who reported them" because
there is no accused.
"""
from django.conf import settings
from django.db import models

from src.storage import DualLocationStorage, uuid_upload_path


def kai_accommodation_attachment_path(instance, filename):
    """`kai_accommodations/<uuid>.<ext>` — see `uuid_upload_path` in src/storage.py."""
    return uuid_upload_path('kai_accommodations')(instance, filename)


def kai_accommodation_response_file_path(instance, filename):
    """`kai_accommodations/custom_fields/<uuid>.<ext>` — see `uuid_upload_path`."""
    return uuid_upload_path('kai_accommodations/custom_fields')(instance, filename)


class KaiAccommodationRequest(models.Model):
    """A member's request to the Kai Committee for an accommodation."""

    STATUS_CHOICES = [
        ('pending', 'Pending Review'),
        ('in_review', 'Under Review'),
        ('approved', 'Approved'),
        ('denied', 'Denied'),
        ('closed', 'Closed'),
    ]

    title = models.CharField(max_length=255, help_text='Brief summary of what the accommodation is for')
    description = models.TextField(help_text='Details of the accommodation being requested')
    attachment = models.FileField(
        upload_to=kai_accommodation_attachment_path,
        storage=DualLocationStorage(),
        blank=True,
        null=True,
        help_text='Optional supporting document (e.g. a doctor\'s note)',
    )

    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='kai_accommodation_requests_submitted',
    )
    submitted_at = models.DateTimeField(auto_now_add=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='kai_accommodation_requests_assigned',
        help_text='Committee member handling this request.',
    )
    committee_notes = models.TextField(
        blank=True,
        help_text='Internal committee notes — not shown to the requester.',
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='kai_accommodation_requests_resolved',
    )

    # ------------------------------------------------------------------
    # Request number — mirrors KaiReport.case_number (v3.18.0), same
    # reasoning: a sequential pk leaks total volume and ordering, and
    # "ACC-2026-014" reads better in committee notes than "#87". A
    # per-year, per-prefix counter rather than sharing KaiReport's, so the
    # two populations don't interleave and one being deleted doesn't
    # change the other's numbering.
    # ------------------------------------------------------------------
    request_number = models.CharField(
        max_length=20,
        blank=True,
        default='',
        db_index=True,
        help_text='Per-year request identifier, e.g. ACC-2026-014. Assigned automatically.',
    )

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Kai Accommodation Request'
        verbose_name_plural = 'Kai Accommodation Requests'
        constraints = [
            models.UniqueConstraint(
                fields=['request_number'],
                condition=~models.Q(request_number=''),
                name='uniq_kai_accommodation_request_number',
            ),
        ]

    def __str__(self):
        return f'{self.title} - {self.requester.name} ({self.submitted_at.strftime("%Y-%m-%d")})'

    @property
    def display_number(self):
        return self.request_number or f'#{self.pk}'

    @classmethod
    def next_request_number(cls, year):
        """The next unused `ACC-<year>-NNN`. See KaiReport.next_case_number —
        identical reasoning, kept as a separate counter (see class docstring)."""
        prefix = f'ACC-{year}-'
        existing = (
            cls.objects.filter(request_number__startswith=prefix)
            .values_list('request_number', flat=True)
        )
        highest = 0
        for number in existing:
            tail = number[len(prefix):]
            if tail.isdigit():
                highest = max(highest, int(tail))
        return f'{prefix}{highest + 1:03d}'

    #: See KaiReport.CASE_NUMBER_MAX_ATTEMPTS — identical reasoning.
    REQUEST_NUMBER_MAX_ATTEMPTS = 5

    @staticmethod
    def _is_request_number_collision(exc):
        message = str(exc).lower()
        return 'uniq_kai_accommodation_request_number' in message or 'request_number' in message

    def save(self, *args, **kwargs):
        # Assign the request number on first save. Same read-then-write-with-
        # retry shape as KaiReport.save() (v3.18.1/v3.18.2) — see there for
        # why a bounded retry loop is needed rather than a single attempt.
        from django.db import IntegrityError, transaction
        from django.utils import timezone

        if not self.request_number:
            year = (self.submitted_at or timezone.now()).year

            update_fields = kwargs.get('update_fields')
            if update_fields is not None and 'request_number' not in update_fields:
                kwargs['update_fields'] = list(update_fields) + ['request_number']

            candidate = self.next_request_number(year)
            prefix = f'ACC-{year}-'
            for _attempt in range(self.REQUEST_NUMBER_MAX_ATTEMPTS):
                self.request_number = candidate
                try:
                    with transaction.atomic():
                        return super().save(*args, **kwargs)
                except IntegrityError as exc:
                    if not self._is_request_number_collision(exc):
                        raise
                    tail = candidate[len(prefix):]
                    counter = int(tail) if tail.isdigit() else 0
                    candidate = f'{prefix}{counter + 1:03d}'
            raise IntegrityError(
                f'Could not allocate a unique Kai accommodation request number for {year} '
                f'after {self.REQUEST_NUMBER_MAX_ATTEMPTS} attempts.'
            )

        super().save(*args, **kwargs)

    def mark_resolved(self, resolver, status):
        from django.utils import timezone
        assert status in ('approved', 'denied', 'closed'), status
        self.status = status
        self.resolved_by = resolver
        self.resolved_at = timezone.now()
        self.save(update_fields=['status', 'resolved_by', 'resolved_at'])


class KaiAccommodationRequestActivity(models.Model):
    """Activity log for an accommodation request — mirrors KaiReportActivity."""

    ACTION_CHOICES = [
        ('created', 'Request Created'),
        ('status_changed', 'Status Changed'),
        ('assigned', 'Assigned'),
        ('notes_updated', 'Committee Notes Updated'),
        ('resolved', 'Request Resolved'),
    ]

    request = models.ForeignKey(
        KaiAccommodationRequest,
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
        verbose_name = 'Kai Accommodation Request Activity'
        verbose_name_plural = 'Kai Accommodation Request Activities'

    def __str__(self):
        user_name = self.user.name if self.user else 'System'
        return f'{user_name} - {self.get_action_display()} - {self.timestamp.strftime("%Y-%m-%d %H:%M")}'


class KaiAccommodationFieldResponse(models.Model):
    """
    Custom field responses for an accommodation request — mirrors
    KaiReportFieldResponse exactly, pointed at KaiAccommodationRequest
    instead of KaiReport. See KaiFormField's docstring for why the field
    DEFINITIONS are shared between both forms while the responses are not:
    a response belongs to one specific request or report, so there's
    nothing to gain from sharing that table, and keeping it separate means
    a bug in one form's response-handling code can't touch the other's data.
    """
    request = models.ForeignKey(
        KaiAccommodationRequest,
        on_delete=models.CASCADE,
        related_name='custom_responses',
    )
    field = models.ForeignKey(
        'KaiFormField',
        on_delete=models.CASCADE,
        related_name='accommodation_responses',
    )

    text_value = models.TextField(blank=True, null=True)
    number_value = models.DecimalField(max_digits=20, decimal_places=5, null=True, blank=True)
    json_value = models.JSONField(null=True, blank=True)
    file_value = models.FileField(
        upload_to=kai_accommodation_response_file_path,
        null=True,
        blank=True,
        storage=DualLocationStorage(),
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['request', 'field']
        verbose_name = 'Kai Accommodation Field Response'
        verbose_name_plural = 'Kai Accommodation Field Responses'

    def __str__(self):
        return f'{self.request.title} - {self.field.label}'

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
