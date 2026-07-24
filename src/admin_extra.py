"""
Django-admin registrations for every model NOT already covered in admin.py
(v3.16.0 — full-coverage pass; admin.py had 44 of 122 models).

Philosophy: the Django admin is the raw-data escape hatch — admin-v2 and the
in-app pages are the everyday tools. So content/config models get normal
editable admins, while records the APP creates (logs, audit trails, ballots,
responses) are view-only here: fixing those by hand in admin would bypass the
in-app workflows and their signals/side effects.

Sensitive-data rules applied below:
  * Secrets never render in admin forms: APIToken.key, EmailVerificationToken
    .token, PushSubscription p256dh/auth, WebAuthnCredential key material are
    excluded (list views show safe prefixes only).
  * Ballot/vote records (SlatingBallot, SlatingVote, CommitteeVote, poll
    responses) are strictly read-only — vote integrity depends on the app's
    hashing/anonymity flow, not admin edits.
  * SlatingInterview is read-only so the notes-destruction feature can't be
    undone by an admin edit resurrecting destroyed notes.

Imported at the bottom of admin.py, after admin_site exists.
"""
from django.contrib import admin

from .admin import admin_site, export_as_csv
from .models import (
    # audit / analytics
    AdminActionLog, PageVisit,
    # announcements / polls
    AnnouncementPoll, AnnouncementPollQuestion, AnnouncementPollOption,
    AnnouncementPollResponse, AnnouncementPollAnswer, AnnouncementEmailRecipient,
    # api
    APIToken, APIAccessLog,
    # chat
    ChatChannelPermission, ChatNotificationPreference,
    # cnb (governing documents)
    GoverningDocument, Article, Section, Resolution, ResolutionAmendment,
    ResolutionCollaborator,
    # committees
    CommitteePermissions, CommitteeLegislation, CommitteeVote,
    # documents / minutes
    CommitteeMinutes, ChapterFolder, MinutesSection, MinutesMotion,
    # education (pledge program)
    PledgeTask, PledgePageRestriction, PledgeTaskCompletion, PledgeTaskQuestion,
    PledgeQuizAnswer,
    # events
    EventReminderLog, EventReminderRecipient, EventSignup,
    # guide
    GuideTour, GuideTourStep, UserTourProgress, GuideArticle,
    # kai
    KaiReportActivity, KaiFormField, KaiReportFieldResponse, KaiClosureRequest,
    KaiMemberPermission,
    # landing page
    ResolutionSectionImpact, LandingPageContent, LandingPagePhoto,
    ContactSubmission, LandingPageSocialLink, LandingPageContactTopic,
    LandingPageFormLink,
    # notifications
    NotificationSchedule, NotificationLog, PushSubscription,
    # recruitment
    RecruitmentCandidate, RecruitmentCandidateNote, RecruitmentEvent,
    RecruitmentEventRSVP, RecruitmentMemberPermission,
    # security extras
    EmailVerificationToken,
    # service hours
    ServicePeriod, ServiceMemberExpectation, ServiceHoursSubmission,
    ServiceFormField, ServiceFieldResponse, ServiceActivity,
    ServiceHoursAdjustment, ServiceEvent,
    # slating
    SlatingPosition, SlatingFormField, SlatingApplicationResponse,
    SlatingInterview, Slate, SlateCandidate, SlatingAttendance, SlatingBallot,
    SlatingVote, SlatingActivity,
    # songs
    SongCategory, Song,
    # users extras
    RoleHistory, UserPreferences, TwoFactorRequirement,
    # webauthn
    WebAuthnCredential,
)


# ─────────────────────────────────────────────────────────────── base classes

class ReadOnlyAdmin(admin.ModelAdmin):
    """View-only: rows are created/managed by the app, admin is for inspection."""
    actions = [export_as_csv]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class ViewDeleteAdmin(ReadOnlyAdmin):
    """View + delete (cleanup escape hatch), still no add/edit."""

    def has_delete_permission(self, request, obj=None):
        return True


# ─────────────────────────────────────────────────────── audit & analytics

