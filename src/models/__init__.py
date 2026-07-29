# Re-export shim — preserves all existing `from src.models import X` import sites.
# All model classes are defined in sub-modules; this file simply re-exports them.

# Users
from src.models.users import (
    MEMBER_DISPLAY_FIELDS,
    MEMBER_PROFILE_FIELDS,
    _default_user_prefs,
    validate_profile_picture,
    ParliamentUserManager,
    ActiveUserManager,
    Role,
    ParliamentUser,
    RoleHistory,
    UserPreferences,
    TwoFactorRequirement,
    UserSession,
)

# Officer Transitions
from src.models.transitions import (
    TransitionChecklistItem,
    TransitionChecklistStatus,
)

# Legislation
from src.models.legislation import (
    validate_legislation_file,
    Legislation,
    Vote,
)

# Committees
from src.models.committees import (
    Committee,
    CommitteePermissions,
    CommitteeLegislation,
    CommitteeVote,
)

# Documents
from src.models.documents import (
    CommitteeMinutes,
    ChapterFolder,
    DocumentTag,
    CommitteeDocument,
    DocumentVersion,
    ChapterMinutes,
    MinutesSection,
    MinutesMotion,
)

# Announcements
from src.models.announcements import (
    Announcement,
    UserAnnouncementView,
    AnnouncementPoll,
    AnnouncementPollQuestion,
    AnnouncementPollOption,
    AnnouncementPollResponse,
    AnnouncementPollAnswer,
    AnnouncementEmailLog,
    AnnouncementEmailRecipient,
)

# Events
from src.models.events import (
    Event,
    EventSignup,
    Attendance,
    AttendanceExcuse,
    EventReminderLog,
    EventReminderRecipient,
)

# Chat
from src.models.chat import (
    ChatChannel,
    ChatChannelPermission,
    ChatMessage,
    ChatNotificationPreference,
    ChatReadReceipt,
)

# Kai
from src.models.kai import (
    KaiReport,
    KaiReportActivity,
    KaiReportTemplate,
    KaiFormField,
    KaiReportFieldResponse,
    KaiClosureRequest,
    KaiMemberPermission,
)

# Slating
from src.models.slating import (
    SlatingPeriod,
    SlatingPosition,
    SlatingFormField,
    SlatingApplication,
    SlatingApplicationResponse,
    SlatingInterview,
    Slate,
    SlateCandidate,
    SlatingAttendance,
    SlatingBallot,
    SlatingVote,
    SlatingActivity,
)

# Service Hours
from src.models.service import (
    ServicePeriod,
    ServiceMemberExpectation,
    ServiceHoursSubmission,
    ServiceFormField,
    ServiceFieldResponse,
    ServiceActivity,
    ServiceHoursAdjustment,
    ServiceEvent,
)

# Security
from src.models.security import (
    LoginHistory,
    LoginAlert,
    UserWatchFlag,
    IPWhitelist,
    IPBlacklist,
    BugReport,
    HoneypotAccess,
    SystemLockdown,
    SecurityNotificationLog,
    CSPViolation,
    LoginLockout,
    QuarantinedAccount,
    EmailVerificationToken,
)

# Passkeys / WebAuthn
from src.models.webauthn import WebAuthnCredential

# Notifications
from src.models.notifications import (
    Notification,
    NotificationSchedule,
    NotificationLog,
    PushSubscription,
)

# Activity Logging
from src.models.activity import (
    ActivityLog,
)

# Guide / Tours
from src.models.guide import (
    GuideTour,
    GuideTourStep,
    UserTourProgress,
    GuideArticle,
)

# Songs
from src.models.songs import (
    SongCategory,
    Song,
)

# Landing Page
from src.models.landing import (
    PassedResolution,
    ResolutionSectionImpact,
    LandingPageContent,
    LandingPagePhoto,
    ContactSubmission,
    LandingPageSocialLink,
    LandingPageContactTopic,
    LandingPageFormLink,
)

# Analytics
from src.models.analytics import PageVisit

# API Tokens
from src.models.api import APIToken, APIAccessLog, DEFINED_SCOPES, ALL_SCOPE_KEYS

# Admin Action Audit Log
from src.models.admin_audit import AdminActionLog, log_admin_action

# Recruitment
from src.models.recruitment import (
    RecruitmentCandidate,
    RecruitmentCandidateNote,
    RecruitmentEvent,
    RecruitmentEventRSVP,
    RecruitmentMemberPermission,
)

