"""
Global search functionality across all Parliament content
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Q
from src.models import (
    Legislation, Announcement, Event,
    ParliamentUser, CommitteeDocument, Committee, ChatChannel,
    PassedResolution, SlatingPeriod, KaiReport
)
from django.utils import timezone


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
]

# Officer-only pages
OFFICER_PAGES = [
    {'name': 'Officer Dashboard', 'url': 'officer_home', 'icon': '⚡', 'keywords': ['officer', 'admin', 'dashboard', 'manage'], 'description': 'Officer management dashboard'},
    {'name': 'Take Attendance', 'url': 'attendance', 'icon': '✅', 'keywords': ['attendance', 'present', 'roll', 'check in', 'checkin'], 'description': 'Take meeting attendance'},
    {'name': 'Manage Events', 'url': 'manage_events', 'icon': '📅', 'keywords': ['manage', 'event', 'create', 'schedule'], 'description': 'Create and manage events'},
    {'name': 'Manage Users', 'url': 'user_list', 'icon': '👥', 'keywords': ['manage', 'user', 'member', 'add', 'edit'], 'description': 'Manage chapter members'},
    {'name': 'View Kai Reports', 'url': 'view_kai_reports', 'icon': '📊', 'keywords': ['kai', 'report', 'view', 'review'], 'description': 'Review submitted Kai reports'},
    {'name': 'Review Excuses', 'url': 'review_excuses', 'icon': '📋', 'keywords': ['excuse', 'review', 'approve', 'absence'], 'description': 'Review excuse requests'},
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
    legislation_results = Legislation.objects.filter(
        Q(title__icontains=query) | Q(description__icontains=query),
        is_active=True
    ).order_by('-created_at')[:10]
    if legislation_results:
        results['legislation'] = legislation_results

    # Search Passed Resolutions
    passed_resolutions = PassedResolution.objects.filter(
        Q(title__icontains=query) | Q(description__icontains=query),
        is_active=True
    ).order_by('-date_passed')[:10]
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
    chapter_docs = CommitteeDocument.objects.filter(
        Q(title__icontains=query) | Q(description__icontains=query),
        published_to_chapter=True
    ).order_by('-uploaded_at')[:10]
    if chapter_docs:
        results['chapter_documents'] = chapter_docs

    # Search Committees (name, code)
    committees = Committee.objects.filter(
        Q(name__icontains=query) | Q(code__icontains=query)
    )
    if committees.exists():
        results['committees'] = committees[:10]

    # Search Chat Channels (name, description) - user must have access
    chat_channels = ChatChannel.objects.filter(
        Q(name__icontains=query) | Q(description__icontains=query)
    )
    # Filter by accessibility
    accessible_channels = []
    for channel in chat_channels:
        if channel.access_type == 'open':
            accessible_channels.append(channel)
        elif channel.access_type == 'committee' and channel.committee:
            if (request.user in channel.committee.members.all() or
                request.user in channel.committee.chairs.all() or
                request.user in channel.committee.advisors.all()):
                accessible_channels.append(channel)
        elif channel.access_type == 'restricted':
            if channel.permissions.filter(user=request.user).exists():
                accessible_channels.append(channel)
            elif channel.permissions.filter(member_type=request.user.member_type).exists():
                accessible_channels.append(channel)
            elif request.user.is_officer and channel.permissions.filter(officers_only=True).exists():
                accessible_channels.append(channel)

    if accessible_channels:
        results['chat_channels'] = accessible_channels[:10]

    # Search Committee Documents (title, description)
    # Only show documents from committees the user has access to
    user_committees = Committee.objects.filter(
        Q(members=request.user) |
        Q(chairs=request.user) |
        Q(advisors=request.user)
    ).distinct()

    committee_docs = CommitteeDocument.objects.filter(
        Q(title__icontains=query) | Q(description__icontains=query),
        committee__in=user_committees
    ).order_by('-uploaded_at')[:10]
    if committee_docs:
        results['committee_documents'] = committee_docs

    # Search Users (name, username) - only for officers
    if request.user.is_officer:
        users = ParliamentUser.objects.exclude(member_status='Removed').filter(
            Q(name__icontains=query) |
            Q(user_id__icontains=query) |
            Q(preferred_name__icontains=query)
        ).order_by('name')[:10]
        if users:
            results['users'] = users

    # Search Slating Periods (for admins/officers)
    if request.user.is_officer or request.user.is_admin:
        slating_periods = SlatingPeriod.objects.filter(
            Q(name__icontains=query) | Q(description__icontains=query)
        ).order_by('-created_at')[:5]
        if slating_periods:
            results['slating_periods'] = slating_periods

    # Search Kai Reports (for Kai committee members and admins)
    if request.user.is_admin or request.user.committees.filter(is_kai_committee=True).exists():
        kai_reports = KaiReport.objects.filter(
            Q(title__icontains=query) | Q(description__icontains=query)
        ).order_by('-submitted_at')[:10]
        if kai_reports:
            results['kai_reports'] = kai_reports

    # Count total results
    total_count = 0
    for key, result in results.items():
        if isinstance(result, list):
            total_count += len(result)
        elif hasattr(result, 'count'):
            total_count += result.count()
        else:
            total_count += len(list(result))

    return render(request, 'global_search.html', {
        'query': query,
        'results': results,
        'total_count': total_count,
    })
