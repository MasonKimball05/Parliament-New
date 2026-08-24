"""
Global search functionality across all Parliament content
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Q, Count
from src.models import (
    Legislation, Announcement, Event,
    ParliamentUser, CommitteeDocument, Committee, ChatChannel,
    PassedResolution, SlatingPeriod, KaiReport
)
from django.utils import timezone
from src.feature_flag_decorators import require_feature_flag
from src.models_feature_flags import FeatureFlag
from src.view.kai_reports import _get_kai_access


# Define all searchable pages with keywords
PAGES = [
    # Main Navigation
    {'name': 'Home', 'url': 'home', 'icon': '🏠', 'keywords': ['home', 'dashboard', 'main', 'start'], 'description': 'Main dashboard and overview'},
    {'name': 'Vote on Legislation', 'url': 'vote', 'icon': '🗳️', 'keywords': ['vote', 'voting', 'ballot', 'legislation', 'bill'], 'description': 'Cast votes on active legislation'},
    {'name': 'Passed Legislation', 'url': 'passed_legislation', 'icon': '📜', 'keywords': ['passed', 'legislation', 'laws', 'approved', 'enacted', 'history'], 'description': 'View passed legislation history'},
    {'name': 'Chapter Documents', 'url': 'chapter_documents', 'icon': '📁', 'keywords': ['document', 'files', 'chapter', 'download', 'resources'], 'description': 'Access chapter documents and files'},
    {'name': 'Announcements', 'url': 'announcements', 'icon': '📢', 'keywords': ['announcement', 'news', 'updates', 'notice', 'bulletin'], 'description': 'View chapter announcements'},
    {'name': 'Event Calendar', 'url': 'calendar', 'icon': '📅', 'keywords': ['calendar', 'event', 'schedule', 'meeting', 'date', 'upcoming'], 'description': 'View upcoming events and meetings'},
    {'name': 'Chat Channels', 'url': 'chat_index', 'icon': '💬', 'keywords': ['chat', 'message', 'talk', 'conversation', 'channel', 'discuss'], 'description': 'Access chat channels'},
    {'name': 'Committees', 'url': 'committee_index', 'icon': '👥', 'keywords': ['committee', 'group', 'team', 'board'], 'description': 'View all committees'},
    {'name': 'Member Directory', 'url': 'member_directory', 'icon': '📇', 'keywords': ['directory', 'member', 'contact', 'people', 'roster', 'list'], 'description': 'Browse member directory'},

    # User Pages
    {'name': 'My Profile', 'url': 'profile', 'icon': '👤', 'keywords': ['profile', 'account', 'me', 'my', 'personal'], 'description': 'View and edit your profile'},
    {'name': 'Preferences', 'url': 'preferences', 'icon': '⚙️', 'keywords': ['settings', 'preferences', 'options', 'configure', 'customize', 'theme', 'dark mode'], 'description': 'Customize your preferences'},
    {'name': 'Change Password', 'url': 'change_password', 'icon': '🔐', 'keywords': ['password', 'change', 'security', 'reset'], 'description': 'Change your password'},
    {'name': 'My Excuses', 'url': 'my_excuses', 'icon': '📝', 'keywords': ['excuse', 'absence', 'miss', 'skip', 'request'], 'description': 'View and submit excuse requests'},

    # Reference Pages
    {'name': "Robert's Rules", 'url': 'roberts_rules', 'icon': '📖', 'keywords': ['robert', 'rules', 'order', 'parliamentary', 'procedure', 'motion'], 'description': 'Parliamentary procedure reference'},
    {'name': 'Constitution & Bylaws', 'url': 'constitution_bylaws', 'icon': '📋', 'keywords': ['constitution', 'bylaws', 'charter', 'founding', 'rules', 'governance'], 'description': 'Chapter constitution and bylaws'},

    # Kai
    {'name': 'Submit Kai Report', 'url': 'submit_kai_report', 'icon': '📝', 'keywords': ['kai', 'report', 'submit', 'concern', 'feedback', 'issue'], 'description': 'Submit a Kai report'},

    # Guide pages (member-facing)
    {'name': 'Guide: Chat Channels', 'url': 'guide_chat', 'icon': '💬', 'keywords': ['guide', 'chat', 'channels', 'messaging', 'how to'], 'description': 'How to use chat channels'},
    {'name': 'Guide: Service Hours', 'url': 'guide_service_hours', 'icon': '⏱️', 'keywords': ['guide', 'service', 'hours', 'logging', 'how to'], 'description': 'How to log service hours'},
    {'name': 'Guide: Pledge Tasks', 'url': 'guide_pledge_tasks', 'icon': '📋', 'keywords': ['guide', 'pledge', 'tasks', 'quiz', 'how to'], 'description': 'How the pledge task tracker works'},
]

# Officer-only pages
OFFICER_PAGES = [
    {'name': 'Officer Dashboard', 'url': 'officer_home', 'icon': '⚡', 'keywords': ['officer', 'admin', 'dashboard', 'manage'], 'description': 'Officer management dashboard'},
    {'name': 'Take Attendance', 'url': 'attendance', 'icon': '✅', 'keywords': ['attendance', 'present', 'roll', 'check in', 'checkin'], 'description': 'Take meeting attendance'},
    {'name': 'Manage Events', 'url': 'manage_events', 'icon': '📅', 'keywords': ['manage', 'event', 'create', 'schedule'], 'description': 'Create and manage events'},
    {'name': 'Manage Users', 'url': 'user_list', 'icon': '👥', 'keywords': ['manage', 'user', 'member', 'add', 'edit'], 'description': 'Manage chapter members'},
    {'name': 'View Kai Reports', 'url': 'view_kai_reports', 'icon': '📊', 'keywords': ['kai', 'report', 'view', 'review'], 'description': 'Review submitted Kai reports'},
    {'name': 'Review Excuses', 'url': 'review_excuses', 'icon': '📋', 'keywords': ['excuse', 'review', 'approve', 'absence'], 'description': 'Review excuse requests'},

    # Guide pages (officer-facing)
    {'name': 'Guide: Recruitment', 'url': 'guide_recruitment', 'icon': '🤝', 'keywords': ['guide', 'recruitment', 'rush', 'candidates', 'how to'], 'description': 'Recruitment committee guide'},
    {'name': 'Guide: Education', 'url': 'guide_education', 'icon': '🎓', 'keywords': ['guide', 'education', 'pledge', 'vpe', 'how to'], 'description': 'Education committee guide'},
]

# Admin-only pages
ADMIN_PAGES = [
    {'name': 'Admin Dashboard', 'url': 'admin_v2_dashboard', 'icon': '🛡️', 'keywords': ['admin', 'system', 'control'], 'description': 'System administration'},
    {'name': 'Activity Logs', 'url': 'activity_logs', 'icon': '📜', 'keywords': ['log', 'activity', 'audit', 'history', 'action'], 'description': 'View system activity logs'},
    {'name': 'Login History', 'url': 'admin_v2_login_history', 'icon': '🔑', 'keywords': ['login', 'history', 'access', 'session'], 'description': 'View login history'},
    {'name': 'Feature Flags', 'url': 'admin_v2_dashboard', 'icon': '🚩', 'keywords': ['feature', 'flag', 'toggle', 'enable', 'disable'], 'description': 'Manage feature flags (in Admin Dashboard)'},
]

# Slating pages (when active)
SLATING_PAGES = [
    {'name': 'Officer Elections', 'url': 'slating_dashboard', 'icon': '🗳️', 'keywords': ['slating', 'election', 'officer', 'nominate', 'candidate', 'apply'], 'description': 'Officer election and slating'},
]


def search_pages(query, user):
    """Search through available pages based on user permissions"""
    query_lower = query.lower()
    matching_pages = []
    seen_urls = set()

    # Get all pages available to this user
    all_pages = list(PAGES)

    if user.is_officer:
        all_pages.extend(OFFICER_PAGES)

    if user.is_admin:
        all_pages.extend(ADMIN_PAGES)

    # Check if slating is active
    active_slating = SlatingPeriod.objects.filter(
        status__in=['setup', 'nominations_open', 'nominations_closed', 'deliberation', 'voting_open', 'results_published']
    ).exists()
    if active_slating:
        all_pages.extend(SLATING_PAGES)

    # Search through pages
    for page in all_pages:
        if page['url'] in seen_urls:
            continue

        # Check if query matches name, keywords, or description
        name_match = query_lower in page['name'].lower()
        keyword_match = any(query_lower in kw for kw in page['keywords'])
        desc_match = query_lower in page.get('description', '').lower()

        # Also check for partial matches in keywords
        partial_keyword_match = any(kw in query_lower or query_lower in kw for kw in page['keywords'])

        if name_match or keyword_match or desc_match or partial_keyword_match:
            matching_pages.append(page)
            seen_urls.add(page['url'])

    return matching_pages


@login_required
@require_feature_flag('global_search')
def global_search(request):
    """
    Search across all content types in Parliament system
    """
    query = request.GET.get('q', '').strip()

    if not query:
        return render(request, 'global_search.html', {
            'query': query,
            'results': {}
        })

    results = {}
    now = timezone.now()

    # Search Pages first (navigation)
    matching_pages = search_pages(query, request.user)
    if matching_pages:
        results['pages'] = matching_pages[:8]

    # Search Legislation (title, description)
    legislation_results = list(Legislation.objects.filter(
        Q(title__icontains=query) | Q(description__icontains=query),
        is_active=True
    ).order_by('-created_at')[:10])
    if legislation_results:
        results['legislation'] = legislation_results

    # Search Passed Resolutions
    passed_resolutions = list(PassedResolution.objects.filter(
        Q(title__icontains=query) | Q(description__icontains=query),
        is_active=True
    ).order_by('-date_passed')[:10])
    if passed_resolutions:
        results['passed_resolutions'] = passed_resolutions

    # Search Announcements (title, content)
    all_announcements = Announcement.objects.filter(
        Q(title__icontains=query) | Q(content__icontains=query),
        is_active=True
    ).filter(
        Q(publish_at__isnull=True) | Q(publish_at__lte=now)
    ).order_by('-posted_at')
    # Filter by visibility
    announcements = [a for a in all_announcements if a.is_visible_to_user(request.user)][:10]
    if announcements:
        results['announcements'] = announcements

    # Search Events (title, description, location)
    all_events = Event.objects.filter(
        Q(title__icontains=query) |
        Q(description__icontains=query) |
        Q(location__icontains=query),
        is_active=True,
        archived=False
    ).order_by('-date_time')
    # Filter by visibility
    events = [e for e in all_events if e.is_visible_to_user(request.user)][:10]
    if events:
        results['events'] = events

    # Search Chapter Documents (CommitteeDocuments published to chapter)
    chapter_docs = list(CommitteeDocument.objects.filter(
        Q(title__icontains=query) | Q(description__icontains=query),
        published_to_chapter=True
    ).order_by('-uploaded_at')[:10])
    if chapter_docs:
        results['chapter_documents'] = chapter_docs

    # Search Committees (name, code)
    # v3.16.3: annotate the two counts the template renders. It previously
    # called committee.members.count / committee.chairs.count inside the
    # result loop — 2 queries per committee row, up to 20 per search.
    committees = list(Committee.objects.filter(
        Q(name__icontains=query) | Q(code__icontains=query)
    ).annotate(
        member_total=Count('members', distinct=True),
        chair_total=Count('chairs', distinct=True),
    )[:10])
    if committees:
        results['committees'] = committees

    # Search Chat Channels (name, description) - user must have access
    # Prefetch permissions + committee membership so the per-channel check is
    # pure Python and doesn't fire a separate DB query per channel.
    chat_channels = list(
        ChatChannel.objects.filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        )
        .select_related('committee')
        .prefetch_related(
            'permissions',
            'committee__members',
            'committee__chairs',
            'committee__advisors',
        )
    )
    user_pk = request.user.pk
    user_member_type = request.user.member_type
    user_is_officer = request.user.is_officer

    accessible_channels = []
    for channel in chat_channels:
        if channel.access_type == 'open':
            accessible_channels.append(channel)
        elif channel.access_type == 'committee' and channel.committee:
            committee = channel.committee
            member_pks = {m.pk for m in committee.members.all()}
            chair_pks = {m.pk for m in committee.chairs.all()}
            advisor_pks = {m.pk for m in committee.advisors.all()}
            if user_pk in (member_pks | chair_pks | advisor_pks):
                accessible_channels.append(channel)
        elif channel.access_type == 'restricted':
            # All permission rows are already in the prefetch cache — no extra queries
            for perm in channel.permissions.all():
                if perm.user_id == user_pk:
                    accessible_channels.append(channel)
                    break
                if perm.member_type and perm.member_type == user_member_type:
                    accessible_channels.append(channel)
                    break
                if perm.officers_only and user_is_officer:
                    accessible_channels.append(channel)
                    break

    if accessible_channels:
        results['chat_channels'] = accessible_channels[:10]

    # Search Committee Documents (title, description)
    # Only show documents from committees the user has access to
    user_committees = Committee.objects.filter(
        Q(members=request.user) |
        Q(chairs=request.user) |
        Q(advisors=request.user)
    ).distinct()

    committee_docs = list(CommitteeDocument.objects.filter(
        Q(title__icontains=query) | Q(description__icontains=query),
        committee__in=user_committees
    ).order_by('-uploaded_at')[:10])
    if committee_docs:
        results['committee_documents'] = committee_docs

    # Search Users (name, username) - only for officers
    if request.user.is_officer:
        # ⚠️ v3.25.0 — `role_number` IS WHAT A MEMBER IS CALLED. Searching
        # `user_id` alone found everyone only because, until v3.23.0, initiation
        # copied the roll number INTO the primary key. It no longer does: a
        # member initiated from now on keeps an opaque `P-XXXXXX` forever and
        # his roll number lives in `role_number`. Typing "173" into the search
        # box found nobody, and would have gone on finding the whole existing
        # roster (whose legacy keys are still roll numbers) while silently
        # failing for every new member — the worst shape of bug to notice.
        users = list(ParliamentUser.objects.exclude(member_status='Removed').filter(
            Q(name__icontains=query) |
            Q(user_id__icontains=query) |
            Q(role_number__icontains=query) |
            Q(preferred_name__icontains=query)
        ).order_by('name')[:10])
        if users:
            results['users'] = users

    # Search Slating Periods (for admins/officers)
    if request.user.is_officer or request.user.is_admin:
        slating_periods = list(SlatingPeriod.objects.filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        ).order_by('-created_at')[:5])
        if slating_periods:
            results['slating_periods'] = slating_periods

    # Search Kai Reports — v3.16.2: gated by the SAME rule the Kai module
    # uses (_get_kai_access → KaiMemberPermission), not by mere committee
    # membership. Previously any member of the Kai committee could full-text
    # search report titles AND descriptions (the allegation body) here even
    # with can_view_report_list/can_view_report_details set False, which is
    # exactly what the in-app module denies them. Also honours the
    # 'kai_reports' feature flag, which this view never checked.
    #
    # v3.16.3 perf: the feature-flag lookup moved BELOW the access check.
    # FeatureFlag.is_feature_enabled is an uncached objects.get, and checking
    # it first meant every searcher on the site — including pledges who can
    # never match this branch — paid for it. Now only Kai-cleared users do.
    #
    # Deliberately NOT short-circuited on `request.user.committees.filter(
    # is_kai_committee=True).exists()`: a KaiMemberPermission row is not
    # guaranteed to imply membership of the committee's `members` M2M (the
    # permission UI also lists chairs and voting_members, and the AJAX grant
    # endpoint takes an arbitrary user_id), so that cheaper pre-check could
    # silently hide results from someone legitimately granted access.
    # _get_kai_access stays the single source of truth.
    kai_can_view_details = False
    kai_committee = Committee.objects.filter(is_kai_committee=True).first()
    if kai_committee is not None:
        kai_access = _get_kai_access(request.user, kai_committee)
        if kai_access['can_view_report_list'] and FeatureFlag.is_feature_enabled('kai_reports'):
            # Only search the allegation body for users cleared to read
            # details; list-only users match on title alone.
            kai_q = Q(title__icontains=query)
            if kai_access['can_view_report_details']:
                kai_q |= Q(description__icontains=query)
            # v3.18.0 — RECUSAL. Global search is the fifth surface that lists
            # Kai cases, and it was the one v3.16.3 had to fix separately for
            # the description preview. A case the viewer is a party to is
            # excluded here for the same reason it is excluded from the
            # reviewer list: bylaws § vi, and because a search hit is itself a
            # disclosure that the case exists.
            from src.view.kai_reports import _recused_case_ids

            kai_reports = list(
                KaiReport.objects
                .filter(kai_q)
                .exclude(pk__in=_recused_case_ids(request.user))
                .order_by('-submitted_at')[:10]
            )
            if kai_reports:
                results['kai_reports'] = kai_reports
                # v3.16.3: the template renders a description preview on each
                # Kai card. That is the allegation body — the same field this
                # view refuses to *search* for list-only reviewers — so the
                # template must gate on the same flag. See global_search.html.
                kai_can_view_details = kai_access['can_view_report_details']

    # All result values are now lists — len() is free, no extra DB queries
    total_count = sum(len(v) for v in results.values())

    return render(request, 'global_search.html', {
        'query': query,
        'results': results,
        'total_count': total_count,
        'kai_can_view_details': kai_can_view_details,
    })
