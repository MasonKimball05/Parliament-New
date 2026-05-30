from django.db import models
from src.storage import DualLocationStorage


class PassedResolution(models.Model):
    """Model for tracking passed resolutions and their impact on Constitution/Bylaws"""

    BORDER_COLOR_CHOICES = [
        ('green', 'Green'),
        ('blue', 'Blue'),
        ('purple', 'Purple'),
        ('pink', 'Pink'),
        ('indigo', 'Indigo'),
        ('red', 'Red'),
        ('yellow', 'Yellow'),
    ]

    title = models.CharField(max_length=200, help_text='Title of the resolution')
    description = models.TextField(help_text='Brief description of what this resolution does')
    date_passed = models.DateField(help_text='Date this resolution was passed')

    # Link to legislation document
    legislation = models.ForeignKey(
        'Legislation',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        help_text='Optional: Link to the actual legislation document'
    )

    # Alternative: Direct document upload
    document = models.FileField(
        upload_to='passed_resolutions/',
        null=True,
        blank=True,
        storage=DualLocationStorage(),
        help_text='Optional: Upload a document if not linked to legislation'
    )

    # Visual styling
    border_color = models.CharField(
        max_length=20,
        choices=BORDER_COLOR_CHOICES,
        default='green',
        help_text='Border color for the resolution card'
    )

    # Impact details
    impact_summary = models.TextField(
        blank=True,
        help_text='Brief summary of sections impacted (displayed in the card)'
    )

    # Display settings
    display_order = models.IntegerField(
        default=0,
        help_text='Order to display resolutions (lower numbers first)'
    )
    is_active = models.BooleanField(
        default=True,
        help_text='Uncheck to hide this resolution from the page'
    )

    # Metadata
    created_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_resolutions'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', '-date_passed']

    def __str__(self):
        return f"{self.title} ({self.date_passed})"

    def get_document_url(self):
        """Get the URL to the resolution document"""
        if self.legislation:
            return self.legislation.document.url if self.legislation.document else None
        elif self.document:
            return self.document.url
        return None


class ResolutionSectionImpact(models.Model):
    """Track which sections of Constitution/Bylaws are impacted by a resolution"""

    SECTION_TYPE_CHOICES = [
        ('constitution', 'Constitution Article'),
        ('bylaws', 'Bylaws Article'),
        ('other', 'Other Document'),
    ]

    resolution = models.ForeignKey(
        PassedResolution,
        on_delete=models.CASCADE,
        related_name='section_impacts'
    )

    section_name = models.CharField(
        max_length=200,
        help_text='Display name for the section (e.g., "Constitution Article III (Leadership)")'
    )

    section_type = models.CharField(
        max_length=20,
        choices=SECTION_TYPE_CHOICES,
        default='constitution'
    )

    # URL/anchor to link to (e.g., "#const-leadership")
    section_anchor = models.CharField(
        max_length=100,
        blank=True,
        help_text='URL anchor/fragment (e.g., "#const-leadership")'
    )

    # Alternative: link to another page
    external_url = models.CharField(
        max_length=200,
        blank=True,
        help_text='Full URL to another page (e.g., officer duties detail page)'
    )

    display_order = models.IntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'section_name']

    def __str__(self):
        return f"{self.resolution.title} - {self.section_name}"

    def get_link_url(self):
        """Get the full URL for this section link"""
        if self.external_url:
            return self.external_url
        elif self.section_anchor:
            # Return just the anchor - template will handle the base URL
            return self.section_anchor
        return None


