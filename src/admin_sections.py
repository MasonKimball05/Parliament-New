"""
Feature-area sections for the Django admin index + sidebar (v3.16.1).

After the v3.16.0 full-coverage pass, /admin/ listed 125 models in one
alphabetical block. `build_sectioned_app_list()` re-buckets the standard
app_list into the ordered sections below; ParliamentAdminSite.get_app_list()
(in admin.py) calls it for the index and nav sidebar.

Maintenance: register a new model anywhere, then add its class name to a
section here. Anything unmapped lands in a trailing "Other / Unsorted"
section — new models never disappear, they just show up unsorted until
placed. `src/test_admin_sections.py`-style checks live in the AST verifier
used at review time; Django itself doesn't validate this mapping, so the
Other-bucket is the safety net.

Imports nothing from admin.py/admin_extra.py — safe to import anywhere.
"""

# Ordered: most-used sections first. Values are model class names
# (object_name), matching what admin.register() was given.
SECTIONS = {
    'Members & Roles': [
        'ParliamentUser', 'Role', 'RoleHistory', 'UserPreferences',
        'TwoFactorRequirement', 'TransitionChecklistItem', 'TransitionChecklistStatus',
    ],
    'Voting & Legislation': [
        'Legislation', 'Vote', 'Attendance', 'AttendanceExcuse', 'PassedResolution',
    ],
    'Committees': [
        'Committee', 'CommitteePermissions', 'CommitteeLegislation', 'CommitteeVote',
        'CommitteeMinutes',
    ],
    'Slating & Elections': [
        'SlatingPeriod', 'SlatingPosition', 'SlatingApplication', 'SlatingFormField',
        'SlatingApplicationResponse', 'SlatingInterview', 'Slate', 'SlateCandidate',
        'SlatingAttendance', 'SlatingBallot', 'SlatingVote', 'SlatingActivity',
    ],
    'Events': [
        'Event', 'EventSignup', 'EventReminderLog', 'EventReminderRecipient',
        'EventCheckinWindow',
    ],
    'Service Hours': [
        'ServicePeriod', 'ServiceMemberExpectation', 'ServiceHoursSubmission',
        'ServiceFormField', 'ServiceFieldResponse', 'ServiceActivity',
        'ServiceHoursAdjustment', 'ServiceEvent',
    ],
    'Recruitment': [
        'RecruitmentCandidate', 'RecruitmentCandidateNote', 'RecruitmentEvent',
        'RecruitmentEventRSVP', 'RecruitmentMemberPermission',
    ],
    'Pledge Education': [
        'PledgeTask', 'PledgeTaskQuestion', 'PledgeTaskCompletion',
        'PledgeQuizAnswer', 'PledgePageRestriction',
    ],
    # v3.16.2: the 'Kai Committee' section was removed along with the Kai admin
    # registrations. Kai (judicial/disciplinary) case data is confidential and
    # is governed by in-app KaiMemberPermission grants — do NOT re-add a Kai
    # section or register Kai models here.
    'Governing Documents (CNB)': [
        'GoverningDocument', 'Article', 'Section', 'Resolution',
        'ResolutionAmendment', 'ResolutionCollaborator', 'ResolutionSectionImpact',
    ],
    'Documents & Minutes': [
        'ChapterFolder', 'CommitteeDocument', 'DocumentVersion', 'DocumentTag',
        'ChapterMinutes', 'MinutesSection', 'MinutesMotion',
    ],
    'Announcements & Polls': [
        'Announcement', 'UserAnnouncementView', 'AnnouncementEmailLog',
        'AnnouncementEmailRecipient', 'AnnouncementPoll', 'AnnouncementPollQuestion',
        'AnnouncementPollOption', 'AnnouncementPollResponse', 'AnnouncementPollAnswer',
    ],
    'Notifications': [
        'Notification', 'NotificationSchedule', 'NotificationLog', 'PushSubscription',
    ],
    'Chat': [
        'ChatChannel', 'ChatMessage', 'ChatReadReceipt', 'ChatChannelPermission',
        'ChatNotificationPreference',
    ],
    'Security — Sessions & Logins': [
        'LoginHistory', 'LoginAlert', 'LoginLockout', 'UserSession',
        'EmailVerificationToken', 'WebAuthnCredential',
    ],
    'Security — Defenses': [
        'IPWhitelist', 'IPBlacklist', 'QuarantinedAccount', 'HoneypotAccess',
        'SystemLockdown', 'SecurityNotificationLog', 'CSPViolation', 'UserWatchFlag',
    ],
    'Audit & Activity': [
        'ActivityLog', 'AdminActionLog', 'PageVisit', 'LogEntry',
    ],
    'API': [
        'APIToken', 'APIAccessLog',
    ],
    'Landing Page & Public': [
        'LandingPageContent', 'LandingPagePhoto', 'LandingPageSocialLink',
        'LandingPageContactTopic', 'LandingPageFormLink', 'ContactSubmission',
    ],
    'Guide & Help': [
        'GuideTour', 'GuideTourStep', 'UserTourProgress', 'GuideArticle',
    ],
    'Songs': [
        'SongCategory', 'Song',
    ],
    'Site Config': [
        'FeatureFlag', 'PageToggle', 'ScheduledMaintenance', 'BugReport',
    ],
}

OTHER_SECTION = 'Other / Unsorted'

# object_name -> section, built once at import
_MODEL_TO_SECTION = {
    model: section for section, model_list in SECTIONS.items() for model in model_list
}


def build_sectioned_app_list(app_list):
    """Re-bucket a standard admin app_list into feature-area sections.

    Takes the list of app dicts Django's AdminSite.get_app_list() produced
    (already permission-filtered and alphabetized) and returns pseudo-app
    dicts, one per non-empty section, in SECTIONS order. Model dicts pass
    through untouched, so admin_url/add_url/perms all keep working; models
    stay alphabetical within their section. Unmapped models go to a trailing
    'Other / Unsorted' section rather than vanishing.
    """
    buckets = {section: [] for section in SECTIONS}
    buckets[OTHER_SECTION] = []

    for app in app_list:
        for model in app['models']:
            section = _MODEL_TO_SECTION.get(model['object_name'], OTHER_SECTION)
            buckets[section].append(model)

    sectioned = []
    for section, models in buckets.items():
        if not models:
            continue
        sectioned.append({
            'name': section,
            'app_label': 'src',           # real app label — keeps /admin/src/ links valid
            'app_url': '#',               # section headers aren't pages
            'has_module_perms': True,     # models are already permission-filtered
            'models': sorted(models, key=lambda m: str(m['name']).lower()),
        })
    return sectioned
