# Parliament - Upcoming Features

**Status:** Planning / Research Phase
**Last Updated:** February 28, 2026

---

## Ideas Under Consideration

### GroupMe Bot Integration for Announcements

**Status:** Researching

Exploring the possibility of using a GroupMe bot to send automated notifications to the chapter's announcements channel instead of SMS text reminders.

---

## Implementation Outline

### 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                      Parliament App                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐   │
│  │ Announcement │    │    Event     │    │  Legislation │   │
│  │   Created    │    │   Created    │    │    Vote      │   │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘   │
│         │                   │                   │           │
│         └───────────────────┼───────────────────┘           │
│                             ▼                               │
│                 ┌───────────────────────┐                   │
│                 │  Notification Service │                   │
│                 │  (New Django App)     │                   │
│                 └───────────┬───────────┘                   │
│                             │                               │
│         ┌───────────────────┼───────────────────┐           │
│         ▼                   ▼                   ▼           │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐      │
│  │  GroupMe    │    │   Email     │    │  In-App     │      │
│  │   Bot API   │    │  (Existing) │    │ (Existing)  │      │
│  └─────────────┘    └─────────────┘    └─────────────┘      │
│                                                             │
└─────────────────────────────────────────────────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  GroupMe API    │
                    │  POST /v3/bots  │
                    └─────────────────┘
                             │
                             ▼
                    ┌─────────────────┐
                    │  Announcements  │
                    │    Channel      │
                    └─────────────────┘
```

---

### 2. Database Models

```python
# New models in src/models.py

class GroupMeBot(models.Model):
    """Store GroupMe bot configuration"""
    name = models.CharField(max_length=100)  # e.g., "Announcements Bot"
    bot_id = models.CharField(max_length=100)  # From GroupMe
    group_id = models.CharField(max_length=100)  # GroupMe group ID
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(ParliamentUser, on_delete=models.SET_NULL, null=True)

    # What this bot handles
    send_announcements = models.BooleanField(default=True)
    send_event_reminders = models.BooleanField(default=True)
    send_vote_reminders = models.BooleanField(default=True)
    send_attendance_reminders = models.BooleanField(default=False)


class GroupMeMessage(models.Model):
    """Log of messages sent via GroupMe"""
    bot = models.ForeignKey(GroupMeBot, on_delete=models.CASCADE)
    message_type = models.CharField(max_length=50)  # announcement, event, vote, etc.
    content = models.TextField()
    related_object_type = models.CharField(max_length=50, blank=True)  # Announcement, Event, etc.
    related_object_id = models.IntegerField(null=True, blank=True)
    sent_at = models.DateTimeField(auto_now_add=True)
    sent_by = models.ForeignKey(ParliamentUser, on_delete=models.SET_NULL, null=True)
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True)


class NotificationSchedule(models.Model):
    """Schedule for automated reminders"""
    name = models.CharField(max_length=100)
    notification_type = models.CharField(max_length=50, choices=[
        ('event_reminder', 'Event Reminder'),
        ('vote_reminder', 'Vote Reminder'),
        ('attendance_reminder', 'Attendance Reminder'),
    ])
    # When to send (relative to event)
    hours_before = models.IntegerField(default=24)  # e.g., 24 hours before event
    is_active = models.BooleanField(default=True)
    send_to_groupme = models.BooleanField(default=True)
    send_to_email = models.BooleanField(default=False)
    message_template = models.TextField()  # With placeholders like {event_name}, {time}
```

---

### 3. Admin Dashboard Pages

#### 3.1 Main Integration Settings (`/admin-v2/integrations/`)

```
┌─────────────────────────────────────────────────────────────┐
│  Integrations                                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  GroupMe Bot                              [Active ●]│    │
│  │  Connected to: Beta Theta Pi Announcements          │    │
│  │  Messages sent today: 3                             │    │
│  │                                  [Configure] [Logs] │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  Email Notifications                    [Active ●]  │    │
│  │  Provider: SendGrid                                 │    │
│  │  Emails sent today: 12                              │    │
│  │                                  [Configure] [Logs] │    │
│  └─────────────────────────────────────────────────────┘    │ 
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 3.2 GroupMe Bot Configuration (`/admin-v2/integrations/groupme/`)

