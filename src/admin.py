from django.contrib import admin, messages
from django.contrib.auth import get_user_model
from .decorators import log_function_call
from .models import (
    Committee, ParliamentUser, Legislation, Vote, Attendance, AttendanceExcuse,
    CommitteeDocument, Role, Announcement, ChatChannel, ChatChannelPermission,
    ChatMessage, ChatReadReceipt, UserAnnouncementView, DocumentTag, DocumentVersion,
    Event, ActivityLog, LoginHistory, LoginAlert, BugReport, Notification,
    IPWhitelist, IPBlacklist, QuarantinedAccount, HoneypotAccess, SystemLockdown,
    SecurityNotificationLog, UserWatchFlag, LoginLockout, KaiReport, KaiReportTemplate,
    PassedResolution, UserSession, AnnouncementEmailLog, ChapterMinutes,
    SlatingPeriod, SlatingApplication,
)
from .models_feature_flags import FeatureFlag, PageToggle, ScheduledMaintenance
import logging
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
from django.http import HttpResponse
import csv
from django.contrib.auth.decorators import user_passes_test
import os
from django.contrib.admin.models import LogEntry
from django.urls import reverse, path
from django.shortcuts import redirect, render
from django.contrib.auth import login
from django.utils.html import format_html
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.timezone import localtime

logger = logging.getLogger('admin_actions')

# === CUSTOM ADMIN SITE ===
class ParliamentAdminSite(admin.AdminSite):
    site_header = "Parliament Administration"
    site_title = "Parliament Admin"
    index_title = "Chapter Management Dashboard"

    def index(self, request, extra_context=None):
        """Custom admin index with dashboard stats"""
        extra_context = extra_context or {}

        # Get quick stats
        total_members = ParliamentUser.objects.filter(member_status='Active').count()
        total_officers = ParliamentUser.objects.filter(member_type='Officer').count()
        total_committees = Committee.objects.count()
        active_legislation = Legislation.objects.filter(voting_closed=False).count()
        upcoming_events = Event.objects.filter(
            is_active=True,
            archived=False,
            date_time__gte=timezone.now()
        ).count()
        unread_announcements = Announcement.objects.filter(is_active=True).count()

        # Recent activity (last 7 days)
        from datetime import timedelta
        week_ago = timezone.now() - timedelta(days=7)
        recent_logins = ActivityLog.objects.filter(
            action_type='login',
            timestamp__gte=week_ago
        ).count()

        # Security stats
        new_security_alerts = LoginAlert.objects.filter(status='new').count()
        high_severity_alerts = LoginAlert.objects.filter(
            severity__in=['high', 'critical'],
            status__in=['new', 'investigating']
        ).count()

        extra_context.update({
            'total_members': total_members,
            'total_officers': total_officers,
            'total_committees': total_committees,
            'active_legislation': active_legislation,
            'upcoming_events': upcoming_events,
            'unread_announcements': unread_announcements,
            'recent_logins': recent_logins,
            'new_security_alerts': new_security_alerts,
            'high_severity_alerts': high_severity_alerts,
        })

        return super().index(request, extra_context)

# Use custom admin site
admin_site = ParliamentAdminSite(name='parliament_admin')
admin.site = admin_site

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

def remove_profile_pictures(modeladmin, request, queryset):
    """Admin action to remove profile pictures from selected users"""
    count = 0
    for user in queryset:
        if user.profile_picture:
            user.profile_picture.delete()
            user.profile_picture_removed_by_admin = True
            user.save()
            count += 1
            logger.info(f"Admin {request.user.username} removed profile picture for {user.username}")

    if count > 0:
        messages.success(request, f"Successfully removed {count} profile picture(s). Users will be notified.")
    else:
        messages.info(request, "No users in the selection had profile pictures to remove.")
remove_profile_pictures.short_description = "Remove profile pictures (users will be notified)"


# === MODEL ADMINS ===
# === ROLE ADMIN ===

# Inline admin for assigning members to roles
class RoleMemberInline(admin.TabularInline):
    model = ParliamentUser.roles.through
    extra = 1
    verbose_name = "Member with this Role"
    verbose_name_plural = "Members with this Role"
    
    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "parliamentuser":
            kwargs["queryset"] = ParliamentUser.objects.filter(member_status="Active").order_by("name")
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

@admin.register(Role, site=admin_site)
class RoleAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'one_per_chapter', 'member_count')
    search_fields = ('name', 'code', 'description')
    list_filter = ('one_per_chapter',)
    ordering = ('name',)
    inlines = [RoleMemberInline]
    list_per_page = 50

    fieldsets = (
        ('Role Information', {
            'fields': ('name', 'code', 'description', 'one_per_chapter')
        }),
    )

    def member_count(self, obj):
        count = obj.parliamentuser_set.count()
        return f"{count} member{'s' if count != 1 else ''}"
    member_count.short_description = 'Members'


@log_function_call
@admin.register(ParliamentUser, site=admin_site)
class ParliamentUserAdmin(admin.ModelAdmin):
    list_display = ('name', 'user_id', 'role_number', 'email', 'member_type', 'is_admin', 'member_status', 'role_list', 'last_login_display', 'login_as_link')
    search_fields = ('name', 'user_id', 'email', 'username', 'role_number')  # Enable autocomplete
    filter_horizontal = ('roles',)
    list_filter = ('member_type', 'member_status', 'is_admin', 'roles', 'has_default_password', 'email_flagged', 'is_quarantined')
    list_per_page = 50
    readonly_fields = ('user_id',)

    def get_form(self, request, obj=None, **kwargs):
        form_class = super().get_form(request, obj, **kwargs)
        if obj is not None:
            # When editing an existing user, ensure validate_unique properly excludes
            # the current instance. AbstractBaseUser subclasses can trigger false
            # "already exists" errors on username/email when _state.adding is incorrect.
            class ParliamentUserChangeForm(form_class):
                def validate_unique(self):
                    self.instance._state.adding = False
                    super().validate_unique()
            return ParliamentUserChangeForm
        return form_class

    fieldsets = (
        ('Personal Information', {
            'fields': ('username', 'name', 'preferred_name', 'user_id', 'email', 'phone_number',)
        }),
        ('Member Information', {
            'fields': ('member_type', 'member_status', 'role_number', 'is_admin', 'is_active', 'anonymous_vote', 'allow_abstain')
        }),
        ('Profile Picture', {
            'fields': ('profile_picture', 'profile_picture_removed_by_admin'),
            'description': 'View user profile picture. Use the "Remove profile pictures" action to remove inappropriate images.'
        }),
        ('Roles & Positions', {
            'fields': ('roles',),
            'description': 'Assign officer roles to this member (e.g., Vice President of Brotherhood)'
        }),
        ('Account Details', {
            'fields': ('last_login', 'password', 'force_password_change', 'has_default_password'),
            'classes': ('collapse',)
        }),
        ('Email Status', {
            'fields': ('email_flagged', 'email_flagged_reason', 'email_flagged_at'),
            'classes': ('collapse',),
            'description': 'Fields set automatically when email delivery fails.'
        }),
        ('Security', {
            'fields': ('is_quarantined',),
            'classes': ('collapse',)
        }),
    )

    def role_list(self, obj):
        roles = obj.roles.all()[:3]
        if not roles:
            return '-'
        role_str = ', '.join([role.name for role in roles])
        if obj.roles.count() > 3:
            role_str += f' (+{obj.roles.count() - 3} more)'
        return role_str
    role_list.short_description = 'Roles'

    def last_login_display(self, obj):
        if obj.last_login:
            return localtime(obj.last_login).strftime('%m/%d/%Y %I:%M %p')
        return 'Never'
    last_login_display.short_description = 'Last Login'
    last_login_display.admin_order_field = 'last_login'

    actions = [export_as_csv, remove_profile_pictures]

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('login-as-<str:user_id>/', self.admin_site.admin_view(self.login_as_user), name='login_as_user'),
            path('migrate-user-id/<str:user_id>/', self.admin_site.admin_view(self.migrate_user_id_view), name='migrate_user_id'),
        ]
        return custom_urls + urls

    def login_as_link(self, obj):
        login_url = reverse('admin:login_as_user', args=[obj.pk])
        migrate_url = reverse('admin:migrate_user_id', args=[obj.pk])
        return format_html(
            '<a class="button" href="{}">Login As User</a>&nbsp;&nbsp;'
            '<a class="button" href="{}" style="background:#ba2121;">Migrate User ID</a>',
            login_url, migrate_url,
        )
    login_as_link.short_description = 'Actions'
    login_as_link.allow_tags = True

    def migrate_user_id_view(self, request, user_id):
        from django.db import transaction
        from src.models import UserPreferences

        if not request.user.is_admin:
            messages.error(request, 'Admin access required.')
            return redirect('/admin/')

        old_user = ParliamentUser.objects.filter(pk=user_id).first()
        if not old_user:
            messages.error(request, f'User with ID "{user_id}" not found.')
            return redirect('/admin/src/parliamentuser/')

        error = None
        if request.method == 'POST':
            new_user_id = request.POST.get('new_user_id', '').strip()
            if not new_user_id:
                error = 'New user ID cannot be empty.'
            elif new_user_id == user_id:
                error = 'New user ID must be different from the current one.'
            elif ParliamentUser.objects.filter(pk=new_user_id).exists():
                error = f'A user with ID "{new_user_id}" already exists.'
            else:
                try:
                    with transaction.atomic():
                        # Save everything we need before creating the new user
                        old_roles = list(old_user.roles.all())
                        old_prefs = getattr(old_user, 'preferences', None)

                        # Snapshot all preference field values before the new user
                        # is saved (which triggers a signal that auto-creates preferences)
                        pref_fields = {}
                        if old_prefs:
                            skip_pref = {'user', 'user_id', 'created_at', 'updated_at'}
                            for f in old_prefs._meta.get_fields():
                                if hasattr(f, 'attname') and f.attname not in skip_pref and f.name not in skip_pref:
                                    pref_fields[f.attname] = getattr(old_prefs, f.attname)

                        # Stash the real credentials before temporarily clearing them
                        real_username = old_user.username
                        real_email = old_user.email
                        real_role_number = old_user.role_number

                        # Temporarily clear all unique fields on the old user so the
                        # new user record can be inserted with the real values while
                        # both rows coexist within the same transaction.
                        ParliamentUser.objects.filter(pk=user_id).update(
                            username=f'__migrating__{user_id}',
                            email=None,
                            role_number=None,
                        )

                        # Create new user, copying every non-pk field
                        new_user = ParliamentUser(
                            user_id=new_user_id,
                            name=old_user.name,
                            preferred_name=old_user.preferred_name,
                            member_type=old_user.member_type,
                            is_active=old_user.is_active,
                            is_admin=old_user.is_admin,
                            username=real_username,
                            email=real_email,
                            phone_number=old_user.phone_number,
                            profile_picture=old_user.profile_picture,
                            profile_picture_removed_by_admin=old_user.profile_picture_removed_by_admin,
                            anonymous_vote=old_user.anonymous_vote,
                            allow_abstain=old_user.allow_abstain,
                            member_status=old_user.member_status,
                            force_password_change=old_user.force_password_change,
                            has_default_password=old_user.has_default_password,
                            is_quarantined=old_user.is_quarantined,
                            email_flagged=old_user.email_flagged,
                            email_flagged_reason=old_user.email_flagged_reason,
                            email_flagged_at=old_user.email_flagged_at,
                            role_number=real_role_number,
                            last_login=old_user.last_login,
                            password=old_user.password,  # raw hashed password — no re-hashing
                        )
                        new_user.save()

                        # Copy preferences from old to the auto-created new preferences
                        if pref_fields:
                            UserPreferences.objects.filter(user=new_user).update(**pref_fields)

                        # Restore roles
                        new_user.roles.set(old_roles)

                        # Reassign all reverse FK / OneToOne relations to new_user
                        # Skip: 'preferences' (handled above), 'roles' (handled above),
                        # 'co_authored_legislation' (handled below as M2M)
                        skip_accessors = {'preferences', 'roles', 'co_authored_legislation'}
                        for rel in old_user._meta.get_fields():
                            if not hasattr(rel, 'get_accessor_name'):
                                continue
                            accessor = rel.get_accessor_name()
                            if not accessor or accessor in skip_accessors:
                                continue

                            if rel.one_to_many or rel.one_to_one:
                                # Standard FK / OneToOne — bulk update FK column
                                try:
                                    related_manager = getattr(old_user, accessor)
                                    related_manager.all().update(**{rel.field.name: new_user})
                                except Exception:
                                    pass

                        # Reassign co-authored legislation M2M
                        for leg in list(old_user.co_authored_legislation.all()):
                            leg.co_authors.remove(old_user)
                            leg.co_authors.add(new_user)

                        # Delete old user; CASCADE removes its now-empty FK rows
                        old_user.delete()

                    logger.info(f"Admin {request.user} migrated user_id '{user_id}' → '{new_user_id}'")
                    messages.success(request, f"Successfully migrated user ID '{user_id}' → '{new_user_id}'. All related data transferred.")
                    return redirect(f'/admin/src/parliamentuser/{new_user_id}/change/')

                except Exception as e:
                    error = f'Migration failed: {e}'

        return render(request, 'admin/migrate_user_id.html', {
            'user': old_user,
            'error': error,
            'title': f'Migrate User ID — {old_user.name}',
            'opts': self.model._meta,
            'has_permission': True,
        })

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


