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
    stats = {**_sched_agg, **_log_agg}

    context = {
        'schedules': schedules,
        'recent_logs': recent_logs,
        'stats': stats,
    }

    return render(request, 'admin_v2/notifications/dashboard.html', context)


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

    return render(request, 'admin_v2/notifications/schedules.html', context)


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
    required = ['name', 'notification_type', 'message_template']
    for field in required:
        if not data.get(field):
            return JsonResponse({
                'success': False,
                'error': f'{field} is required'
            }, status=400)

    # Create the schedule
    schedule = NotificationSchedule.objects.create(
        name=data['name'],
        notification_type=data['notification_type'],
        description=data.get('description', ''),
        hours_before=data.get('hours_before', 24),
        send_email=data.get('send_email', True),
        send_in_app=data.get('send_in_app', True),
        target_audience=data.get('target_audience', 'all_active'),
        target_committee_id=data.get('target_committee_id'),
        message_template=data['message_template'],
        email_subject_template=data.get('email_subject_template', ''),
        is_active=data.get('is_active', True),
        created_by=request.user,
    )

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

    # Update fields
    if 'name' in data:
        schedule.name = data['name']
    if 'notification_type' in data:
        schedule.notification_type = data['notification_type']
    if 'description' in data:
        schedule.description = data['description']
    if 'hours_before' in data:
        schedule.hours_before = data['hours_before']
    if 'send_email' in data:
        schedule.send_email = data['send_email']
    if 'send_in_app' in data:
        schedule.send_in_app = data['send_in_app']
    if 'target_audience' in data:
        schedule.target_audience = data['target_audience']
    if 'target_committee_id' in data:
        schedule.target_committee_id = data['target_committee_id'] or None
    if 'message_template' in data:
        schedule.message_template = data['message_template']
    if 'email_subject_template' in data:
        schedule.email_subject_template = data['email_subject_template']
    if 'is_active' in data:
        schedule.is_active = data['is_active']

    schedule.save()

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
    schedule.is_active = not schedule.is_active
    schedule.save()

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
    name = schedule.name
    schedule.delete()

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

    if status:
        logs = logs.filter(status=status)
    if notification_type:
        logs = logs.filter(notification_type=notification_type)
    if date_from:
        logs = logs.filter(created_at__date__gte=date_from)
    if date_to:
        logs = logs.filter(created_at__date__lte=date_to)

    # Pagination
    page = int(request.GET.get('page', 1))
    per_page = 50
    total = logs.count()
    logs = logs[(page - 1) * per_page:page * per_page]

    # Stats
    stats = NotificationLog.objects.aggregate(
        total=Count('id'),
        sent=Count('id', filter=Q(status='sent')),
        failed=Count('id', filter=Q(status='failed')),
        pending=Count('id', filter=Q(status='pending')),
    )

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

    return render(request, 'admin_v2/notifications/logs.html', context)


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

    return render(request, 'admin_v2/notifications/log_detail.html', context)