@admin.register(AdminActionLog, site=admin_site)
class AdminActionLogAdmin(ReadOnlyAdmin):
    list_display = ('timestamp', 'actor', 'action', 'target_user', 'target_repr', 'ip_address')
    list_filter = ('action', 'timestamp')
    search_fields = ('actor__username', 'actor__name', 'target_user__username', 'target_repr', 'detail')
    date_hierarchy = 'timestamp'


@admin.register(PageVisit, site=admin_site)
class PageVisitAdmin(ReadOnlyAdmin):
    list_display = ('user', 'path', 'count')
    search_fields = ('user__username', 'user__name', 'path')


# ──────────────────────────────────────────────────── announcements & polls

class AnnouncementPollQuestionInline(admin.TabularInline):
    model = AnnouncementPollQuestion
    extra = 0


@admin.register(AnnouncementPoll, site=admin_site)
class AnnouncementPollAdmin(admin.ModelAdmin):
    list_display = ('title', 'announcement', 'is_anonymous', 'is_open', 'closes_at', 'created_by', 'created_at')
    list_filter = ('is_open', 'is_anonymous')
    search_fields = ('title', 'description')
    inlines = [AnnouncementPollQuestionInline]


class AnnouncementPollOptionInline(admin.TabularInline):
    model = AnnouncementPollOption
    extra = 0


@admin.register(AnnouncementPollQuestion, site=admin_site)
class AnnouncementPollQuestionAdmin(admin.ModelAdmin):
    list_display = ('poll', 'text', 'question_type', 'order', 'is_required')
    list_filter = ('question_type',)
    search_fields = ('text',)
    inlines = [AnnouncementPollOptionInline]


@admin.register(AnnouncementPollOption, site=admin_site)
class AnnouncementPollOptionAdmin(admin.ModelAdmin):
    list_display = ('question', 'text', 'order')
    search_fields = ('text',)


@admin.register(AnnouncementPollResponse, site=admin_site)
class AnnouncementPollResponseAdmin(ReadOnlyAdmin):
    """Read-only. NOTE: respondent is visible here even for polls marked
    is_anonymous — anonymity is enforced at the app layer, so treat this
    view as officer-eyes-only and don't screenshot it into chapter chats."""
    list_display = ('poll', 'respondent', 'submitted_at')
    list_filter = ('submitted_at',)
    search_fields = ('poll__title',)


@admin.register(AnnouncementPollAnswer, site=admin_site)
class AnnouncementPollAnswerAdmin(ReadOnlyAdmin):
    list_display = ('response', 'question', 'text_answer')
    search_fields = ('text_answer',)


@admin.register(AnnouncementEmailRecipient, site=admin_site)
class AnnouncementEmailRecipientAdmin(ReadOnlyAdmin):
    list_display = ('email_log', 'user_name', 'user_email', 'status', 'created_at')
    list_filter = ('status',)
    search_fields = ('user_name', 'user_email')


# ─────────────────────────────────────────────────────────────────────── api

@admin.register(APIToken, site=admin_site)
class APITokenAdmin(ReadOnlyAdmin):
    """Read-only: token lifecycle (request → approve → revoke) lives in the
    in-app flow, which handles notification + audit. The raw key never
    renders here — list shows an 8-char prefix."""
    exclude = ('key',)
    list_display = ('name', 'user', 'key_prefix', 'status', 'created_at', 'last_used_at', 'expires_at')
    list_filter = ('status',)
    search_fields = ('name', 'user__username', 'user__name')

    @admin.display(description='Key')
    def key_prefix(self, obj):
        return f'{obj.key[:8]}…' if obj.key else '—'


@admin.register(APIAccessLog, site=admin_site)
class APIAccessLogAdmin(ReadOnlyAdmin):
    list_display = ('timestamp', 'username', 'token_key_prefix', 'method', 'endpoint', 'response_status', 'ip_address')
    list_filter = ('method', 'response_status')
    search_fields = ('username', 'endpoint', 'ip_address', 'token_key_prefix')
    date_hierarchy = 'timestamp'


