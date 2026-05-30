from django.db import models
from django.conf import settings


class GuideTour(models.Model):
    """
    Represents an interactive guide tour for a specific feature or page.
    Tours can have multiple steps that guide users through functionality.
    """
    CATEGORY_CHOICES = [
        ('officer', 'Officer Guides'),
        ('member', 'Member Guides'),
        ('admin', 'Admin Guides'),
        ('general', 'General Guides'),
    ]

    name = models.CharField(max_length=100, help_text="Display name of the tour")
    slug = models.SlugField(unique=True, help_text="URL-friendly identifier")
    description = models.TextField(help_text="Brief description of what this tour covers")
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default='general',
        help_text="Category for organizing tours"
    )
    icon = models.CharField(
        max_length=50,
        blank=True,
        help_text="Icon class or name (e.g., 'calendar', 'megaphone')"
    )
    is_active = models.BooleanField(default=True, help_text="Whether this tour is available")
    display_order = models.IntegerField(default=0, help_text="Order for displaying in lists")
    estimated_time = models.PositiveIntegerField(
        default=5,
        help_text="Estimated time to complete in minutes"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'display_order', 'name']
        verbose_name = 'Guide Tour'
        verbose_name_plural = 'Guide Tours'

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"

    @property
    def step_count(self):
        return self.steps.count()


class GuideTourStep(models.Model):
    """
    Individual step within a guide tour.
    Each step can target a specific element on a page or provide general information.
    """
    POSITION_CHOICES = [
        ('top', 'Top'),
        ('bottom', 'Bottom'),
        ('left', 'Left'),
        ('right', 'Right'),
        ('top-left', 'Top Left'),
        ('top-right', 'Top Right'),
        ('bottom-left', 'Bottom Left'),
        ('bottom-right', 'Bottom Right'),
        ('center', 'Center (Modal)'),
    ]

    tour = models.ForeignKey(
        GuideTour,
        on_delete=models.CASCADE,
        related_name='steps'
    )
    step_number = models.PositiveIntegerField(help_text="Order of this step in the tour")
    title = models.CharField(max_length=200, help_text="Step title/heading")
    content = models.TextField(help_text="Step content (supports markdown)")

    # Element targeting for interactive tours
    target_selector = models.CharField(
        max_length=200,
        blank=True,
        help_text="CSS selector of element to highlight (e.g., '#create-event-btn')"
    )
    target_page = models.CharField(
        max_length=200,
        blank=True,
        help_text="URL path where this step should appear (e.g., '/events/')"
    )
    position = models.CharField(
        max_length=20,
        choices=POSITION_CHOICES,
        default='bottom',
        help_text="Position of tooltip relative to target element"
    )

    # Optional action requirements
    wait_for_click = models.BooleanField(
        default=False,
        help_text="Wait for user to click target before advancing"
    )
    advance_on_event = models.CharField(
        max_length=100,
        blank=True,
        help_text="DOM event to listen for to auto-advance (e.g., 'submit', 'change')"
    )

    class Meta:
        ordering = ['tour', 'step_number']
        unique_together = ['tour', 'step_number']
        verbose_name = 'Guide Tour Step'
        verbose_name_plural = 'Guide Tour Steps'

    def __str__(self):
        return f"{self.tour.name} - Step {self.step_number}: {self.title}"


class UserTourProgress(models.Model):
    """
    Tracks a user's progress through guide tours.
    """
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tour_progress'
    )
    tour = models.ForeignKey(
        GuideTour,
        on_delete=models.CASCADE,
        related_name='user_progress'
    )
    current_step = models.PositiveIntegerField(
        default=0,
        help_text="Current step number (0 = not started)"
    )
    completed = models.BooleanField(default=False)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ['user', 'tour']
        verbose_name = 'User Tour Progress'
        verbose_name_plural = 'User Tour Progress'

    def __str__(self):
        status = "Completed" if self.completed else f"Step {self.current_step}"
        return f"{self.user.name} - {self.tour.name}: {status}"

    def advance_step(self):
        """Advance to next step, mark complete if at end."""
        from django.utils import timezone

        if self.current_step < self.tour.step_count:
            self.current_step += 1
            if self.current_step >= self.tour.step_count:
                self.completed = True
                self.completed_at = timezone.now()
            self.save()
            return True
        return False


class GuideArticle(models.Model):
    """
    Static guide article/documentation page.
    For longer-form documentation that doesn't fit the tour format.
    """
    CATEGORY_CHOICES = [
        ('officer', 'Officer Guides'),
        ('member', 'Member Guides'),
        ('admin', 'Admin Guides'),
        ('general', 'General Guides'),
    ]

    title = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    category = models.CharField(
        max_length=20,
        choices=CATEGORY_CHOICES,
        default='general'
    )
    summary = models.TextField(
        blank=True,
        help_text="Brief summary shown in article lists"
    )
    content = models.TextField(help_text="Article content (supports markdown)")
    icon = models.CharField(max_length=50, blank=True)

    # Related tour (optional)
    related_tour = models.ForeignKey(
        GuideTour,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='articles',
        help_text="Optional interactive tour related to this article"
    )

    is_published = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['category', 'display_order', 'title']
        verbose_name = 'Guide Article'
        verbose_name_plural = 'Guide Articles'

    def __str__(self):
        return f"{self.title} ({self.get_category_display()})"
