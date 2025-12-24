# Chat System Design - Polling-based with Committee Channels

## Overview
Polling-based chat system where users can chat in committee-specific channels. Only members of a committee can see/send messages in that committee's channel.

---

## 1. Database Models

### ChatMessage Model
```python
class ChatMessage(models.Model):
    """Individual chat messages in committee channels"""
    committee = models.ForeignKey('Committee', on_delete=models.CASCADE, related_name='chat_messages')
    sender = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, related_name='sent_messages')
    message = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False)  # Soft delete for "Message deleted" placeholder

    class Meta:
        ordering = ['-created_at']  # Most recent first
        indexes = [
            models.Index(fields=['committee', '-created_at']),
        ]

    def __str__(self):
        return f"{self.sender.name} in {self.committee.code}: {self.message[:50]}"
```

### ChatReadReceipt Model (Optional - for "unread" badges)
```python
class ChatReadReceipt(models.Model):
    """Track last read message per user per committee"""
    user = models.ForeignKey('ParliamentUser', on_delete=models.CASCADE, related_name='chat_receipts')
    committee = models.ForeignKey('Committee', on_delete=models.CASCADE, related_name='read_receipts')
    last_read_message = models.ForeignKey('ChatMessage', on_delete=models.SET_NULL, null=True, blank=True)
    last_read_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['user', 'committee']

    def get_unread_count(self):
        """Get number of unread messages in this committee"""
        if not self.last_read_message:
            return self.committee.chat_messages.filter(is_deleted=False).count()

        return self.committee.chat_messages.filter(
            created_at__gt=self.last_read_message.created_at,
            is_deleted=False
        ).count()
```

---

## 2. URL Structure

```python
# In src/urls.py
path('committee/<str:code>/chat/', committee_chat, name='committee_chat'),
path('api/committee/<str:code>/chat/messages/', get_chat_messages, name='get_chat_messages'),
path('api/committee/<str:code>/chat/send/', send_chat_message, name='send_chat_message'),
path('api/committee/<str:code>/chat/delete/<int:message_id>/', delete_chat_message, name='delete_chat_message'),
```

---

## 3. Views

### Main Chat Page View
```python
@login_required
def committee_chat(request, code):
    """Main chat page for a committee"""
    committee = get_object_or_404(Committee, code=code)

    # Check if user is member of committee
    if not committee.is_member(request.user) and not request.user.is_admin:
        return HttpResponseForbidden("You must be a member of this committee to access chat.")

    # Get initial messages (last 50)
    messages = ChatMessage.objects.filter(
        committee=committee,
        is_deleted=False
    ).select_related('sender').order_by('-created_at')[:50]

    # Get or create read receipt
    receipt, created = ChatReadReceipt.objects.get_or_create(
        user=request.user,
        committee=committee
    )

    return render(request, 'committee/chat.html', {
        'committee': committee,
        'initial_messages': reversed(messages),  # Oldest first for display
        'is_chair': committee.is_chair(request.user),
        'is_admin': request.user.is_admin,
    })
```

### API: Get New Messages (Polling Endpoint)
```python
@login_required
def get_chat_messages(request, code):
    """API endpoint to get new messages since a given timestamp"""
    committee = get_object_or_404(Committee, code=code)

    # Check membership
    if not committee.is_member(request.user) and not request.user.is_admin:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    # Get 'since' parameter (timestamp of last message)
    since = request.GET.get('since')

    if since:
        messages = ChatMessage.objects.filter(
            committee=committee,
            created_at__gt=since,
            is_deleted=False
        ).select_related('sender').order_by('created_at')
    else:
        # Return last 50 messages if no timestamp provided
        messages = ChatMessage.objects.filter(
            committee=committee,
            is_deleted=False
        ).select_related('sender').order_by('-created_at')[:50]
        messages = reversed(messages)

    data = [{
        'id': msg.id,
        'sender_name': msg.sender.name,
        'sender_id': msg.sender.user_id,
        'message': msg.message,
        'created_at': msg.created_at.isoformat(),
        'is_own_message': msg.sender == request.user,
    } for msg in messages]

    return JsonResponse({'messages': data})
```