# ──────────────────────────────────────────────────────────────────── chat

@admin.register(ChatChannelPermission, site=admin_site)
class ChatChannelPermissionAdmin(admin.ModelAdmin):
    list_display = ('channel', 'user', 'member_type', 'can_read', 'can_write', 'can_edit', 'can_delete', 'expires_at')
    list_filter = ('can_read', 'can_write', 'chairs_only', 'officers_only', 'alumni_only')
    search_fields = ('user__username', 'user__name')


@admin.register(ChatNotificationPreference, site=admin_site)
class ChatNotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ('user', 'channel', 'level')
    list_filter = ('level',)
    search_fields = ('user__username', 'user__name')


# ──────────────────────────────────────── cnb (constitution & bylaws system)

class ArticleInline(admin.TabularInline):
    model = Article
    extra = 0
    fields = ('number', 'title', 'display_order', 'is_active')


@admin.register(GoverningDocument, site=admin_site)
class GoverningDocumentAdmin(admin.ModelAdmin):
    list_display = ('title', 'doc_type', 'last_reviewed', 'amendment_protection_weeks')
    list_filter = ('doc_type',)
    search_fields = ('title',)
    inlines = [ArticleInline]


class SectionInline(admin.TabularInline):
    model = Section
    extra = 0
    fields = ('number', 'title', 'display_order', 'is_active')


@admin.register(Article, site=admin_site)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ('document', 'number', 'title', 'display_order', 'is_active')
    list_filter = ('is_active', 'document')
    search_fields = ('title', 'number')
    inlines = [SectionInline]


@admin.register(Section, site=admin_site)
class SectionAdmin(admin.ModelAdmin):
    list_display = ('article', 'number', 'title', 'display_order', 'is_active', 'amendment_protected')
    list_filter = ('is_active', 'amendment_protected')
    search_fields = ('title', 'number', 'content')


@admin.register(Resolution, site=admin_site)
class ResolutionAdmin(admin.ModelAdmin):
    list_display = ('title', 'resolution_type', 'status', 'vote_date', 'created_by', 'created_at')
    list_filter = ('resolution_type', 'status')
    search_fields = ('title', 'authors', 'sponsors')
    date_hierarchy = 'created_at'


@admin.register(ResolutionAmendment, site=admin_site)
class ResolutionAmendmentAdmin(admin.ModelAdmin):
    list_display = ('resolution', 'section', 'amendment_type', 'applied', 'added_at')
    list_filter = ('amendment_type', 'applied')
    search_fields = ('resolution__title',)


@admin.register(ResolutionCollaborator, site=admin_site)
class ResolutionCollaboratorAdmin(admin.ModelAdmin):
    list_display = ('resolution', 'user', 'role', 'added_by', 'added_at')
    list_filter = ('role',)
    search_fields = ('resolution__title', 'user__username', 'user__name')


@admin.register(ResolutionSectionImpact, site=admin_site)
class ResolutionSectionImpactAdmin(admin.ModelAdmin):
    list_display = ('resolution', 'section_name', 'section_type', 'display_order')
    list_filter = ('section_type',)
    search_fields = ('section_name',)


# ───────────────────────────────────────────────────────────── committees

@admin.register(CommitteePermissions, site=admin_site)
class CommitteePermissionsAdmin(admin.ModelAdmin):
    list_display = ('committee', 'user', 'can_view_docs', 'can_upload_docs', 'can_vote',
                    'can_manage_members', 'can_view_results', 'can_take_minutes')
    list_filter = ('committee',)
    search_fields = ('user__username', 'user__name')


@admin.register(CommitteeLegislation, site=admin_site)
class CommitteeLegislationAdmin(admin.ModelAdmin):
    list_display = ('title', 'committee', 'posted_by', 'vote_mode', 'status',
                    'voting_closed', 'passed', 'pushed_to_chapter', 'created_at')
    list_filter = ('vote_mode', 'status', 'voting_closed', 'passed', 'pushed_to_chapter')
    search_fields = ('title', 'description')
    date_hierarchy = 'created_at'


