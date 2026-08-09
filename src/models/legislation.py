import logging
import os
import uuid

from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone
from src.storage import DualLocationStorage

logger = logging.getLogger(__name__)


def validate_legislation_file(value):
    """Validates the file extension."""
    if not value.name.endswith('.pdf') and not value.name.endswith('.docx'):
        raise ValidationError('Only PDF and DOCX files are allowed.')


def legislation_draft_upload_path(instance, filename):
    """
    Storage path for a draft's attachment: `legislation_drafts/<uuid>.<ext>`.

    ⚠️ v3.19.3 — THE RANDOM NAME IS DEFENCE IN DEPTH, NOT THE ACCESS CONTROL.
    The access control is `serve_legislation_draft_document`, which goes through
    `_get_own_draft()`. Read that first; this callable exists because of what
    was underneath it.

    Drafts previously used `upload_to='legislation_drafts/'`, which keeps the
    author's own filename — and `SanitizedFilenameMixin` (v3.14.2) slugifies it
    at save time, so the stored name was *derived deterministically from the
    title the author chose*. Django's random 7-character suffix only appears on
    a collision. A draft called "Dues Restructuring Amendment" landed at
    `/media/legislation_drafts/dues-restructuring-amendment.docx`, and `/media/`
    is served by `serve_media`, which is `@login_required` and nothing else — so
    the path was guessable by any member, including a pledge.

    The slugifier is still the right fix for what it was written for. It simply
    moved this file from unguessable to guessable, and nothing else was
    protecting the path. The published bill gets a readable name again: publish
    COPIES the file into `legislation_docs/` under
    `document_original_name` (see `publish_legislation_draft`), so nothing in
    `legislation_drafts/` is ever chapter-visible and the opaque name never
    reaches a member's download.
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ('.pdf', '.docx'):
        # Belt and braces: the form and `validate_legislation_file` both reject
        # anything else, so reaching here means a programmatic save. Refuse to
        # invent an extension rather than writing an unlabelled blob.
        ext = ''
    return f'legislation_drafts/{uuid.uuid4().hex}{ext}'


def delete_draft_document_file(name):
    """
    Remove a draft attachment from disk. Returns True if a file was deleted.

    ⚠️ v3.19.4 — THE ONE PLACE A DRAFT FILE IS DELETED, and the guard is the
    point of it.

    `DualLocationStorage.path()` falls back to `BASE_DIR/exportable_media/` when
    a name is absent from `MEDIA_ROOT` — and `exportable_media/` is **committed
    to a public repo by design** (CLAUDE.md's standing disposition). An unguarded
    `document.delete()` on a row whose file had already gone missing would
    therefore resolve to the public directory and delete a governing document
    from the working tree. Nothing in the current code can reach that state, and
    that is exactly the kind of reasoning that stops being true later; the guard
    costs two lines.

    So: resolve, confirm the result is under `MEDIA_ROOT`, then unlink. Anything
    else is left alone and reported as "nothing deleted", never as an error — a
    file that is already gone is the desired end state.
    """
    if not name:
        return False

    from django.conf import settings

    media_root = os.path.realpath(settings.MEDIA_ROOT)
    candidate = os.path.realpath(os.path.join(media_root, name))

    if not candidate.startswith(media_root + os.sep):
        return False
    if not os.path.isfile(candidate):
        return False

    try:
        os.remove(candidate)
        return True
    except OSError:
        # Disk-level failure. The row is already gone or already re-pointed, so
        # there is nothing to roll back and nothing a caller could usefully do.
        # Losing the file is the cleanup; losing the request is not.
        logger.warning('Could not delete draft attachment %s', name, exc_info=True)
        return False


class LegislationQuerySet(models.QuerySet):
    """v3.14.1 — the open-for-voting invariant lives HERE, nowhere else.

    Before this, the same Q-logic was hand-copied in home.py and
    api/views.py (and had already rotted once: the pre-v3.13.3
    `status='active'` filters that never matched anything). New list views
    should call these methods instead of re-deriving the predicates.

    The `now` parameter exists for deterministic tests and matches the
    server-resolved-time pattern from v3.14.0; production callers can omit it.
    """

    def visible(self, now=None):
        """Legislation whose availability date has passed."""
        now = now or timezone.now()
        return self.filter(available_at__lte=now)

    def open_for_voting(self, now=None):
        """Legislation a member could cast a ballot on right now.

        Mirrors Legislation.voting_has_started() at queryset level:
        available, not closed, not in a terminal/parked status, and either
        the scheduled start has passed or voting auto-opened with
        availability (blank voting_starts_at + voting_manual_open=False —
        manual-open bills stay closed until the author opens them, which
        sets voting_starts_at).

        Note: this is "open to the chapter" — it does NOT include the
        author's own not-yet-open bills; the vote page composes that
        visibility separately (see vote_view.py / passed_legislation.py).
        """
        now = now or timezone.now()
        return self.filter(
            voting_closed=False,
            available_at__lte=now,
        ).filter(
            models.Q(voting_starts_at__lte=now) |
            models.Q(voting_starts_at__isnull=True, voting_manual_open=False)
        ).exclude(status__in=Legislation.CLOSED_STATUSES)


class Legislation(models.Model):
    VOTE_THRESHOLDS = [
        ('51', '51%'),
        ('60', '60%'),
        ('67', '67%'),
        ('75', '75%'),
        ('100', 'Unanimous'),
    ]

    required_percentage = models.CharField(max_length=10, choices=[
        ('51', '51%'),
        ('60', '60%'),
        ('67', '67%'),
        ('75', '75%'),
        ('100', 'Unanimous')
    ], default='51')

    STATUS_CHOICES = [
        ('draft', 'Draft (Open)'),
        ('pending', 'Pending'),
        ('active', 'Active Voting'),
        ('passed', 'Passed'),
        ('failed', 'Failed'),
        ('tabled', 'Tabled'),
        ('removed', 'Removed'),
    ]

    # v3.13.3: this field was accidentally defined TWICE in this class (the
    # second definition, default='draft', silently shadowed this one). Every
    # legislation has therefore always been created with status='draft' —
    # which wasn't even in STATUS_CHOICES. 'draft' is now an official choice
    # (it's the de facto "open" status; admin-v2 and the committee model
    # already treat it that way) and the duplicate definition is removed.
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    # Statuses that mean "not on the ballot": terminal (passed/failed/removed)
    # or parked (pending/tabled). Used by LegislationQuerySet.open_for_voting()
    # and anywhere else that lists actionable legislation.
    CLOSED_STATUSES = ['pending', 'tabled', 'passed', 'failed', 'removed']

    objects = LegislationQuerySet.as_manager()

    title = models.CharField(max_length=200)
    description = models.TextField()
    document = models.FileField(upload_to='legislation_docs/', validators=[validate_legislation_file], storage=DualLocationStorage(), blank=True, null=True)
    posted_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    co_authors = models.ManyToManyField('ParliamentUser', blank=True, related_name='co_authored_legislation')
    available_at = models.DateTimeField(help_text="When the document becomes visible for review")
    voting_starts_at = models.DateTimeField(null=True, blank=True, help_text="When voting opens (defaults to available_at if not set)")
    # v3.13.3: when True and voting_starts_at is empty, voting does NOT open
    # with availability — it waits for the author to hit "Open Voting Now"
    # (which sets voting_starts_at). False keeps the historical unified
    # behavior: blank voting_starts_at = voting opens at available_at.
    voting_manual_open = models.BooleanField(
        default=False,
        help_text="Voting stays closed until the author opens it manually "
                  "(only meaningful while voting_starts_at is empty)")
    created_at = models.DateTimeField(auto_now_add=True)

    #: v3.19.0 — when the chapter was told this bill exists.
    #:
    #: Before this, `upload_legislation._notify()` fired the moment the row was
    #: saved, even for a bill dated three weeks out: everyone got "New
    #: Legislation" for something they could not open. The notification now
    #: fires when `available_at` actually arrives (see
    #: `tasks.notify_available_legislation`), and this column is what makes
    #: that idempotent — the task claims a row by stamping it, so a beat that
    #: runs twice, or a worker that dies mid-loop, cannot double-notify.
    #:
    #: ⚠️ NULL MEANS "NOT YET ANNOUNCED", so every row that existed before this
    #: field did is backfilled in migration 0014. Without that backfill the
    #: first beat tick after deploy would treat the entire historical table as
    #: unannounced and push one notification per bill to every active member.
    availability_notified_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the chapter was notified this became available. '
                  'NULL means not yet announced.',
    )

    voting_ends_at = models.DateTimeField(null=True, blank=True, help_text="Optional: When voting should automatically close")
    voting_ended_at = models.DateTimeField(null=True, blank=True)
    passed = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    anonymous_vote = models.BooleanField(default=False)
    allow_abstain = models.BooleanField(default=True)
    voting_closed = models.BooleanField(default=False)
    vote_mode = models.CharField(
        max_length=20,
        choices=[('percentage', 'Percentage'), ('piecewise', 'Piecewise'), ('plurality', 'Plurality')],
        default='percentage',
    )

    required_number = models.PositiveIntegerField(null=True, blank=True)
    # List of option strings. JSONField (not postgres ArrayField) so the schema
    # is backend-agnostic — sqlite dev/test runs work (swapped v3.13.2).
    plurality_options = models.JSONField(blank=True, null=True)

    # Plurality voting enhancements
    plurality_votes_allowed = models.PositiveIntegerField(
        default=1,
        help_text="Number of options each voter can select (1-10)"
    )
    plurality_runoff_enabled = models.BooleanField(
        default=False,
        help_text="Allow creating a runoff vote with top options"
    )
    plurality_runoff_count = models.PositiveIntegerField(
        default=2,
        help_text="Number of top options to include in runoff"
    )
    plurality_is_runoff = models.BooleanField(
        default=False,
        help_text="Whether this legislation is a runoff from another vote"
    )
    plurality_parent = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='runoff_votes',
        help_text="Original legislation if this is a runoff vote"
    )

    # Admin note — optional note added after voting closes (does not affect vote result)
    admin_note = models.TextField(blank=True, default='', help_text="Optional admin/officer note shown on the legislation record after voting closes")

    # Historical vote counts (for manually entered legislation)
    historical_yes_votes = models.PositiveIntegerField(null=True, blank=True, help_text="Historical yes vote count")
    historical_no_votes = models.PositiveIntegerField(null=True, blank=True, help_text="Historical no vote count")
    historical_abstain_votes = models.PositiveIntegerField(null=True, blank=True, help_text="Historical abstain vote count")

    # (duplicate `status` field removed here in v3.13.3 — see the field near
    # the top of the class)

    # Chair appointment fields — only populated when legislation_type == 'appointment'
    LEGISLATION_TYPES = [
        ('general', 'General'),
        ('appointment', 'Chair Appointment'),
    ]
    legislation_type = models.CharField(max_length=20, choices=LEGISLATION_TYPES, default='general')
    appointment_role = models.ForeignKey(
        'Role', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='appointment_legislation',
        help_text="Role being filled (appointment votes only)"
    )
    appointment_member = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='appointment_nominations',
        help_text="Nominated member for single-nominee votes; null for plurality"
    )
    appointment_assigned = models.BooleanField(
        default=False,
        help_text="Set to True once the role has been formally assigned after the vote passed"
    )

    @property
    def required_yes_votes(self):
        if self.vote_mode == 'piecewise':
            return self.required_number or 0
        return None

    def is_available(self):
        from django.utils import timezone
        return timezone.now() >= self.available_at

    def voting_has_started(self):
        """Check if voting period has begun."""
        from django.utils import timezone
        # v3.13.3: manual-open mode — the uploader chose to separate voting
        # from availability without scheduling a start; voting stays closed
        # until they hit "Open Voting Now" (which sets voting_starts_at).
        if self.voting_manual_open and not self.voting_starts_at:
            return False
        # If voting_starts_at is set, use it; otherwise voting starts when available
        start_time = self.voting_starts_at or self.available_at
        return timezone.now() >= start_time

    def get_voting_start_time(self):
        """Get the effective voting start time."""
        return self.voting_starts_at or self.available_at

    def __str__(self):
        return self.title

    def set_passed(self, commit=True, counts=None):
        """
        Recompute `passed` from the votes cast.

        v3.17.1 — two changes, both because this was being called for every row
        on every GET of the legislation history page:

        `counts`: an optional {vote_choice: n} mapping. Supply it and this method
        issues **no queries at all**; the caller has usually just aggregated the
        same numbers and there is no reason to fetch them again per row.

        `commit`: the save is now conditional and narrow. Previously every call
        did a full-row `self.save()`, so simply *viewing* the page rewrote every
        closed piece of legislation — a write on a GET request, bumping any
        auto_now field and generating write load proportional to page views. Now
        it writes only when the value actually changed, and only that column.

        Returns the computed value.
        """
        from collections import Counter

        previous = self.passed

        if counts is not None:
            vote_counts = Counter({k: v for k, v in counts.items() if v})
            total_votes = None
        else:
            total_votes = Vote.objects.filter(legislation=self)
            vote_counts = None

        if self.vote_mode == 'plurality':
            # Count votes for each option (each vote counts as 1, even with multi-select)
            if vote_counts is None:
                vote_choices = [v.vote_choice for v in total_votes]
                vote_counts = Counter(vote_choices)
            if vote_counts:
                max_votes = max(vote_counts.values())
                winners = [option for option, count in vote_counts.items() if count == max_votes]
                # Only passes if there is a single clear winner
                # If runoff is enabled and there's a tie, it can still "pass" to trigger runoff
                self.passed = len(winners) == 1
            else:
                self.passed = False
        elif self.vote_mode == 'piecewise':
            if counts is not None:
                yes_votes = counts.get('yes', 0)
            else:
                yes_votes = total_votes.filter(vote_choice='yes').count()
            self.passed = yes_votes >= self.required_yes_votes
        else:  # percentage
            if counts is not None:
                yes = counts.get('yes', 0)
                total = sum(n for choice, n in counts.items() if choice != 'abstain')
            else:
                total_votes = total_votes.exclude(vote_choice='abstain')
                total = total_votes.count()
                yes = total_votes.filter(vote_choice='yes').count()
            if total > 0:
                yes_pct = (yes / total) * 100
                self.passed = yes_pct >= float(self.required_percentage)
            else:
                self.passed = False

        if commit and self.passed != previous:
            self.save(update_fields=['passed'])
        return self.passed

    def get_plurality_results(self):
        """Get vote counts for each plurality option, sorted by count descending."""
        from collections import Counter
        if self.vote_mode != 'plurality':
            return []

        votes = Vote.objects.filter(legislation=self)
        vote_counts = Counter(v.vote_choice for v in votes)

        # Include all options, even those with 0 votes
        results = []
        for option in (self.plurality_options or []):
            results.append({
                'option': option,
                'count': vote_counts.get(option, 0)
            })

        # Sort by count descending
        results.sort(key=lambda x: x['count'], reverse=True)
        return results

    def get_top_options_for_runoff(self):
        """Get the top N options for a runoff vote."""
        if not self.plurality_runoff_enabled:
            return []

        results = self.get_plurality_results()
        return [r['option'] for r in results[:self.plurality_runoff_count]]

    def has_plurality_tie(self):
        """Check if there's a tie for first place in plurality voting."""
        results = self.get_plurality_results()
        if len(results) < 2:
            return False
        return results[0]['count'] == results[1]['count'] and results[0]['count'] > 0

    def get_unique_voter_count(self):
        """Get the number of unique voters (for multi-select plurality)."""
        return Vote.objects.filter(legislation=self).values('user').distinct().count()


class LegislationDraft(models.Model):
    """
    A bill an author is still writing — private to them until they publish it.

    ⚠️ WHY THIS IS A SEPARATE MODEL AND NOT `Legislation.is_draft` (v3.19.0).
    ============================================================================
    The obvious implementation is a boolean on `Legislation`. It was rejected
    deliberately, and the reason is worth keeping because it will look like
    over-engineering to the next reader.

    `Legislation` is queried from **35+ places**: every view in `src/view/`,
    `src/api/views.py`, `global_search.py`, four Celery tasks, `admin.py`,
    `chapter_stats.py` and five management commands. A boolean means every one
    of those has to remember to exclude drafts. That is the exact failure shape
    this codebase has paid for in five consecutive releases (v3.16.3, v3.18.1,
    v3.18.3, v3.18.4, v3.18.5) — *a rule stated correctly, a helper written to
    enforce it, then one call site left outside the helper* — except that here
    the consequence is an unfinished bill appearing on the chapter ballot.

    Filtering in the default manager instead fails closed, but breaks the
    reverse related managers: `user.co_authored_legislation.all()` is used by
    the user-merge code in `admin.py:440` and `manage_members.py:482`, and a
    filtered default manager would silently drop a draft's co-authorship during
    a merge. `_base_manager` stays unfiltered, so FK traversal would disagree
    with `_default_manager` about whether a row exists — two answers to one
    question, which is the "second copy" pattern again.

    **A separate table has neither problem by construction.** A draft cannot
    leak into a `Legislation` queryset because it is not in that table. The
    migration is purely additive: nothing that works today changes.

    The cost is that the publish step copies fields across. That is a real cost
    and it is the right one to pay — a copy that is visibly wrong beats a
    filter that is invisibly missing.

    NOT stored here, deliberately: vote counts, `passed`, `voting_closed`,
    `status`. A draft has never been voted on, so those fields would be
    meaningless and would invite someone to render a draft through a template
    built for a real bill.
    """

    author = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.CASCADE,
        related_name='legislation_drafts',
        help_text='Only this member can see, edit or publish the draft.',
    )

    title = models.CharField(max_length=200)
    description = models.TextField(
        blank=True,
        help_text='Working text. Unlike Legislation, a draft may be empty — '
                  'the 20-character floor is enforced at publish, not while writing.',
    )
    document = models.FileField(
        # v3.19.3: a callable returning `legislation_drafts/<uuid>.<ext>`. See
        # that function — the random name is defence in depth underneath
        # `serve_legislation_draft_document`, not a substitute for it.
        upload_to=legislation_draft_upload_path,
        validators=[validate_legislation_file],
        storage=DualLocationStorage(),
        blank=True, null=True,
    )

    #: The name the author uploaded, kept because the stored name is now a
    #: uuid. Publish restores it on the copy it makes, so a member downloading
    #: a bill gets `dues-restructuring-amendment.docx` and never sees the
    #: opaque draft name. Blank on rows created before v3.19.3, and on those
    #: the stored basename is used instead.
    document_original_name = models.CharField(max_length=255, blank=True)

    #: What the author intends `Legislation.available_at` to be. Nullable
    #: because a draft is allowed to be undated; publish requires a value.
    planned_available_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When you intend to present this. Becomes available_at at publish.',
    )
    planned_voting_ends_at = models.DateTimeField(null=True, blank=True)

    #: Author-only scratch space. Never copied to the published bill — this is
    #: the field that makes a draft worth having rather than just an early
    #: upload, and it must not survive publication.
    notes = models.TextField(
        blank=True,
        help_text='Private notes. Never copied to the published bill.',
    )

    vote_mode = models.CharField(
        max_length=20,
        choices=[('percentage', 'Percentage'), ('piecewise', 'Piecewise'), ('plurality', 'Plurality')],
        default='percentage',
    )
    required_percentage = models.CharField(
        max_length=10, choices=Legislation.VOTE_THRESHOLDS, default='51',
    )
    anonymous_vote = models.BooleanField(default=False)
    allow_abstain = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    #: Set once the draft has been turned into a real bill. The draft row is
    #: KEPT rather than deleted so the author's My Work page can show the link,
    #: and so `notes` remain available to them afterwards.
    published_legislation = models.ForeignKey(
        Legislation,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='source_draft',
    )
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-updated_at']
        verbose_name = 'Legislation Draft'
        verbose_name_plural = 'Legislation Drafts'
        indexes = [
            # ⚠️ The name is explicit, not auto-generated. Django derives an
            # index name from a hash of the table and columns when you omit it,
            # and migration 0014 is hand-written — an auto name in the model and
            # a chosen one in the migration disagree, and `makemigrations
            # --check` (the CI gate from v3.18.1) fails on the difference. Naming
            # it in both places is what makes the two agree.
            models.Index(fields=['author', '-updated_at'], name='src_legdraft_author_upd_idx'),
        ]

    def __str__(self):
        return f'Draft: {self.title}'

    @property
    def is_published(self):
        return self.published_legislation_id is not None

    @property
    def document_display_name(self):
        """
        What to show the author for their attachment.

        v3.19.3: the stored name is a uuid, so templates must not render
        `document.name` any more — it would show the author a filename they did
        not choose and cannot recognise. Falls back to the stored basename for
        rows written before this release, whose name IS the original.
        """
        if self.document_original_name:
            return self.document_original_name
        if self.document:
            return os.path.basename(self.document.name)
        return ''

    def ready_to_publish(self):
        """
        `(ok: bool, reason: str)` — the same floor `LegislationForm.clean`
        applies, checked here so the My Work page can grey out the button and
        say why instead of failing on submit.
        """
        if self.is_published:
            return False, 'This draft has already been published.'
        if not self.planned_available_at:
            return False, 'Set a date for when this becomes available.'
        if not self.document and len((self.description or '').strip()) < 20:
            return False, 'Attach a document or write at least 20 characters of description.'
        return True, ''