### API: Send Message
```python
@login_required
@require_http_methods(["POST"])
def send_chat_message(request, code):
    """API endpoint to send a new message"""
    committee = get_object_or_404(Committee, code=code)

    # Check membership
    if not committee.is_member(request.user) and not request.user.is_admin:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    message_text = request.POST.get('message', '').strip()

    if not message_text:
        return JsonResponse({'error': 'Message cannot be empty'}, status=400)

    if len(message_text) > 2000:
        return JsonResponse({'error': 'Message too long (max 2000 characters)'}, status=400)

    # Create the message
    message = ChatMessage.objects.create(
        committee=committee,
        sender=request.user,
        message=message_text
    )

    return JsonResponse({
        'success': True,
        'message': {
            'id': message.id,
            'sender_name': message.sender.name,
            'sender_id': message.sender.user_id,
            'message': message.message,
            'created_at': message.created_at.isoformat(),
            'is_own_message': True,
        }
    })
```

### API: Delete Message
```python
@login_required
@require_http_methods(["POST"])
def delete_chat_message(request, code, message_id):
    """API endpoint to delete a message (soft delete)"""
    committee = get_object_or_404(Committee, code=code)
    message = get_object_or_404(ChatMessage, id=message_id, committee=committee)

    # Only sender, chair, or admin can delete
    is_sender = message.sender == request.user
    is_chair = committee.is_chair(request.user)
    is_admin = request.user.is_admin

    if not (is_sender or is_chair or is_admin):
        return JsonResponse({'error': 'Forbidden'}, status=403)

    message.is_deleted = True
    message.save()

    return JsonResponse({'success': True})
```

---

## 4. Template Structure

### Main Chat Page (`templates/committee/chat.html`)

Key elements:
- Header with committee name
- Chat container (scrollable area showing messages)
- Message input form at bottom
- JavaScript for polling and sending messages

```html
{% extends "base.html" %}

{% block title %}{{ committee.name }} - Chat{% endblock %}

{% block content %}
<div class="container mx-auto px-4 py-8 h-screen flex flex-col">
    <!-- Header -->
    <div class="mb-4">
        <a href="{% url 'committee_home' committee.code %}" class="text-blue-600 hover:text-blue-800">
            ← Back to {{ committee.code }}
        </a>
        <h2 class="text-3xl font-bold mt-2">{{ committee.name }} Chat</h2>
        <p class="text-gray-600">{{ committee.members.count }} members</p>
    </div>

    <!-- Chat Messages Container -->
    <div id="chat-container" class="flex-1 bg-white rounded-lg shadow-md p-4 overflow-y-auto mb-4">
        <div id="messages-list">
            {% for msg in initial_messages %}
                {% include 'committee/chat_message.html' %}
            {% endfor %}
        </div>
    </div>

    <!-- Message Input -->
    <div class="bg-white rounded-lg shadow-md p-4">
        <form id="message-form" class="flex gap-2">
            {% csrf_token %}
            <input
                type="text"
                id="message-input"
                name="message"
                placeholder="Type a message..."
                maxlength="2000"
                class="flex-1 border border-gray-300 rounded px-4 py-2 focus:outline-none focus:border-blue-500"
                autocomplete="off"
            >
            <button
                type="submit"
                class="bg-blue-500 text-white px-6 py-2 rounded hover:bg-blue-600"
            >
                Send
            </button>
        </form>
        <p class="text-xs text-gray-500 mt-2">
            <span id="char-count">0</span>/2000 characters
        </p>
    </div>
</div>

<script>
    const committeeCode = "{{ committee.code }}";
    const currentUserId = {{ request.user.user_id }};
    let lastMessageTimestamp = null;
    let isPolling = false;

    // Initialize with last message timestamp from initial messages
    {% if initial_messages %}
        const messages = {{ initial_messages|safe }};  // Would need to JSON serialize this
        if (messages.length > 0) {
            lastMessageTimestamp = messages[messages.length - 1].created_at;
        }
    {% endif %}

    // Poll for new messages every 3 seconds
    setInterval(pollForMessages, 3000);

    function pollForMessages() {
        if (isPolling) return;
        isPolling = true;

        let url = `/api/committee/${committeeCode}/chat/messages/`;
        if (lastMessageTimestamp) {
            url += `?since=${encodeURIComponent(lastMessageTimestamp)}`;
        }

        fetch(url)
            .then(response => response.json())
            .then(data => {
                if (data.messages && data.messages.length > 0) {
                    data.messages.forEach(msg => {
                        appendMessage(msg);
                        lastMessageTimestamp = msg.created_at;
                    });
                    scrollToBottom();
                }
            })
            .catch(error => console.error('Error polling messages:', error))
            .finally(() => {
                isPolling = false;
            });
    }

    // Send message
    document.getElementById('message-form').addEventListener('submit', function(e) {
        e.preventDefault();

        const input = document.getElementById('message-input');
        const message = input.value.trim();

        if (!message) return;

        const formData = new FormData();
        formData.append('message', message);
        formData.append('csrfmiddlewaretoken', '{{ csrf_token }}');

        fetch(`/api/committee/${committeeCode}/chat/send/`, {
            method: 'POST',
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                input.value = '';
                updateCharCount();
                // Message will appear via polling
            } else {
                alert(data.error || 'Failed to send message');
            }
        })
        .catch(error => {
            console.error('Error sending message:', error);
            alert('Failed to send message');
        });
    });

    // Append message to chat
    function appendMessage(msg) {
        const messagesList = document.getElementById('messages-list');

        // Check if message already exists
        if (document.getElementById(`msg-${msg.id}`)) {
            return;
        }

        const timestamp = new Date(msg.created_at).toLocaleTimeString('en-US', {
            hour: 'numeric',
            minute: '2-digit'
        });

        // Get initials for avatar
        const nameParts = msg.sender_name.split(' ');
        const initials = nameParts.length >= 2
            ? nameParts[0][0] + nameParts[nameParts.length - 1][0]
            : nameParts[0][0];

        const messageDiv = document.createElement('div');
        messageDiv.id = `msg-${msg.id}`;
        messageDiv.className = `flex gap-3 p-2 rounded hover:bg-gray-50 ${msg.is_own_message ? 'bg-blue-50' : ''}`;

        messageDiv.innerHTML = `
            <div class="w-9 h-9 rounded text-white flex items-center justify-center font-bold text-sm flex-shrink-0" style="background-color: ${msg.is_own_message ? '#CD8C95' : '#003DA5'}">
                ${initials.toUpperCase()}
            </div>
            <div class="flex-1 min-w-0">
                <div class="flex items-baseline gap-2 mb-1">
                    <span class="font-bold text-gray-900">${msg.sender_name}</span>
                    <span class="text-xs text-gray-500">${timestamp}</span>
                </div>
                <div class="text-gray-900">${escapeHtml(msg.message)}</div>
            </div>
        `;

        messagesList.appendChild(messageDiv);
    }

    // Auto-scroll to bottom
    function scrollToBottom() {
        const container = document.getElementById('chat-container');
        container.scrollTop = container.scrollHeight;
    }

    // Character counter
    const messageInput = document.getElementById('message-input');
    messageInput.addEventListener('input', updateCharCount);

    function updateCharCount() {
        const count = messageInput.value.length;
        document.getElementById('char-count').textContent = count;
    }

    // HTML escape
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Initial scroll
    scrollToBottom();
</script>
{% endblock %}
```