@admin.register(CommitteeVote, site=admin_site)
class CommitteeVoteAdmin(ReadOnlyAdmin):
    """Read-only: same integrity rule as chapter votes. NOTE: voter identity
    is visible here even on anonymous_vote legislation — anonymity is an
    app-layer promise."""
    list_display = ('legislation', 'user', 'vote_choice', 'is_active', 'created_at')
    list_filter = ('vote_choice', 'is_active')
    search_fields = ('legislation__title', 'user__username', 'user__name')


# ──────────────────────────────────────────────────── documents & minutes

@admin.register(ChapterFolder, site=admin_site)
class ChapterFolderAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_by', 'created_at')
    search_fields = ('name', 'description')


@admin.register(CommitteeMinutes, site=admin_site)
class CommitteeMinutesAdmin(admin.ModelAdmin):
    list_display = ('title', 'committee', 'date', 'posted_by', 'created_at')
    list_filter = ('committee',)
    search_fields = ('title', 'content')
    date_hierarchy = 'date'


@admin.register(MinutesSection, site=admin_site)
class MinutesSectionAdmin(admin.ModelAdmin):
    list_display = ('minutes', 'section_type', 'order', 'title')
    list_filter = ('section_type',)
    search_fields = ('title', 'content')


@admin.register(MinutesMotion, site=admin_site)
class MinutesMotionAdmin(admin.ModelAdmin):
    list_display = ('section', 'motion_type', 'result', 'received_second',
                    'votes_for', 'votes_against', 'votes_abstain')
    list_filter = ('motion_type', 'result', 'received_second')
    search_fields = ('motion_text',)


# ─────────────────────────────────────────────── education (pledge program)

class PledgeTaskQuestionInline(admin.TabularInline):
    model = PledgeTaskQuestion
    extra = 0


@admin.register(PledgeTask, site=admin_site)
class PledgeTaskAdmin(admin.ModelAdmin):
    list_display = ('title', 'task_type', 'phase', 'due_date', 'is_required',
                    'points', 'is_published', 'is_active')
    list_filter = ('task_type', 'phase', 'is_required', 'is_published', 'is_active')
    search_fields = ('title', 'description')
    inlines = [PledgeTaskQuestionInline]


@admin.register(PledgePageRestriction, site=admin_site)
class PledgePageRestrictionAdmin(admin.ModelAdmin):
    list_display = ('url_name', 'display_name', 'updated_by', 'updated_at')
    search_fields = ('url_name', 'display_name')


@admin.register(PledgeTaskCompletion, site=admin_site)
class PledgeTaskCompletionAdmin(admin.ModelAdmin):
    list_display = ('task', 'pledge', 'status', 'reviewed_by', 'completed_at')
    list_filter = ('status',)
    search_fields = ('task__title', 'pledge__username', 'pledge__name')


@admin.register(PledgeTaskQuestion, site=admin_site)
class PledgeTaskQuestionAdmin(admin.ModelAdmin):
    list_display = ('task', 'question_text', 'display_order')
    search_fields = ('question_text',)


@admin.register(PledgeQuizAnswer, site=admin_site)
class PledgeQuizAnswerAdmin(ReadOnlyAdmin):
    list_display = ('question', 'pledge', 'submitted_at')
    search_fields = ('pledge__username', 'pledge__name', 'answer_text')


# ──────────────────────────────────────────────────────────────── events

@admin.register(EventSignup, site=admin_site)
class EventSignupAdmin(admin.ModelAdmin):
    list_display = ('event', 'user', 'signed_up_at', 'is_cancelled', 'waitlist_position')
    list_filter = ('is_cancelled',)
    search_fields = ('event__title', 'user__username', 'user__name')


@admin.register(EventReminderLog, site=admin_site)
class EventReminderLogAdmin(ViewDeleteAdmin):
    list_display = ('event', 'reminder_slot', 'status', 'users_eligible',
                    'notifications_dispatched', 'sent_at')
    list_filter = ('status',)
    search_fields = ('event__title',)


