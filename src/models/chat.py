from django.db import models
from django.conf import settings


class ChatChannel(models.Model):
    """Represents a chat channel - committee or custom"""

    CHANNEL_TYPES = [
        ('committee', 'Committee Chat'),
        ('custom', 'Custom Channel'),
        ('direct', 'Direct Message'),  # Future: DMs between users
    ]

    ACCESS_TYPES = [
        ('open', 'All Members'),           # Anyone can access
        ('committee', 'Committee Members'), # Tied to committee
        ('restricted', 'Restricted'),      # Custom permissions
    ]

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    channel_type = models.CharField(max_length=20, choices=CHANNEL_TYPES, default='custom')
    access_type = models.CharField(max_length=20, choices=ACCESS_TYPES, default='restricted')

    # Link to committee (for committee chats)
    committee = models.ForeignKey(
        'Committee',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='chat_channel'
    )

    created_by = models.ForeignKey('ParliamentUser', on_delete=models.SET_NULL, null=True, related_name='created_channels')
    created_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)
    is_read_only = models.BooleanField(default=False, help_text='No one can send new messages; existing messages remain visible')

    # Icon/color for customization
    icon = models.CharField(max_length=10, default='💬')
    color = models.CharField(max_length=7, default='#003DA5')  # Hex color

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @classmethod
    def access_context(cls, user):
        """
        Everything `has_access` needs about `user`, fetched once.

        v3.17.3. `has_access` asks the database the same questions about the
        same user for every channel it is called on, and the chat index calls it
        **twice per channel**. Dev mode measured the fallout: `is_member` 15×,
        the guest-permission `.exists()` 9×, on one page load.

        This is deliberately a *context object consumed by the existing method*
        rather than a second copy of the access rules. Duplicating an
        authorization predicate is how v3.16.3's Kai search ended up wrong in
        two places at once — "the duplication is why both were wrong". There is
        still exactly one implementation of who can read a channel; this only
        changes where its inputs come from.

        Pass it to `has_access(..., ctx=...)`, or use `access_map()`.
        """
        from django.utils import timezone

        permissions = list(
            ChatChannelPermission.objects
            .filter(can_read=True)
            .filter(models.Q(expires_at__isnull=True)
                    | models.Q(expires_at__gt=timezone.now()))
            .values('channel_id', 'user_id', 'member_type', 'chairs_only',
                    'officers_only', 'alumni_only')
        )
        return {
            'user_id': user.pk,
            'member_type': user.member_type,
            'member_status': user.member_status,
            'is_admin': bool(user.is_admin),
            'is_officer': bool(user.is_officer),
            'is_chair': user.chair_roles.exists(),
            'committee_ids': set(user.committees.values_list('id', flat=True)),
            'permissions': permissions,
        }

    def _has_access_cached(self, ctx, admin_override=False):
        """`has_access`, answered from an `access_context()` — no queries."""
        if not self.is_active:
            return False
        if admin_override and ctx['is_admin']:
            return True
        if self.access_type == 'open':
            return True

        rows = [p for p in ctx['permissions'] if p['channel_id'] == self.pk]

        if self.access_type == 'committee' and self.committee_id:
            if self.committee_id in ctx['committee_ids']:
                return True
            return any(p['user_id'] == ctx['user_id'] for p in rows)

        if self.access_type == 'restricted':
            return (
                any(p['user_id'] == ctx['user_id'] for p in rows)
                or any(p['member_type'] == ctx['member_type'] for p in rows)
                or (any(p['chairs_only'] for p in rows) and ctx['is_chair'])
                or (any(p['officers_only'] for p in rows) and ctx['is_officer'])
                or (any(p['alumni_only'] for p in rows)
                    and ctx['member_status'] == 'Alumni')
            )

        return False

    @classmethod
    def access_map(cls, channels, user, admin_override=False):
        """
        ``{channel_id: bool}`` for many channels in a fixed number of queries.

        Three queries (guest permissions, the user's committees, the chair
        check) whatever the channel count, against 2–13 per channel before.
        """
        ctx = cls.access_context(user)
        return {
            channel.pk: channel._has_access_cached(ctx, admin_override)
            for channel in channels
        }

    @classmethod
    def unread_map(cls, channels, user):
        """
        ``{channel_id: unread_count}`` in two queries.

        v3.17.3: `get_unread_count` was one `ChatReadReceipt.objects.get` plus
        one `.count()` per channel. Same arithmetic, batched: one pass for the
        user's receipts, one grouped count of undeleted messages newer than each
        receipt's marker.
        """
        channel_ids = [c.pk for c in channels]
        if not channel_ids:
            return {}

        markers = {}
        for row in (ChatReadReceipt.objects
                    .filter(user=user, channel_id__in=channel_ids)
                    .select_related('last_read_message')
                    .values('channel_id', 'last_read_message__created_at')):
            markers[row['channel_id']] = row['last_read_message__created_at']

        counts = {cid: 0 for cid in channel_ids}
        for row in (ChatMessage.objects
                    .filter(channel_id__in=channel_ids, is_deleted=False)
                    .values('channel_id', 'created_at')):
            marker = markers.get(row['channel_id'])
            # No receipt, or a receipt with no marker message, means everything
            # is unread — matching get_unread_count's two fallback branches.
            if marker is None or row['created_at'] > marker:
                counts[row['channel_id']] += 1
        return counts

    def has_access(self, user, admin_override=False, ctx=None):
        """
        Check if user has access to this channel.

        `ctx` is an optional `access_context()`; supply it and this answers
        without touching the database. See `access_map()` for many channels.
        """
        if ctx is not None:
            return self._has_access_cached(ctx, admin_override)

        if not self.is_active:
            return False

        # Admin override for "View All Channels" mode
        if admin_override and user.is_admin:
            return True

        if self.access_type == 'open':
            return True

        if self.access_type == 'committee' and self.committee:
            # Check if user is a committee member first
            if self.committee.is_member(user):
                return True
            # Check if user has guest permission with can_read=True (not expired)
            from django.utils import timezone
            return ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_read=True
            ).filter(
                models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
            ).exists()

        if self.access_type == 'restricted':
            from django.utils import timezone
            not_expired = models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
            # Check custom permissions - must have can_read=True
            return ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_read=True
            ).filter(not_expired).exists() or ChatChannelPermission.objects.filter(
                channel=self,
                member_type=user.member_type,
                can_read=True
            ).exists() or (
                ChatChannelPermission.objects.filter(
                    channel=self,
                    chairs_only=True,
                    can_read=True
                ).exists() and user.chair_roles.exists()
            ) or (
                ChatChannelPermission.objects.filter(
                    channel=self,
                    officers_only=True,
                    can_read=True
                ).exists() and user.is_officer
            ) or (
                ChatChannelPermission.objects.filter(
                    channel=self,
                    alumni_only=True,
                    can_read=True
                ).exists() and user.member_status == 'Alumni'
            )

        return False

    def get_unread_count(self, user):
        """Get unread message count for a user"""
        try:
            receipt = ChatReadReceipt.objects.get(user=user, channel=self)
            if not receipt.last_read_message:
                return self.messages.filter(is_deleted=False).count()

            return self.messages.filter(
                created_at__gt=receipt.last_read_message.created_at,
                is_deleted=False
            ).count()
        except ChatReadReceipt.DoesNotExist:
            return self.messages.filter(is_deleted=False).count()

    def can_read(self, user):
        """Check if user can read messages in this channel"""
        if not self.is_active:
            return False

        # Admins always have access
        if user.is_admin:
            return True

        # Committee members always have read access
        if self.committee and self.committee.is_member(user):
            return True

        # Check if user has specific permission
        if self.access_type == 'open':
            return True

        # For committee and restricted channels, check guest permissions
        if self.access_type in ['committee', 'restricted']:
            from django.utils import timezone
            perm = ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_read=True
            ).filter(
                models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
            ).first()
            return perm is not None

        return False

    def can_write(self, user):
        """Check if user can send messages in this channel"""
        if not self.is_active:
            return False

        if self.is_read_only:
            return False

        # Admins always have access
        if user.is_admin:
            return True

        # Committee members always have write access
        if self.committee and self.committee.is_member(user):
            return True

        # Check if user has specific permission
        if self.access_type == 'open':
            return True

        # For committee and restricted channels, check guest permissions
        if self.access_type in ['committee', 'restricted']:
            from django.utils import timezone
            perm = ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_write=True
            ).filter(
                models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
            ).first()
            return perm is not None

        return False

    def can_delete_messages(self, user):
        """Check if user can delete their own messages in this channel"""
        if not self.is_active:
            return False

        # Admins always have access
        if user.is_admin:
            return True

        # Chairs can always delete
        if self.committee and self.committee.is_chair(user):
            return True

        # Committee members always have delete access
        if self.committee and self.committee.is_member(user):
            return True

        # Check if user has specific permission
        if self.access_type == 'open':
            return True

        # For committee and restricted channels, check guest permissions
        if self.access_type in ['committee', 'restricted']:
            from django.utils import timezone
            perm = ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_delete=True
            ).filter(
                models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
            ).first()
            return perm is not None

        return False

    def can_edit_messages(self, user):
        """Check if user can edit their own messages in this channel"""
        if not self.is_active:
            return False

        # Admins always have access
        if user.is_admin:
            return True

        # Committee members can always edit their own messages
        if self.committee and self.committee.is_member(user):
            return True

        # Open channels: everyone can edit
        if self.access_type == 'open':
            return True

        # For committee and restricted channels, check guest permissions
        if self.access_type in ['committee', 'restricted']:
            from django.utils import timezone
            perm = ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_edit=True
            ).filter(
                models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=timezone.now())
            ).first()
            return perm is not None

        return False


