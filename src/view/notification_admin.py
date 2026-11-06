"""
Notification Administration Views

Admin dashboard for managing notification schedules and viewing logs.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.db.models import Count, Q
import json

from src.models import NotificationSchedule, NotificationLog, Committee
from src.decorators import admin_required
from src.view.admin_v2 import require_admin_v2_auth
from src.models.users import member_defer


# ── Schedules: shared by these views and the app (src/mobile/views_site_admin_config.py) ──

#: The fields a schedule's create/update can set, in the order the page's form has them.
#: `send_at_time` is the model's optional time of day; the page's form has no input for it,
#: so it never sends one (the app does).
SCHEDULE_FIELDS = (
    'name', 'notification_type', 'description', 'hours_before', 'send_at_time', 'send_email',
    'send_in_app', 'target_audience', 'target_committee_id', 'message_template',
    'email_subject_template', 'is_active',
)
SCHEDULE_REQUIRED = ('name', 'notification_type', 'message_template')
#: What the page's create used when a field was left out.
SCHEDULE_CREATE_DEFAULTS = {
    'description': '', 'hours_before': 24, 'send_at_time': None, 'send_email': True,
    'send_in_app': True, 'target_audience': 'all_active', 'target_committee_id': None,
    'email_subject_template': '', 'is_active': True,
}

#: Placeholders `fire_scheduled_notifications` (src/tasks/notifications.py) fills in, by type.
#: dues_reminder and custom schedules aren't sent by it yet.
SCHEDULE_PLACEHOLDERS = {
    'event_reminder': ('event_name', 'event_date', 'event_time', 'event_location'),
    'attendance_reminder': ('event_name', 'event_date', 'event_time', 'event_location'),
    'vote_reminder': ('legislation_title', 'vote_deadline', 'vote_deadline_date', 'vote_deadline_time'),
}

#: hours_before for the app's form: the page's input has min="1"; a year is plenty.
SCHEDULE_HOURS_MAX = 24 * 365


class ScheduleInputError(ValueError):
    """A schedule field the app sent that the page's form would not have allowed."""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field
        self.message = message


def schedule_missing_field(data):
    """The first required field that is missing or empty (the page's check), else None."""
    for field in SCHEDULE_REQUIRED:
        if not data.get(field):
            return field
    return None


def create_notification_schedule(user, data):
    """Create a schedule from `data` (the page's create: defaults for what's left out)."""
    values = {**SCHEDULE_CREATE_DEFAULTS,
              **{k: data[k] for k in SCHEDULE_FIELDS if k in data}}
    return NotificationSchedule.objects.create(created_by=user, **values)


def update_notification_schedule(schedule, data):
    """Set the fields present in `data` and save (the page's update)."""
    for field in SCHEDULE_FIELDS:
        if field in data:
            value = data[field]
            if field == 'target_committee_id':
                value = value or None
            setattr(schedule, field, value)
    schedule.save()
    return schedule


def toggle_notification_schedule(schedule):
    schedule.is_active = not schedule.is_active
    schedule.save()
    return schedule.is_active


def delete_notification_schedule(schedule):
    """Delete it; returns its name. Its sent logs stay (NotificationLog.schedule is SET_NULL)."""
    name = schedule.name
    schedule.delete()
    return name


def _choice(value, choices, field, message):
    if value not in {c for c, _ in choices}:
        raise ScheduleInputError(field, message)
    return value


def _clean_text(data, field, max_len, required=False, label=None):
    value = data.get(field, '')
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise ScheduleInputError(field, f'{label or field} must be text.')
    value = value.strip()
    if required and not value:
        raise ScheduleInputError(field, f'{label or field} is required.')
    if len(value) > max_len:
        raise ScheduleInputError(field, f'{label or field} can be at most {max_len} characters.')
    return value


def _clean_bool(data, field, label):
    value = data.get(field)
    if not isinstance(value, bool):
        raise ScheduleInputError(field, f'Say whether to {label}.')
    return value