@admin.register(EventReminderRecipient, site=admin_site)
class EventReminderRecipientAdmin(ReadOnlyAdmin):
    list_display = ('reminder_log', 'user_name', 'user_member_type', 'status')
    list_filter = ('status',)
    search_fields = ('user_name',)


# ──────────────────────────────────────────────────────────────── guide

class GuideTourStepInline(admin.TabularInline):
    model = GuideTourStep
    extra = 0
    fields = ('step_number', 'title', 'target_page', 'position')


@admin.register(GuideTour, site=admin_site)
class GuideTourAdmin(admin.ModelAdmin):
    list_display = ('name', 'category', 'is_active', 'display_order', 'estimated_time', 'updated_at')
    list_filter = ('category', 'is_active')
    search_fields = ('name', 'description')
    prepopulated_fields = {'slug': ('name',)}
    inlines = [GuideTourStepInline]


@admin.register(GuideTourStep, site=admin_site)
class GuideTourStepAdmin(admin.ModelAdmin):
    list_display = ('tour', 'step_number', 'title', 'target_page', 'position')
    list_filter = ('tour',)
    search_fields = ('title', 'content')


@admin.register(UserTourProgress, site=admin_site)
class UserTourProgressAdmin(ViewDeleteAdmin):
    list_display = ('user', 'tour', 'current_step', 'completed', 'started_at', 'completed_at')
    list_filter = ('completed', 'tour')
    search_fields = ('user__username', 'user__name')


@admin.register(GuideArticle, site=admin_site)
class GuideArticleAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'is_published', 'display_order', 'updated_at')
    list_filter = ('category', 'is_published')
    search_fields = ('title', 'summary', 'content')
    prepopulated_fields = {'slug': ('title',)}


# ─────────────────────────────────────────── kai (confidential — tread lightly)

@admin.register(KaiFormField, site=admin_site)
class KaiFormFieldAdmin(admin.ModelAdmin):
    list_display = ('label', 'field_name', 'field_type', 'section', 'display_order',
                    'is_required', 'is_active', 'is_builtin')
    list_filter = ('field_type', 'is_active', 'is_builtin')
    search_fields = ('label', 'field_name')


@admin.register(KaiReportActivity, site=admin_site)
class KaiReportActivityAdmin(ReadOnlyAdmin):
    list_display = ('report', 'user', 'action', 'timestamp')
    list_filter = ('action',)
    date_hierarchy = 'timestamp'


@admin.register(KaiReportFieldResponse, site=admin_site)
class KaiReportFieldResponseAdmin(ReadOnlyAdmin):
    list_display = ('report', 'field', 'created_at')


@admin.register(KaiClosureRequest, site=admin_site)
class KaiClosureRequestAdmin(ReadOnlyAdmin):
    """Read-only: closure review is an in-app Kai-committee flow."""
    list_display = ('report', 'requested_by', 'request_type', 'status',
                    'requested_at', 'reviewed_by')
    list_filter = ('request_type', 'status')


@admin.register(KaiMemberPermission, site=admin_site)
class KaiMemberPermissionAdmin(admin.ModelAdmin):
    list_display = ('user', 'committee', 'can_view_report_details', 'can_view_submitter_identity',
                    'can_view_accused_identity', 'can_close_cases', 'granted_by', 'granted_at')
    list_filter = ('can_view_submitter_identity', 'can_view_accused_identity', 'can_close_cases')
    search_fields = ('user__username', 'user__name')


# ─────────────────────────────────────────────────────────── landing page

@admin.register(LandingPageContent, site=admin_site)
class LandingPageContentAdmin(admin.ModelAdmin):
    list_display = ('tagline', 'recruitment_banner_active', 'updated_by', 'updated_at')


@admin.register(LandingPagePhoto, site=admin_site)
class LandingPagePhotoAdmin(admin.ModelAdmin):
    list_display = ('caption', 'display_order', 'uploaded_by', 'uploaded_at')