@admin.register(Legislation, site=admin_site)
class LegislationAdmin(admin.ModelAdmin):
    list_display = ('title', 'status_badge', 'vote_count', 'required_percentage', 'voting_closed', 'anonymous_vote')
    list_filter = ('voting_closed', 'status', 'anonymous_vote')
    search_fields = ('title', 'description')
    actions = [export_as_csv, update_status, remove_passed_legislation]
    list_per_page = 25

    fieldsets = (
        ('Legislation Information', {
            'fields': ('title', 'description', 'document', 'posted_by', 'available_at')
        }),
        ('Voting Settings', {
            'fields': ('required_percentage', 'anonymous_vote', 'allow_abstain', 'voting_closed', 'vote_mode')
        }),
        ('Status & Results', {
            'fields': ('status', 'passed', 'voting_ended_at'),
            'classes': ('collapse',)
        }),
    )

    def status_badge(self, obj):
        colors = {
            'passed': '#10b981',  # green
            'removed': '#ef4444',  # red
            'pending': '#f59e0b',  # yellow
        }
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.status.upper()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def vote_count(self, obj):
        count = Vote.objects.filter(legislation=obj).count()
        return f'{count} votes'
    vote_count.short_description = 'Votes'

    def has_delete_permission(self, request, obj=None):
        # Prevent deleting passed legislation
        if obj and obj.status == 'passed':
            return False
        return super().has_delete_permission(request, obj)


@admin.register(Vote, site=admin_site)
class VoteAdmin(admin.ModelAdmin):
    list_display = ('user', 'legislation', 'vote_choice_badge')
    search_fields = ('user__name', 'legislation__title')
    list_filter = ('vote_choice', 'legislation')
    list_per_page = 100
    autocomplete_fields = ['user']

    def vote_choice_badge(self, obj):
        colors = {
            'yes': '#10b981',  # green
            'no': '#ef4444',   # red
            'abstain': '#6b7280',  # gray
        }
        color = colors.get(obj.vote_choice, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.vote_choice.upper()
        )
    vote_choice_badge.short_description = 'Vote'
    vote_choice_badge.admin_order_field = 'vote_choice'


@admin.register(Attendance, site=admin_site)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('user', 'event', 'status_badge', 'marked_by', 'marked_at', 'created_at')
    search_fields = ('user__name', 'event__title', 'notes')
    list_filter = ('status', 'event', 'marked_at', 'created_at')
    actions = [export_as_csv]
    list_per_page = 100
    date_hierarchy = 'created_at'
    autocomplete_fields = ['user', 'event', 'marked_by']
    readonly_fields = ('created_at', 'date')

    fieldsets = (
        ('Attendance Record', {
            'fields': ('event', 'user', 'status')
        }),
        ('Tracking', {
            'fields': ('marked_by', 'marked_at', 'notes')
        }),
        ('Legacy Fields', {
            'fields': ('date', 'present', 'created_at'),
            'classes': ('collapse',)
        }),
    )

    def status_badge(self, obj):
        colors = {
            'pending': '#6b7280',
            'present': '#10b981',
            'absent': '#ef4444',
            'excused': '#3b82f6',
            'late': '#f59e0b'
        }
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def present_badge(self, obj):
        if obj.present:
            return format_html('<span style="color: #10b981; font-weight: bold;">✓ Present</span>')
        return format_html('<span style="color: #ef4444; font-weight: bold;">✗ Absent</span>')
    present_badge.short_description = 'Legacy Status'
    present_badge.admin_order_field = 'present'


@admin.register(AttendanceExcuse, site=admin_site)
class AttendanceExcuseAdmin(admin.ModelAdmin):
    list_display = ('user', 'event', 'status_badge', 'submitted_at', 'reviewed_by', 'reviewed_at')
    search_fields = ('user__name', 'event__title', 'reason', 'review_notes')
    list_filter = ('status', 'event', 'submitted_at', 'reviewed_at')
    readonly_fields = ('submitted_at', 'updated_at', 'is_past_deadline_display')
    autocomplete_fields = ['user', 'event', 'reviewed_by']
    actions = ['approve_excuses', 'deny_excuses']
    list_per_page = 50
    date_hierarchy = 'submitted_at'

    fieldsets = (
        ('Excuse Request', {
            'fields': ('event', 'user', 'reason', 'supporting_document', 'submitted_at', 'is_past_deadline_display')
        }),
        ('Review', {
            'fields': ('status', 'reviewed_by', 'reviewed_at', 'review_notes')
        }),
        ('Metadata', {
            'fields': ('updated_at',),
            'classes': ('collapse',)
        }),
    )

    def status_badge(self, obj):
        colors = {
            'pending': '#f59e0b',
            'approved': '#10b981',
            'denied': '#ef4444',
            'expired': '#6b7280'
        }
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def is_past_deadline_display(self, obj):
        if obj.is_past_deadline():
            return format_html('<span style="color: #ef4444; font-weight: bold;">⚠️ Past Deadline</span>')
        return format_html('<span style="color: #10b981;">✓ On Time</span>')
    is_past_deadline_display.short_description = 'Deadline Status'

    # Bulk actions
    def approve_excuses(self, request, queryset):
        """Approve selected excuse requests"""
        count = 0
        for excuse in queryset.filter(status='pending'):
            excuse.approve(request.user, 'Bulk approved by admin')
            count += 1
        self.message_user(request, f"{count} excuse(s) approved.")
    approve_excuses.short_description = "Approve selected excuses"

    def deny_excuses(self, request, queryset):
        """Deny selected excuse requests"""
        count = 0
        for excuse in queryset.filter(status='pending'):
            excuse.deny(request.user, 'Bulk denied by admin')
            count += 1
        self.message_user(request, f"{count} excuse(s) denied.")
    deny_excuses.short_description = "Deny selected excuses"


@admin.register(Committee, site=admin_site)
class CommitteeAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'role', 'member_count', 'chair_count')
    search_fields = ('name', 'code', 'description')
    filter_horizontal = ('members', 'chairs', 'advisors', 'voting_members')
    ordering = ('name',)
    list_filter = ('role', 'allow_multiple_chairs')
    list_per_page = 25

    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'code', 'role', 'allow_multiple_chairs')
        }),
        ('Leadership', {
            'fields': ('chairs', 'advisors')
        }),
        ('Membership', {
            'fields': ('voting_members', 'members')
        }),
    )

    def member_count(self, obj):
        count = obj.members.count()
        return f'{count} member{"s" if count != 1 else ""}'
    member_count.short_description = 'Members'

    def chair_count(self, obj):
        count = obj.chairs.count()
        return f'{count} chair{"s" if count != 1 else ""}'
    chair_count.short_description = 'Chairs'

@admin.register(DocumentTag, site=admin_site)
class DocumentTagAdmin(admin.ModelAdmin):
    list_display = ('name', 'color_badge', 'document_count', 'created_at')
    search_fields = ('name', 'description')
    list_filter = ('color', 'created_at')
    ordering = ('name',)
    list_per_page = 50

    def color_badge(self, obj):
        colors = {
            'blue': '#3b82f6',
            'green': '#10b981',
            'red': '#ef4444',
            'yellow': '#f59e0b',
            'purple': '#8b5cf6',
            'pink': '#ec4899',
            'gray': '#6b7280',
        }
        color = colors.get(obj.color, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.name
        )
    color_badge.short_description = 'Tag'
    color_badge.admin_order_field = 'name'

    def document_count(self, obj):
        count = obj.documents.count()
        return f"{count} document{'s' if count != 1 else ''}"
    document_count.short_description = 'Documents'


class DocumentVersionInline(admin.TabularInline):
    model = DocumentVersion
    extra = 0
    readonly_fields = ('version_number', 'uploaded_by', 'uploaded_at', 'file_size')
    fields = ('version_number', 'file', 'uploaded_by', 'uploaded_at', 'change_notes', 'file_size')
    can_delete = False


def set_visibility_all_members(modeladmin, request, queryset):
    queryset.update(visibility='all_members')
set_visibility_all_members.short_description = "Set visibility to: All Chapter Members"

def set_visibility_committee_only(modeladmin, request, queryset):
    queryset.update(visibility='committee_only')
