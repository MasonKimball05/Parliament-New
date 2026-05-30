from django.db import models


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

    # Icon/color for customization
    icon = models.CharField(max_length=10, default='💬')
    color = models.CharField(max_length=7, default='#003DA5')  # Hex color

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    def has_access(self, user, admin_override=False):
        """Check if user has access to this channel"""
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
            # Check if user has guest permission with can_read=True
            return ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_read=True
            ).exists()

        if self.access_type == 'restricted':
            # Check custom permissions - must have can_read=True
            return ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_read=True
            ).exists() or ChatChannelPermission.objects.filter(
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
            # Check for explicit permission with can_read=True
            perm = ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_read=True
            ).first()
            return perm is not None

        return False

    def can_write(self, user):
        """Check if user can send messages in this channel"""
        if not self.is_active:
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
            # Check for explicit permission with can_write=True
            perm = ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_write=True
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
            # Check for explicit permission with can_delete=True
            perm = ChatChannelPermission.objects.filter(
                channel=self,
                user=user,
                can_delete=True
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

    # Read/Write permissions for guest users (non-committee members)
    can_read = models.BooleanField(default=True, help_text='User can read messages in this channel')
    can_write = models.BooleanField(default=True, help_text='User can send messages in this channel')
    can_delete = models.BooleanField(default=False, help_text='User can delete their own messages in this channel')

    created_at = models.DateTimeField(auto_now_add=True)

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