# Constitution & Bylaws Builder
from src.models.cnb import (
    GoverningDocument,
    Article,
    Section,
    Resolution,
    ResolutionAmendment,
    ResolutionCollaborator,
)

# Education / Pledge Tracker
from src.models.education import (
    PledgeTask,
    PledgeTaskCompletion,
    PledgePageRestriction,
    PledgeTaskQuestion,
    PledgeQuizAnswer,
)

# Feature Flags (defined in separate module)
from src.models_feature_flags import FeatureFlag, PageToggle

__all__ = [
    # Users
    '_default_user_prefs',
    'validate_profile_picture',
    'ParliamentUserManager',
    'ActiveUserManager',
    'Role',
    'ParliamentUser',
    'RoleHistory',
    'UserPreferences',
    'TwoFactorRequirement',
    'UserSession',
    # Legislation
    'validate_legislation_file',
    'Legislation',
    'Vote',
    # Committees
    'Committee',
    'CommitteePermissions',
    'CommitteeLegislation',
    'CommitteeVote',
    # Documents
    'CommitteeMinutes',
    'ChapterFolder',
    'DocumentTag',
    'CommitteeDocument',
    'DocumentVersion',
    'ChapterMinutes',
    'MinutesSection',
    'MinutesMotion',
    # Announcements
    'Announcement',
    'UserAnnouncementView',
    'AnnouncementPoll',
    'AnnouncementPollQuestion',
    'AnnouncementPollOption',
    'AnnouncementPollResponse',
    'AnnouncementPollAnswer',
    'AnnouncementEmailLog',
    'AnnouncementEmailRecipient',
    # Events
    'Event',
    'EventSignup',
    'Attendance',
    'AttendanceExcuse',
    'EventReminderLog',
    'EventReminderRecipient',
    # Chat
    'ChatChannel',
    'ChatChannelPermission',
    'ChatMessage',
    'ChatNotificationPreference',
    'ChatReadReceipt',
    # Kai
    'KaiReport',
    'KaiReportActivity',
    'KaiReportTemplate',
    'KaiFormField',
    'KaiReportFieldResponse',
    'KaiClosureRequest',
    'KaiMemberPermission',
    # Slating
    'SlatingPeriod',
    'SlatingPosition',
    'SlatingFormField',
    'SlatingApplication',
    'SlatingApplicationResponse',
    'SlatingInterview',
    'Slate',
    'SlateCandidate',
    'SlatingAttendance',
    'SlatingBallot',
    'SlatingVote',
    'SlatingActivity',
    # Service Hours
    'ServicePeriod',
    'ServiceMemberExpectation',
    'ServiceHoursSubmission',
    'ServiceFormField',
    'ServiceFieldResponse',
    'ServiceActivity',
    'ServiceHoursAdjustment',
    'ServiceEvent',
    # Security
    'LoginHistory',
    'LoginAlert',
    'UserWatchFlag',
    'IPWhitelist',
    'IPBlacklist',
    'BugReport',
    'HoneypotAccess',
    'SystemLockdown',
    'SecurityNotificationLog',
    'CSPViolation',
    'LoginLockout',
    'QuarantinedAccount',
    'EmailVerificationToken',
    # Notifications
    'Notification',
    'NotificationSchedule',
    'NotificationLog',
    'PushSubscription',
    # Activity Logging
    'ActivityLog',
    # Guide / Tours
    'GuideTour',
    'GuideTourStep',
    'UserTourProgress',
    'GuideArticle',
    # Songs
    'SongCategory',
    'Song',
    # Landing Page
    'PassedResolution',
    'ResolutionSectionImpact',
    'LandingPageContent',
    'LandingPagePhoto',
    'ContactSubmission',
    'LandingPageSocialLink',
    'LandingPageContactTopic',
    'LandingPageFormLink',
    # Analytics
    'PageVisit',
    # Constitution & Bylaws Builder
    'GoverningDocument',
    'Article',
    'Section',
    'Resolution',
    'ResolutionAmendment',
    'ResolutionCollaborator',
    # Feature Flags
    'FeatureFlag',
    'PageToggle',
    # API Tokens
    'APIToken',
    'APIAccessLog',
    'DEFINED_SCOPES',
    'ALL_SCOPE_KEYS',
    # Admin Audit Log
    'AdminActionLog',
    'log_admin_action',
    # Recruitment
    'RecruitmentCandidate',
    'RecruitmentCandidateNote',
    'RecruitmentEvent',
    'RecruitmentEventRSVP',
    'RecruitmentMemberPermission',
    # Officer Transitions
    'TransitionChecklistItem',
    'TransitionChecklistStatus',
]
