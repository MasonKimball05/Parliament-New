from django.db import models
from django.conf import settings
from src.storage import DualLocationStorage


class SlatingPeriod(models.Model):
    """
    Represents an election cycle with configurable dates, status, and settings.
    This is the main container for all slating-related data for a given election.
    """
    STATUS_CHOICES = [
        ('setup', 'Setup'),
        ('nominations_open', 'Nominations Open'),
        ('nominations_closed', 'Nominations Closed'),
        ('deliberation', 'Deliberation'),
        ('voting_open', 'Voting Open'),
        ('voting_closed', 'Voting Closed'),
        ('results_published', 'Results Published'),
        ('archived', 'Archived'),
    ]

    VOTE_TYPE_CHOICES = [
        ('slate', 'Full Slate Vote'),
        ('individual', 'Individual Position Votes'),
    ]

    name = models.CharField(max_length=200, help_text='e.g., "Fall 2022 Officer Elections"')
    description = models.TextField(blank=True)
    academic_term = models.CharField(max_length=50, help_text='e.g., "Fall 2022"')
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='setup')

    # Configurable date windows
    nominations_open_at = models.DateTimeField(null=True, blank=True)
    nominations_close_at = models.DateTimeField(null=True, blank=True)
    deliberation_start_at = models.DateTimeField(null=True, blank=True)
    voting_open_at = models.DateTimeField(null=True, blank=True)
    voting_close_at = models.DateTimeField(null=True, blank=True)
    results_publish_at = models.DateTimeField(null=True, blank=True)

    # Voting configuration (CONFIGURABLE - not hardcoded 60%)
    required_approval_percentage = models.IntegerField(
        default=60,
        help_text='Percentage needed to approve slate (bylaws: 60%)'
    )
    current_voting_attempt = models.IntegerField(default=0)
    allow_abstain = models.BooleanField(
        default=True,
        help_text='Allow members to abstain from voting'
    )
    vote_type = models.CharField(
        max_length=20,
        choices=VOTE_TYPE_CHOICES,
        default='slate',
        help_text='Whether to vote on the full slate or individual positions'
    )
    quorum = models.IntegerField(
        null=True, blank=True,
        help_text='Minimum number of members required present to hold a valid vote (optional)'
    )

    # GPA configuration
    min_gpa_requirement = models.DecimalField(
        max_digits=3, decimal_places=2, default=2.50,
        help_text='Minimum GPA for Level 1 eligibility'
    )
    gpa_level_2_threshold = models.DecimalField(
        max_digits=3, decimal_places=2, default=0.20,
        help_text='How far below minimum for Level 2 (bylaws: 0.20)'
    )

    # Committee assignment
    slating_committee = models.ForeignKey(
        'Committee',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='slating_periods',
        help_text='Committee managing this election'
    )

    # Designated manager for this specific slating period (can be different from committee chair)
    slating_manager = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='managed_slating_periods',
        help_text='Member responsible for running this slating period (has full setup access)'
    )

    # Flexible settings as JSON
    extra_settings = models.JSONField(
        default=dict, blank=True,
        help_text='Additional configurable settings (JSON)'
    )

    # Metadata
    created_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True, related_name='created_slating_periods'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Admin transfer tracking (if President transfers admin to someone else)
    admin_transferred = models.BooleanField(default=False)
    admin_transfer_reason = models.TextField(blank=True, help_text='Reason for admin transfer (e.g., President is running)')
    admin_transferred_at = models.DateTimeField(null=True, blank=True)
    admin_transferred_from = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='transferred_slating_admin_from',
        help_text='Previous admin before transfer'
    )

    # Officer transition scheduling
    officer_transition_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When the officer transition should take effect'
    )
    officer_transition_data = models.JSONField(
        default=dict, blank=True,
        help_text='Pending transition assignments (position_id -> member_id)'
    )
    officer_transition_completed = models.BooleanField(
        default=False,
        help_text='Whether the officer transition has been executed'
    )
    officer_transition_completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} ({self.get_status_display()})"

    def can_apply(self):
        """Check if nominations are currently open"""
        from django.utils import timezone
        now = timezone.now()
        return (
            self.status == 'nominations_open' and
            (not self.nominations_open_at or now >= self.nominations_open_at) and
            (not self.nominations_close_at or now < self.nominations_close_at)
        )

    def can_vote(self):
        """Check if voting is currently open"""
        from django.utils import timezone
        now = timezone.now()
        return (
            self.status == 'voting_open' and
            (not self.voting_open_at or now >= self.voting_open_at) and
            (not self.voting_close_at or now < self.voting_close_at)
        )

    def transition_officers(self, performed_by=None):
        """
        Transition officer roles based on election results.
        - Adds roles to newly elected officers
        - Removes roles from outgoing officers
        - Logs all changes

        Returns dict with 'added', 'removed', 'errors' lists.
        """
        if self.status != 'results_published':
            raise ValueError('Cannot transition officers until results are published')

        results = {'added': [], 'removed': [], 'errors': []}

        # Get winning candidates (from passed slate or individual votes)
        passed_slate = self.slates.filter(passed=True).first()
        if passed_slate:
            winners = passed_slate.candidates.all()
        else:
            # Individual voting - get candidates that passed individually
            winners = SlateCandidate.objects.filter(
                slate__period=self,
                individual_passed=True
            )

        for candidate in winners:
            position = candidate.position
            new_officer = candidate.application.applicant
            role = position.role

            if not role:
                results['errors'].append(f'No role linked to position {position.title}')
                continue

            try:
                from src.models.users import ParliamentUser
                # Add role to new officer
                new_officer.roles.add(role)
                results['added'].append({
                    'user': new_officer.name,
                    'role': role.name,
                    'position': position.title
                })

                # Remove role from old officers (exclude the new one)
                old_holders = ParliamentUser.objects.filter(roles=role).exclude(pk=new_officer.pk)
                for old_officer in old_holders:
                    old_officer.roles.remove(role)
                    results['removed'].append({
                        'user': old_officer.name,
                        'role': role.name
                    })

            except Exception as e:
                results['errors'].append(f'Error with {position.title}: {str(e)}')

        # Log the transition
        SlatingActivity.objects.create(
            period=self,
            user=performed_by,
            action='officers_transitioned',
            details=f"Added: {len(results['added'])}, Removed: {len(results['removed'])}, Errors: {len(results['errors'])}",
            metadata=results
        )

        return results