class Vote(models.Model):
    user = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, limit_choices_to={'member_status': 'Active'})
    legislation = models.ForeignKey(Legislation, on_delete=models.CASCADE)
    vote_choice = models.CharField(max_length=100)
    # v3.14.0: when the ballot was cast (null for rows predating this field).
    # Used by My Ballots and to keep regenerated receipts anchored to the
    # original cast time.
    cast_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)


# ─────────────────────────────────────────────────────── draft file lifecycle
#
# ⚠️ v3.19.4 — WHO OWNS A DRAFT'S FILE. Registered at module scope, in the model
# module, matching `models_feature_flags.py`. This module is imported by
# `src/models/__init__.py`, so the receiver is connected whenever models are.
#
# WHY THIS EXISTS. Django has not deleted files on model delete since 1.3 — a
# deliberate change, because a file can be shared between rows and an ORM delete
# is not a good place to find out. For `LegislationDraft` that reasoning does not
# apply and has not since v3.19.3: the attachment lives at a uuid under
# `legislation_drafts/`, it is referenced by exactly one row, and publish now
# COPIES it into `legislation_docs/` rather than re-pointing at it. So the file
# has precisely one owner, and without this receiver every deleted draft left an
# unreferenced blob that nothing in the codebase could ever identify again.
#
# ⚠️ DO NOT GENERALISE THIS TO `Legislation.document`. That field's file IS
# shared in the way Django's default protects against: publish copies a draft's
# bytes into it, and older bills may point at paths under `exportable_media/`,
# which is public-by-design and hand-curated. A published bill's document is
# chapter history and must outlive the row.
from django.db.models.signals import post_delete       # noqa: E402
from django.dispatch import receiver                    # noqa: E402


@receiver(post_delete, sender=LegislationDraft, dispatch_uid='legislation_draft_document_cleanup')
def _delete_draft_document_on_delete(sender, instance, **kwargs):
    """
    Remove the attachment when its draft row goes away.

    `post_delete`, not `pre_delete`: if the delete is rolled back the row comes
    back, and a `pre_delete` unlink would have already destroyed a file the
    restored row still points at. `post_delete` still fires inside the
    transaction, so a rollback after this point can strand a row without its
    file — that is the lesser failure, and it is the same trade
    `publish_legislation_draft` makes for the same reason.

    Deliberately silent. A member clicking Delete has been told the draft is
    gone; whether a blob was reclaimed is not their problem, and raising here
    would fail a delete that already succeeded.
    """
    if instance.document:
        delete_draft_document_file(instance.document.name)
