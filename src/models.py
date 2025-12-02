from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.utils import timezone
from django.core.exceptions import ValidationError
import logging
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.conf import settings

logger = logging.getLogger('function_calls')

class ParliamentUserManager(BaseUserManager):
    def create_user(self, user_id, name, username, member_type, password=None):
        if not user_id:
            raise ValueError('Users must have an ID')
        if not username:
            raise ValueError('Users must have an username')
        user = self.model(user_id=user_id, name=name, member_type=member_type)
        user.username = name  # Set username as name by default
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, user_id, name, username, member_type, password):
        user = self.create_user(user_id, name, username, member_type, password)
        user.is_admin = True
        user.save(using=self._db)
        return user

class ActiveUserManager(ParliamentUserManager):
    def get_queryset(self):
        return super().get_queryset().filter(member_status='Active')

class Role(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField(blank=True)

    one_per_chapter = models.BooleanField(default=False)

    def __str__(self):
        return self.name

class ParliamentUser(AbstractBaseUser):
    MEMBER_TYPES = (
        ('Member', 'Member'),
        ('Chair', 'Chair'),
        ('Officer', 'Officer'),
    )
    MEMBER_STATUS = (
        ('Active', 'Active'),
        ('Inactive', 'Inactive'),
        ('Alumni', 'Alumni'),
    )

    user_id = models.CharField(max_length=30, unique=True, primary_key=True)
    name = models.CharField(max_length=100)
    member_type = models.CharField(max_length=20, choices=MEMBER_TYPES)
    is_active = models.BooleanField(default=True)
    is_admin = models.BooleanField(default=False)
    username = models.CharField(max_length=100, unique=True)
    anonymous_vote = models.BooleanField(default=False)
    allow_abstain = models.BooleanField(default=True)
    roles = models.ManyToManyField(Role, blank=True)

    member_status = models.CharField(max_length=20, choices=MEMBER_STATUS, default='Active')

    objects = ParliamentUserManager()
    active = ActiveUserManager()

    USERNAME_FIELD = 'username'
    REQUIRED_FIELDS = ['name', 'member_type', 'user_id']

    def __str__(self):
        return f'{self.name} ({self.member_type})'

    def has_perm(self, perm, obj=None):
        return self.is_admin

    def has_module_perms(self, app_label):
        return self.is_admin

    @property
    def is_staff(self):
        return self.is_admin

    class Meta:
        ordering = ['user_id']


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
        ('draft', 'Draft'),
        ('passed', 'Passed'),
        ('removed', 'Removed'),
    ]

    title = models.CharField(max_length=200)
    description = models.TextField()
    document = models.FileField(upload_to='legislation_docs/', validators=[validate_legislation_file])
    posted_by = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE)
    available_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    voting_ended_at = models.DateTimeField(null=True, blank=True)
    passed = models.BooleanField(default=False)

    anonymous_vote = models.BooleanField(default=False)
    allow_abstain = models.BooleanField(default=True)
    voting_closed = models.BooleanField(default=False)
    vote_mode = models.CharField(
        max_length=20,
        choices=[('percentage', 'Percentage'), ('peacewise', 'Peacewise'), ('plurality', 'Plurality')],
        default='percentage',
    )

    required_number = models.PositiveIntegerField(null=True, blank=True)
    plurality_options = ArrayField(models.CharField(max_length=100), blank=True, null=True)  # Only for PostgreSQL

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')

    @property
    def required_yes_votes(self):
        if self.vote_mode == 'peacewise':
            return self.required_number or 0
        return None

    def is_available(self):
        from django.utils import timezone
        return timezone.now() >= self.available_at

    def __str__(self):
        return self.title

    def set_passed(self):
        from collections import Counter

        total_votes = Vote.objects.filter(legislation=self)

        if self.vote_mode == 'plurality':
            vote_choices = [v.vote_choice for v in total_votes]
            vote_counts = Counter(vote_choices)
            if vote_counts:
                max_votes = max(vote_counts.values())
                winners = [option for option, count in vote_counts.items() if count == max_votes]
                self.passed = len(winners) == 1  # Only passes if there is a single clear winner
            else:
                self.passed = False
        elif self.vote_mode == 'peacewise':
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

class Attendance(models.Model):
    user = models.ForeignKey(ParliamentUser, on_delete=models.CASCADE, limit_choices_to={'member_status': 'Active'})
    date = models.DateField(auto_now_add=True)
    present = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

class Vote(models.Model):
    user = models.ForeignKey(ParliamentUser, on_delete=models.CASCADE, limit_choices_to={'member_status': 'Active'})
    legislation = models.ForeignKey(Legislation, on_delete=models.CASCADE)
    vote_choice = models.CharField(max_length=100)


@receiver(post_save)
def log_model_save(sender, instance, created, **kwargs):
    if sender.__module__.startswith('django.'):
        return
    action = 'Created' if created else 'Updated'
    logger.info(f"{action} {sender.__name__} instance with ID {instance.pk}")

@receiver(post_delete)
def log_model_delete(sender, instance, **kwargs):
    if sender.__module__.startswith('django.'):
        return
    logger.info(f"Deleted {sender.__name__} instance with ID {instance.pk}")

class Committee(models.Model):
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
    allow_multiple_chairs = models.BooleanField(default=False)
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True, blank=True, related_name="committees")
    created_at = models.DateTimeField(auto_now_add=True)
    committee_id = models.IntegerField(default=0, unique=True)

    def __str__(self):
        return self.name

    def chair_list(self):
        return ", ".join([c.name for c in self.chairs.all()])
    chair_list.short_description = "Chairs"

class CommitteePermissions(models.Model):
    committee = models.ForeignKey(Committee, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    can_view_docs = models.BooleanField(default=False)
    can_upload_docs = models.BooleanField(default=False)
    can_vote = models.BooleanField(default=False)
    can_manage_members = models.BooleanField(default=False)
    can_view_results = models.BooleanField(default=True)