class ChatChannelPermission(models.Model):
    """Defines who has access to a restricted channel"""

    MEMBER_TYPES = [
        ('Member', 'Member'),
        ('Chair', 'Chair'),
        ('Officer', 'Officer'),
        ('Advisor', 'Advisor'),
        ('Pledge', 'Pledge'),
    ]

    channel = models.ForeignKey(ChatChannel, on_delete=models.CASCADE, related_name='permissions')

    # Specific user access (nullable)
    user = models.ForeignKey(
        'ParliamentUser',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='channel_permissions'
    )

    # Role-based access (nullable)
    member_type = models.CharField(max_length=50, choices=MEMBER_TYPES, null=True, blank=True)

    # Chair-only access
    chairs_only = models.BooleanField(default=False, help_text='Only committee chairs can access')

    # Officer-only access
    officers_only = models.BooleanField(default=False, help_text='Only officers can access')

    # Alumni-only access
    alumni_only = models.BooleanField(default=False, help_text='Only alumni can access')

    # Read/Write permissions for guest users (non-committee members)
    can_read = models.BooleanField(default=True, help_text='User can read messages in this channel')
    can_write = models.BooleanField(default=True, help_text='User can send messages in this channel')
    can_delete = models.BooleanField(default=False, help_text='User can delete their own messages in this channel')
    can_edit = models.BooleanField(default=False, help_text='User can edit their own messages in this channel')

    expires_at = models.DateTimeField(
        null=True, blank=True,
        help_text='If set, this permission expires at the given time and the guest loses access'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_expired(self):
        if self.expires_at is None:
            return False
        from django.utils import timezone
        return timezone.now() >= self.expires_at

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['channel', 'user'],
                name='unique_channel_user',
                condition=models.Q(user__isnull=False)
            ),
            models.UniqueConstraint(
                fields=['channel', 'member_type'],
                name='unique_channel_member_type',
                condition=models.Q(member_type__isnull=False)
            ),
        ]

    def __str__(self):
        if self.user:
            return f"{self.channel.name} - {self.user.name}"
        if self.member_type:
            return f"{self.channel.name} - {self.member_type}"
        if self.chairs_only:
            return f"{self.channel.name} - Chairs Only"
        if self.officers_only:
            return f"{self.channel.name} - Officers Only"
        return f"{self.channel.name} - Permission"


