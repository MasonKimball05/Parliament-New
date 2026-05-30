from django.db import models
from src.storage import DualLocationStorage


class CommitteeMinutes(models.Model):
    committee = models.ForeignKey('Committee', on_delete=models.CASCADE, related_name='minutes')
    title = models.CharField(max_length=200)
    date = models.DateField()
    content = models.TextField(blank=True)
    document = models.FileField(upload_to='committee_minutes/', null=True, blank=True, storage=DualLocationStorage())
    posted_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date']
        verbose_name_plural = "Committee Minutes"

    def __str__(self):
        return f"{self.committee.code} - {self.title} ({self.date})"


class ChapterFolder(models.Model):
    """Custom folders for organizing chapter documents"""
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, related_name='created_folders')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class DocumentTag(models.Model):
    """Tags for categorizing and organizing documents"""
    name = models.CharField(max_length=50, unique=True)
    color = models.CharField(
        max_length=20,
        default='gray',
        help_text='Badge color for the tag (e.g., blue, green, red, yellow, purple, pink)'
    )
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class CommitteeDocument(models.Model):
    DOCUMENT_TYPES = [
        ('general', 'General Document'),
        ('minutes', 'Meeting Minutes'),
        ('agenda', 'Meeting Agenda'),
        ('report', 'Report'),
        ('policy', 'Policy Document'),
    ]

    VISIBILITY_CHOICES = [
        ('all_members', 'All Chapter Members'),
        ('committee_only', 'Committee Members Only'),
        ('chairs_only', 'Committee Chairs Only'),
        ('officers_only', 'Officers Only'),
        ('custom', 'Custom Users'),
    ]

    committee = models.ForeignKey('Committee', on_delete=models.CASCADE, null=True, blank=True, related_name='documents')
    title = models.CharField(max_length=200)
    document = models.FileField(upload_to='committee_documents/', storage=DualLocationStorage())
    uploaded_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    description = models.TextField(blank=True)
    published_to_chapter = models.BooleanField(default=False)
    chapter_folder = models.ForeignKey(ChapterFolder, on_delete=models.SET_NULL, null=True, blank=True, related_name='documents', help_text='Optional custom folder for chapter documents')
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES, default='general')
    meeting_date = models.DateField(null=True, blank=True, help_text='For minutes and agendas')

    # Enhanced document management features
    tags = models.ManyToManyField(DocumentTag, blank=True, related_name='documents')
    version_number = models.IntegerField(default=1, help_text='Current version number')
    is_latest_version = models.BooleanField(default=True, help_text='Whether this is the latest version')

    # Visibility controls
    visibility = models.CharField(
        max_length=20,
        choices=VISIBILITY_CHOICES,
        default='committee_only',
        help_text='Control who can view this document'
    )
    custom_viewers = models.ManyToManyField(
        'ParliamentUser',
        blank=True,
        related_name='viewable_documents',
        help_text='Specific users who can view this document (only applies when visibility is set to Custom)'
    )

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        if self.committee:
            return f"{self.committee.code} - {self.title}"
        return f"Chapter - {self.title}"

    def get_version_string(self):
        """Return formatted version string like 'v1.0'"""
        return f"v{self.version_number}.0"

    def can_user_view(self, user):
        """Check if a user has permission to view this document"""
        # Documents published to chapter are visible to all members
        if self.published_to_chapter:
            return True

        # Admins and the uploader can always view
        if user.is_admin or user == self.uploaded_by:
            return True

        # Check based on visibility setting
        if self.visibility == 'all_members':
            return True
        elif self.visibility == 'committee_only':
            if not self.committee:
                return True  # Chapter-level docs with committee_only treated as all_members
            return user in self.committee.members.all()
        elif self.visibility == 'chairs_only':
            if not self.committee:
                return user.is_officer
            return user in self.committee.chairs.all()
        elif self.visibility == 'officers_only':
            return user.member_type == 'Officer' or user.is_officer
        elif self.visibility == 'custom':
            return user in self.custom_viewers.all()

        return False


class DocumentVersion(models.Model):
    """Track document version history"""
    document = models.ForeignKey(CommitteeDocument, on_delete=models.CASCADE, related_name='versions')
    version_number = models.IntegerField()
    file = models.FileField(upload_to='document_versions/', storage=DualLocationStorage())
    uploaded_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    change_notes = models.TextField(blank=True, help_text='Description of changes in this version')
    file_size = models.BigIntegerField(null=True, blank=True, help_text='File size in bytes')

    class Meta:
        ordering = ['-version_number']
        unique_together = ['document', 'version_number']

    def __str__(self):
        return f"{self.document.title} - v{self.version_number}"

    def get_file_size_display(self):
        """Return human-readable file size"""
        if not self.file_size:
            return 'Unknown'
        size = self.file_size
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size:.1f} TB"