def clean_schedule_input(data, schedule=None):
    """
    The app's schedule form, checked the way the page's form checks it in the
    browser (required fields, a type and audience from the lists, at least 1
    hour) plus what the page's server took on trust: the value types, the field
    lengths, a real committee for a committee audience, and the time of day.

    `schedule` set → an update: only the fields present are checked and
    returned. Raises ScheduleInputError(field, message).
    """
    from django.utils.dateparse import parse_time

    if not isinstance(data, dict):
        raise ScheduleInputError(None, 'Send the schedule as an object.')
    partial = schedule is not None
    out = {}

    def wanted(field):
        return not partial or field in data

    if wanted('name'):
        out['name'] = _clean_text(data, 'name', 100, required=True, label='Name')
    if wanted('notification_type'):
        out['notification_type'] = _choice(data.get('notification_type'),
                                           NotificationSchedule.NOTIFICATION_TYPE_CHOICES,
                                           'notification_type', 'Choose a type.')
    if wanted('description'):
        out['description'] = _clean_text(data, 'description', 2000, label='Description')
    if wanted('hours_before'):
        hours = data.get('hours_before', 24 if not partial else None)
        if isinstance(hours, bool) or not isinstance(hours, int):
            raise ScheduleInputError('hours_before', 'Hours before must be a whole number.')
        if not 1 <= hours <= SCHEDULE_HOURS_MAX:
            raise ScheduleInputError('hours_before', f'Hours before must be from 1 to {SCHEDULE_HOURS_MAX}.')
        out['hours_before'] = hours
    if wanted('send_at_time'):
        raw = data.get('send_at_time')
        if raw in (None, ''):
            out['send_at_time'] = None
        else:
            try:
                parsed = parse_time(raw) if isinstance(raw, str) else None
            except ValueError:  # well formed but out of range (25:00)
                parsed = None
            if parsed is None:
                raise ScheduleInputError('send_at_time', 'The time of day must look like 09:00.')
            out['send_at_time'] = parsed.replace(second=0, microsecond=0)
    for field, label in (('send_email', 'send an email'), ('send_in_app', 'send an in-app notification'),
                         ('is_active', 'turn it on')):
        if wanted(field):
            out[field] = _clean_bool(data, field, label) if field in data else True
    if wanted('target_audience'):
        out['target_audience'] = _choice(data.get('target_audience', 'all_active'),
                                         NotificationSchedule.TARGET_AUDIENCE_CHOICES,
                                         'target_audience', 'Choose who it goes to.')
    if wanted('message_template'):
        out['message_template'] = _clean_text(data, 'message_template', 5000, required=True,
                                              label='Message')
    if wanted('email_subject_template'):
        out['email_subject_template'] = _clean_text(data, 'email_subject_template', 200,
                                                    label='Email subject')

    # The committee: required (and real) for a committee audience, cleared otherwise.
    audience = out.get('target_audience', schedule.target_audience if partial else 'all_active')
    if audience == 'committee':
        raw = data.get('target_committee_id') if 'target_committee_id' in data else (
            schedule.target_committee_id if partial else None)
        if isinstance(raw, bool) or not isinstance(raw, int) or not Committee.objects.filter(pk=raw).exists():
            raise ScheduleInputError('target_committee_id', 'Choose the committee it goes to.')
        out['target_committee_id'] = raw
    elif 'target_audience' in out or 'target_committee_id' in data:
        out['target_committee_id'] = None
    return out


def notification_dashboard_stats():
    """The Notifications dashboard's four numbers (v4.4.5: shared with the app)."""
    # Get stats — two aggregate() calls instead of four separate COUNT queries
    _sched_agg = NotificationSchedule.objects.aggregate(
        total_schedules=Count('id'),
        active_schedules=Count('id', filter=Q(is_active=True)),
    )
    _log_agg = NotificationLog.objects.filter(
        # v3.17.4: `__date` on a DateTimeField is evaluated in the CURRENT
        # timezone, so it must be compared against the local date — a UTC date
        # counted the wrong day's logs all evening.
        created_at__date=timezone.localdate()
    ).aggregate(
        logs_today=Count('id'),
        failed_today=Count('id', filter=Q(status='failed')),
    )
    return {**_sched_agg, **_log_agg}


def notification_log_stats():
    """The Notification logs page's totals by status (v4.4.5: shared)."""
    return NotificationLog.objects.aggregate(
        total=Count('id'),
        sent=Count('id', filter=Q(status='sent')),
        failed=Count('id', filter=Q(status='failed')),
        pending=Count('id', filter=Q(status='pending')),
    )


def filter_notification_logs(logs, status=None, notification_type=None, date_from=None, date_to=None):
    """The Notification logs page's filters (dates as YYYY-MM-DD, local days). v4.4.5: shared."""
    if status:
        logs = logs.filter(status=status)
    if notification_type:
        logs = logs.filter(notification_type=notification_type)
    if date_from:
        logs = logs.filter(created_at__date__gte=date_from)
    if date_to:
        logs = logs.filter(created_at__date__lte=date_to)
    return logs


@login_required
@require_admin_v2_auth
def notification_dashboard(request):
    """
    Main notification settings dashboard.
    Shows overview of schedules and recent logs.
    """
    # Get all schedules
    # v3.17.3: neither `target_committee` nor `created_by` is read by
    # dashboard.html — both joins were pure cost.
    schedules = NotificationSchedule.objects.all()

    # Get recent logs
    recent_logs = NotificationLog.objects.all()[:20]

    stats = notification_dashboard_stats()

    context = {
        'schedules': schedules,
        'recent_logs': recent_logs,
        'stats': stats,
    }

    from src.admin_console import console_enabled
    return render(request, 'admin_v2/console/notifications.html' if console_enabled(request)
                  else 'admin_v2/notifications/dashboard.html', context)


