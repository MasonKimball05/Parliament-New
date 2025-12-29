"""
Global search functionality across all Parliament content
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.db.models import Q
from src.models import (
    Legislation, Announcement, Event,
    ParliamentUser, CommitteeDocument, Committee, ChatChannel
)
from django.utils import timezone


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

    # Search Legislation (title, description)
    legislation_results = Legislation.objects.filter(
        Q(title__icontains=query) | Q(description__icontains=query)
    ).order_by('-created_at')[:10]
    if legislation_results:
        results['legislation'] = legislation_results

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

    # Search Users (name, username) - only for officers
    if request.user.is_officer:
        users = ParliamentUser.objects.filter(
            Q(name__icontains=query) | Q(user_id__icontains=query)
        ).order_by('name')[:10]
        if users:
            results['users'] = users

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

    # Search Chat Channels (name, description) - user must have access
    chat_channels = ChatChannel.objects.filter(
        Q(name__icontains=query) | Q(description__icontains=query)
    )
    # Filter by accessibility
    accessible_channels = []
    for channel in chat_channels:
        # Open channels - everyone can access
        if channel.access_type == 'open':
            accessible_channels.append(channel)
        # Committee channels - check if user is part of the committee
        elif channel.access_type == 'committee' and channel.committee:
            if (request.user in channel.committee.members.all() or
                request.user in channel.committee.chairs.all() or
                request.user in channel.committee.advisors.all()):
                accessible_channels.append(channel)
        # Restricted channels - check permissions
        elif channel.access_type == 'restricted':
            # Check if user has explicit permission
            if channel.permissions.filter(user=request.user).exists():
                accessible_channels.append(channel)
            # Check role-based permissions
            elif channel.permissions.filter(member_type=request.user.member_type).exists():
                accessible_channels.append(channel)
            # Check officer/chair only permissions
            elif request.user.is_officer and channel.permissions.filter(officers_only=True).exists():
                accessible_channels.append(channel)

    if accessible_channels:
        results['chat_channels'] = accessible_channels[:10]

    # Search Committees (name, code)
    committees = Committee.objects.filter(
        Q(name__icontains=query) | Q(code__icontains=query)
    )
    # Show all committees to all users (they're public knowledge)
    if committees.exists():
        results['committees'] = committees[:10]

    # Smart suggestions based on query
    suggestions = []
    query_lower = query.lower()

    # Common page suggestions
    page_suggestions = {
        'home': {'name': 'Home', 'url': 'home', 'icon': '🏠'},
        'vote': {'name': 'Vote on Legislation', 'url': 'vote', 'icon': '🗳️'},
        'voting': {'name': 'Vote on Legislation', 'url': 'vote', 'icon': '🗳️'},
        'legislation': {'name': 'Passed Legislation', 'url': 'passed_legislation', 'icon': '📜'},
        'laws': {'name': 'Passed Legislation', 'url': 'passed_legislation', 'icon': '📜'},
        'document': {'name': 'Chapter Documents', 'url': 'chapter_documents', 'icon': '📁'},
        'files': {'name': 'Chapter Documents', 'url': 'chapter_documents', 'icon': '📁'},
        'announcement': {'name': 'Announcements', 'url': 'announcements', 'icon': '📢'},
        'news': {'name': 'Announcements', 'url': 'announcements', 'icon': '📢'},
        'calendar': {'name': 'Event Calendar', 'url': 'calendar', 'icon': '📅'},
        'event': {'name': 'Event Calendar', 'url': 'calendar', 'icon': '📅'},
        'schedule': {'name': 'Event Calendar', 'url': 'calendar', 'icon': '📅'},
        'chat': {'name': 'Chat Channels', 'url': 'chat_index', 'icon': '💬'},
        'message': {'name': 'Chat Channels', 'url': 'chat_index', 'icon': '💬'},
        'committee': {'name': 'Committees', 'url': 'committee_index', 'icon': '👥'},
        'profile': {'name': 'My Profile', 'url': 'profile', 'icon': '👤'},
        'account': {'name': 'My Profile', 'url': 'profile', 'icon': '👤'},
        'settings': {'name': 'Preferences', 'url': 'preferences', 'icon': '⚙️'},
        'preferences': {'name': 'Preferences', 'url': 'preferences', 'icon': '⚙️'},
        'robert': {'name': "Robert's Rules", 'url': 'roberts_rules', 'icon': '📖'},
        'rules': {'name': "Robert's Rules", 'url': 'roberts_rules', 'icon': '📖'},
        'constitution': {'name': 'Constitution & Bylaws', 'url': 'constitution_bylaws', 'icon': '📋'},
        'bylaws': {'name': 'Constitution & Bylaws', 'url': 'constitution_bylaws', 'icon': '📋'},
    }

    # Officer-only suggestions
    if request.user.is_officer:
        page_suggestions.update({
            'officer': {'name': 'Officer Dashboard', 'url': 'officer_home', 'icon': '⚡'},
            'admin': {'name': 'Officer Dashboard', 'url': 'officer_home', 'icon': '⚡'},
            'attendance': {'name': 'Take Attendance', 'url': 'attendance', 'icon': '✅'},
            'present': {'name': 'Take Attendance', 'url': 'attendance', 'icon': '✅'},
        })

    # Find matching suggestions
    for keyword, suggestion in page_suggestions.items():
        if keyword in query_lower:
            # Avoid duplicates
            if not any(s['name'] == suggestion['name'] for s in suggestions):
                suggestions.append(suggestion)

    if suggestions:
        results['suggestions'] = suggestions[:5]

    # Count total results
    total_count = sum(len(result) if isinstance(result, list) else result.count()
                     for result in results.values())

    return render(request, 'global_search.html', {
        'query': query,
        'results': results,
        'total_count': total_count,
        'suggestions': suggestions
    })