@admin.register(ContactSubmission, site=admin_site)
class ContactSubmissionAdmin(admin.ModelAdmin):
    """Submitted data is immutable; only the is_read flag is editable."""
    list_display = ('name', 'email', 'topic', 'submitted_at', 'is_read')
    list_filter = ('is_read', 'topic')
    search_fields = ('name', 'email', 'message')
    readonly_fields = ('name', 'email', 'message', 'topic', 'recipient_email', 'submitted_at')
    date_hierarchy = 'submitted_at'

    def has_add_permission(self, request):
        return False


@admin.register(LandingPageSocialLink, site=admin_site)
class LandingPageSocialLinkAdmin(admin.ModelAdmin):
    list_display = ('label', 'url', 'display_order')


@admin.register(LandingPageContactTopic, site=admin_site)
class LandingPageContactTopicAdmin(admin.ModelAdmin):
    list_display = ('label', 'role_code', 'display_order', 'is_active')
    list_filter = ('is_active',)


@admin.register(LandingPageFormLink, site=admin_site)
class LandingPageFormLinkAdmin(admin.ModelAdmin):
    list_display = ('title', 'url', 'button_text', 'display_order', 'is_active')
    list_filter = ('is_active',)


# ─────────────────────────────────────────────────────────── notifications

@admin.register(NotificationSchedule, site=admin_site)
class NotificationScheduleAdmin(admin.ModelAdmin):
    list_display = ('name', 'notification_type', 'target_audience', 'send_email',
                    'send_in_app', 'is_active')
    list_filter = ('notification_type', 'target_audience', 'is_active')
    search_fields = ('name', 'description')


@admin.register(NotificationLog, site=admin_site)
class NotificationLogAdmin(ViewDeleteAdmin):
    list_display = ('title', 'notification_type', 'status', 'recipient_count',
                    'successful_count', 'failed_count', 'sent_at')
    list_filter = ('status', 'notification_type')
    search_fields = ('title', 'message')
    date_hierarchy = 'created_at'


@admin.register(PushSubscription, site=admin_site)
class PushSubscriptionAdmin(ViewDeleteAdmin):
    """View/delete only — endpoint keys (p256dh/auth) never render."""
    exclude = ('p256dh', 'auth')
    list_display = ('user', 'user_agent', 'created_at', 'last_used_at')
    search_fields = ('user__username', 'user__name')


# ───────────────────────────────────────────────────────────── recruitment

@admin.register(RecruitmentCandidate, site=admin_site)
class RecruitmentCandidateAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'status', 'committee', 'assigned_to',
                    'last_contacted', 'created_at')
    list_filter = ('status', 'committee')
    search_fields = ('name', 'email', 'phone')


@admin.register(RecruitmentCandidateNote, site=admin_site)
class RecruitmentCandidateNoteAdmin(admin.ModelAdmin):
    list_display = ('candidate', 'author', 'created_at', 'updated_at')
    search_fields = ('candidate__name', 'body')


@admin.register(RecruitmentEvent, site=admin_site)
class RecruitmentEventAdmin(admin.ModelAdmin):
    list_display = ('event', 'committee', 'event_type', 'visibility', 'status',
                    'rsvp_reminder_enabled')
    list_filter = ('event_type', 'visibility', 'status')
    search_fields = ('event__title',)


@admin.register(RecruitmentEventRSVP, site=admin_site)
class RecruitmentEventRSVPAdmin(admin.ModelAdmin):
    list_display = ('recruitment_event', 'user', 'status', 'checked_in', 'updated_at')
    list_filter = ('status', 'checked_in')
    search_fields = ('user__username', 'user__name')


@admin.register(RecruitmentMemberPermission, site=admin_site)
class RecruitmentMemberPermissionAdmin(admin.ModelAdmin):
    list_display = ('user', 'committee', 'can_manage_events', 'can_view_private',
                    'can_take_attendance', 'granted_by', 'granted_at')
    list_filter = ('can_manage_events', 'can_view_private', 'can_take_attendance')
    search_fields = ('user__username', 'user__name')


# ─────────────────────────────────────────────────────────── security extras