@login_required
@require_admin_v2_auth
def notification_schedules(request):
    """
    Manage notification schedules.
    """
    # v3.17.3: same as the dashboard — schedules.html reads neither relation.
    schedules = NotificationSchedule.objects.all().order_by(
        'notification_type', 'name')

    committees = Committee.objects.filter(is_active=True)

    context = {
        'schedules': schedules,
        'committees': committees,
        'notification_types': NotificationSchedule.NOTIFICATION_TYPE_CHOICES,
        'target_audiences': NotificationSchedule.TARGET_AUDIENCE_CHOICES,
    }

    from src.admin_console import console_enabled
    return render(request, 'admin_v2/console/notification_schedules.html' if console_enabled(request)
                  else 'admin_v2/notifications/schedules.html', context)


@login_required
@require_admin_v2_auth
@require_POST
def create_schedule(request):
    """
    Create a new notification schedule.
    """
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    # Validate required fields
    missing = schedule_missing_field(data)
    if missing:
        return JsonResponse({
            'success': False,
            'error': f'{missing} is required'
        }, status=400)

    # Create the schedule
    schedule = create_notification_schedule(request.user, data)

    return JsonResponse({
        'success': True,
        'schedule_id': schedule.id,
        'message': f'Schedule "{schedule.name}" created successfully.'
    })


@login_required
@require_admin_v2_auth
@require_POST
def update_schedule(request, schedule_id):
    """
    Update an existing notification schedule.
    """
    schedule = get_object_or_404(NotificationSchedule, id=schedule_id)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON'}, status=400)

    update_notification_schedule(schedule, data)

    return JsonResponse({
        'success': True,
        'message': f'Schedule "{schedule.name}" updated successfully.'
    })


@login_required
@require_admin_v2_auth
@require_POST
def toggle_schedule(request, schedule_id):
    """
    Toggle a schedule's active status.
    """
    schedule = get_object_or_404(NotificationSchedule, id=schedule_id)
    toggle_notification_schedule(schedule)

    return JsonResponse({
        'success': True,
        'is_active': schedule.is_active,
        'message': f'Schedule "{schedule.name}" {"activated" if schedule.is_active else "deactivated"}.'
    })


@login_required
@require_admin_v2_auth
@require_POST
def delete_schedule(request, schedule_id):
    """
    Delete a notification schedule.
    """
    schedule = get_object_or_404(NotificationSchedule, id=schedule_id)
    name = delete_notification_schedule(schedule)

    return JsonResponse({
        'success': True,
        'message': f'Schedule "{name}" deleted successfully.'
    })


@login_required
@require_admin_v2_auth
def notification_logs(request):
    """
    View notification logs with filtering.
    """
    # v3.17.3: `schedule` was joined and never read by logs.html.
    logs = NotificationLog.objects.all()

    # Filtering
    status = request.GET.get('status')
    notification_type = request.GET.get('type')
    date_from = request.GET.get('from')
    date_to = request.GET.get('to')

    logs = filter_notification_logs(logs, status, notification_type, date_from, date_to)

    # Pagination
    page = int(request.GET.get('page', 1))
    per_page = 50
    total = logs.count()
    logs = logs[(page - 1) * per_page:page * per_page]

    # Stats
    stats = notification_log_stats()

    context = {
        'logs': logs,
        'stats': stats,
        'current_page': page,
        'total_pages': (total + per_page - 1) // per_page,
        'total_count': total,
        'filters': {
            'status': status,
            'type': notification_type,
            'from': date_from,
            'to': date_to,
        },
        'status_choices': NotificationLog.STATUS_CHOICES,
        'type_choices': NotificationSchedule.NOTIFICATION_TYPE_CHOICES,
    }

    from src.admin_console import console_enabled
    return render(request, 'admin_v2/console/notification_logs.html' if console_enabled(request)
                  else 'admin_v2/notifications/logs.html', context)


@login_required
@require_admin_v2_auth
def notification_log_detail(request, log_id):
    """
    View details of a specific notification log.
    """
    log = get_object_or_404(NotificationLog, id=log_id)

    context = {
        'log': log,
    }

    # Console: ?panel=1 is the slide-out fragment (same gate).
    from src.admin_console import console_enabled, panel_response, wants_panel
    if wants_panel(request):
        return panel_response(render(request, 'admin_v2/console/notification_log_panel.html', context))
    return render(request, 'admin_v2/console/notification_log_detail.html' if console_enabled(request)
                  else 'admin_v2/notifications/log_detail.html', context)