```
┌─────────────────────────────────────────────────────────────┐
│  GroupMe Bot Configuration                                  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Bot Status: ● Active                    [Test Message]     │
│                                                             │
│  ┌─ Connection Settings ─────────────────────────────────┐  │
│  │  Bot ID:     [••••••••••••••••••••]    [Show/Hide]    │  │
│  │  Group ID:   [12345678]                               │  │
│  │  Group Name: Beta Theta Pi Announcements (read-only)  │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─ Notification Types ──────────────────────────────────┐  │
│  │  [✓] Send new announcements                           │  │
│  │  [✓] Send event reminders                             │  │
│  │  [✓] Send vote reminders                              │  │
│  │  [ ] Send attendance reminders                        │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─ Message Formatting ──────────────────────────────────┐  │
│  │  Include Parliament link: [✓]                         │  │
│  │  Use emoji indicators:    [✓]                         │  │
│  │  Preview:                                             │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │ New Announcement                                │  │  │
│  │  │ Title: Chapter Meeting Agenda                   │  │  │
│  │  │ View: parliament.example.com/announcements/123  │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│                             [Cancel] [Save Configuration]   │
└─────────────────────────────────────────────────────────────┘
```

#### 3.3 Notification Schedules (`/admin-v2/integrations/schedules/`)

```
┌─────────────────────────────────────────────────────────────┐
│  Notification Schedules                     [+ New Schedule]│
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─ Event Reminders ─────────────────────────────────────┐  │
│  │  ● 24 hours before    [GroupMe ✓] [Email ✓]  [Edit]   │  │
│  │  ● 1 hour before      [GroupMe ✓] [Email ○]  [Edit]   │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─ Vote Reminders ──────────────────────────────────────┐  │
│  │  ● Daily at 9am (if pending votes) [GroupMe ✓] [Edit] │  │
│  │  ● 2 hours before close            [GroupMe ✓] [Edit] │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 3.4 Message Log (`/admin-v2/integrations/groupme/logs/`)

```
┌─────────────────────────────────────────────────────────────┐
│  GroupMe Message Log                   [Export] [Clear Old] │
├─────────────────────────────────────────────────────────────┤
│  Filter: [All Types ▼] [Last 7 days ▼] [All Status ▼]       │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ✓  Feb 17, 3:45 PM  │ Announcement │ "Chapter Meeting..."  │
│  ✓  Feb 17, 2:30 PM  │ Event        │ "Reminder: Social..." │
│  ✓  Feb 17, 9:00 AM  │ Vote         │ "3 pending votes..."  │
│  ✗  Feb 16, 4:00 PM  │ Announcement │ "Failed: Rate limit"  │
│                                                             │
│  Showing 1-20 of 156                       [← Prev] [Next →]│
└─────────────────────────────────────────────────────────────┘
```

---

### 4. Integration Points (Existing Code Changes)

#### 4.1 When Creating Announcements

```python
# In src/view/officer/manage_announcements.py

def create_announcement(request):
    # ... existing code ...

    if form.is_valid():
        announcement = form.save()

        # NEW: Send to GroupMe if enabled
        if request.POST.get('send_to_groupme'):
            from src.services.notifications import send_groupme_notification
            send_groupme_notification(
                notification_type='announcement',
                title=announcement.title,
                url=request.build_absolute_uri(announcement.get_absolute_url()),
                sent_by=request.user
            )
```

#### 4.2 When Creating Events

```python
# In src/view/officer/manage_events.py

def create_event(request):
    # ... existing code ...

    if form.is_valid():
        event = form.save()

        # NEW: Send to GroupMe if enabled
        if request.POST.get('notify_groupme'):
            from src.services.notifications import send_groupme_notification
            send_groupme_notification(
                notification_type='event',
                title=event.title,
                event_date=event.start_datetime,
                url=request.build_absolute_uri(reverse('calendar')),
                sent_by=request.user
            )
```

#### 4.3 Scheduled Tasks (Celery or Django-Q)

```python
# src/tasks/notification_tasks.py

from celery import shared_task

@shared_task
def send_scheduled_reminders():
    """Run every hour to check for scheduled reminders"""
    from src.services.notifications import check_and_send_reminders
    check_and_send_reminders()

@shared_task
def send_daily_vote_reminder():
    """Run daily at 9am to remind about pending votes"""
    from src.services.notifications import send_vote_reminder_if_needed
    send_vote_reminder_if_needed()
```

---

### 5. Notification Service

```python
# src/services/notifications.py

import requests
from django.conf import settings
from src.models import GroupMeBot, GroupMeMessage

GROUPME_API_URL = "https://api.groupme.com/v3/bots/post"