@admin.register(EmailVerificationToken, site=admin_site)
class EmailVerificationTokenAdmin(ViewDeleteAdmin):
    """View/delete only — the token value itself never renders."""
    exclude = ('token',)
    list_display = ('user', 'new_email', 'created_at', 'expires_at', 'used')
    list_filter = ('used',)
    search_fields = ('user__username', 'user__name', 'new_email')


# ───────────────────────────────────────────────────────────── service hours

@admin.register(ServicePeriod, site=admin_site)
class ServicePeriodAdmin(admin.ModelAdmin):
    list_display = ('name', 'start_date', 'end_date', 'default_hours_required',
                    'requires_approval', 'is_active')
    list_filter = ('is_active', 'requires_approval')
    search_fields = ('name',)


@admin.register(ServiceMemberExpectation, site=admin_site)
class ServiceMemberExpectationAdmin(admin.ModelAdmin):
    list_display = ('period', 'member', 'expected_hours', 'created_by', 'created_at')
    list_filter = ('period',)
    search_fields = ('member__username', 'member__name')


@admin.register(ServiceHoursSubmission, site=admin_site)
class ServiceHoursSubmissionAdmin(admin.ModelAdmin):
    list_display = ('submitted_by', 'period', 'hours', 'service_date', 'organization',
                    'status', 'reviewed_by')
    list_filter = ('status', 'period')
    search_fields = ('submitted_by__username', 'submitted_by__name', 'organization', 'description')
    date_hierarchy = 'service_date'


@admin.register(ServiceFormField, site=admin_site)
class ServiceFormFieldAdmin(admin.ModelAdmin):
    list_display = ('label', 'field_name', 'field_type', 'section', 'display_order',
                    'is_required', 'is_active', 'is_builtin')
    list_filter = ('field_type', 'is_active', 'is_builtin')
    search_fields = ('label', 'field_name')


@admin.register(ServiceFieldResponse, site=admin_site)
class ServiceFieldResponseAdmin(ReadOnlyAdmin):
    list_display = ('submission', 'field', 'created_at')


@admin.register(ServiceActivity, site=admin_site)
class ServiceActivityAdmin(ReadOnlyAdmin):
    list_display = ('submission', 'user', 'action', 'timestamp')
    list_filter = ('action',)
    date_hierarchy = 'timestamp'


@admin.register(ServiceHoursAdjustment, site=admin_site)
class ServiceHoursAdjustmentAdmin(admin.ModelAdmin):
    list_display = ('period', 'member', 'hours', 'adjusted_by', 'created_at')
    list_filter = ('period',)
    search_fields = ('member__username', 'member__name', 'reason')


@admin.register(ServiceEvent, site=admin_site)
class ServiceEventAdmin(admin.ModelAdmin):
    list_display = ('event', 'period', 'hours_awarded', 'hours_applied',
                    'email_reminder_enabled', 'created_by')
    list_filter = ('hours_applied', 'email_reminder_enabled', 'period')
    search_fields = ('event__title',)


# ──────────────────────────────────────────────────────────────── slating

@admin.register(SlatingPosition, site=admin_site)
class SlatingPositionAdmin(admin.ModelAdmin):
    list_display = ('period', 'title', 'code', 'display_order', 'is_active', 'allow_abstain')
    list_filter = ('is_active', 'period')
    search_fields = ('title', 'code')


@admin.register(SlatingFormField, site=admin_site)
class SlatingFormFieldAdmin(admin.ModelAdmin):
    list_display = ('period', 'label', 'field_name', 'field_type', 'is_confidential',
                    'display_order', 'is_active')
    list_filter = ('field_type', 'is_confidential', 'is_active')
    search_fields = ('label', 'field_name')


@admin.register(SlatingApplicationResponse, site=admin_site)
class SlatingApplicationResponseAdmin(ReadOnlyAdmin):
    list_display = ('application', 'field', 'created_at')


