"""
v3.49.0 — GroupMe bot: announcements posted on Parliament are echoed into
GroupMe groups.

Mason, 10-17-26: "when someone sends an announcement on Parliament a bot echos
it on GroupMe … be able to invite/add the bot to groups on GroupMe and set what
the announcement channel is so in case the groupme group is moved in the future
it can be kept up from the dashboard." The chapter has two GroupMes, one with
only brothers and one with brothers and pledges, and an announcement goes to
whichever ones its "Visible to" allows.

How GroupMe works, briefly, because it decides the shape of these models:

* A GroupMe **bot belongs to exactly one group**. "Adding the bot to a group"
  means creating a new bot in that group (`POST /v3/bots`), which needs a
  GroupMe user's **access token** (from dev.groupme.com). That token is the
  key to that person's whole GroupMe account, so it is stored encrypted and is
  never shown again once saved.
* **Posting** as a bot needs only the `bot_id` (`POST /v3/bots/post`). Anyone
  holding a bot_id can post as the bot, so it is a credential too: encrypted,
  masked on the dashboard, never exported.
* So a "channel" here is one bot in one group. Moving the chapter to a new
  GroupMe = create a bot in the new group and retire the old one; the channel
  keeps its name, audience and history.

⚠️ None of these models are registered in /admin/ (see the note in
admin_extra.py): the token and bot ids are credentials, and the dashboard at
/officers/groupme/ is the one place they are managed.
"""
from django.db import models
from django.db.models import Q

from src.encrypted_fields import EncryptedCharField
from src.models.singleton import SingletonRow


#: The member types a GroupMe group can contain. Same keys as
#: `Announcement.MEMBER_TYPES`, because the rule that matters is "is everyone
#: in this GroupMe allowed to see this announcement" (see src/groupme.py).
AUDIENCE_CHOICES = (
    ('Member', 'Brothers'),
    ('Pledge', 'Pledges'),
    ('Advisor', 'Advisors'),
)
AUDIENCE_KEYS = tuple(key for key, _ in AUDIENCE_CHOICES)


class GroupMeSettings(SingletonRow, models.Model):
    """The chapter's GroupMe connection: one row (pk=1)."""

    access_token = EncryptedCharField(
        max_length=200, blank=True,
        help_text='GroupMe access token (dev.groupme.com). Needed to list groups '
                  'and add or move bots; NOT needed for posting. Encrypted.',
    )
    token_owner_name = models.CharField(
        max_length=200, blank=True,
        help_text='Whose GroupMe account the token belongs to (from GroupMe).',
    )
    bot_name = models.CharField(
        max_length=30, default='Parliament',
        help_text='Name new bots get in GroupMe.',
    )
    include_full_text = models.BooleanField(
        default=True,
        help_text='Post the whole announcement. Off = title and link only.',
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )

    class Meta:
        verbose_name = 'GroupMe settings'
        verbose_name_plural = 'GroupMe settings'

    def __str__(self):
        return 'GroupMe settings'

    @property
    def has_token(self):
        return bool(self.access_token)


class GroupMeChannel(models.Model):
    """One bot in one GroupMe group, plus who is in that group."""

    name = models.CharField(
        max_length=100,
        help_text='What officers see, e.g. "Brothers" or "Brothers + Pledges".',
    )
    group_id = models.CharField(max_length=50, blank=True)
    group_name = models.CharField(max_length=200, blank=True)
    bot_id = EncryptedCharField(max_length=100)
    bot_created_here = models.BooleanField(
        default=False,
        help_text='Parliament made this bot, so it can also remove it from GroupMe.',
    )
    audience = models.JSONField(
        default=list,
        help_text='Who is in this GroupMe. An announcement is only posted here '
                  'if all of them can see it on Parliament.',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def audience_labels(self):
        labels = dict(AUDIENCE_CHOICES)
        return [labels[k] for k in AUDIENCE_KEYS if k in (self.audience or [])]

    @property
    def masked_bot_id(self):
        bot_id = self.bot_id or ''
        return f'…{bot_id[-4:]}' if len(bot_id) > 4 else '…'


class GroupMeMessage(models.Model):
    """Every post the bot makes (or tried to make). The dashboard's log."""

    KIND_CHOICES = (
        ('announcement', 'Announcement'),
        ('event_reminder', 'Event reminder'),
        ('test', 'Test message'),
    )
    STATUS_CHOICES = (
        ('queued', 'Queued'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
    )

    channel = models.ForeignKey(
        GroupMeChannel, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='messages',
    )
    #: Kept so the log still reads right after a channel is removed.
    channel_name = models.CharField(max_length=100, blank=True)
    announcement = models.ForeignKey(
        'Announcement', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='groupme_messages',
    )
    #: v3.50.0 — set on event reminders.
    event = models.ForeignKey(
        'Event', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='groupme_messages',
    )
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default='announcement')
    text = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='queued')
    error = models.CharField(max_length=500, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_by = models.ForeignKey(
        'ParliamentUser', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+',
    )

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # One post per announcement per channel, however many times the
            # dispatcher runs (create + the 5-minute sweep + a retry).
            models.UniqueConstraint(
                fields=['announcement', 'channel'],
                condition=Q(kind='announcement'),
                name='groupme_one_post_per_announcement_channel',
            ),
            models.UniqueConstraint(
                fields=['event', 'channel'],
                condition=Q(kind='event_reminder'),
                name='groupme_one_reminder_per_event_channel',
            ),
        ]

    def __str__(self):
        return f'{self.get_kind_display()} → {self.channel_name} ({self.status})'