### Message Partial (`templates/committee/chat_message.html`)
```html
<div id="msg-{{ msg.id }}" class="flex gap-3 p-2 rounded hover:bg-gray-50 {% if msg.sender == request.user %}bg-pink-50{% endif %}">
    <!-- Avatar with initials (Beta colors: Pink #CD8C95 for own, Blue #003DA5 for others) -->
    <div class="w-9 h-9 rounded text-white flex items-center justify-center font-bold text-sm flex-shrink-0" style="background-color: {% if msg.sender == request.user %}#CD8C95{% else %}#003DA5{% endif %}">
        {% with first=msg.sender.name|first last=msg.sender.name|split:' '|last|first %}
            {{ first|upper }}{{ last|upper }}
        {% endwith %}
    </div>

    <!-- Message content -->
    <div class="flex-1 min-w-0">
        <div class="flex items-baseline gap-2 mb-1">
            <span class="font-bold text-gray-900">{{ msg.sender.name }}</span>
            <span class="text-xs text-gray-500">{{ msg.created_at|date:"g:i A" }}</span>
        </div>
        <div class="text-gray-900">{{ msg.message }}</div>
    </div>
</div>
```

**Color Scheme (Beta Theta Pi):**
- Beta Blue: `#003DA5` - Used for other users' avatars and primary buttons
- Beta Pink: `#CD8C95` - Used for your own avatar
- Light pink background: `#f7f2f4` - Subtle highlight for your own messages

**Note:** You may need to add a custom template filter for splitting names:
```python
# In src/templatetags/custom_filters.py
@register.filter
def split(value, arg):
    return value.split(arg)
```