set_visibility_committee_only.short_description = "Set visibility to: Committee Members Only"

def set_visibility_chairs_only(modeladmin, request, queryset):
    queryset.update(visibility='chairs_only')
set_visibility_chairs_only.short_description = "Set visibility to: Committee Chairs Only"

def set_visibility_officers_only(modeladmin, request, queryset):
    queryset.update(visibility='officers_only')
set_visibility_officers_only.short_description = "Set visibility to: Officers Only"


@admin.register(CommitteeDocument, site=admin_site)
class CommitteeDocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'committee', 'document_type', 'version_number', 'visibility', 'tag_list', 'meeting_date', 'uploaded_by', 'uploaded_at', 'published_to_chapter')
    list_filter = ('published_to_chapter', 'document_type', 'committee', 'uploaded_at', 'tags', 'is_latest_version', 'visibility')
    search_fields = ('title', 'description', 'committee__name')
    readonly_fields = ('uploaded_at', 'version_number')
    filter_horizontal = ('tags', 'custom_viewers')
    ordering = ('-uploaded_at',)
    inlines = [DocumentVersionInline]
    actions = [
        export_as_csv,
        set_visibility_all_members,
        set_visibility_committee_only,
        set_visibility_chairs_only,
        set_visibility_officers_only,
    ]

    fieldsets = (
        ('Document Information', {
            'fields': ('committee', 'title', 'description', 'document', 'document_type', 'meeting_date')
        }),
        ('Visibility & Access', {
            'fields': ('visibility', 'custom_viewers', 'published_to_chapter'),
            'description': 'Control who can view this document. Custom viewers only apply when visibility is set to "Custom Users".'
        }),
        ('Organization', {
            'fields': ('chapter_folder', 'tags'),
            'description': 'Categorize and organize this document'
        }),
        ('Metadata', {
            'fields': ('uploaded_by', 'uploaded_at', 'version_number', 'is_latest_version'),
            'classes': ('collapse',)
        }),
    )

    def tag_list(self, obj):
        return ', '.join([tag.name for tag in obj.tags.all()[:3]])
    tag_list.short_description = 'Tags'


@admin.register(DocumentVersion, site=admin_site)
class DocumentVersionAdmin(admin.ModelAdmin):
    list_display = ('document', 'version_number', 'uploaded_by', 'uploaded_at', 'get_file_size_display')
    list_filter = ('uploaded_at', 'document__committee')
    search_fields = ('document__title', 'change_notes', 'uploaded_by__name')
    readonly_fields = ('uploaded_at', 'file_size')
    ordering = ('-uploaded_at',)
    list_per_page = 50
    autocomplete_fields = ['uploaded_by']


@admin.register(Announcement, site=admin_site)
class AnnouncementAdmin(admin.ModelAdmin):
    list_display = ('title', 'posted_by', 'posted_at', 'publish_at', 'event_date', 'is_active_badge', 'send_email_on_publish', 'view_count')
    list_filter = ('is_active', 'send_email_on_publish', 'posted_at', 'event_date')
    search_fields = ('title', 'content', 'posted_by__name')
    readonly_fields = ('posted_at', 'email_sent_at')
    ordering = ('-posted_at',)
    list_per_page = 25
    date_hierarchy = 'posted_at'
    autocomplete_fields = ['posted_by']

    def is_active_badge(self, obj):
        if obj.is_active:
            return format_html('<span style="color: #10b981; font-weight: bold;">✓ Active</span>')
        return format_html('<span style="color: #ef4444; font-weight: bold;">✗ Inactive</span>')
    is_active_badge.short_description = 'Status'
    is_active_badge.admin_order_field = 'is_active'

    def view_count(self, obj):
        count = UserAnnouncementView.objects.filter(announcement=obj).count()
        return f'{count} views'
    view_count.short_description = 'Views'

    fieldsets = (
        ('Announcement Details', {
            'fields': ('title', 'content', 'posted_by')
        }),
        ('Visibility & Scheduling', {
            'fields': ('visible_to', 'publish_at', 'is_active'),
            'description': 'Control who sees this announcement and when it publishes. Leave visibility empty for all members.'
        }),
        ('Event Information', {
            'fields': ('event_date',),
            'description': 'Optional: Set a date/time if this announcement is for a specific event'
        }),
        ('Email Notifications', {
            'fields': ('send_email_on_publish', 'email_sent_at'),
            'classes': ('collapse',),
            'description': 'Email status — sent_at is set automatically when notifications are dispatched.'
        }),
        ('Metadata', {
            'fields': ('posted_at',),
            'classes': ('collapse',)
        }),
    )

    def has_add_permission(self, request):
        # Only officers and admins can create announcements
        return request.user.is_authenticated and (request.user.is_admin or request.user.is_officer)

    def has_change_permission(self, request, obj=None):
        # Only officers and admins can edit announcements
        return request.user.is_authenticated and (request.user.is_admin or request.user.is_officer)

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

import re
LOG_PATTERN = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) \[(\w+)\] ([^:]+): (.*)$')

@user_passes_test(lambda u: hasattr(u, 'is_admin') and u.is_admin)
def view_logs(request):
    log_path = os.path.join('logs', 'django_actions.log')
    logs = []

    try:
        with open(log_path, 'r') as f:
            for line in f.readlines()[-200:][::-1]:  # Show last 200 lines, most recent first
                line = line.strip()
                if not line:
                    continue

                match = LOG_PATTERN.match(line)
                if match:
                    timestamp, level, logger_name, message = match.groups()
                    logs.append({
                        'timestamp': timestamp,
                        'logger': logger_name,
                        'level': level,
                        'message': f"[{level}] {message}",
                    })
                else:
                    logs.append({
                        'timestamp': '',
                        'logger': '',
                        'level': '',
                        'message': line
                    })
    except Exception as e:
        logs.append({
            'timestamp': '',
            'logger': '',
            'level': 'ERROR',
            'message': f"Error reading log file: {e}"
        })

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


# === CHAT CHANNEL ADMIN ===

class ChatChannelPermissionInline(admin.TabularInline):
    model = ChatChannelPermission
    extra = 1
    verbose_name = "Permission"
    verbose_name_plural = "Channel Permissions"
    fields = ('user', 'member_type', 'chairs_only', 'officers_only')

@admin.register(Event, site=admin_site)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'date_time', 'location', 'created_by', 'requires_attendance', 'attendance_finalized', 'is_active_badge', 'archived_badge')
    list_filter = ('is_active', 'archived', 'requires_attendance', 'attendance_finalized', 'date_time', 'created_at')
    search_fields = ('title', 'description', 'location', 'created_by__name')
    readonly_fields = ('created_at', 'finalized_by', 'finalized_at', 'attendance_stats_display')
    ordering = ('-date_time',)
    list_per_page = 25
    date_hierarchy = 'date_time'
    autocomplete_fields = ['created_by', 'finalized_by']

    fieldsets = (
        ('Event Information', {
            'fields': ('title', 'description', 'date_time', 'location')
        }),
        ('Visibility', {
            'fields': ('visible_to', 'created_by')
        }),
        ('Attendance Tracking', {
            'fields': ('requires_attendance', 'allow_excuses', 'excuse_deadline', 'attendance_finalized', 'finalized_by', 'finalized_at', 'attendance_stats_display'),
            'description': 'Configure attendance tracking and excuse submission for this event'
        }),
        ('Status', {
            'fields': ('is_active', 'archived', 'created_at'),
            'classes': ('collapse',)
        }),
    )

    def attendance_stats_display(self, obj):
        """Display attendance statistics"""
        if not obj.requires_attendance:
            return "N/A - Attendance tracking not enabled"

        stats = obj.get_attendance_stats()
        if not stats:
            return "No attendance data"

        html = f"""
        <strong>Total Members:</strong> {stats['total_members']}<br>
        <strong>Present:</strong> {stats['present']} ({stats['attendance_rate']:.1f}%)<br>
        <strong>Absent:</strong> {stats['absent']}<br>
        <strong>Excused:</strong> {stats['excused']}<br>
        <strong>Pending:</strong> {stats['pending']}<br>
        <strong>Unmarked:</strong> {stats['unmarked']}
        """
        return format_html(html)
    attendance_stats_display.short_description = 'Attendance Statistics'

    def is_active_badge(self, obj):
        if obj.is_active:
            return format_html('<span style="color: #10b981; font-weight: bold;">✓ Active</span>')
        return format_html('<span style="color: #ef4444; font-weight: bold;">✗ Inactive</span>')
    is_active_badge.short_description = 'Active'
    is_active_badge.admin_order_field = 'is_active'

    def archived_badge(self, obj):
        if obj.archived:
            return format_html('<span style="color: #6b7280;">📦 Archived</span>')
        return format_html('<span style="color: #10b981;">📌 Current</span>')
    archived_badge.short_description = 'Archive Status'
    archived_badge.admin_order_field = 'archived'


@admin.register(ActivityLog, site=admin_site)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'action_category_badge', 'action_type', 'description_short', 'ip_address')
    list_filter = ('action_category', 'action_type', 'timestamp')
    search_fields = ('user__name', 'description', 'object_repr', 'ip_address')
    readonly_fields = ('timestamp', 'user', 'action_category', 'action_type', 'description', 'ip_address', 'user_agent', 'metadata')
    ordering = ('-timestamp',)
    list_per_page = 100
    date_hierarchy = 'timestamp'

    fieldsets = (
        ('Action Details', {
            'fields': ('timestamp', 'user', 'action_category', 'action_type', 'description')
        }),
        ('Object Information', {
            'fields': ('object_type', 'object_id', 'object_repr'),
            'classes': ('collapse',)
        }),
        ('Request Information', {
            'fields': ('ip_address', 'user_agent'),
            'classes': ('collapse',)
        }),
        ('Additional Data', {
            'fields': ('metadata',),
            'classes': ('collapse',)
        }),
    )

    def action_category_badge(self, obj):
        colors = {
            'auth': '#3b82f6',
            'legislation': '#8b5cf6',
            'vote': '#10b981',
            'committee': '#f59e0b',
            'document': '#6366f1',
            'announcement': '#ec4899',
            'event': '#f97316',
            'user': '#ef4444',
            'admin': '#dc2626',
            'other': '#6b7280',
        }
        color = colors.get(obj.action_category, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.get_action_category_display()
        )
    action_category_badge.short_description = 'Category'
    action_category_badge.admin_order_field = 'action_category'

    def description_short(self, obj):
        return obj.description[:75] + '...' if len(obj.description) > 75 else obj.description
    description_short.short_description = 'Description'

    def has_add_permission(self, request):
        return False  # Logs are auto-created only

    def has_delete_permission(self, request, obj=None):
        return request.user.is_admin  # Only admins can delete logs


