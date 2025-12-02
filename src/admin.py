from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from .decorators import log_function_call
from .models import Committee, ParliamentUser, Legislation, Vote, Attendance
import logging
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.http import HttpResponse
import csv
from django.contrib.auth.decorators import user_passes_test
import os
from django.contrib.admin.models import LogEntry
from django.urls import reverse
from django.shortcuts import redirect
from django.contrib.auth import login

logger = logging.getLogger('admin_actions')

@receiver(post_save, sender=LogEntry)
def log_admin_action(sender, instance, created, **kwargs):
    if created:
        logger.info(
            f"Admin Action: {instance.get_change_message()} | User: {instance.user} | Model: {instance.content_type} | Action: {instance.get_action_flag_display()} | Sender: {instance.object_repr}"
        )

# === SIGNALS ===

@receiver(post_save, sender=ParliamentUser)
def log_user_created(sender, instance, created, **kwargs):
    if created:
        logger.info(f"Created {sender.__name__} with ID {instance.user_id}")

@receiver(pre_delete, sender=ParliamentUser)
def log_user_deleted(sender, instance, **kwargs):
    logger.info(f"Deleted {sender.__name__} with ID {instance.user_id}")


# === CUSTOM ACTIONS ===

def export_as_csv(modeladmin, request, queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="export.csv"'
    writer = csv.writer(response)

    fields = [field.name for field in queryset.model._meta.fields]
    writer.writerow(fields)

    for obj in queryset:
        row = [getattr(obj, field) for field in fields]
        writer.writerow(row)

    return response

export_as_csv.short_description = "Export selected as CSV"

def remove_passed_legislation(modeladmin, request, queryset):
    queryset.update(status='removed')
remove_passed_legislation.short_description = "Remove selected passed legislation"

def update_status(modeladmin, request, queryset):
    for legislation in queryset:
        if legislation.voting_closed:
            votes = Vote.objects.filter(legislation=legislation)
            yes_votes = votes.filter(vote_choice='yes').count()
            no_votes = votes.filter(vote_choice='no').count()
            total_votes = yes_votes + no_votes
            if total_votes > 0:
                yes_pct = (yes_votes / total_votes) * 100
                if yes_pct >= legislation.required_percentage:
                    legislation.status = 'passed'
                else:
                    legislation.status = 'removed'
                legislation.save()
update_status.short_description = "Update status for closed voting legislation"


# === MODEL ADMINS ===
@log_function_call
@admin.register(ParliamentUser)
class ParliamentUserAdmin(admin.ModelAdmin):
    list_display = ('name', 'user_id', 'member_type', 'is_admin', 'member_status', 'login_as_link')
    search_fields = ('name', 'user_id', 'member_type', 'is_admin')
    actions = [export_as_csv]

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('login-as-<int:user_id>/', self.admin_site.admin_view(self.login_as_user), name='login_as_user'),
        ]
        return custom_urls + urls

    def login_as_link(self, obj):
        url = reverse('admin:login_as_user', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">Login As User</a>',
            url,
        )
    login_as_link.short_description = 'Login As'
    login_as_link.allow_tags = True

    def login_as_user(self, request, user_id):
        logger = logging.getLogger('function_calls')
        User = get_user_model()
        requesting_user = User.objects.get(pk=request.user.pk)

        if not request.user.is_authenticated or not request.user.is_admin:
            messages.error(request, 'You are not an admin')
            return redirect('/admin/')

        logger.info(f"User {request.user} attempted to login as user id ({user_id})")

        try:
            user = ParliamentUser.objects.get(pk=user_id)
            login(request, user)

            logger.info(f"{requesting_user} logged in as {user.username}")

            messages.success(request, f'You are now logged in as {user.name}')

            return redirect('home')
        except ParliamentUser.DoesNotExist:
            messages.error(request, 'User not found')
            return redirect('/admin/')


@admin.register(Legislation)
class LegislationAdmin(admin.ModelAdmin):
    list_display = ('title', 'status', 'required_percentage', 'voting_closed', 'anonymous_vote')
    list_filter = ('voting_closed', 'status', 'anonymous_vote')
    search_fields = ('title',)
    actions = [export_as_csv, update_status, remove_passed_legislation]

    def has_delete_permission(self, request, obj=None):
        # Prevent deleting passed legislation
        if obj and obj.status == 'passed':
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ('user', 'legislation', 'vote_choice')
    search_fields = ('user__name', 'legislation__title')
    list_filter = ('vote_choice', 'legislation')


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('user', 'date', 'present', 'created_at')
    search_fields = ('user__name',)
    list_filter = ('present', 'date')
    actions = [export_as_csv]

@admin.register(Committee)
class CommitteeAdmin(admin.ModelAdmin):
    list_display = ('name', 'chair_list', 'member_count', 'created_at')
    search_fields = ('name', 'chair__name')
    filter_horizontal = ('members',)
    ordering = ('name',)

    def member_count(self, obj):
        return obj.members.count()
    member_count.short_description = 'Members'

"""
@admin.register(LogEntry)
class LogEntryAdmin(admin.ModelAdmin):
    list_display = ('user', 'action', 'date', 'reason')
    search_fields = ('user__name',)
    list_filter = ('action', 'date')
    actions = ['export_as_csv']
"""

# === VIEW LOGS IN ADMIN ===

from django.urls import path
from django.utils.html import format_html
from django.shortcuts import render

@user_passes_test(lambda u: hasattr(u, 'is_admin') and u.is_admin)
def view_logs(request):
    log_path = os.path.join('logs', 'django_actions.log')
    logs = []

    try:
        with open(log_path, 'r') as f:
            for line in f.readlines()[-200:][::-1]:  # Show last 200 lines, most recent first
                logs.append(line.strip())
    except Exception as e:
        logs.append(f"Error reading log file: {e}")

    return render(request, 'admin/view_logs.html', {
        'logs': logs,
        'title': 'View Logs',
    })

@user_passes_test(lambda u: hasattr(u, 'is_admin') and u.is_admin)
def view_error_logs(request):
    log_path = os.path.join('logs', 'django_errors.log')
    logs = []

    try:
        with open(log_path, 'r') as f:
            for line in f.readlines()[-200:][::-1]:
                logs.append(line.strip())
    except Exception as e:
        logs.append(f"Error reading log file: {e}")

    return render(request, 'admin/view_error_logs.html', {
        'logs': logs,
        'title': 'View Error Logs',
    })


original_get_urls = admin.site.get_urls

def custom_admin_urls():
    return [path('view-logs/', view_logs, name="view_logs")] + original_get_urls()

admin.site.get_urls = custom_admin_urls