class ChapterMinutes(models.Model):
    """
    Chapter meeting minutes with attendance tracking and embedded motions
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('finalized', 'Finalized'),
        ('published', 'Published'),
    ]

    VISIBILITY_CHOICES = [
        ('all_members', 'All Chapter Members'),
        ('officers_only', 'Officers Only'),
        ('custom', 'Custom Users'),
    ]

    title = models.CharField(max_length=200)
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField(null=True, blank=True, help_text='Time the meeting was adjourned')
    committee = models.ForeignKey(
        'Committee', on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='committee_minutes_sessions',
        help_text='If set, these are committee minutes; if null, chapter minutes'
    )
    event = models.ForeignKey('Event', on_delete=models.SET_NULL, null=True, blank=True, related_name='chapter_minutes')
    created_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, related_name='created_minutes')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    attendance_taken = models.BooleanField(default=False)
    attendance_data = models.JSONField(null=True, blank=True, help_text='Snapshot of attendance: [{user_id, name, status}, ...]')
    published_document = models.ForeignKey(CommitteeDocument, on_delete=models.SET_NULL, null=True, blank=True, related_name='source_minutes')
    publish_visibility = models.CharField(max_length=20, choices=VISIBILITY_CHOICES, default='all_members')

    # Edit tracking for published minutes
    edited_after_publish = models.BooleanField(default=False)
    last_edit_at = models.DateTimeField(null=True, blank=True)
    last_edit_by = models.ForeignKey('ParliamentUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='edited_minutes')
    last_edit_reason = models.TextField(blank=True, help_text='Reason for editing after publication')

    class Meta:
        ordering = ['-date', '-start_time']
        verbose_name_plural = 'Chapter Minutes'

    def __str__(self):
        return f"{self.title} - {self.date}"


class MinutesSection(models.Model):
    """
    Ordered content blocks within chapter minutes (text, motion, header, or section_end)
    """
    SECTION_TYPES = [
        ('text', 'Text'),
        ('motion', 'Motion'),
        ('header', 'Section Header'),
        ('section_end', 'Section End'),
    ]

    minutes = models.ForeignKey(ChapterMinutes, on_delete=models.CASCADE, related_name='sections')
    section_type = models.CharField(max_length=20, choices=SECTION_TYPES)
    order = models.IntegerField(default=0)
    content = models.TextField(blank=True, help_text='Text content for text sections')
    title = models.CharField(max_length=200, blank=True, help_text='Title for section headers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"{self.minutes.title} - Section {self.order} ({self.section_type})"


class MinutesMotion(models.Model):
    """
    A motion or vote recorded within chapter minutes
    """
    MOTION_TYPE_CHOICES = [
        ('custom', 'Custom Motion'),
        ('approve_prev_minutes', 'Approval of Previous Minutes'),
        ('approve_prev_minutes_uc', 'Approval of Previous Minutes by Unanimous Consent'),
        ('table_motion', 'Motion to Table'),
        ('call_question', 'Call the Question'),
        ('adjourn', 'Motion to Adjourn'),
        ('recess', 'Motion to Recess'),
        ('amend', 'Motion to Amend'),
        ('reconsider', 'Motion to Reconsider'),
        ('point_of_order', 'Point of Order'),
        ('other', 'Other'),
    ]

    VOTE_METHOD_CHOICES = [
        ('voice', 'Voice Vote'),
        ('show_of_hands', 'Show of Hands'),
        ('roll_call', 'Roll Call'),
        ('ballot', 'Ballot'),
        ('unanimous_consent', 'Unanimous Consent'),
        ('standing', 'Standing Vote'),
    ]

    RESULT_CHOICES = [
        ('passed', 'Passed'),
        ('failed', 'Failed'),
        ('tabled', 'Tabled'),
        ('withdrawn', 'Withdrawn'),
        ('referred', 'Referred to Committee'),
        ('no_vote', 'No Vote Taken'),
    ]

    CAUCUS_TYPE_CHOICES = [
        ('moderated', 'Moderated'),
        ('unmoderated', 'Unmoderated'),
    ]

    section = models.OneToOneField(MinutesSection, on_delete=models.CASCADE, related_name='motion')
    motion_type = models.CharField(max_length=30, choices=MOTION_TYPE_CHOICES, default='custom')
    motion_text = models.TextField()
    context_notes = models.TextField(blank=True, help_text='Notes relevant to this motion')
    author = models.ForeignKey('ParliamentUser', on_delete=models.SET_NULL, null=True, blank=True, related_name='authored_motions')
    author_text = models.CharField(max_length=200, blank=True, help_text='Typed author name if not selected from dropdown')
    received_second = models.BooleanField(default=False)
    seconded_by_text = models.CharField(max_length=200, blank=True)
    vote_method = models.CharField(max_length=20, choices=VOTE_METHOD_CHOICES, default='voice')
    result = models.CharField(max_length=20, choices=RESULT_CHOICES, default='passed')
    votes_for = models.PositiveIntegerField(null=True, blank=True)
    votes_against = models.PositiveIntegerField(null=True, blank=True)
    votes_abstain = models.PositiveIntegerField(null=True, blank=True)
    caucus_held = models.BooleanField(default=False)
    caucus_duration = models.PositiveIntegerField(null=True, blank=True, help_text='Duration in minutes')
    caucus_type = models.CharField(max_length=15, choices=CAUCUS_TYPE_CHOICES, blank=True)
    speaker_time = models.PositiveIntegerField(null=True, blank=True, help_text='Seconds per speaker (moderated caucus)')

    def __str__(self):
        return f"{self.get_motion_type_display()} - {self.motion_text[:50]}"

    def get_author_display(self):
        if self.author:
            return self.author.get_display_name()
        return self.author_text or 'Unknown'