@admin.register(LoginHistory, site=admin_site)
class LoginHistoryAdmin(admin.ModelAdmin):
    list_display = ('timestamp', 'user', 'status_badge', 'location_display_admin', 'device_type', 'risk_badge', 'is_suspicious', 'alert_created')
    list_filter = ('status', 'is_suspicious', 'risk_level', 'device_type', 'timestamp', 'alert_created', 'reviewed')
    search_fields = ('user__name', 'ip_address', 'city', 'country', 'browser', 'os')
    readonly_fields = ('timestamp', 'user', 'status', 'ip_address', 'country', 'city', 'region',
                      'latitude', 'longitude', 'user_agent', 'device_type', 'browser', 'os',
                      'is_suspicious', 'risk_level', 'risk_factors', 'distance_from_last',
                      'time_from_last', 'alert_created', 'location_map')
    ordering = ('-timestamp',)
    list_per_page = 50
    date_hierarchy = 'timestamp'
    autocomplete_fields = ['user', 'reviewed_by']

    fieldsets = (
        ('Login Details', {
            'fields': ('timestamp', 'user', 'status', 'ip_address')
        }),
        ('Location Information', {
            'fields': ('country', 'city', 'region', 'latitude', 'longitude', 'location_map')
        }),
        ('Device Information', {
            'fields': ('device_type', 'browser', 'os', 'user_agent'),
            'classes': ('collapse',)
        }),
        ('Security Analysis', {
            'fields': ('is_suspicious', 'risk_level', 'risk_factors', 'distance_from_last', 'time_from_last', 'alert_created')
        }),
        ('Review', {
            'fields': ('reviewed', 'reviewed_by', 'reviewed_at', 'notes')
        }),
    )

    def status_badge(self, obj):
        colors = {
            'success': '#10b981',
            'failed': '#ef4444',
            'blocked': '#dc2626'
        }
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def risk_badge(self, obj):
        colors = {
            'low': '#10b981',
            'medium': '#f59e0b',
            'high': '#ef4444',
            'critical': '#dc2626'
        }
        color = colors.get(obj.risk_level, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_risk_level_display()
        )
    risk_badge.short_description = 'Risk Level'
    risk_badge.admin_order_field = 'risk_level'

    def location_display_admin(self, obj):
        return obj.location_display
    location_display_admin.short_description = 'Location'

    def location_map(self, obj):
        """Display a link to view the location on a map"""
        if obj.latitude and obj.longitude:
            return format_html(
                '<a href="https://www.google.com/maps?q={},{}" target="_blank">View on Map ({}°, {}°)</a>',
                obj.latitude, obj.longitude, obj.latitude, obj.longitude
            )
        return "No coordinates available"
    location_map.short_description = 'Map Link'

    def has_add_permission(self, request):
        return False  # Logins are auto-tracked only

    def has_delete_permission(self, request, obj=None):
        return request.user.is_admin  # Only admins can delete login history


@admin.register(LoginAlert, site=admin_site)
class LoginAlertAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'alert_type_badge', 'severity_badge', 'status_badge', 'title', 'user_notified', 'reviewed_by')
    list_filter = ('alert_type', 'severity', 'status', 'user_notified', 'created_at')
    search_fields = ('user__name', 'title', 'description')
    readonly_fields = ('created_at', 'user', 'login_history', 'alert_type', 'severity', 'title', 'description', 'login_details')
    ordering = ('-created_at',)
    list_per_page = 50
    date_hierarchy = 'created_at'
    autocomplete_fields = ['user', 'reviewed_by']

    actions = ['mark_as_investigating', 'mark_as_resolved', 'mark_as_false_positive']

    fieldsets = (
        ('Alert Information', {
            'fields': ('created_at', 'user', 'alert_type', 'severity', 'status', 'title', 'description')
        }),
        ('Related Login', {
            'fields': ('login_history', 'login_details')
        }),
        ('Review & Resolution', {
            'fields': ('reviewed_by', 'reviewed_at', 'resolution_notes')
        }),
        ('User Notification', {
            'fields': ('user_notified', 'notified_at')
        }),
    )

    def alert_type_badge(self, obj):
        colors = {
            'impossible_travel': '#dc2626',
            'new_location': '#f59e0b',
            'new_device': '#3b82f6',
            'multiple_failures': '#ef4444',
            'unusual_time': '#f97316',
            'vpn_detected': '#8b5cf6',
            'other': '#6b7280'
        }
        color = colors.get(obj.alert_type, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.get_alert_type_display()
        )
    alert_type_badge.short_description = 'Type'
    alert_type_badge.admin_order_field = 'alert_type'

    def severity_badge(self, obj):
        colors = {
            'low': '#10b981',
            'medium': '#f59e0b',
            'high': '#ef4444',
            'critical': '#dc2626'
        }
        color = colors.get(obj.severity, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px; font-weight: bold;">{}</span>',
            color,
            obj.get_severity_display()
        )
    severity_badge.short_description = 'Severity'
    severity_badge.admin_order_field = 'severity'

    def status_badge(self, obj):
        colors = {
            'new': '#ef4444',
            'investigating': '#f59e0b',
            'resolved': '#10b981',
            'false_positive': '#6b7280'
        }
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def login_details(self, obj):
        """Display key details about the related login"""
        lh = obj.login_history
        details = f"""
        <strong>Time:</strong> {localtime(lh.timestamp).strftime('%Y-%m-%d %H:%M:%S %Z')}<br>
        <strong>IP:</strong> {lh.ip_address}<br>
        <strong>Location:</strong> {lh.location_display}<br>
        <strong>Device:</strong> {lh.device_type} - {lh.browser}<br>
        <strong>Risk Level:</strong> {lh.get_risk_level_display()}
        """
        if lh.latitude and lh.longitude:
            details += f'<br><a href="https://www.google.com/maps?q={lh.latitude},{lh.longitude}" target="_blank">View on Map</a>'
        return format_html(details)
    login_details.short_description = 'Login Details'

    # Admin actions
    def mark_as_investigating(self, request, queryset):
        from django.utils import timezone
        queryset.update(
            status='investigating',
            reviewed_by=request.user,
            reviewed_at=timezone.now()
        )
        self.message_user(request, f"{queryset.count()} alert(s) marked as under investigation.")
    mark_as_investigating.short_description = "Mark as Under Investigation"

    def mark_as_resolved(self, request, queryset):
        from django.utils import timezone
        queryset.update(
            status='resolved',
            reviewed_by=request.user,
            reviewed_at=timezone.now()
        )
        self.message_user(request, f"{queryset.count()} alert(s) marked as resolved.")
    mark_as_resolved.short_description = "Mark as Resolved"

    def mark_as_false_positive(self, request, queryset):
        from django.utils import timezone
        queryset.update(
            status='false_positive',
            reviewed_by=request.user,
            reviewed_at=timezone.now()
        )
        self.message_user(request, f"{queryset.count()} alert(s) marked as false positive.")
    mark_as_false_positive.short_description = "Mark as False Positive"

    def has_add_permission(self, request):
        return False  # Alerts are auto-created only


@admin.register(ChatChannel, site=admin_site)
class ChatChannelAdmin(admin.ModelAdmin):
    list_display = ('name', 'channel_type', 'access_type', 'is_active', 'created_by', 'created_at')
    list_filter = ('channel_type', 'access_type', 'is_active')
    search_fields = ('name', 'description')
    ordering = ('name',)
    inlines = [ChatChannelPermissionInline]
    readonly_fields = ('created_at', 'created_by')

    def save_model(self, request, obj, form, change):
        if not change:  # Only set created_by on creation
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

@admin.register(ChatMessage, site=admin_site)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ('sender', 'channel', 'committee', 'message_preview', 'created_at', 'is_deleted_badge')
    list_filter = ('is_deleted', 'created_at', 'channel', 'committee')
    search_fields = ('sender__name', 'message', 'channel__name', 'committee__code')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'edited_at')
    list_per_page = 100
    date_hierarchy = 'created_at'
    autocomplete_fields = ['sender']

    def message_preview(self, obj):
        return obj.message[:50] + '...' if len(obj.message) > 50 else obj.message
    message_preview.short_description = 'Message'

    def is_deleted_badge(self, obj):
        if obj.is_deleted:
            return format_html('<span style="color: #ef4444;">🗑️ Deleted</span>')
        return format_html('<span style="color: #10b981;">✓ Active</span>')
    is_deleted_badge.short_description = 'Status'
    is_deleted_badge.admin_order_field = 'is_deleted'

@admin.register(ChatReadReceipt, site=admin_site)
class ChatReadReceiptAdmin(admin.ModelAdmin):
    list_display = ('user', 'channel', 'committee', 'last_read_at')
    list_filter = ('last_read_at', 'channel', 'committee')
    search_fields = ('user__name', 'channel__name', 'committee__code')
    ordering = ('-last_read_at',)
    readonly_fields = ('last_read_at',)
    list_per_page = 100
    date_hierarchy = 'last_read_at'


@admin.register(UserAnnouncementView, site=admin_site)
class UserAnnouncementViewAdmin(admin.ModelAdmin):
    list_display = ('user', 'announcement', 'viewed_at', 'dismissed_badge')
    list_filter = ('dismissed', 'viewed_at')
    search_fields = ('user__name', 'announcement__title')
    ordering = ('-viewed_at',)
    readonly_fields = ('viewed_at',)
    list_per_page = 100
    date_hierarchy = 'viewed_at'

    def dismissed_badge(self, obj):
        if obj.dismissed:
            return format_html('<span style="color: #6b7280;">✓ Dismissed</span>')
        return format_html('<span style="color: #3b82f6;">👁️ Viewed</span>')
    dismissed_badge.short_description = 'Status'
    dismissed_badge.admin_order_field = 'dismissed'


@admin.register(Notification, site=admin_site)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'notification_type_badge', 'title', 'is_read_badge', 'created_at')
    list_filter = ('notification_type', 'is_read', 'created_at')
    search_fields = ('title', 'message', 'recipient__name')
    readonly_fields = ('created_at', 'read_at')
    ordering = ('-created_at',)
    list_per_page = 50
    date_hierarchy = 'created_at'
    autocomplete_fields = ['recipient']

    fieldsets = (
        ('Notification', {
            'fields': ('recipient', 'notification_type', 'title', 'message', 'link')
        }),
        ('Status', {
            'fields': ('is_read', 'created_at', 'read_at')
        }),
        ('Source', {
            'fields': ('source_type', 'source_id'),
            'classes': ('collapse',)
        }),
    )

    def notification_type_badge(self, obj):
        colors = {
            'announcement': '#3b82f6',
            'legislation_new': '#8b5cf6',
            'vote_ended': '#10b981',
            'event_new': '#f97316',
        }
        color = colors.get(obj.notification_type, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 3px; font-size: 11px;">{}</span>',
            color,
            obj.get_notification_type_display()
        )
    notification_type_badge.short_description = 'Type'
    notification_type_badge.admin_order_field = 'notification_type'

    def is_read_badge(self, obj):
        if obj.is_read:
            return format_html('<span style="color: #6b7280;">✓ Read</span>')
        return format_html('<span style="color: #3b82f6; font-weight: bold;">● Unread</span>')
    is_read_badge.short_description = 'Status'
    is_read_badge.admin_order_field = 'is_read'

    def has_add_permission(self, request):
        return False  # Notifications are auto-created only