class ChatMessage(models.Model):
    """Chat messages - now linked to channels"""
    # New channel-based system
    channel = models.ForeignKey(ChatChannel, on_delete=models.CASCADE, related_name='messages', null=True, blank=True)

    # Legacy committee field (will be deprecated after migration)
    committee = models.ForeignKey('Committee', on_delete=models.CASCADE, related_name='chat_messages', null=True, blank=True)

    sender = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, related_name='sent_messages')
    message = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False, help_text='Soft delete - show "Message deleted"')

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['channel', '-created_at']),
            models.Index(fields=['committee', '-created_at']),  # Legacy index
            # Covers the unread-count subquery: filter(channel=X, is_deleted=False, created_at__gt=Y)
            models.Index(fields=['channel', 'is_deleted', 'created_at'], name='chat_msg_channel_unread_idx'),
        ]

    def __str__(self):
        if self.channel:
            return f"{self.sender.name} in {self.channel.name}: {self.message[:50]}"
        elif self.committee:
            return f"{self.sender.name} in {self.committee.code}: {self.message[:50]}"
        return f"{self.sender.name}: {self.message[:50]}"


class ChatReadReceipt(models.Model):
    """Track last read message per user per channel"""
    user = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, related_name='chat_receipts')

    # New channel-based system
    channel = models.ForeignKey(ChatChannel, on_delete=models.CASCADE, related_name='read_receipts', null=True, blank=True)

    # Legacy committee field (will be deprecated after migration)
    committee = models.ForeignKey('Committee', on_delete=models.CASCADE, related_name='read_receipts', null=True, blank=True)

    last_read_message = models.ForeignKey(ChatMessage, on_delete=models.SET_NULL, null=True, blank=True)
    last_read_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'channel'],
                name='unique_user_channel',
                condition=models.Q(channel__isnull=False)
            ),
            models.UniqueConstraint(
                fields=['user', 'committee'],
                name='unique_user_committee',
                condition=models.Q(committee__isnull=False)
            ),
        ]

    def __str__(self):
        if self.channel:
            return f"{self.user.name} - {self.channel.name}"
        elif self.committee:
            return f"{self.user.name} - {self.committee.code}"
        return f"{self.user.name}"

    def get_unread_count(self):
        """Get number of unread messages in this channel/committee"""
        if self.channel:
            if not self.last_read_message:
                return self.channel.messages.filter(is_deleted=False).count()

            return self.channel.messages.filter(
                created_at__gt=self.last_read_message.created_at,
                is_deleted=False
            ).count()
        elif self.committee:
            # Legacy support
            if not self.last_read_message:
                return self.committee.chat_messages.filter(is_deleted=False).count()

            return self.committee.chat_messages.filter(
                created_at__gt=self.last_read_message.created_at,
                is_deleted=False
            ).count()
        return 0

class ChatNotificationPreference(models.Model):
    """Per-user, per-channel notification level preference."""

    LEVEL_ALL = 'all'
    LEVEL_MENTIONS = 'mentions'
    LEVEL_NONE = 'none'

    NOTIFICATION_LEVELS = [
        (LEVEL_ALL, 'All Messages'),
        (LEVEL_MENTIONS, '@Mentions Only'),
        (LEVEL_NONE, 'None'),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='chat_notification_prefs',
    )
    channel = models.ForeignKey(
        ChatChannel,
        on_delete=models.CASCADE,
        related_name='notification_prefs',
    )
    level = models.CharField(
        max_length=10,
        choices=NOTIFICATION_LEVELS,
        default=LEVEL_ALL,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'channel'],
                name='unique_user_channel_notif_pref',
            )
        ]

    def __str__(self):
        return f"{self.user.name} — #{self.channel.name}: {self.level}"
