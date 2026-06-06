from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.conf import settings
from src.storage import DualLocationStorage
from src.models.legislation import validate_legislation_file, Legislation


class Committee(models.Model):
    # Hard-coded committees (ID, Code, Name)
    # These are the canonical source of truth for committees in the system
    DEFAULT_COMMITTEES = [
        (1, 'BYLAWS', 'Constitution and Bylaws Committee'),
        (2, 'RITUAL', 'Ritual Committee'),
        (3, 'EXEC', 'Executive Board'),
        (4, 'KAI', 'Kai Committee'),
        (5, 'BROTHER', 'Brotherhood Committee'),
        (6, 'RECRUIT', 'Recruitment Committee'),
        (7, 'EDUCATION', 'Education Committee'),
        (8, 'SOCIAL', ' Social Committee'),
        (9, 'FINANCE', 'Finance Committee'),
        (10, 'ADMIN', 'Administration Committee'),
        (11, 'PROGRAM', 'Programming Committee'),
    ]

    name = models.CharField(max_length=225, unique=True)
    chairs = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='chair_roles'
    )
    members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='committees',
    )
    advisors = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='advisor_roles'
    )
    voting_members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name='committee_voters'
    )
    code = models.CharField(
        max_length=255,
        help_text='Code used to identify committee (ex. RISK, FINANCE)',
        blank=True,
        null=True,
        unique=True,
    )

    allow_multiple_chairs = models.BooleanField(default=False)
    role = models.ForeignKey('Role', on_delete=models.SET_NULL, null=True, blank=True, related_name="committees")
    created_at = models.DateTimeField(auto_now_add=True)
    committee_id = models.IntegerField(unique=True, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    # Special committee flags
    is_exec_board = models.BooleanField(default=False, help_text='If True, membership auto-syncs with exec role holders and all members have chair-level permissions')
    is_slating_committee = models.BooleanField(default=False, help_text='If True, has special visibility rules and President is auto-assigned as admin')
    is_kai_committee = models.BooleanField(default=False, help_text='If True, this is the conduct committee — enables Kai report management, form builder, and chair notifications')
    is_chapter_committee = models.BooleanField(default=False, help_text='If True, this committee owns chapter-level documents (the chapter documents page)')
    is_ad_hoc = models.BooleanField(default=False, help_text='If True, this is a temporary ad-hoc committee')
    ad_hoc_expiration = models.DateField(
        null=True,
        blank=True,
        help_text='Optional expiration date for ad-hoc committees'
    )
    is_archived = models.BooleanField(
        default=False,
        help_text='If True, committee is archived (read-only). Used for expired ad-hoc committees.'
    )

    # Explicit admin for committees with restricted visibility (is_slating_committee=True)
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='admin_of_committees',
        help_text='Explicit admin for committees with restricted visibility'
    )

    def __str__(self):
        return f"{self.code} - {self.name}"

    def chair_list(self):
        return ", ".join([c.name for c in self.chairs.all()])
    chair_list.short_description = "Chairs"

    def is_chair(self, user):
        # Exec board members all carry chair-level permissions
        if self.is_exec_board and self.members.filter(pk=user.pk).exists():
            return True
        return self.chairs.filter(pk=user.pk).exists()

    def is_member(self, user):
        return self.members.filter(pk=user.pk).exists()

    def is_voter(self, user):
        return self.voting_members.filter(pk=user.pk).exists()

    def is_vp(self, user):
        """Check to see if the member is the Admin/VP of the committee"""
        if not self.role:
            return False
        return user.roles.filter(pk=self.role.id).exists()

    def get_vp(self):
        """Get the VP of the committee"""
        if not self.role:
            return None
        from src.models.users import ParliamentUser
        vps = ParliamentUser.objects.filter(roles=self.role)
        return vps.first() if vps.exists() else None

    def is_visible_to(self, user):
        """Check if this committee should be visible to the user."""
        if not self.is_slating_committee:
            return True  # Normal committees visible to all

        # Slating committee special rules:
        # - Admin always sees it
        # - Site admins always see it
        # - If has members/chairs, those people see it
        # - Otherwise, only admin sees it
        if self.admin == user:
            return True
        if user.is_admin:
            return True
        if self.members.exists() or self.chairs.exists():
            return self.is_member(user) or self.is_chair(user)
        return False