class LandingPageContent(models.Model):
    """
    Singleton model for officer-editable landing page content.
    Always access via LandingPageContent.get_instance().
    """
    tagline = models.CharField(
        max_length=300,
        blank=True,
        default='A chapter built on scholarship, friendship, and integrity. Welcome to our home.'
    )
    who_we_are_html = models.TextField(
        blank=True,
        help_text='Rich text for the "Who We Are" section. Supports links.'
    )
    what_we_believe_html = models.TextField(
        blank=True,
        help_text='Rich text for the "What We Believe" section.'
    )
    chapter_history_html = models.TextField(
        blank=True,
        help_text='Rich text for the Chapter History section.'
    )
    chapter_history_title = models.CharField(
        max_length=200,
        blank=True,
        default='Chapter History'
    )

    # ── SEO / link preview ────────────────────────────────────────────────────
    meta_description = models.CharField(
        max_length=300, blank=True,
        help_text='Shown in search results and link previews (recommended ≤ 160 characters).'
    )
    og_image = models.ImageField(
        upload_to='og_images/', blank=True, null=True,
        help_text='Image shown when the page is shared on social media (1200×630 px recommended).'
    )

    # Social links are managed via LandingPageSocialLink (separate model)

    # ── Contact section ───────────────────────────────────────────────────────
    contact_location = models.CharField(
        max_length=200, blank=True,
        default='Samford University, Birmingham, AL'
    )
    contact_address = models.CharField(max_length=200, blank=True)
    contact_phone   = models.CharField(max_length=30, blank=True)

    # ── Section visibility ────────────────────────────────────────────────────
    show_parliament_info = models.BooleanField(
        default=True,
        help_text='Show the "What is Parliament?" info box on the landing page.'
    )
    show_contact_section = models.BooleanField(
        default=True,
        help_text='Show the contact form section on the landing page.'
    )

    # ── Recruitment banner ────────────────────────────────────────────────────
    recruitment_banner_active = models.BooleanField(default=False)
    recruitment_banner_message = models.CharField(
        max_length=300, blank=True,
        help_text='Short message shown in the banner, e.g. "Recruitment is open — spring rush runs Jan 15–20."'
    )
    recruitment_banner_end = models.DateField(
        null=True, blank=True,
        help_text='Optional. Banner auto-hides after this date.'
    )

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='landing_page_edits'
    )

    @classmethod
    def get_instance(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    class Meta:
        verbose_name = 'Landing Page Content'

    def __str__(self):
        return 'Landing Page Content'


class LandingPagePhoto(models.Model):
    """Photos displayed in the chapter history section of the landing page."""
    image = models.ImageField(upload_to='landing_photos/')
    caption = models.CharField(max_length=300, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    uploaded_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='landing_photos_uploaded'
    )

    class Meta:
        ordering = ['display_order', 'uploaded_at']
        verbose_name = 'Landing Page Photo'
        verbose_name_plural = 'Landing Page Photos'

    def __str__(self):
        return f"Photo {self.pk}: {self.caption or 'No caption'}"


class ContactSubmission(models.Model):
    """Message submitted via the public landing page contact form."""
    name = models.CharField(max_length=200)
    email = models.EmailField()
    message = models.TextField()
    topic = models.CharField(max_length=100, blank=True, help_text="Selected contact topic label.")
    recipient_email = models.EmailField(
        blank=True,
        help_text="Email address the mailto was directed to at time of submission."
    )
    submitted_at = models.DateTimeField(auto_now_add=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ['-submitted_at']
        verbose_name = 'Contact Submission'
        verbose_name_plural = 'Contact Submissions'

    def __str__(self):
        return f"Contact from {self.name} ({self.email}) at {self.submitted_at:%Y-%m-%d %H:%M}"


class LandingPageSocialLink(models.Model):
    """A custom social / external link shown in the landing page footer and contact section."""
    label = models.CharField(max_length=100, help_text="Display name, e.g. 'Instagram' or 'Chapter Blog'.")
    url   = models.URLField()
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['display_order', 'pk']
        verbose_name = 'Social Link'
        verbose_name_plural = 'Social Links'

    def __str__(self):
        return f'{self.label}: {self.url}'


class LandingPageContactTopic(models.Model):
    """A topic choice in the public landing page contact form, each routed to a specific role holder."""
    label = models.CharField(max_length=100, help_text="Shown in the dropdown, e.g. 'Recruitment'.")
    role_code = models.CharField(
        max_length=100, blank=True,
        help_text="Role code of the officer who receives messages for this topic. Leave blank to fall back to President → VPR."
    )
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['display_order', 'label']
        verbose_name = 'Contact Topic'
        verbose_name_plural = 'Contact Topics'

    def __str__(self):
        return self.label


class LandingPageFormLink(models.Model):
    """A form/application card displayed in the public landing page."""
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    url = models.URLField(help_text="Link to the form (Google Forms, Typeform, internal page, etc.)")
    button_text = models.CharField(max_length=100, default='Apply Now')
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='landing_form_links_created'
    )

    class Meta:
        ordering = ['display_order', 'created_at']
        verbose_name = 'Landing Page Form Link'
        verbose_name_plural = 'Landing Page Form Links'

    def __str__(self):
        return self.title