class SlatingPosition(models.Model):
    """
    Configurable position within a slating period.
    Linked to Role model but allows period-specific customization.
    """
    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='positions'
    )
    role = models.ForeignKey(
        'Role',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        help_text='Link to existing Role (optional)'
    )

    title = models.CharField(max_length=200)
    code = models.CharField(max_length=20)
    description = models.TextField(blank=True)

    # Position-specific requirements (override period defaults)
    min_gpa = models.DecimalField(
        max_digits=3, decimal_places=2, null=True, blank=True,
        help_text='Position-specific GPA requirement (overrides period default)'
    )
    requires_prior_experience = models.BooleanField(default=False)
    min_semesters_active = models.IntegerField(default=0)

    # Eligibility restrictions as JSON (flexible)
    eligible_member_types = models.JSONField(
        default=list, blank=True,
        help_text='List of member types eligible (empty = all)'
    )
    eligible_class_years = models.JSONField(
        default=list, blank=True,
        help_text='List of eligible class years (empty = all)'
    )
    custom_requirements = models.JSONField(
        default=dict, blank=True,
        help_text='Position-specific custom requirements'
    )

    display_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    allow_abstain = models.BooleanField(
        default=True,
        help_text='Allow members to abstain when voting on this position individually'
    )

    class Meta:
        ordering = ['display_order', 'title']
        unique_together = ['period', 'code']

    def __str__(self):
        return f"{self.title} ({self.period.name})"