class CommitteePermissions(models.Model):
    committee = models.ForeignKey(Committee, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    can_view_docs = models.BooleanField(default=False)
    can_upload_docs = models.BooleanField(default=False)
    can_vote = models.BooleanField(default=False)
    can_manage_members = models.BooleanField(default=False)
    can_view_results = models.BooleanField(default=True)
    can_take_minutes = models.BooleanField(default=False, help_text='Designated secretary: can create/edit committee minutes')


class CommitteeLegislation(models.Model):
    VOTE_THRESHOLDS = [
        ('51', '51%'),
        ('60', '60%'),
        ('67', '67%'),
        ('75', '75%'),
        ('100', 'Unanimous'),
    ]

    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('passed', 'Passed'),
        ('removed', 'Removed'),
    ]

    committee = models.ForeignKey(Committee, on_delete=models.CASCADE, related_name='legislation')
    title = models.CharField(max_length=200)
    description = models.TextField()
    document = models.FileField(upload_to='committee_legislation/', validators=[validate_legislation_file], null=True,
                                blank=True, storage=DualLocationStorage())
    posted_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    available_at = models.DateTimeField(help_text="When the document becomes visible for review")
    voting_starts_at = models.DateTimeField(null=True, blank=True, help_text="When voting opens (defaults to available_at if not set)")
    voting_ends_at = models.DateTimeField(null=True, blank=True, help_text="Optional: When voting should automatically close")
    created_at = models.DateTimeField(auto_now_add=True)
    voting_ended_at = models.DateTimeField(null=True, blank=True)

    anonymous_vote = models.BooleanField(default=False)
    allow_abstain = models.BooleanField(default=True)
    voting_closed = models.BooleanField(default=False)

    vote_mode = models.CharField(
        max_length=20,
        choices=[('percentage', 'Percentage'), ('piecewise', 'Piecewise'), ('plurality', 'Plurality')],
        default='percentage',
    )

    required_percentage = models.CharField(max_length=10, choices=VOTE_THRESHOLDS, default='51')
    required_number = models.PositiveIntegerField(null=True, blank=True)
    plurality_options = ArrayField(models.CharField(max_length=100), blank=True, null=True)

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

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    passed = models.BooleanField(default=False)

    # Track if this was pushed to chapter
    pushed_to_chapter = models.BooleanField(default=False)
    chapter_legislation = models.ForeignKey(Legislation, on_delete=models.SET_NULL, null=True, blank=True,
                                            related_name='committee_source')

    def is_available(self):
        from django.utils import timezone
        return timezone.now() >= self.available_at

    def voting_has_started(self):
        """Check if voting period has begun."""
        from django.utils import timezone
        start_time = self.voting_starts_at or self.available_at
        return timezone.now() >= start_time

    def get_voting_start_time(self):
        """Get the effective voting start time."""
        return self.voting_starts_at or self.available_at

    def __str__(self):
        return f"{self.committee.code} - {self.title}"

    def set_passed(self):
        from collections import Counter

        total_votes = CommitteeVote.objects.filter(legislation=self)

        if self.vote_mode == 'plurality':
            vote_choices = [v.vote_choice for v in total_votes]
            vote_counts = Counter(vote_choices)
            if vote_counts:
                max_votes = max(vote_counts.values())
                winners = [option for option, count in vote_counts.items() if count == max_votes]
                self.passed = len(winners) == 1
            else:
                self.passed = False
        elif self.vote_mode == 'piecewise':
            yes_votes = total_votes.filter(vote_choice='yes').count()
            self.passed = yes_votes >= self.required_number
        else:  # percentage
            total_votes = total_votes.exclude(vote_choice='abstain')
            total = total_votes.count()
            yes = total_votes.filter(vote_choice='yes').count()
            if total > 0:
                yes_pct = (yes / total) * 100
                self.passed = yes_pct >= float(self.required_percentage)
            else:
                self.passed = False

        if self.passed:
            self.status = 'passed'
        else:
            self.status = 'removed'
        self.save()

    def get_plurality_results(self):
        """Get vote counts for each plurality option, sorted by count descending."""
        from collections import Counter
        if self.vote_mode != 'plurality':
            return []

        votes = CommitteeVote.objects.filter(legislation=self)
        vote_counts = Counter(v.vote_choice for v in votes)

        results = []
        for option in (self.plurality_options or []):
            results.append({
                'option': option,
                'count': vote_counts.get(option, 0)
            })

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
        return CommitteeVote.objects.filter(legislation=self).values('user').distinct().count()


class CommitteeVote(models.Model):
    user = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, limit_choices_to={'member_status': 'Active'})
    legislation = models.ForeignKey(CommitteeLegislation, on_delete=models.CASCADE)
    vote_choice = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=False)

    class Meta:
        # Allow multiple votes per user for multi-select plurality voting
        # Uniqueness is enforced per user+legislation+choice to prevent duplicate selections
        unique_together = ('user', 'legislation', 'vote_choice')
