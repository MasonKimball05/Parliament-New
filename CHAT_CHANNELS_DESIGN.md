# Chat Channels System Design

## Overview
Expand the chat system to support:
- Committee chats (existing)
- Custom admin-created channels with role-based permissions
- Chat menu/index page showing all accessible chats with unread counts

---

## Database Models

### ChatChannel Model
Represents any chat channel (committee or custom).

```python
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

    def has_access(self, user):
        """Check if user has access to this channel"""
        if not self.is_active:
            return False

        # Admins always have access
        if user.is_admin:
            return True

        if self.access_type == 'open':
            return True

        if self.access_type == 'committee' and self.committee:
            return self.committee.is_member(user)

        if self.access_type == 'restricted':
            # Check custom permissions
            return ChatChannelPermission.objects.filter(
                channel=self,
                user=user
            ).exists() or ChatChannelPermission.objects.filter(
                channel=self,
                member_type=user.member_type
            ).exists() or (
                ChatChannelPermission.objects.filter(
                    channel=self,
                    chairs_only=True
                ).exists() and user.chair_roles.exists()
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


class ChatChannelPermission(models.Model):
    """Defines who has access to a restricted channel"""

    MEMBER_TYPES = [
        ('Active Member', 'Active Member'),
        ('Officer', 'Officer'),
        ('Alumni', 'Alumni'),
        ('New Member', 'New Member'),
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

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [
            ['channel', 'user'],
            ['channel', 'member_type'],
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
```

### Update ChatMessage Model
```python
class ChatMessage(models.Model):
    """Chat messages - now linked to channels instead of committees"""

    # OLD: committee = models.ForeignKey(Committee, on_delete=models.CASCADE, related_name='chat_messages')
    # NEW:
    channel = models.ForeignKey(ChatChannel, on_delete=models.CASCADE, related_name='messages')

    sender = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, related_name='sent_messages')
    message = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['channel', '-created_at']),
        ]
```

### Update ChatReadReceipt Model
```python
class ChatReadReceipt(models.Model):
    """Track last read message per user per channel"""

    user = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, related_name='chat_receipts')
    # OLD: committee = models.ForeignKey(Committee, on_delete=models.CASCADE, related_name='read_receipts')
    # NEW:
    channel = models.ForeignKey(ChatChannel, on_delete=models.CASCADE, related_name='read_receipts')

    last_read_message = models.ForeignKey(ChatMessage, on_delete=models.SET_NULL, null=True, blank=True)
    last_read_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['user', 'channel']
```

---

## Views

### Chat Index/Menu Page
```python
@login_required
def chat_index(request):
    """Show all accessible chat channels with unread counts"""
    user = request.user

    # Get all channels user has access to
    accessible_channels = []

    # Committee chats
    committee_channels = ChatChannel.objects.filter(
        channel_type='committee',
        is_active=True
    ).select_related('committee')

    for channel in committee_channels:
        if channel.has_access(user):
            accessible_channels.append({
                'channel': channel,
                'unread_count': channel.get_unread_count(user),
                'type': 'committee'
            })

    # Custom channels
    custom_channels = ChatChannel.objects.filter(
        channel_type='custom',
        is_active=True
    )

    for channel in custom_channels:
        if channel.has_access(user):
            accessible_channels.append({
                'channel': channel,
                'unread_count': channel.get_unread_count(user),
                'type': 'custom'
            })

    # Sort by unread count (most unread first), then name
    accessible_channels.sort(key=lambda x: (-x['unread_count'], x['channel'].name))

    return render(request, 'chat/index.html', {
        'channels': accessible_channels,
        'is_admin': user.is_admin
    })
```