**Future Enhancement Note:** Profile pictures can be added later by:
1. Adding `profile_picture` field to ParliamentUser model (ImageField)
2. Updating avatar div to show `<img>` if profile picture exists, otherwise show initials
3. Adding upload functionality in user profile settings

---

## 5. Adding to Committee Pages

### Update Committee Home/Detail Page

Add a "Chat" button/tab:

```html
<a href="{% url 'committee_chat' committee.code %}" class="bg-green-500 text-white px-4 py-2 rounded hover:bg-green-600 flex items-center">
    <svg class="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z"></path>
    </svg>
    Chat
    {% if unread_count %}
        <span class="ml-2 bg-red-500 text-white text-xs rounded-full px-2 py-0.5">{{ unread_count }}</span>
    {% endif %}
</a>
```

---

## 6. Migration Plan

1. **Create models** - Add ChatMessage and ChatReadReceipt to `src/models.py`
2. **Run migrations** - `python manage.py makemigrations && python manage.py migrate`
3. **Create views** - Add all chat views to `src/view/committee/chat.py`
4. **Add URLs** - Update `src/urls.py`
5. **Create templates** - Add chat templates
6. **Test locally** - Test with multiple users/browsers
7. **Deploy** - Push to production

---

## 7. Message Retention & Storage Management

### Option A: Time-based Auto-cleanup (Recommended)

Delete messages older than 90 days automatically.

**Django Management Command** (`src/management/commands/cleanup_old_chat_messages.py`):
```python
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
from src.models import ChatMessage

class Command(BaseCommand):
    help = 'Delete chat messages older than 90 days'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=90,
            help='Number of days to keep messages (default: 90)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting'
        )

    def handle(self, *args, **options):
        days = options['days']
        dry_run = options['dry_run']

        cutoff_date = timezone.now() - timedelta(days=days)

        old_messages = ChatMessage.objects.filter(created_at__lt=cutoff_date)
        count = old_messages.count()

        if dry_run:
            self.stdout.write(
                self.style.WARNING(f'[DRY RUN] Would delete {count} messages older than {days} days')
            )
        else:
            old_messages.delete()
            self.stdout.write(
                self.style.SUCCESS(f'Deleted {count} messages older than {days} days')
            )
```

**Run manually:**
```bash
python manage.py cleanup_old_chat_messages
```

**Run automatically with cron** (on server):
```bash
# Edit crontab
crontab -e

# Add line to run cleanup daily at 3am
0 3 * * * cd /var/www/Parliament-New && /var/www/Parliament-New/.venv/bin/python manage.py cleanup_old_chat_messages
```

**Shell script** (`shell/cleanup_chat.sh`):
```bash
#!/bin/bash
# Cleanup old chat messages

source $(dirname $0)/colors.sh

DAYS=${1:-90}

echo -e "${YELLOW}Cleaning up chat messages older than $DAYS days...${NC}"

python manage.py cleanup_old_chat_messages --days $DAYS

echo -e "${GREEN}Done!${NC}"
```

---

### Option B: Storage Size Cap (MB-based)

Limit total chat storage to a specific size (e.g., 50 MB), delete oldest messages when exceeded.

**Management Command** (`src/management/commands/cleanup_chat_by_size.py`):
```python
from django.core.management.base import BaseCommand
from django.db import connection
from src.models import ChatMessage

class Command(BaseCommand):
    help = 'Delete oldest chat messages if total storage exceeds limit'

    def add_arguments(self, parser):
        parser.add_argument(
            '--max-mb',
            type=int,
            default=50,
            help='Maximum MB to keep (default: 50)'
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be deleted without actually deleting'
        )

    def handle(self, *args, **options):
        max_mb = options['max_mb']
        dry_run = options['dry_run']
        max_bytes = max_mb * 1024 * 1024  # Convert MB to bytes

        # Get total size of ChatMessage table
        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT pg_total_relation_size('src_chatmessage')
            """)
            total_size = cursor.fetchone()[0]

        total_mb = total_size / (1024 * 1024)

        self.stdout.write(f'Current chat storage: {total_mb:.2f} MB')

        if total_size <= max_bytes:
            self.stdout.write(
                self.style.SUCCESS(f'Under limit ({max_mb} MB). No cleanup needed.')
            )
            return

        # Calculate how much to delete (delete 20% extra to avoid running constantly)
        target_size = max_bytes * 0.8
        bytes_to_delete = total_size - target_size

        # Estimate: average message is ~200 bytes
        estimated_messages_to_delete = int(bytes_to_delete / 200)

        if dry_run:
            self.stdout.write(
                self.style.WARNING(
                    f'[DRY RUN] Would delete approximately {estimated_messages_to_delete} '
                    f'oldest messages to get under {max_mb} MB'
                )
            )
        else:
            # Delete oldest messages
            old_messages = ChatMessage.objects.order_by('created_at')[:estimated_messages_to_delete]
            count = old_messages.count()
            old_messages.delete()

            # Check new size
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT pg_total_relation_size('src_chatmessage')
                """)
                new_size = cursor.fetchone()[0]

            new_mb = new_size / (1024 * 1024)

            self.stdout.write(
                self.style.SUCCESS(
                    f'Deleted {count} messages. '
                    f'New size: {new_mb:.2f} MB (was {total_mb:.2f} MB)'
                )
            )
```