@admin.register(SlatingInterview, site=admin_site)
class SlatingInterviewAdmin(ReadOnlyAdmin):
    """Read-only so the notes-destruction feature can't be reversed by an
    admin edit."""
    list_display = ('application', 'scheduled_at', 'completed_at', 'recommendation',
                    'notes_destroyed')
    list_filter = ('recommendation', 'notes_destroyed')


@admin.register(Slate, site=admin_site)
class SlateAdmin(admin.ModelAdmin):
    list_display = ('period', 'name', 'slate_type', 'is_approved', 'passed',
                    'approval_percentage', 'total_votes')
    list_filter = ('slate_type', 'is_approved', 'passed')
    search_fields = ('name',)


@admin.register(SlateCandidate, site=admin_site)
class SlateCandidateAdmin(admin.ModelAdmin):
    list_display = ('slate', 'position', 'application', 'write_in_member',
                    'is_runoff', 'display_order')
    list_filter = ('is_runoff',)


@admin.register(SlatingAttendance, site=admin_site)
class SlatingAttendanceAdmin(admin.ModelAdmin):
    list_display = ('period', 'member', 'marked_at', 'marked_by')
    list_filter = ('period',)
    search_fields = ('member__username', 'member__name')


@admin.register(SlatingBallot, site=admin_site)
class SlatingBallotAdmin(ReadOnlyAdmin):
    """Read-only: participation record (who voted, not what for — choices live
    in SlatingVote without a voter FK). Never edit; ballot integrity is
    hash-chained."""
    list_display = ('period', 'voter', 'voting_attempt', 'vote_type', 'position', 'voted_at')
    list_filter = ('vote_type', 'voting_attempt')


@admin.register(SlatingVote, site=admin_site)
class SlatingVoteAdmin(ReadOnlyAdmin):
    """Read-only: anonymous vote content — deliberately no voter FK here."""
    list_display = ('period', 'slate', 'slate_candidate', 'voting_attempt',
                    'vote_choice', 'voted_at')
    list_filter = ('vote_choice', 'voting_attempt')


@admin.register(SlatingActivity, site=admin_site)
class SlatingActivityAdmin(ReadOnlyAdmin):
    list_display = ('period', 'user', 'action', 'ip_address', 'timestamp')
    list_filter = ('action',)
    search_fields = ('user__username', 'user__name', 'details')
    date_hierarchy = 'timestamp'


# ──────────────────────────────────────────────────────────────────── songs

@admin.register(SongCategory, site=admin_site)
class SongCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'color', 'display_order', 'created_at')
    search_fields = ('name',)


@admin.register(Song, site=admin_site)
class SongAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'is_active', 'created_by', 'updated_at')
    list_filter = ('category', 'is_active')
    search_fields = ('title', 'lyrics')


# ───────────────────────────────────────────────────────────── users extras

@admin.register(RoleHistory, site=admin_site)
class RoleHistoryAdmin(admin.ModelAdmin):
    list_display = ('user', 'role_name', 'start_semester', 'end_semester')
    search_fields = ('user__username', 'user__name', 'role_name')


@admin.register(UserPreferences, site=admin_site)
class UserPreferencesAdmin(admin.ModelAdmin):
    list_display = ('user', 'theme', 'updated_at')
    list_filter = ('theme',)
    search_fields = ('user__username', 'user__name')


@admin.register(TwoFactorRequirement, site=admin_site)
class TwoFactorRequirementAdmin(admin.ModelAdmin):
    list_display = ('user', 'requirement', 'set_by', 'updated_at')
    list_filter = ('requirement',)
    search_fields = ('user__username', 'user__name')


# ──────────────────────────────────────────────────────────────── webauthn

@admin.register(WebAuthnCredential, site=admin_site)
class WebAuthnCredentialAdmin(ViewDeleteAdmin):
    """View/delete — deleting here is the escape hatch for a lost/compromised
    passkey. Key material (credential_id/public_key) never renders."""
    exclude = ('credential_id', 'public_key')
    list_display = ('user', 'name', 'aaguid', 'sign_count', 'created_at', 'last_used_at')
    search_fields = ('user__username', 'user__name', 'name')