### Create Custom Channel
```python
@admin_required
def create_channel(request):
    """Admin-only: Create custom chat channel"""
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        access_type = request.POST.get('access_type', 'restricted')
        icon = request.POST.get('icon', '💬')
        color = request.POST.get('color', '#003DA5')

        # Create channel
        channel = ChatChannel.objects.create(
            name=name,
            description=description,
            channel_type='custom',
            access_type=access_type,
            created_by=request.user,
            icon=icon,
            color=color
        )

        # Add permissions if restricted
        if access_type == 'restricted':
            # Specific users
            user_ids = request.POST.getlist('users')
            for user_id in user_ids:
                ChatChannelPermission.objects.create(
                    channel=channel,
                    user_id=user_id
                )

            # Member types
            member_types = request.POST.getlist('member_types')
            for member_type in member_types:
                ChatChannelPermission.objects.create(
                    channel=channel,
                    member_type=member_type
                )

            # Special roles
            if request.POST.get('chairs_only'):
                ChatChannelPermission.objects.create(
                    channel=channel,
                    chairs_only=True
                )

            if request.POST.get('officers_only'):
                ChatChannelPermission.objects.create(
                    channel=channel,
                    officers_only=True
                )

        messages.success(request, f'Channel "{name}" created successfully!')
        return redirect('chat_index')

    # GET: Show form
    all_users = ParliamentUser.objects.filter(member_status='Active').order_by('name')

    return render(request, 'chat/create_channel.html', {
        'all_users': all_users,
        'member_types': ChatChannelPermission.MEMBER_TYPES
    })
```

---

## Templates

### Chat Index Page (`templates/chat/index.html`)
```html
{% extends "base.html" %}

{% block title %}Chats{% endblock %}

{% block content %}
<div class="container mx-auto px-4 py-8">
    <div class="flex justify-between items-center mb-6">
        <h1 class="text-3xl font-bold">Your Chats</h1>
        {% if is_admin %}
            <a href="{% url 'create_channel' %}" class="bg-blue-600 text-white px-4 py-2 rounded hover:bg-blue-700">
                + Create Channel
            </a>
        {% endif %}
    </div>

    {% if channels %}
        <div class="grid gap-4">
            {% for item in channels %}
                {% with channel=item.channel unread=item.unread_count %}
                    <a href="{% url 'channel_chat' channel.id %}"
                       class="bg-white rounded-lg shadow-md p-4 hover:shadow-lg transition flex items-center justify-between">
                        <div class="flex items-center gap-4">
                            <div class="text-4xl">{{ channel.icon }}</div>
                            <div>
                                <h3 class="text-lg font-semibold">{{ channel.name }}</h3>
                                {% if channel.description %}
                                    <p class="text-sm text-gray-600">{{ channel.description }}</p>
                                {% endif %}
                                {% if item.type == 'committee' %}
                                    <span class="text-xs text-gray-500">Committee Chat</span>
                                {% endif %}
                            </div>
                        </div>

                        {% if unread > 0 %}
                            <div class="bg-red-500 text-white text-sm font-bold rounded-full w-8 h-8 flex items-center justify-center">
                                {{ unread }}
                            </div>
                        {% endif %}
                    </a>
                {% endwith %}
            {% endfor %}
        </div>
    {% else %}
        <div class="bg-white rounded-lg shadow-md p-8 text-center">
            <p class="text-gray-500">No chats available</p>
        </div>
    {% endif %}
</div>
{% endblock %}
```

---

## Migration Strategy

Since we're changing the structure significantly:

1. **Create new models** (ChatChannel, ChatChannelPermission)
2. **Add channel field to ChatMessage** (nullable at first)
3. **Data migration**: Create ChatChannel for each Committee, link existing messages
4. **Update views** to use channels
5. **Remove old committee field** from ChatMessage after migration

---

## URL Structure

```python
# Chat menu/index
path('chats/', chat_index, name='chat_index'),

# Channel chat (generic - works for all channel types)
path('chat/<int:channel_id>/', channel_chat, name='channel_chat'),

# Admin channel management
path('chats/create/', create_channel, name='create_channel'),
path('chats/<int:channel_id>/edit/', edit_channel, name='edit_channel'),
path('chats/<int:channel_id>/delete/', delete_channel, name='delete_channel'),

# Keep old committee URLs for backward compatibility
path('committee/<str:code>/chat/', committee_chat_redirect, name='committee_chat'),
```

---

## Implementation Steps

1. Add ChatChannel and ChatChannelPermission models
2. Create migration that auto-creates channels for existing committees
3. Update ChatMessage and ChatReadReceipt models
4. Create chat index view and template
5. Create channel creation view and template
6. Update existing chat views to work with channels
7. Add navigation links to chat index

Ready to implement?