class SlatingFormField(models.Model):
    """
    Dynamic form field definition for application forms.
    Chair/admin can create custom fields without code changes.
    This is the core of the configurable form system.
    """
    FIELD_TYPES = [
        ('text', 'Text (Single Line)'),
        ('textarea', 'Text Area (Multi-line)'),
        ('number', 'Number'),
        ('decimal', 'Decimal Number'),
        ('email', 'Email Address'),
        ('phone', 'Phone Number'),
        ('date', 'Date'),
        ('datetime', 'Date & Time'),
        ('select', 'Dropdown Select'),
        ('multiselect', 'Multi-Select'),
        ('checkbox', 'Checkbox'),
        ('radio', 'Radio Buttons'),
        ('file', 'File Upload'),
        ('image', 'Image Upload'),
        ('gpa', 'GPA Entry (with screenshot)'),
        ('position_preference', 'Position Preference Ranking'),
        ('member_select', 'Member Selection'),
    ]

    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='form_fields'
    )

    # Field Definition
    field_name = models.CharField(max_length=100, help_text='Internal field name (no spaces)')
    label = models.CharField(max_length=200, help_text='Display label')
    field_type = models.CharField(max_length=30, choices=FIELD_TYPES)
    placeholder = models.CharField(max_length=200, blank=True)
    help_text = models.TextField(blank=True)

    # Options (for select, multiselect, radio, checkbox)
    options = models.JSONField(
        default=list, blank=True,
        help_text='List of options for select/radio/checkbox fields'
    )

    # Validation
    is_required = models.BooleanField(default=False)
    validation_rules = models.JSONField(
        default=list, blank=True,
        help_text='List of validation rules with parameters'
    )

    # File Upload Settings
    allowed_file_types = models.JSONField(
        default=list, blank=True,
        help_text='Allowed MIME types for file uploads'
    )
    max_file_size_mb = models.IntegerField(default=10)

    # Display Settings
    display_order = models.IntegerField(default=0)
    section = models.CharField(
        max_length=100, blank=True,
        help_text='Group fields into sections'
    )
    show_in_review = models.BooleanField(
        default=True,
        help_text='Show this field in application review'
    )
    is_confidential = models.BooleanField(
        default=False,
        help_text='Only show to slating committee'
    )

    # Conditional Display
    depends_on_field = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        help_text='Only show if another field has specific value'
    )
    depends_on_value = models.JSONField(
        default=list, blank=True,
        help_text='Values that trigger this field to show'
    )

    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['section', 'display_order']
        unique_together = ['period', 'field_name']

    def __str__(self):
        return f"{self.label} ({self.field_type})"


