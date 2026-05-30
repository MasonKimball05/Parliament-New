from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.core.exceptions import ValidationError
from src.storage import DualLocationStorage


def validate_legislation_file(value):
    """Validates the file extension."""
    if not value.name.endswith('.pdf') and not value.name.endswith('.docx'):
        raise ValidationError('Only PDF and DOCX files are allowed.')


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
        ('pending', 'Pending'),
        ('active', 'Active Voting'),
        ('passed', 'Passed'),
        ('failed', 'Failed'),
        ('tabled', 'Tabled'),
        ('removed', 'Removed'),
    ]

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    title = models.CharField(max_length=200)
    description = models.TextField()
    document = models.FileField(upload_to='legislation_docs/', validators=[validate_legislation_file], storage=DualLocationStorage(), blank=True, null=True)
    posted_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    co_authors = models.ManyToManyField('ParliamentUser', blank=True, related_name='co_authored_legislation')
    available_at = models.DateTimeField(help_text="When the document becomes visible for review")
    voting_starts_at = models.DateTimeField(null=True, blank=True, help_text="When voting opens (defaults to available_at if not set)")
    created_at = models.DateTimeField(auto_now_add=True)
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
    plurality_options = ArrayField(models.CharField(max_length=100), blank=True, null=True)  # Only for PostgreSQL

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

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

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
        # If voting_starts_at is set, use it; otherwise voting starts when available
        start_time = self.voting_starts_at or self.available_at
        return timezone.now() >= start_time

    def get_voting_start_time(self):
        """Get the effective voting start time."""
        return self.voting_starts_at or self.available_at

    def __str__(self):
        return self.title

    def set_passed(self):
        from collections import Counter

        total_votes = Vote.objects.filter(legislation=self)

        if self.vote_mode == 'plurality':
            # Count votes for each option (each vote counts as 1, even with multi-select)
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
            yes_votes = total_votes.filter(vote_choice='yes').count()
            self.passed = yes_votes >= self.required_yes_votes
        else:  # percentage
            total_votes = total_votes.exclude(vote_choice='abstain')
            total = total_votes.count()
            yes = total_votes.filter(vote_choice='yes').count()
            if total > 0:
                yes_pct = (yes / total) * 100
                self.passed = yes_pct >= float(self.required_percentage)
            else:
                self.passed = False

        self.save()

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


class Vote(models.Model):
    user = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, limit_choices_to={'member_status': 'Active'})
    legislation = models.ForeignKey(Legislation, on_delete=models.CASCADE)
    vote_choice = models.CharField(max_length=100)