def send_groupme_notification(notification_type, title, url=None, event_date=None, sent_by=None):
    """Send a notification to GroupMe"""

    bot = GroupMeBot.objects.filter(is_active=True).first()
    if not bot:
        return False

    # Check if this notification type is enabled
    if notification_type == 'announcement' and not bot.send_announcements:
        return False
    if notification_type == 'event' and not bot.send_event_reminders:
        return False
    if notification_type == 'vote' and not bot.send_vote_reminders:
        return False

    # Build message
    message = format_notification_message(notification_type, title, url, event_date)

    # Send to GroupMe
    try:
        response = requests.post(GROUPME_API_URL, json={
            "bot_id": bot.bot_id,
            "text": message
        }, timeout=10)

        success = response.status_code == 202

        # Log the message
        GroupMeMessage.objects.create(
            bot=bot,
            message_type=notification_type,
            content=message,
            sent_by=sent_by,
            success=success,
            error_message="" if success else response.text
        )

        return success

    except Exception as e:
        GroupMeMessage.objects.create(
            bot=bot,
            message_type=notification_type,
            content=message,
            sent_by=sent_by,
            success=False,
            error_message=str(e)
        )
        return False


def format_notification_message(notification_type, title, url=None, event_date=None):
    """Format the message based on type"""

    if notification_type == 'announcement':
        msg = f"📢 New Announcement\n{title}"
    elif notification_type == 'event':
        date_str = event_date.strftime("%A, %B %d at %I:%M %p") if event_date else ""
        msg = f"📅 New Event\n{title}\n{date_str}"
    elif notification_type == 'vote':
        msg = f"🗳️ Vote Reminder\n{title}"
    elif notification_type == 'event_reminder':
        msg = f"⏰ Reminder\n{title} is coming up!"
    else:
        msg = title

    if url:
        msg += f"\n\n{url}"

    return msg
```

---

### 6. UI Changes to Existing Forms

#### 6.1 Announcement Form - Add GroupMe Toggle

```html
<!-- In announcement creation form -->
<div class="flex items-center mt-4">
    <input type="checkbox" name="send_to_groupme" id="send_to_groupme" checked
           class="rounded border-gray-300">
    <label for="send_to_groupme" class="ml-2 text-sm text-gray-700">
        Send to GroupMe announcements channel
    </label>
</div>
```

#### 6.2 Event Form - Add GroupMe Toggle

```html
<!-- In event creation form -->
<div class="flex items-center mt-4">
    <input type="checkbox" name="notify_groupme" id="notify_groupme" checked
           class="rounded border-gray-300">
    <label for="notify_groupme" class="ml-2 text-sm text-gray-700">
        Notify GroupMe when event is created
    </label>
</div>
```

---

### 7. Implementation Effort Estimate

| Component | Effort | Priority |
|-----------|--------|----------|
| **Database Models** | 2-3 hours | High |
| GroupMeBot, GroupMeMessage, NotificationSchedule models | | |
| **Notification Service** | 3-4 hours | High |
| Core send logic, message formatting, error handling | | |
| **Admin Dashboard - Main Page** | 2-3 hours | Medium |
| Integration overview, status indicators | | |
| **Admin Dashboard - Bot Config** | 3-4 hours | High |
| Bot settings, test message, connection management | | |
| **Admin Dashboard - Schedules** | 4-5 hours | Medium |
| Schedule CRUD, template editor, preview | | |
| **Admin Dashboard - Message Log** | 2-3 hours | Low |
| Log viewer, filtering, export | | |
| **Existing Form Updates** | 2-3 hours | High |
| Add toggles to announcement/event forms | | |
| **Scheduled Tasks Setup** | 2-3 hours | Medium |
| Celery/Django-Q configuration, task definitions | | |
| **Testing & Documentation** | 3-4 hours | High |
| Unit tests, integration tests, setup docs | | |

**Total Estimate: 23-32 hours**

---

### 8. Setup Requirements

1. **GroupMe Developer Account**
   - Register at dev.groupme.com
   - Create a bot associated with the announcements group
   - Obtain Bot ID

2. **Environment Variables**
   ```
   GROUPME_BOT_ID=your_bot_id_here
   ```

3. **Dependencies**
   ```
   pip install requests  # Already likely installed
   ```

4. **Optional: Background Tasks**
   - Celery + Redis for scheduled reminders
   - OR Django-Q for simpler setup
   - OR cron job hitting a management command

---

### 9. Rollout Plan

**Phase 1: Basic Integration (MVP)**
- Bot configuration storage
- Manual send from admin
- Announcement notifications only
- Basic logging

**Phase 2: Full Automation**
- Event creation notifications
- Scheduled reminders
- Vote reminders
- Full dashboard

**Phase 3: Polish**
- Message templates/customization
- Analytics/reporting
- User preferences for notification channels

---

## Planned Features

*No features confirmed yet for v2.8.0*

---

## Notes

This changelog tracks ideas and features being considered for future releases. Items here are not committed to and may change or be removed based on research and feedback.