**Run in cron** (daily at 3am):
```bash
0 3 * * * cd /var/www/Parliament-New && /var/www/Parliament-New/.venv/bin/python manage.py cleanup_chat_by_size --max-mb 50
```

**Pros:** Limits actual disk space used
**Cons:** Requires database-specific queries (shown for PostgreSQL)

---

### Option C: Message Count Cap Per Committee

Limit each committee to max 1000 messages, delete oldest when exceeded.

**Add to ChatMessage model**:
```python
class ChatMessage(models.Model):
    # ... existing fields ...

    MAX_MESSAGES_PER_COMMITTEE = 1000

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        # After saving, check if we need to cleanup old messages
        total_messages = ChatMessage.objects.filter(
            committee=self.committee
        ).count()

        if total_messages > self.MAX_MESSAGES_PER_COMMITTEE:
            # Delete oldest messages to stay under cap
            excess = total_messages - self.MAX_MESSAGES_PER_COMMITTEE
            old_messages = ChatMessage.objects.filter(
                committee=self.committee
            ).order_by('created_at')[:excess]

            old_messages.delete()
```

**Pros:** Automatic, no cron job needed, database-agnostic
**Cons:** Slightly slower message sending (needs to count/delete on each message)

---

### Option D: Hybrid Approach

Combine multiple limits:
- Keep messages for 90 days (time-based)
- AND enforce 50 MB total storage limit (size-based)
- Run both cleanup commands daily

**Cron job:**
```bash
# Run both cleanups daily at 3am
0 3 * * * cd /var/www/Parliament-New && /var/www/Parliament-New/.venv/bin/python manage.py cleanup_old_chat_messages --days 90
5 3 * * * cd /var/www/Parliament-New && /var/www/Parliament-New/.venv/bin/python manage.py cleanup_chat_by_size --max-mb 50
```

**Why both:**
- Time-based ensures old messages are removed even if under size limit
- Size-based ensures storage never exceeds limit even with heavy use
- Double protection

---

### Recommended Approaches:

**For your use case (MB-based focus):**

**Option A + Option B** (Time + Size limits)
- Run both cleanup commands daily
- Set 90-day retention (cleans up by age)
- Set 50 MB max storage (cleans up by size)
- Whichever limit is hit first triggers cleanup

**Why this combo:**
- Time limit: Prevents indefinite data retention
- Size limit: Hard cap on storage usage (what you want)
- Both are cron jobs (no performance impact)
- Easy to adjust either limit

**Storage Reality:**
- Average message: ~200 bytes (text + metadata)
- 50 MB limit = ~250,000 messages total across ALL committees
- That's years of chat history even with very active committees
- For comparison: A single uploaded PDF is often 1-5 MB

**Recommended limits:**
- Time: 90 days (3 months of history)
- Size: 25-50 MB (plenty of room, won't impact your storage budget)

---

## 8. Future Enhancements

- **Typing indicators** - "User is typing..."
- **Message reactions** - 👍 emoji reactions
- **File attachments** - Share images/PDFs in chat
- **Search messages** - Search chat history
- **@mentions** - Notify specific users
- **Pin messages** - Important announcements
- **Export chat history** - Download before auto-deletion
- **WebSocket upgrade** - True real-time when ready
- **Embed GIFS** - Allow for gifs and gif links to be sent and embed to play the animation

---

## Estimated Implementation Time

- Models & Migrations: 30 minutes
- Views (API + main page): 1-2 hours
- Templates & JavaScript: 2-3 hours
- Cleanup management command: 20 minutes
- Testing & Polish: 1 hour

**Total: ~5-7 hours** for a working committee chat system with auto-cleanup