# === ADMIN V2 MODELS ===
@admin.register(FeatureFlag, site=admin_site)
class FeatureFlagAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'name', 'category', 'enabled_badge', 'maintenance_info_badge', 'last_toggled_by', 'last_toggled_at', 'updated_at')
    list_filter = ('is_enabled', 'category', 'updated_at')
    search_fields = ('name', 'display_name', 'description')
    ordering = ('category', 'display_name')
    readonly_fields = ('created_at', 'updated_at', 'last_toggled_by', 'last_toggled_at', 'maintenance_stats_display')
    list_per_page = 50

    def get_fieldsets(self, request, obj=None):
        """Dynamic fieldsets - show maintenance stats for maintenance_mode flag"""
        base_fieldsets = [
            ('Feature Information', {
                'fields': ('name', 'display_name', 'description', 'category')
            }),
            ('Status', {
                'fields': ('is_enabled',)
            }),
            ('Tracking', {
                'fields': ('created_at', 'updated_at', 'last_toggled_by', 'last_toggled_at'),
                'classes': ('collapse',)
            }),
        ]

        # Add maintenance stats section for maintenance_mode flag
        if obj and obj.name == 'maintenance_mode':
            base_fieldsets.insert(2, ('Maintenance Mode Statistics', {
                'fields': ('maintenance_stats_display',),
                'description': 'Real-time statistics about maintenance mode activity'
            }))

        return base_fieldsets

    def maintenance_stats_display(self, obj):
        """Display detailed maintenance mode statistics"""
        if obj.name != 'maintenance_mode':
            return "N/A - Not maintenance mode flag"

        from django.core.cache import cache
        from django.utils import timezone as tz
        import sys

        # Get maintenance stats from cache
        started_at = cache.get('maintenance_mode_started_at')
        blocked_count = cache.get('maintenance_blocked_count', 0)

        if not obj.is_enabled:
            return format_html(
                '<div style="padding: 15px; background: #f3f4f6; border-radius: 8px; border: 1px solid #e5e7eb;">'
                '<p style="color: #6b7280; margin: 0;"><strong>Maintenance mode is currently disabled.</strong></p>'
                '<p style="color: #9ca3af; margin: 5px 0 0 0; font-size: 12px;">Enable it above to put the site in maintenance mode. '
                'Non-admin users will see a maintenance page.</p>'
                '</div>'
            )

        # Calculate duration
        if started_at:
            duration = tz.now() - started_at
            hours, remainder = divmod(duration.total_seconds(), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours > 0:
                duration_str = f"{int(hours)}h {int(minutes)}m {int(seconds)}s"
            elif minutes > 0:
                duration_str = f"{int(minutes)}m {int(seconds)}s"
            else:
                duration_str = f"{int(seconds)}s"
            started_str = localtime(started_at).strftime('%Y-%m-%d %H:%M:%S %Z')
        else:
            duration_str = "Just started"
            started_str = "Now"

        # Get active user count
        try:
            active_users = ParliamentUser.objects.filter(member_status='Active').count()
            admin_users = ParliamentUser.objects.filter(is_admin=True, member_status='Active').count()
        except Exception:
            active_users = "N/A"
            admin_users = "N/A"

        # Get recent activity log entries during maintenance
        try:
            recent_logs = ActivityLog.objects.filter(
                created_at__gte=started_at if started_at else tz.now()
            ).order_by('-created_at')[:5]
            log_html = ""
            for log in recent_logs:
                log_html += f'<li style="margin: 3px 0; font-size: 12px;">{localtime(log.created_at).strftime("%H:%M:%S %Z")} - {log.action_type}: {log.description[:50]}...</li>'
            if not log_html:
                log_html = '<li style="color: #9ca3af;">No activity logged during maintenance</li>'
        except Exception:
            log_html = '<li style="color: #9ca3af;">Could not load activity logs</li>'

        return format_html(
            '<div style="padding: 20px; background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%); border-radius: 12px; border: 2px solid #f59e0b;">'
            # Status header
            '<div style="display: flex; align-items: center; margin-bottom: 15px;">'
            '<span style="background: #f59e0b; color: white; padding: 5px 12px; border-radius: 20px; font-weight: bold; font-size: 14px;">'
            '⚠️ MAINTENANCE MODE ACTIVE</span>'
            '</div>'

            # Stats grid
            '<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 15px;">'

            # Duration card
            '<div style="background: white; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">'
            '<div style="color: #6b7280; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Duration</div>'
            '<div style="font-size: 24px; font-weight: bold; color: #1f2937;">{}</div>'
            '<div style="color: #9ca3af; font-size: 11px;">Started: {}</div>'
            '</div>'

            # Blocked requests card
            '<div style="background: white; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">'
            '<div style="color: #6b7280; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Blocked Requests</div>'
            '<div style="font-size: 24px; font-weight: bold; color: #dc2626;">{}</div>'
            '<div style="color: #9ca3af; font-size: 11px;">Non-admin access attempts</div>'
            '</div>'

            # Users card
            '<div style="background: white; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">'
            '<div style="color: #6b7280; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">User Access</div>'
            '<div style="font-size: 24px; font-weight: bold; color: #059669;">{} <span style="font-size: 14px; color: #6b7280;">admins</span></div>'
            '<div style="color: #9ca3af; font-size: 11px;">{} total active users blocked</div>'
            '</div>'

            # Server info card
            '<div style="background: white; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">'
            '<div style="color: #6b7280; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px;">Server Status</div>'
            '<div style="font-size: 14px; color: #1f2937;"><strong>Python:</strong> {}</div>'
            '<div style="font-size: 14px; color: #1f2937;"><strong>Time:</strong> {}</div>'
            '</div>'

            '</div>'

            # Recent activity section
            '<div style="background: white; padding: 15px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">'
            '<div style="color: #6b7280; font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px;">Recent Admin Activity</div>'
            '<ul style="margin: 0; padding-left: 20px; color: #4b5563;">{}</ul>'
            '</div>'

            # Warning note
            '<div style="margin-top: 15px; padding: 10px; background: #fef2f2; border-radius: 6px; border: 1px solid #fecaca;">'
            '<p style="margin: 0; color: #991b1b; font-size: 12px;">'
            '<strong>⚠️ Remember:</strong> Uncheck "Is enabled" above and save to disable maintenance mode and restore normal access for all users.'
            '</p>'
            '</div>'

            '</div>',
            duration_str,
            started_str,
            blocked_count,
            admin_users,
            active_users - admin_users if isinstance(active_users, int) and isinstance(admin_users, int) else "N/A",
            sys.version.split()[0],
            localtime(tz.now()).strftime('%Y-%m-%d %H:%M:%S %Z'),
            log_html
        )
    maintenance_stats_display.short_description = 'Maintenance Statistics'

    def maintenance_info_badge(self, obj):
        """Show maintenance info in list view"""
        if obj.name != 'maintenance_mode' or not obj.is_enabled:
            return '-'

        from django.core.cache import cache
        blocked_count = cache.get('maintenance_blocked_count', 0)
        started_at = cache.get('maintenance_mode_started_at')

        if started_at:
            from django.utils import timezone as tz
            duration = tz.now() - started_at
            minutes = int(duration.total_seconds() / 60)
            if minutes < 60:
                time_str = f"{minutes}m"
            else:
                time_str = f"{minutes // 60}h {minutes % 60}m"
        else:
            time_str = "now"

        return format_html(
            '<span style="background: #fef3c7; color: #92400e; padding: 3px 8px; border-radius: 4px; font-size: 11px;">'
            '⏱️ {} | 🚫 {} blocked</span>',
            time_str, blocked_count
        )
    maintenance_info_badge.short_description = 'Maintenance Info'

    def enabled_badge(self, obj):
        if obj.is_enabled:
            return format_html('<span style="background-color: #10b981; color: white; padding: 3px 8px; border-radius: 4px; font-weight: 500;">✓ Enabled</span>')
        return format_html('<span style="background-color: #ef4444; color: white; padding: 3px 8px; border-radius: 4px; font-weight: 500;">✗ Disabled</span>')
    enabled_badge.short_description = 'Status'
    enabled_badge.admin_order_field = 'is_enabled'

    def save_model(self, request, obj, form, change):
        if change and 'is_enabled' in form.changed_data:
            obj.last_toggled_by = request.user.name
            obj.last_toggled_at = timezone.now()

            # Log the activity
            action_type = 'feature_flag_enabled' if obj.is_enabled else 'feature_flag_disabled'
            ActivityLog.log_activity(
                action_type=action_type,
                user=request.user,
                description=f'{request.user.get_display_name()} {"enabled" if obj.is_enabled else "disabled"} feature flag: {obj.display_name}',
                request=request
            )

            # Clear maintenance stats when disabling maintenance mode
            if obj.name == 'maintenance_mode' and not obj.is_enabled:
                from django.core.cache import cache
                cache.delete('maintenance_mode_started_at')
                cache.delete('maintenance_blocked_count')

        super().save_model(request, obj, form, change)


@admin.register(ScheduledMaintenance, site=admin_site)
class ScheduledMaintenanceAdmin(admin.ModelAdmin):
    list_display = ('title', 'status_badge', 'scheduled_start', 'time_until_display', 'estimated_duration_minutes', 'notify_email', 'created_by')
    list_filter = ('is_active', 'maintenance_started', 'scheduled_start')
    search_fields = ('title', 'message', 'notify_email')
    ordering = ('-scheduled_start',)
    readonly_fields = ('maintenance_started', 'started_at', 'completed_at', 'email_sent', 'created_at', 'updated_at', 'maintenance_preview', 'current_server_time')
    date_hierarchy = 'scheduled_start'

    fieldsets = (
        ('Maintenance Details', {
            'fields': ('title', 'message', 'maintenance_preview')
        }),
        ('Schedule', {
            'fields': ('current_server_time', 'scheduled_start', 'estimated_duration_minutes'),
            'description': 'Set when maintenance should automatically start. A warning banner will be shown to users beforehand.'
        }),
        ('Notifications', {
            'fields': ('notify_email',),
            'description': 'You will receive an email when maintenance automatically starts.'
        }),
        ('Status', {
            'fields': ('is_active', 'maintenance_started', 'started_at', 'completed_at', 'email_sent'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def current_server_time(self, obj):
        """Display current server time for reference"""
        from django.conf import settings
        now = timezone.now()
        local_now = timezone.localtime(now)
        return format_html(
            '<div style="padding: 12px; background: #f0fdf4; border: 1px solid #22c55e; border-radius: 8px; margin-bottom: 10px;">'
            '<div style="display: flex; align-items: center; gap: 8px;">'
            '<span style="font-size: 20px;">🕐</span>'
            '<div>'
            '<div style="font-size: 18px; font-weight: bold; color: #166534;">{}</div>'
            '<div style="font-size: 12px; color: #15803d;">Timezone: {} ({})</div>'
            '</div>'
            '</div>'
            '</div>',
            local_now.strftime('%B %d, %Y at %I:%M %p'),
            settings.TIME_ZONE,
            local_now.strftime('%Z')
        )
    current_server_time.short_description = 'Current Server Time'

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('<int:pk>/start/', self.admin_site.admin_view(self.start_maintenance_view), name='scheduledmaintenance_start'),
            path('<int:pk>/complete/', self.admin_site.admin_view(self.complete_maintenance_view), name='scheduledmaintenance_complete'),
        ]
        return custom_urls + urls

    def start_maintenance_view(self, request, pk):
        """View to start maintenance immediately"""
        obj = ScheduledMaintenance.objects.get(pk=pk)
        if not obj.maintenance_started and not obj.completed_at:
            obj.start_maintenance()
            self.message_user(request, f"Maintenance started: {obj.title}", messages.SUCCESS)
            ActivityLog.log_activity(
                action_type='maintenance_started_manually',
                user=request.user,
                description=f'{request.user.get_display_name()} manually started maintenance: {obj.title}',
                request=request
            )
        else:
            self.message_user(request, "Maintenance already started or completed", messages.WARNING)
        from django.urls import reverse
        return redirect(reverse('admin:src_scheduledmaintenance_change', args=[pk]))

    def complete_maintenance_view(self, request, pk):
        """View to complete maintenance"""
        obj = ScheduledMaintenance.objects.get(pk=pk)
        if not obj.completed_at:
            obj.complete_maintenance()
            self.message_user(request, f"Maintenance completed: {obj.title}", messages.SUCCESS)
            ActivityLog.log_activity(
                action_type='maintenance_completed',
                user=request.user,
                description=f'{request.user.get_display_name()} completed maintenance: {obj.title}',
                request=request
            )
        else:
            self.message_user(request, "Maintenance already completed", messages.WARNING)
        from django.urls import reverse
        return redirect(reverse('admin:src_scheduledmaintenance_change', args=[pk]))

    def change_view(self, request, object_id, form_url='', extra_context=None):
        extra_context = extra_context or {}
        obj = ScheduledMaintenance.objects.get(pk=object_id)

        # Add action buttons context
        extra_context['show_start_button'] = not obj.maintenance_started and not obj.completed_at
        extra_context['show_complete_button'] = obj.maintenance_started and not obj.completed_at
        extra_context['maintenance_obj'] = obj

        return super().change_view(request, object_id, form_url, extra_context)

    def maintenance_preview(self, obj):
        """Preview of the maintenance warning banner"""
        if not obj.pk:
            return format_html(
                '<div style="padding: 15px; background: #dbeafe; border: 1px solid #3b82f6; border-radius: 8px;">'
                '<p style="margin: 0; color: #1e40af;">Save the scheduled maintenance to see a preview of the warning banner.</p>'
                '</div>'
            )

        time_until = obj.time_until_start or "now"
        return format_html(
            '<div style="background: #2563eb; color: white; padding: 15px; border-radius: 8px; margin-bottom: 10px;">'
            '<div style="display: flex; align-items: center; gap: 10px; margin-bottom: 8px;">'
            '<span style="font-size: 20px;">🕐</span>'
            '<strong style="font-size: 16px;">{}</strong>'
            '<span style="color: #bfdbfe;">— Starting in {}</span>'
            '</div>'
            '<p style="margin: 0; color: #dbeafe; font-size: 14px;">{}</p>'
            '</div>'
            '<p style="color: #6b7280; font-size: 12px; margin: 5px 0 0 0;">'
            '↑ This is how the banner will appear to users before maintenance starts.</p>',
            obj.title,
            time_until,
            obj.message
        )
    maintenance_preview.short_description = 'Banner Preview'

    def status_badge(self, obj):
        """Visual status indicator"""
        if obj.completed_at:
            return format_html(
                '<span style="background: #10b981; color: white; padding: 3px 10px; border-radius: 12px; font-size: 12px;">✓ Completed</span>'
            )
        elif obj.maintenance_started:
            return format_html(
                '<span style="background: #f59e0b; color: white; padding: 3px 10px; border-radius: 12px; font-size: 12px; animation: pulse 2s infinite;">🔧 In Progress</span>'
            )
        elif not obj.is_active:
            return format_html(
                '<span style="background: #6b7280; color: white; padding: 3px 10px; border-radius: 12px; font-size: 12px;">✗ Cancelled</span>'
            )
        elif obj.scheduled_start <= timezone.now():
            return format_html(
                '<span style="background: #ef4444; color: white; padding: 3px 10px; border-radius: 12px; font-size: 12px;">⏰ Pending Start</span>'
            )
        else:
            return format_html(
                '<span style="background: #3b82f6; color: white; padding: 3px 10px; border-radius: 12px; font-size: 12px;">📅 Scheduled</span>'
            )
    status_badge.short_description = 'Status'

    def time_until_display(self, obj):
        """Display time until maintenance"""
        if obj.completed_at or obj.maintenance_started:
            return '-'
        time_until = obj.time_until_start
        if time_until:
            return format_html(
                '<span style="color: #2563eb; font-weight: 500;">{}</span>',
                time_until
            )
        return '-'
    time_until_display.short_description = 'Starts In'

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user

        super().save_model(request, obj, form, change)

        # Log the activity
        if change:
            ActivityLog.log_activity(
                action_type='scheduled_maintenance_updated',
                user=request.user,
                description=f'{request.user.get_display_name()} updated scheduled maintenance: {obj.title}',
                request=request
            )
        else:
            ActivityLog.log_activity(
                action_type='scheduled_maintenance_created',
                user=request.user,
                description=f'{request.user.get_display_name()} scheduled maintenance: {obj.title} for {obj.scheduled_start}',
                request=request
            )

    actions = ['start_maintenance_now', 'mark_completed', 'cancel_maintenance']

    @admin.action(description='Start maintenance now')
    def start_maintenance_now(self, request, queryset):
        for obj in queryset.filter(maintenance_started=False, completed_at__isnull=True):
            obj.start_maintenance()
            self.message_user(request, f"Started maintenance: {obj.title}", messages.SUCCESS)
            ActivityLog.log_activity(
                action_type='maintenance_started_manually',
                user=request.user,
                description=f'{request.user.get_display_name()} manually started maintenance: {obj.title}',
                request=request
            )

    @admin.action(description='Mark as completed')
    def mark_completed(self, request, queryset):
        for obj in queryset.filter(completed_at__isnull=True):
            obj.complete_maintenance()
            self.message_user(request, f"Completed maintenance: {obj.title}", messages.SUCCESS)
            ActivityLog.log_activity(
                action_type='maintenance_completed',
                user=request.user,
                description=f'{request.user.get_display_name()} marked maintenance as completed: {obj.title}',
                request=request
            )

    @admin.action(description='Cancel scheduled maintenance')
    def cancel_maintenance(self, request, queryset):
        count = queryset.filter(maintenance_started=False, completed_at__isnull=True).update(is_active=False)
        self.message_user(request, f"Cancelled {count} scheduled maintenance(s)", messages.SUCCESS)


@admin.register(PageToggle, site=admin_site)
class PageToggleAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'url_name', 'enabled_badge', 'last_toggled_by', 'last_toggled_at', 'updated_at')
    list_filter = ('is_enabled', 'updated_at')
    search_fields = ('url_name', 'display_name', 'description', 'disabled_message')
    ordering = ('display_name',)
    readonly_fields = ('created_at', 'updated_at', 'last_toggled_by', 'last_toggled_at')
    list_per_page = 50

    fieldsets = (
        ('Page Information', {
            'fields': ('url_name', 'display_name', 'description')
        }),
        ('Status', {
            'fields': ('is_enabled', 'disabled_message')
        }),
        ('Tracking', {
            'fields': ('created_at', 'updated_at', 'last_toggled_by', 'last_toggled_at'),
            'classes': ('collapse',)
        }),
    )

    def enabled_badge(self, obj):
        if obj.is_enabled:
            return format_html('<span style="background-color: #10b981; color: white; padding: 3px 8px; border-radius: 4px; font-weight: 500;">✓ Enabled</span>')
        return format_html('<span style="background-color: #ef4444; color: white; padding: 3px 8px; border-radius: 4px; font-weight: 500;">✗ Disabled</span>')
    enabled_badge.short_description = 'Status'
    enabled_badge.admin_order_field = 'is_enabled'

    def save_model(self, request, obj, form, change):
        if change and 'is_enabled' in form.changed_data:
            obj.last_toggled_by = request.user.name
            obj.last_toggled_at = timezone.now()

            # Log the activity
            action_type = 'page_toggle_enabled' if obj.is_enabled else 'page_toggle_disabled'
            ActivityLog.log_activity(
                action_type=action_type,
                user=request.user,
                description=f'{request.user.get_display_name()} {"enabled" if obj.is_enabled else "disabled"} page: {obj.display_name}',
                request=request
            )
        super().save_model(request, obj, form, change)


@admin.register(BugReport, site=admin_site)
class BugReportAdmin(admin.ModelAdmin):
    list_display = ('id', 'issue_type_badge', 'priority_badge', 'status_badge', 'description_short', 'page', 'submitted_by', 'submitted_at')
    list_filter = ('status', 'issue_type', 'priority', 'page', 'submitted_at')
    search_fields = ('description', 'feature', 'page_url', 'submitted_by__name')
    ordering = ('-submitted_at',)
    readonly_fields = ('submitted_at', 'updated_at', 'browser_info', 'submitted_by')
    list_per_page = 50
    date_hierarchy = 'submitted_at'
    autocomplete_fields = ['resolved_by']

    actions = ['mark_acknowledged', 'mark_in_progress', 'mark_resolved', 'mark_wont_fix']

    fieldsets = (
        ('Bug Information', {
            'fields': ('issue_type', 'priority', 'status', 'description')
        }),
        ('Location', {
            'fields': ('page', 'page_url', 'feature')
        }),
        ('Reproduction Details', {
            'fields': ('steps_to_reproduce', 'expected_behavior', 'actual_behavior'),
            'classes': ('collapse',)
        }),
        ('Screenshot', {
            'fields': ('screenshot',),
            'classes': ('collapse',)
        }),
        ('Technical Info', {
            'fields': ('browser_info',),
            'classes': ('collapse',)
        }),
        ('Resolution', {
            'fields': ('admin_notes', 'resolved_at', 'resolved_by')
        }),
        ('Metadata', {
            'fields': ('submitted_by', 'submitted_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def description_short(self, obj):
        return obj.description[:60] + '...' if len(obj.description) > 60 else obj.description
    description_short.short_description = 'Description'

    def issue_type_badge(self, obj):
        colors = {
            'ui': '#3b82f6',
            'functionality': '#f59e0b',
            'error_500': '#dc2626',
            'error_404': '#ef4444',
            'error_403': '#f97316',
            'performance': '#8b5cf6',
            'mobile': '#06b6d4',
            'accessibility': '#10b981',
            'data': '#ec4899',
            'other': '#6b7280'
        }
        color = colors.get(obj.issue_type, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 4px; font-size: 11px;">{}</span>',
            color,
            obj.get_issue_type_display()
        )
    issue_type_badge.short_description = 'Type'
    issue_type_badge.admin_order_field = 'issue_type'

    def priority_badge(self, obj):
        colors = {
            'low': '#10b981',
            'medium': '#f59e0b',
            'high': '#ef4444',
            'critical': '#dc2626'
        }
        color = colors.get(obj.priority, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold;">{}</span>',
            color,
            obj.get_priority_display()
        )
    priority_badge.short_description = 'Priority'
    priority_badge.admin_order_field = 'priority'

    def status_badge(self, obj):
        colors = {
            'new': '#ef4444',
            'acknowledged': '#f59e0b',
            'in_progress': '#3b82f6',
            'resolved': '#10b981',
            'wont_fix': '#6b7280',
            'duplicate': '#8b5cf6'
        }
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 4px; font-weight: 500;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def mark_acknowledged(self, request, queryset):
        queryset.update(status='acknowledged')
        self.message_user(request, f"{queryset.count()} bug report(s) marked as acknowledged.")
    mark_acknowledged.short_description = "Mark as Acknowledged"

    def mark_in_progress(self, request, queryset):
        queryset.update(status='in_progress')
        self.message_user(request, f"{queryset.count()} bug report(s) marked as in progress.")
    mark_in_progress.short_description = "Mark as In Progress"

    def mark_resolved(self, request, queryset):
        queryset.update(status='resolved', resolved_at=timezone.now(), resolved_by=request.user)
        self.message_user(request, f"{queryset.count()} bug report(s) marked as resolved.")
    mark_resolved.short_description = "Mark as Resolved"

    def mark_wont_fix(self, request, queryset):
        queryset.update(status='wont_fix')
        self.message_user(request, f"{queryset.count()} bug report(s) marked as won't fix.")
    mark_wont_fix.short_description = "Mark as Won't Fix"

    def has_add_permission(self, request):
        return False  # Bug reports are submitted through the form only


@admin.register(IPWhitelist, site=admin_site)
class IPWhitelistAdmin(admin.ModelAdmin):
    list_display = ('ip_address', 'description', 'added_by', 'added_at', 'is_active')
    list_filter = ('is_active', 'added_at')
    search_fields = ('ip_address', 'description')
    readonly_fields = ('added_at',)
    ordering = ('-added_at',)


@admin.register(IPBlacklist, site=admin_site)
class IPBlacklistAdmin(admin.ModelAdmin):
    list_display = ('ip_address', 'reason', 'added_by', 'added_at', 'is_active', 'expires_at', 'block_count')
    list_filter = ('is_active', 'added_at')
    search_fields = ('ip_address', 'reason')
    readonly_fields = ('added_at', 'block_count', 'last_blocked')
    ordering = ('-added_at',)


@admin.register(QuarantinedAccount, site=admin_site)
class QuarantinedAccountAdmin(admin.ModelAdmin):
    list_display = ('user', 'ip_address', 'quarantined_at', 'is_auto', 'quarantined_by', 'status_display', 'released_at', 'released_by')
    list_filter = ('is_auto', 'quarantined_at')
    search_fields = ('user__name', 'ip_address', 'reason')
    readonly_fields = ('quarantined_at',)
    ordering = ('-quarantined_at',)

    def status_display(self, obj):
        if obj.is_active:
            return format_html('<span style="background-color: #ef4444; color: white; padding: 3px 8px; border-radius: 4px; font-size: 11px;">QUARANTINED</span>')
        return format_html('<span style="background-color: #10b981; color: white; padding: 3px 8px; border-radius: 4px; font-size: 11px;">Released</span>')
    status_display.short_description = 'Status'


@admin.register(HoneypotAccess, site=admin_site)
class HoneypotAccessAdmin(admin.ModelAdmin):
    list_display = ('ip_address', 'endpoint', 'request_method', 'action_taken', 'accessed_at')
    list_filter = ('action_taken', 'request_method', 'accessed_at')
    search_fields = ('ip_address', 'endpoint', 'user_agent')
    readonly_fields = ('accessed_at', 'ip_address', 'endpoint', 'user_agent', 'referer', 'request_method', 'request_body', 'additional_data')
    ordering = ('-accessed_at',)

    def has_add_permission(self, request):
        return False


@admin.register(SystemLockdown, site=admin_site)
class SystemLockdownAdmin(admin.ModelAdmin):
    list_display = ('__str__', 'is_active', 'activated_by', 'activated_at', 'deactivated_by', 'deactivated_at')
    readonly_fields = ('activated_at', 'deactivated_at')

    def has_add_permission(self, request):
        return not SystemLockdown.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SecurityNotificationLog, site=admin_site)
class SecurityNotificationLogAdmin(admin.ModelAdmin):
    list_display = ('sent_at', 'severity_badge', 'event_type', 'ip_address', 'user', 'email_sent')
    list_filter = ('severity', 'email_sent', 'sent_at')
    search_fields = ('event_type', 'ip_address', 'details', 'user__name')
    readonly_fields = ('sent_at', 'event_type', 'severity', 'details', 'ip_address', 'user', 'email_sent_to', 'email_sent', 'email_error')
    ordering = ('-sent_at',)

    def severity_badge(self, obj):
        colors = {'low': '#10b981', 'medium': '#f59e0b', 'high': '#ef4444', 'critical': '#7f1d1d'}
        color = colors.get(obj.severity, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold;">{}</span>',
            color,
            obj.get_severity_display()
        )
    severity_badge.short_description = 'Severity'
    severity_badge.admin_order_field = 'severity'

    def has_add_permission(self, request):
        return False



# === SECURITY — USER WATCH FLAG ===

@admin.register(UserWatchFlag, site=admin_site)
class UserWatchFlagAdmin(admin.ModelAdmin):
    list_display = ('user', 'active_badge', 'reason_short', 'created_by', 'created_at', 'updated_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('user__name', 'user__username', 'reason', 'notes', 'created_by__name')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ['user', 'created_by']

    fieldsets = (
        ('Watched User', {
            'fields': ('user', 'is_active')
        }),
        ('Flag Details', {
            'fields': ('reason', 'notes')
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def active_badge(self, obj):
        if obj.is_active:
            return format_html('<span style="background-color: #ef4444; color: white; padding: 3px 8px; border-radius: 4px; font-weight: bold;">ACTIVE</span>')
        return format_html('<span style="background-color: #f59e0b; color: white; padding: 3px 8px; border-radius: 4px;">PAUSED</span>')
    active_badge.short_description = 'Status'
    active_badge.admin_order_field = 'is_active'

    def reason_short(self, obj):
        return obj.reason[:60] + '...' if len(obj.reason) > 60 else obj.reason
    reason_short.short_description = 'Reason'


# === SECURITY — LOGIN LOCKOUT ===

@admin.register(LoginLockout, site=admin_site)
class LoginLockoutAdmin(admin.ModelAdmin):
    list_display = ('ip_address', 'username', 'source_badge', 'active_badge', 'locked_at', 'expires_at', 'cleared_by')
    list_filter = ('source', 'is_cleared', 'locked_at')
    search_fields = ('ip_address', 'username')
    ordering = ('-locked_at',)
    readonly_fields = ('locked_at', 'is_active_display')
    list_per_page = 50

    fieldsets = (
        ('Lockout Details', {
            'fields': ('ip_address', 'username', 'source', 'locked_at', 'expires_at', 'is_active_display')
        }),
        ('Resolution', {
            'fields': ('is_cleared', 'cleared_at', 'cleared_by')
        }),
    )

    def source_badge(self, obj):
        colors = {
            'ip': '#3b82f6',
            'middleware_ip': '#8b5cf6',
            'middleware_user': '#f97316',
        }
        color = colors.get(obj.source, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 7px; border-radius: 4px; font-size: 11px;">{}</span>',
            color,
            obj.get_source_display()
        )
    source_badge.short_description = 'Source'
    source_badge.admin_order_field = 'source'

    def active_badge(self, obj):
        if obj.is_active:
            return format_html('<span style="color: #ef4444; font-weight: bold;">● Active</span>')
        if obj.is_cleared:
            return format_html('<span style="color: #10b981;">✓ Cleared</span>')
        return format_html('<span style="color: #6b7280;">Expired</span>')
    active_badge.short_description = 'State'

    def is_active_display(self, obj):
        return 'Yes — lockout is currently enforced' if obj.is_active else 'No — expired or manually cleared'
    is_active_display.short_description = 'Currently Active'

    def has_add_permission(self, request):
        return False


# === KAI COMMITTEE ===

@admin.register(KaiReport, site=admin_site)
class KaiReportAdmin(admin.ModelAdmin):
    list_display = ('title', 'category_badge', 'status_badge', 'deliberation_badge', 'submitted_by', 'targeted_to', 'submitted_at', 'accused_notified')
    list_filter = ('status', 'category', 'deliberation_outcome', 'accused_notified', 'submitted_at')
    search_fields = ('title', 'description', 'submitted_by__name', 'targeted_to__name', 'chair_notes')
    ordering = ('-submitted_at',)
    readonly_fields = ('submitted_at',)
    list_per_page = 50
    date_hierarchy = 'submitted_at'
    autocomplete_fields = ['submitted_by', 'targeted_to', 'reviewed_by']
    filter_horizontal = ('related_reports',)

    fieldsets = (
        ('Report Information', {
            'fields': ('title', 'category', 'description', 'attachment')
        }),
        ('Submission', {
            'fields': ('submitted_by', 'submitted_at', 'targeted_to')
        }),
        ('Status & Review', {
            'fields': ('status', 'reviewed_by', 'reviewed_at', 'tags', 'chair_notes')
        }),
        ('Deliberation', {
            'fields': ('deliberation_outcome', 'committee_notes', 'closed_by_accused_request'),
            'classes': ('collapse',)
        }),
        ('Accused Notification', {
            'fields': ('accused_notified', 'accused_notified_at', 'accused_notification_message', 'accused_email_viewed_at'),
            'classes': ('collapse',)
        }),
        ('Related Reports', {
            'fields': ('related_reports',),
            'classes': ('collapse',)
        }),
    )

    def category_badge(self, obj):
        colors = {
            'academic': '#3b82f6',
            'behavioral': '#f59e0b',
            'hazing': '#dc2626',
            'social': '#8b5cf6',
            'financial': '#10b981',
            'other': '#6b7280',
        }
        color = colors.get(obj.category, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 7px; border-radius: 4px; font-size: 11px;">{}</span>',
            color,
            obj.get_category_display()
        )
    category_badge.short_description = 'Category'
    category_badge.admin_order_field = 'category'

    def status_badge(self, obj):
        colors = {'pending': '#f59e0b', 'reviewed': '#3b82f6', 'archived': '#6b7280'}
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 7px; border-radius: 4px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def deliberation_badge(self, obj):
        colors = {
            'pending': '#6b7280',
            'under_investigation': '#f59e0b',
            'scheduled': '#3b82f6',
            'heard': '#8b5cf6',
            'warning_issued': '#f97316',
            'sanctions_applied': '#ef4444',
            'mediation': '#06b6d4',
            'referred': '#dc2626',
            'dismissed': '#10b981',
            'thrown_out': '#6b7280',
        }
        color = colors.get(obj.deliberation_outcome, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 7px; border-radius: 4px; font-size: 11px;">{}</span>',
            color,
            obj.get_deliberation_outcome_display()
        )
    deliberation_badge.short_description = 'Deliberation'
    deliberation_badge.admin_order_field = 'deliberation_outcome'


@admin.register(KaiReportTemplate, site=admin_site)
class KaiReportTemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'active_badge', 'created_by', 'created_at', 'updated_at')
    list_filter = ('is_active', 'category')
    search_fields = ('name', 'description', 'title_template')
    ordering = ('category', 'name')
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ['created_by']

    fieldsets = (
        ('Template Info', {
            'fields': ('name', 'description', 'category', 'is_active')
        }),
        ('Template Content', {
            'fields': ('title_template', 'description_template', 'suggested_tags')
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def active_badge(self, obj):
        if obj.is_active:
            return format_html('<span style="color: #10b981; font-weight: bold;">✓ Active</span>')
        return format_html('<span style="color: #6b7280;">✗ Inactive</span>')
    active_badge.short_description = 'Status'
    active_badge.admin_order_field = 'is_active'


# === PASSED RESOLUTIONS ===

@admin.register(PassedResolution, site=admin_site)
class PassedResolutionAdmin(admin.ModelAdmin):
    list_display = ('title', 'date_passed', 'border_color', 'display_order', 'active_badge', 'created_by', 'created_at')
    list_filter = ('is_active', 'border_color', 'date_passed')
    search_fields = ('title', 'description', 'impact_summary')
    ordering = ('display_order', '-date_passed')
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ['created_by']
    list_per_page = 50

    fieldsets = (
        ('Resolution Details', {
            'fields': ('title', 'description', 'date_passed')
        }),
        ('Document', {
            'fields': ('legislation', 'document'),
            'description': 'Link to existing legislation OR upload a document directly.'
        }),
        ('Display', {
            'fields': ('border_color', 'impact_summary', 'display_order', 'is_active')
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def active_badge(self, obj):
        if obj.is_active:
            return format_html('<span style="color: #10b981; font-weight: bold;">✓ Visible</span>')
        return format_html('<span style="color: #6b7280;">✗ Hidden</span>')
    active_badge.short_description = 'Visible'
    active_badge.admin_order_field = 'is_active'


# === CHAPTER MINUTES ===

@admin.register(ChapterMinutes, site=admin_site)
class ChapterMinutesAdmin(admin.ModelAdmin):
    list_display = ('title', 'date', 'status_badge', 'committee', 'created_by', 'attendance_taken', 'edited_after_publish')
    list_filter = ('status', 'attendance_taken', 'edited_after_publish', 'date')
    search_fields = ('title', 'created_by__name')
    ordering = ('-date', '-start_time')
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ['created_by', 'last_edit_by', 'event']
    list_per_page = 50
    date_hierarchy = 'date'

    fieldsets = (
        ('Minutes Info', {
            'fields': ('title', 'date', 'start_time', 'end_time', 'committee', 'event', 'status')
        }),
        ('Attendance', {
            'fields': ('attendance_taken',)
        }),
        ('Visibility', {
            'fields': ('publish_visibility', 'published_document')
        }),
        ('Edit History', {
            'fields': ('edited_after_publish', 'last_edit_at', 'last_edit_by', 'last_edit_reason'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def status_badge(self, obj):
        colors = {'draft': '#6b7280', 'finalized': '#f59e0b', 'published': '#10b981'}
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'


# === SLATING ===

@admin.register(SlatingPeriod, site=admin_site)
class SlatingPeriodAdmin(admin.ModelAdmin):
    list_display = ('name', 'academic_term', 'status_badge', 'required_approval_percentage', 'slating_committee', 'created_by', 'created_at')
    list_filter = ('status', 'academic_term')
    search_fields = ('name', 'description', 'academic_term')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')
    autocomplete_fields = ['created_by']
    list_per_page = 25

    fieldsets = (
        ('Period Info', {
            'fields': ('name', 'description', 'academic_term', 'status', 'slating_committee')
        }),
        ('Schedule', {
            'fields': ('nominations_open_at', 'nominations_close_at', 'deliberation_start_at', 'voting_open_at', 'voting_close_at', 'results_publish_at')
        }),
        ('Voting Configuration', {
            'fields': ('required_approval_percentage', 'max_slate_voting_attempts', 'current_voting_attempt', 'allow_abstain')
        }),
        ('GPA Configuration', {
            'fields': ('min_gpa_requirement', 'gpa_level_2_threshold')
        }),
        ('Settings', {
            'fields': ('extra_settings', 'admin_transferred'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def status_badge(self, obj):
        colors = {
            'setup': '#6b7280',
            'nominations_open': '#10b981',
            'nominations_closed': '#f59e0b',
            'deliberation': '#8b5cf6',
            'voting_open': '#3b82f6',
            'voting_closed': '#f97316',
            'results_published': '#10b981',
            'archived': '#9ca3af',
        }
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'


@admin.register(SlatingApplication, site=admin_site)
class SlatingApplicationAdmin(admin.ModelAdmin):
    list_display = ('applicant', 'period', 'status_badge', 'gpa_level', 'reported_gpa', 'gpa_verified', 'submitted_at', 'reviewer')
    list_filter = ('status', 'gpa_level', 'gpa_verified', 'period')
    search_fields = ('applicant__name', 'period__name', 'review_notes')
    ordering = ('-submitted_at', '-created_at')
    readonly_fields = ('created_at', 'updated_at', 'submitted_at')
    autocomplete_fields = ['applicant', 'reviewer']
    list_per_page = 50

    fieldsets = (
        ('Application', {
            'fields': ('period', 'applicant', 'status', 'submitted_at')
        }),
        ('Position Preferences', {
            'fields': ('position_preferences',)
        }),
        ('GPA', {
            'fields': ('reported_gpa', 'gpa_level', 'gpa_verified', 'gpa_screenshot')
        }),
        ('Review', {
            'fields': ('reviewer', 'review_notes')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def status_badge(self, obj):
        colors = {
            'draft': '#6b7280',
            'submitted': '#3b82f6',
            'under_review': '#f59e0b',
            'interview_scheduled': '#8b5cf6',
            'interviewed': '#06b6d4',
            'recommended': '#10b981',
            'not_recommended': '#ef4444',
            'withdrawn': '#9ca3af',
            'slated': '#059669',
        }
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'


# === ANNOUNCEMENT EMAIL LOG ===

@admin.register(AnnouncementEmailLog, site=admin_site)
class AnnouncementEmailLogAdmin(admin.ModelAdmin):
    list_display = ('announcement', 'status_badge', 'emails_sent', 'emails_failed', 'users_matching_visibility', 'initiated_by', 'created_at', 'completed_at')
    list_filter = ('status', 'created_at')
    search_fields = ('announcement__title', 'initiated_by__name', 'error_message')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'completed_at', 'announcement', 'initiated_by',
                       'total_active_users', 'users_matching_visibility', 'users_with_valid_email',
                       'emails_sent', 'emails_failed', 'visible_to_raw', 'expanded_member_types',
                       'error_message', 'console_log')
    list_per_page = 50
    date_hierarchy = 'created_at'

    fieldsets = (
        ('Send Details', {
            'fields': ('announcement', 'initiated_by', 'status', 'created_at', 'completed_at')
        }),
        ('Targeting', {
            'fields': ('visible_to_raw', 'expanded_member_types', 'total_active_users', 'users_matching_visibility', 'users_with_valid_email')
        }),
        ('Results', {
            'fields': ('emails_sent', 'emails_failed', 'error_message')
        }),
        ('Debug Log', {
            'fields': ('console_log',),
            'classes': ('collapse',)
        }),
    )

    def status_badge(self, obj):
        colors = {
            'warming_up': '#6b7280',
            'pending': '#6b7280',
            'started': '#3b82f6',
            'completed': '#10b981',
            'partial': '#f59e0b',
            'failed': '#ef4444',
            'cancelled': '#6b7280',
        }
        color = colors.get(obj.status, '#6b7280')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_badge.short_description = 'Status'
    status_badge.admin_order_field = 'status'

    def has_add_permission(self, request):
        return False


# === USER SESSION ===

@admin.register(UserSession, site=admin_site)
class UserSessionAdmin(admin.ModelAdmin):
    list_display = ('user', 'device_type', 'browser', 'operating_system', 'ip_address', 'location', 'last_activity', 'is_current')
    list_filter = ('device_type', 'is_current', 'last_activity')
    search_fields = ('user__name', 'ip_address', 'browser', 'operating_system', 'location')
    ordering = ('-last_activity',)
    readonly_fields = ('session_key', 'created_at', 'last_activity')
    list_per_page = 100
    date_hierarchy = 'last_activity'
    autocomplete_fields = ['user']

    fieldsets = (
        ('Session', {
            'fields': ('user', 'session_key', 'is_current')
        }),
        ('Device & Browser', {
            'fields': ('device_type', 'browser', 'operating_system', 'user_agent')
        }),
        ('Network', {
            'fields': ('ip_address', 'location')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'last_activity'),
            'classes': ('collapse',)
        }),
    )

    def has_add_permission(self, request):
        return False


original_get_urls = admin.site.get_urls

def custom_admin_urls():
    return [path('view-logs/', view_logs, name="view_logs")] + original_get_urls()

admin.site.get_urls = custom_admin_urls