class SlatingApplication(models.Model):
    """
    A candidate's application for a slating period.
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('submitted', 'Submitted'),
        ('under_review', 'Under Review'),
        ('interview_scheduled', 'Interview Scheduled'),
        ('interviewed', 'Interviewed'),
        ('recommended', 'Recommended'),
        ('not_recommended', 'Not Recommended'),
        ('withdrawn', 'Withdrawn'),
        ('slated', 'Slated'),
    ]

    GPA_LEVEL_CHOICES = [
        (1, 'Level 1 - Meets or exceeds minimum'),
        (2, 'Level 2 - Within 0.20 below minimum'),
        (3, 'Level 3 - Below Level 2'),
    ]

    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='applications'
    )
    applicant = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='slating_applications'
    )

    # Status
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    # Position Preferences (tiered categories)
    position_preferences = models.JSONField(
        default=dict,
        help_text='Tiered position preferences: {first_choice: [], second_choice: [], third_choice: [], do_not_want: []}'
    )

    # GPA Information
    reported_gpa = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    gpa_verified = models.BooleanField(default=False)
    gpa_level = models.IntegerField(choices=GPA_LEVEL_CHOICES, null=True, blank=True)
    gpa_screenshot = models.FileField(
        upload_to='slating/gpa_screenshots/',
        null=True, blank=True,
        storage=DualLocationStorage()
    )

    # Timestamps
    submitted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Committee Review
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviewed_slating_applications'
    )
    review_notes = models.TextField(blank=True, help_text='Confidential review notes')

    class Meta:
        ordering = ['-submitted_at', '-created_at']
        unique_together = ['period', 'applicant']

    def __str__(self):
        return f"{self.applicant.name} - {self.period.name}"

    def calculate_gpa_level(self):
        """Calculate GPA level based on period settings"""
        if not self.reported_gpa:
            return None

        min_gpa = self.period.min_gpa_requirement
        level_2_threshold = self.period.gpa_level_2_threshold

        if self.reported_gpa >= min_gpa:
            return 1
        elif self.reported_gpa >= (min_gpa - level_2_threshold):
            return 2
        else:
            return 3

    def get_preferred_positions(self):
        """
        Get all wanted positions in preference order (first_choice, second_choice, third_choice).
        Returns list of position IDs, excluding do_not_want.
        """
        prefs = self.position_preferences or {}

        # Handle legacy format (simple list)
        if isinstance(prefs, list):
            return prefs

        # New tiered format
        result = []
        for tier in ['first_choice', 'second_choice', 'third_choice']:
            result.extend(prefs.get(tier, []))
        return result

    def get_first_choice_positions(self):
        """Get first choice position IDs."""
        prefs = self.position_preferences or {}
        if isinstance(prefs, list):
            return prefs[:1] if prefs else []
        return prefs.get('first_choice', [])

    def get_position_tier(self, position_id):
        """
        Get the preference tier for a specific position.
        Returns 'first_choice', 'second_choice', 'third_choice', 'do_not_want', or None.
        """
        prefs = self.position_preferences or {}

        # Handle legacy format
        if isinstance(prefs, list):
            if position_id in prefs:
                idx = prefs.index(position_id)
                if idx == 0:
                    return 'first_choice'
                elif idx < len(prefs) // 2:
                    return 'second_choice'
                else:
                    return 'third_choice'
            return None

        # New tiered format
        for tier in ['first_choice', 'second_choice', 'third_choice', 'do_not_want']:
            if position_id in prefs.get(tier, []):
                return tier
        return None


class SlatingApplicationResponse(models.Model):
    """
    Stores individual field responses for an application.
    Enables fully dynamic forms without schema changes.
    """
    application = models.ForeignKey(
        SlatingApplication,
        on_delete=models.CASCADE,
        related_name='responses'
    )
    field = models.ForeignKey(
        SlatingFormField,
        on_delete=models.CASCADE,
        related_name='responses'
    )

    # Store all types as text/JSON for flexibility
    text_value = models.TextField(blank=True, null=True)
    number_value = models.DecimalField(max_digits=20, decimal_places=5, null=True, blank=True)
    json_value = models.JSONField(null=True, blank=True)  # For arrays, objects
    file_value = models.FileField(
        upload_to='slating/application_files/',
        null=True, blank=True,
        storage=DualLocationStorage()
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['application', 'field']

    def get_display_value(self):
        """Return appropriate value based on field type"""
        if self.field.field_type in ['select', 'radio']:
            return self.text_value
        elif self.field.field_type in ['multiselect', 'checkbox']:
            return self.json_value or []
        elif self.field.field_type in ['number', 'decimal', 'gpa']:
            return self.number_value
        elif self.field.field_type in ['file', 'image']:
            return self.file_value.url if self.file_value else None
        else:
            return self.text_value


class SlatingInterview(models.Model):
    """
    Interview tracking for slating applications.
    Notes are confidential and destroyed after approval.
    """
    RECOMMENDATION_CHOICES = [
        ('strongly_recommend', 'Strongly Recommend'),
        ('recommend', 'Recommend'),
        ('neutral', 'Neutral'),
        ('not_recommend', 'Do Not Recommend'),
        ('strongly_not_recommend', 'Strongly Do Not Recommend'),
    ]

    application = models.ForeignKey(
        SlatingApplication,
        on_delete=models.CASCADE,
        related_name='interviews'
    )

    # Scheduling
    scheduled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    location = models.CharField(max_length=200, blank=True)

    # Interview Panel
    interviewers = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        related_name='conducted_slating_interviews'
    )

    # CONFIDENTIAL Notes (destroyed after minutes approval)
    notes = models.TextField(blank=True, help_text='CONFIDENTIAL interview notes')
    strengths = models.TextField(blank=True)
    concerns = models.TextField(blank=True)

    # Recommendation
    recommendation = models.CharField(
        max_length=25,
        choices=RECOMMENDATION_CHOICES,
        blank=True
    )
    recommended_positions = models.ManyToManyField(
        'SlatingPosition',
        blank=True,
        related_name='recommended_candidates'
    )

    # Confidentiality tracking
    notes_destroyed = models.BooleanField(default=False)
    destroyed_at = models.DateTimeField(null=True, blank=True)
    destroyed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='destroyed_slating_notes'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-scheduled_at']

    def destroy_notes(self, user):
        """Destroy confidential notes after minutes approval"""
        from django.utils import timezone
        self.notes = '[DESTROYED - Slate Approved]'
        self.strengths = ''
        self.concerns = ''
        self.notes_destroyed = True
        self.destroyed_at = timezone.now()
        self.destroyed_by = user
        self.save()


class Slate(models.Model):
    """
    A complete slate for chapter voting.
    Multiple slates can exist (primary, alternatives) per period.
    """
    SLATE_TYPES = [
        ('primary', 'Primary Slate'),
        ('alternative', 'Alternative Slate'),
        ('draft', 'Draft Slate'),
    ]

    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='slates'
    )

    name = models.CharField(max_length=200, default='Primary Slate')
    slate_type = models.CharField(max_length=20, choices=SLATE_TYPES, default='primary')
    description = models.TextField(blank=True)

    # Approval tracking
    is_approved = models.BooleanField(default=False)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='approved_slates'
    )

    # Voting results
    total_votes = models.IntegerField(default=0)
    approval_votes = models.IntegerField(default=0)
    rejection_votes = models.IntegerField(default=0)
    abstain_votes = models.IntegerField(default=0)
    approval_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    passed = models.BooleanField(null=True)

    # Metadata
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_slates'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.period.name}"

    def calculate_results(self):
        """Calculate voting results from SlatingVote records"""
        from django.db.models import Count
        votes = self.votes.filter(voting_attempt=self.period.current_voting_attempt)
        self.total_votes = votes.count()
        self.approval_votes = votes.filter(vote_choice='approve').count()
        self.rejection_votes = votes.filter(vote_choice='reject').count()
        self.abstain_votes = votes.filter(vote_choice='abstain').count()

        # Calculate percentage (excluding abstentions)
        counted_votes = self.approval_votes + self.rejection_votes
        if counted_votes > 0:
            self.approval_percentage = (self.approval_votes / counted_votes) * 100
            self.passed = self.approval_percentage >= self.period.required_approval_percentage
        else:
            self.approval_percentage = None
            self.passed = None

        self.save()


class SlateCandidate(models.Model):
    """
    A candidate assigned to a position on a slate.
    """
    slate = models.ForeignKey(
        Slate,
        on_delete=models.CASCADE,
        related_name='candidates'
    )
    position = models.ForeignKey(
        SlatingPosition,
        on_delete=models.CASCADE,
        related_name='slate_assignments'
    )
    application = models.ForeignKey(
        SlatingApplication,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='slate_assignments'
    )
    write_in_member = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='write_in_slate_assignments',
        help_text='Active member assigned directly without an application (write-in)'
    )

    # For individual position voting (fallback)
    individual_votes_for = models.IntegerField(default=0)
    individual_votes_against = models.IntegerField(default=0)
    individual_passed = models.BooleanField(null=True)

    is_runoff = models.BooleanField(
        default=False,
        help_text='True if this candidate is a runoff option alongside the primary candidate'
    )

    display_order = models.IntegerField(default=0)
    notes = models.TextField(blank=True, help_text='Public notes about this assignment')

    class Meta:
        ordering = ['display_order', 'is_runoff']
        unique_together = ['slate', 'position', 'is_runoff']  # One primary + one optional runoff per position

    @property
    def candidate_name(self):
        if self.application_id:
            return self.application.applicant.name
        if self.write_in_member_id:
            return self.write_in_member.name
        return 'Unknown'

    @property
    def is_write_in(self):
        return self.write_in_member_id is not None

    def __str__(self):
        suffix = ' (runoff)' if self.is_runoff else ''
        return f"{self.candidate_name} for {self.position.title}{suffix}"


class SlatingAttendance(models.Model):
    """
    Tracks which members are marked present for a slating period's voting session.
    A member must be present to cast a vote.
    """
    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='attendance'
    )
    member = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.CASCADE,
        related_name='slating_attendance'
    )
    marked_at = models.DateTimeField(auto_now_add=True)
    marked_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True,
        related_name='slating_attendance_marked'
    )

    class Meta:
        unique_together = ['period', 'member']
        ordering = ['member__name']

    def __str__(self):
        return f"{self.member.name} present at {self.period.name}"


class SlatingBallot(models.Model):
    """
    Tracks who has voted (but not how they voted) for audit.
    Implements secret ballot requirement.
    """
    VOTE_TYPE_CHOICES = [
        ('slate', 'Full Slate Vote'),
        ('individual', 'Individual Position Vote'),
    ]

    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='ballots'
    )
    voter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='slating_ballots'
    )

    # Track voting attempt
    voting_attempt = models.IntegerField(default=1)

    # Whether this is a full slate vote or individual position vote
    vote_type = models.CharField(max_length=20, choices=VOTE_TYPE_CHOICES, default='slate')

    # If individual vote, which position
    position = models.ForeignKey(
        SlatingPosition,
        on_delete=models.SET_NULL,
        null=True, blank=True
    )

    # Timestamp and verification
    voted_at = models.DateTimeField(auto_now_add=True)
    ballot_hash = models.CharField(
        max_length=64, unique=True,
        help_text='Hash for vote verification without revealing vote'
    )

    class Meta:
        # One ballot per voter per attempt per type
        unique_together = ['period', 'voter', 'voting_attempt', 'vote_type', 'position']

    def __str__(self):
        return f"Ballot: {self.voter.name} - {self.period.name} (Attempt {self.voting_attempt})"


class SlatingVote(models.Model):
    """
    Anonymous vote record. No direct link to voter.
    """
    VOTE_CHOICES = [
        ('approve', 'Approve'),
        ('reject', 'Reject'),
        ('abstain', 'Abstain'),
    ]

    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='votes'
    )

    # Slate-level vote
    slate = models.ForeignKey(
        Slate,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='votes'
    )

    # Or individual position vote
    slate_candidate = models.ForeignKey(
        SlateCandidate,
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='votes'
    )

    voting_attempt = models.IntegerField(default=1)
    vote_choice = models.CharField(max_length=10, choices=VOTE_CHOICES)

    # For rejection votes - which positions are being rejected
    rejected_positions = models.JSONField(
        default=list,
        blank=True,
        help_text='List of position IDs that the voter objects to (required for reject votes)'
    )

    # Anonymous tracking
    vote_hash = models.CharField(max_length=64, unique=True)
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-voted_at']


class SlatingActivity(models.Model):
    """
    Activity log for slating actions.
    """
    ACTION_CHOICES = [
        ('period_created', 'Period Created'),
        ('period_status_changed', 'Period Status Changed'),
        ('position_added', 'Position Added'),
        ('position_modified', 'Position Modified'),
        ('form_field_added', 'Form Field Added'),
        ('form_field_modified', 'Form Field Modified'),
        ('application_submitted', 'Application Submitted'),
        ('application_reviewed', 'Application Reviewed'),
        ('interview_scheduled', 'Interview Scheduled'),
        ('interview_completed', 'Interview Completed'),
        ('slate_created', 'Slate Created'),
        ('slate_modified', 'Slate Modified'),
        ('voting_opened', 'Voting Opened'),
        ('vote_cast', 'Vote Cast'),  # Generic, no details about how
        ('voting_closed', 'Voting Closed'),
        ('results_published', 'Results Published'),
        ('notes_destroyed', 'Confidential Notes Destroyed'),
        ('admin_transferred', 'Admin Transferred'),
        ('officers_transitioned', 'Officers Transitioned'),
    ]

    period = models.ForeignKey(
        SlatingPeriod,
        on_delete=models.CASCADE,
        related_name='activity_log'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True
    )
    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    details = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ['-timestamp']
        verbose_name_plural = 'Slating Activities'

    def __str__(self):
        return f"{self.get_action_display()} - {self.period.name}"
