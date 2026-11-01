"""
Guide System Views

Static guide pages and article views for user documentation.
"""

import os
import re
import bleach
import markdown as md_lib
from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils import timezone
from django.utils.safestring import mark_safe
import logging

_GUIDE_ALLOWED_TAGS = [
    'p', 'br', 'b', 'i', 'em', 'strong', 'u', 's',
    'a', 'blockquote', 'ol', 'ul', 'li',
    'h1', 'h2', 'h3', 'h4', 'h5',
    'img', 'hr', 'span', 'div', 'pre', 'code',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
]
_GUIDE_ALLOWED_ATTRS = {
    # 'target' removed 07-06-26 (reverse-tabnabbing without forced rel=noopener),
    # matching the docx allowlist fix in view_document.py.
    'a':   ['href', 'rel'],
    'img': ['src', 'alt', 'class', 'width', 'height'],
    'span': ['class'],
    'div':  ['class'],
    'code': ['class'],
    'td':   ['colspan', 'rowspan'],
    'th':   ['colspan', 'rowspan'],
}

from src.models import GuideTour, GuideTourStep, UserTourProgress, GuideArticle

logger = logging.getLogger('function_calls')


def _page(url_name, section, title, summary, keywords, page_title, template=None, doc=None, officer_only=False):
    return url_name, {
        'url_name': url_name, 'section': section, 'title': title, 'summary': summary,
        'keywords': keywords, 'page_title': page_title, 'template': template, 'doc': doc,
        'officer_only': officer_only,
    }


#: The guide's pages, in the index's order (templates/guide/index.html's cards:
#: their titles, blurbs and search words). Each static view renders its entry,
#: and the app's guide (src/mobile/views_guide.py) lists and shows the same.
GUIDE_SECTIONS = (
    ('officer', 'Officer Guides', 'Essential guides for chapter officers'),
    ('member', 'Member Guides', 'Essential guides for all chapter members'),
)
GUIDE_PAGES = dict([
    _page('guide_events', 'officer', 'Events Management', 'Create, manage, and track attendance for chapter events.',
          'Events Management create manage track attendance chapter events schedule', 'Events Management Guide',
          'guide/officers/events.html'),
    _page('guide_announcements', 'officer', 'Announcements', 'Send announcements via in-app notifications and email.',
          'Announcements send notifications email bulletin news', 'Announcements Guide',
          'guide/officers/announcements.html'),
    _page('guide_attendance', 'officer', 'Attendance', 'Take attendance and review member excuse requests.',
          'Attendance take roll check in excuse requests present', 'Attendance Management Guide',
          'guide/officers/attendance.html'),
    _page('guide_chapter_minutes', 'officer', 'Chapter Minutes', 'Record meeting minutes with sections, motions, and votes.',
          'Chapter Minutes record meeting sections motions votes', 'Chapter Minutes Guide',
          'guide/officers/chapter_minutes.html'),
    _page('guide_managing_members', 'officer', 'Managing Members', 'Add, edit, and manage members, roles, and initiations.',
          'Managing Members add edit roles initiations roster', 'Managing Members Guide',
          'guide/officers/managing_members.html'),
    _page('guide_slating', 'officer', 'Slating & Elections', 'Run officer elections with applications, interviews, and voting.',
          'Slating Elections officer applications interviews voting nominate candidate', 'Slating & Elections Guide',
          'guide/officers/slating.html'),
    _page('guide_kai', 'officer', 'Kai Reports', 'Submit conduct reports and manage case resolutions.',
          'Kai Reports conduct submit manage case resolutions', 'Kai Reports Guide', 'guide/officers/kai.html'),
    _page('guide_resolutions', 'officer', 'Resolution Management', 'Track and document chapter decisions and their impacts.',
          'Resolution Management decisions chapter track document passed', 'Resolution Management Guide',
          'guide/officers/resolutions.html'),
    _page('guide_activity_logs', 'officer', 'Activity Logs', 'View audit trails and system activity history.',
          'Activity Logs audit trails system history', 'Activity Logs Guide', 'guide/officers/activity_logs.html'),
    _page('guide_kai_forms', 'officer', 'Kai Form Customization', 'Build custom forms for Kai report submissions.',
          'Kai Form Customization custom forms submission build', 'Kai Form Customization Guide',
          'guide/officers/kai_forms.html'),
    _page('guide_officer_handoff', 'officer', 'Officer & Admin Guide',
          'Complete reference guide for officers and administrators.',
          'Officer Admin Guide complete reference administrators handoff', 'Officer & Admin Guide',
          doc='OFFICER_GUIDE.md'),
    _page('guide_recruitment', 'officer', 'Recruitment Committee', 'Manage rush sign-ups, events, and the pledge roster.',
          'Recruitment Committee rush sign-ups events pledge roster candidates', 'Recruitment Committee Guide',
          'guide/officers/recruitment.html'),
    _page('guide_education', 'officer', 'Education Committee', 'Manage pledge tasks, quizzes, and education schedules.',
          'Education Committee pledge tasks quizzes schedules vpe', 'Education Committee Guide',
          'guide/officers/education.html'),
    _page('guide_developer_handoff', 'officer', 'Developer Handoff Guide',
          'Server setup, deployment, codebase architecture, and gotchas for developers.',
          'Developer Handoff Guide server setup deployment codebase architecture gotchas', 'Developer Handoff Guide',
          doc='HANDOFF_DEVELOPER.md', officer_only=True),
    _page('guide_profile', 'member', 'Profile Management', 'Customize your profile, username, and contact info.',
          'Profile Management customize username contact info', 'Profile Management Guide',
          'guide/members/profile.html'),
    _page('guide_calendar', 'member', 'Calendar & Subscription', 'View events and sync with your calendar app.',
          'Calendar Subscription view events sync schedule', 'Calendar Guide', 'guide/members/calendar.html'),
    _page('guide_notifications', 'member', 'Notifications Center', 'Stay updated with chapter activity.',
          'Notifications Center chapter activity updates bell', 'Notifications Guide',
          'guide/members/notifications.html'),
    _page('guide_excuses', 'member', 'Submitting Excuses', 'Request an excused absence from events.',
          'Submitting Excuses absence request miss skip', 'Submitting Excuses Guide', 'guide/members/excuses.html'),
    _page('guide_2fa', 'member', 'Two-Factor Authentication', 'Add extra security to your account.',
          'Two-Factor Authentication security account 2fa totp', 'Two-Factor Authentication Guide',
          'guide/members/2fa.html'),
    _page('guide_directory', 'member', 'Member Directory', 'Find and connect with chapter members.',
          'Member Directory find connect people roster', 'Member Directory Guide', 'guide/members/directory.html'),
    _page('guide_search', 'member', 'Global Search', 'Find anything in Parliament quickly.',
          'Global Search find quickly navigate', 'Global Search Guide', 'guide/members/search.html'),
    _page('guide_developer_api', 'member', 'Developer API', 'Access Parliament data programmatically for integrations.',
          'Developer API access data programmatically integrations rest', 'Developer API Guide',
          'guide/members/developer_api.html'),
    _page('guide_chat', 'member', 'Chat Channels', 'Communicate with members, committees, and groups.',
          'Chat Channels communicate members committees groups message', 'Chat Channels Guide',
          'guide/members/chat.html'),
    _page('guide_service_hours', 'member', 'Service Hours', 'Log and track your community service hours.',
          'Service Hours log track community volunteer', 'Service Hours Guide', 'guide/members/service_hours.html'),
    _page('guide_pledge_tasks', 'member', 'Pledge Task Tracker', 'View and complete your assigned pledge tasks and quizzes.',
          'Pledge Task Tracker assigned tasks quizzes complete', 'Pledge Task Tracker Guide',
          'guide/members/pledge_tasks.html'),
    _page('guide_legislation', 'member', 'Legislation & Voting',
          'Understand how to participate in chapter governance and cast your vote.',
          'Legislation Voting bills resolutions cast vote view results', 'Legislation Guide',
          'guide/members/legislation.html'),
    _page('guide_committees', 'member', 'Committees', 'Understand chapter committees and how to participate.',
          'Committees join participate dashboard chair documents', 'Committees Guide', 'guide/members/committees.html'),
])


def _render_static_guide(request, url_name):
    """A static guide page: its registry entry's template, title and category."""
    page = GUIDE_PAGES[url_name]
    return render(request, page['template'], {'page_title': page['page_title'], 'category': page['section']})


@login_required
def guide_index(request):
    """
    Guide landing page - shows all available guides organized by category.
    """
    # Get all published articles grouped by category
    officer_articles = GuideArticle.objects.filter(
        is_published=True,
        category='officer'
    )
    member_articles = GuideArticle.objects.filter(
        is_published=True,
        category='member'
    )
    admin_articles = GuideArticle.objects.filter(
        is_published=True,
        category='admin'
    )
    general_articles = GuideArticle.objects.filter(
        is_published=True,
        category='general'
    )

    # Get active tours
    tours = GuideTour.objects.filter(is_active=True)

    # Get user's tour progress
    user_progress = {}
    if request.user.is_authenticated:
        progress_records = UserTourProgress.objects.filter(user=request.user)
        for p in progress_records:
            user_progress[p.tour_id] = {
                'current_step': p.current_step,
                'completed': p.completed
            }

    context = {
        'officer_articles': officer_articles,
        'member_articles': member_articles,
        'admin_articles': admin_articles,
        'general_articles': general_articles,
        'tours': tours,
        'user_progress': user_progress,
    }

    return render(request, 'guide/index.html', context)


@login_required
def guide_officer_hub(request):
    """
    Officer guide hub - shows all officer-specific guides.
    """
    articles = GuideArticle.objects.filter(
        is_published=True,
        category='officer'
    )

    tours = GuideTour.objects.filter(
        is_active=True,
        category='officer'
    )

    context = {
        'articles': articles,
        'tours': tours,
        'category': 'officer',
        'category_title': 'Officer Guides',
    }

    return render(request, 'guide/category_hub.html', context)


@login_required
def guide_article(request, slug):
    """
    View a specific guide article.
    """
    article = get_object_or_404(GuideArticle, slug=slug, is_published=True)

    # Sanitize HTML before rendering to prevent stored XSS
    safe_content = mark_safe(  # nosec  # B308,B703: bleach-cleaned against a strict allowlist; default protocols block javascript:
        guide_article_html(article)
    )

    # Get related articles in same category
    related_articles = GuideArticle.objects.filter(
        is_published=True,
        category=article.category
    ).exclude(id=article.id)[:5]

    context = {
        'article': article,
        'article_content': safe_content,
        'related_articles': related_articles,
    }

    return render(request, 'guide/article.html', context)


# =============================================================================
# OFFICER GUIDE PAGES (Static Content)
# =============================================================================

@login_required
def guide_events(request):
    """
    Events management guide for officers.
    """
    return _render_static_guide(request, 'guide_events')


@login_required
def guide_announcements(request):
    """
    Announcements guide for officers.
    """
    return _render_static_guide(request, 'guide_announcements')


@login_required
def guide_attendance(request):
    """
    Attendance management guide for officers.
    """
    return _render_static_guide(request, 'guide_attendance')


@login_required
def guide_chapter_minutes(request):
    """
    Chapter minutes guide for officers.
    """
    return _render_static_guide(request, 'guide_chapter_minutes')


@login_required
def guide_managing_members(request):
    """
    Managing members guide for officers.
    """
    return _render_static_guide(request, 'guide_managing_members')


@login_required
def guide_slating(request):
    """
    Slating and elections guide for officers.
    """
    return _render_static_guide(request, 'guide_slating')


@login_required
def guide_kai(request):
    """
    Kai reports guide for officers.
    """
    return _render_static_guide(request, 'guide_kai')


@login_required
def guide_legislation(request):
    """
    Legislation guide for all members.
    """
    return _render_static_guide(request, 'guide_legislation')


@login_required
def guide_committees(request):
    """
    Committees guide for all members.
    """
    return _render_static_guide(request, 'guide_committees')


# =============================================================================
# MEMBER GUIDE PAGES (Additional)
# =============================================================================

@login_required
def guide_profile(request):
    """
    Profile management guide for all members.
    """
    return _render_static_guide(request, 'guide_profile')


@login_required
def guide_calendar(request):
    """
    Calendar and event subscription guide for all members.
    """
    return _render_static_guide(request, 'guide_calendar')


@login_required
def guide_notifications(request):
    """
    Notifications center guide for all members.
    """
    return _render_static_guide(request, 'guide_notifications')


@login_required
def guide_excuses(request):
    """
    Submitting excuses guide for all members.
    """
    return _render_static_guide(request, 'guide_excuses')


@login_required
def guide_2fa(request):
    """
    Two-factor authentication guide for all members.
    """
    return _render_static_guide(request, 'guide_2fa')


@login_required
def guide_directory(request):
    """
    Member directory guide for all members.
    """
    return _render_static_guide(request, 'guide_directory')


@login_required
def guide_search(request):
    """
    Global search guide for all members.
    """
    return _render_static_guide(request, 'guide_search')


@login_required
def guide_developer_api(request):
    """
    Developer API guide for members who want to integrate with Parliament.
    """
    return _render_static_guide(request, 'guide_developer_api')


@login_required
def guide_chat(request):
    """Chat channels guide for all members."""
    return _render_static_guide(request, 'guide_chat')


@login_required
def guide_service_hours(request):
    """Service hours guide for all members."""
    return _render_static_guide(request, 'guide_service_hours')


@login_required
def guide_pledge_tasks(request):
    """Pledge task tracker guide."""
    return _render_static_guide(request, 'guide_pledge_tasks')


# =============================================================================
# OFFICER GUIDE PAGES (Additional)
# =============================================================================

@login_required
def guide_recruitment(request):
    """Recruitment committee guide for officers."""
    return _render_static_guide(request, 'guide_recruitment')


@login_required
def guide_education(request):
    """Education committee / pledge education guide for officers."""
    return _render_static_guide(request, 'guide_education')


@login_required
def guide_resolutions(request):
    """
    Resolution management guide for officers.
    """
    return _render_static_guide(request, 'guide_resolutions')


@login_required
def guide_activity_logs(request):
    """
    Activity logs guide for officers.
    """
    return _render_static_guide(request, 'guide_activity_logs')


@login_required
def guide_kai_forms(request):
    """
    Kai form customization guide for officers.
    """
    return _render_static_guide(request, 'guide_kai_forms')


# =============================================================================
# TOUR API ENDPOINTS
# =============================================================================

@login_required
def tour_start(request, tour_slug):
    """
    Start or resume a tour. Returns tour data as JSON.
    """
    tour = get_object_or_404(GuideTour, slug=tour_slug, is_active=True)

    # Get or create user progress
    progress, created = UserTourProgress.objects.get_or_create(
        user=request.user,
        tour=tour,
        defaults={'current_step': 0}
    )

    # If completed, allow restart
    if progress.completed and request.GET.get('restart'):
        progress.current_step = 0
        progress.completed = False
        progress.completed_at = None
        progress.save(update_fields=['current_step', 'completed', 'completed_at'])

    # Get all steps
    steps = tour.steps.all().values(
        'step_number', 'title', 'content',
        'target_selector', 'target_page', 'position',
        'wait_for_click', 'advance_on_event'
    )

    return JsonResponse({
        'tour': {
            'id': tour.id,
            'name': tour.name,
            'slug': tour.slug,
            'description': tour.description,
        },
        'steps': list(steps),
        'current_step': progress.current_step,
        'completed': progress.completed,
    })


@login_required
def tour_advance(request, tour_slug):
    """
    Advance to next step in a tour.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    tour = get_object_or_404(GuideTour, slug=tour_slug, is_active=True)

    try:
        progress = UserTourProgress.objects.get(user=request.user, tour=tour)
    except UserTourProgress.DoesNotExist:
        return JsonResponse({'error': 'Tour not started'}, status=400)

    if progress.advance_step():
        return JsonResponse({
            'success': True,
            'current_step': progress.current_step,
            'completed': progress.completed,
        })
    else:
        return JsonResponse({
            'success': False,
            'message': 'Already completed',
            'completed': True,
        })


@login_required
def tour_complete(request, tour_slug):
    """
    Mark a tour as completed.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    tour = get_object_or_404(GuideTour, slug=tour_slug, is_active=True)

    progress, created = UserTourProgress.objects.get_or_create(
        user=request.user,
        tour=tour
    )

    if not progress.completed:
        progress.completed = True
        progress.completed_at = timezone.now()
        progress.current_step = tour.step_count
        progress.save(update_fields=['completed', 'completed_at', 'current_step'])

    return JsonResponse({
        'success': True,
        'completed': True,
    })


@login_required
def tour_skip(request, tour_slug):
    """
    Skip/dismiss a tour.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    tour = get_object_or_404(GuideTour, slug=tour_slug, is_active=True)

    # Mark as completed so it doesn't show again
    progress, created = UserTourProgress.objects.get_or_create(
        user=request.user,
        tour=tour
    )

    progress.completed = True
    progress.completed_at = timezone.now()
    progress.save(update_fields=['completed', 'completed_at'])

    return JsonResponse({
        'success': True,
        'skipped': True,
    })


# =============================================================================
# HANDOFF DOCUMENT VIEWS
# =============================================================================

_HANDOFF_ALLOWED_TAGS = _GUIDE_ALLOWED_TAGS + ['table', 'thead', 'tbody', 'tr', 'th', 'td']
_HANDOFF_ALLOWED_ATTRS = {
    **_GUIDE_ALLOWED_ATTRS,
    'table': ['class'],
    'th': ['colspan', 'rowspan'],
    'td': ['colspan', 'rowspan'],
}


def _render_markdown_doc(file_path):
    """Read a markdown file and return sanitized HTML."""
    with open(file_path, 'r') as f:
        raw = f.read()
    html = md_lib.markdown(raw, extensions=['tables', 'fenced_code', 'toc'])
    return mark_safe(bleach.clean(html, tags=_HANDOFF_ALLOWED_TAGS,  # nosec  # B308,B703: repo-controlled docs/ files, bleach-cleaned anyway
                                  attributes=_HANDOFF_ALLOWED_ATTRS, strip=True))


def _handoff_doc_path(filename):
    return os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'docs', filename))


def can_read_developer_handoff(user):
    """The Developer Handoff Guide's rule (staff or Officers), shared with the app."""
    return bool(user.is_staff or getattr(user, 'member_type', None) == 'Officer')


@login_required
def guide_officer_handoff(request):
    """
    Officer & Admin Guide rendered from docs/OFFICER_GUIDE.md.
    """
    page = GUIDE_PAGES['guide_officer_handoff']
    content = _render_markdown_doc(_handoff_doc_path(page['doc']))
    context = {
        'page_title': page['page_title'],
        'doc_content': content,
        'back_url': 'guide_officer_hub',
        'back_label': 'Officer Guides',
    }
    return render(request, 'guide/handoff.html', context)


@login_required
def guide_developer_handoff(request):
    """
    Developer Handoff Guide rendered from docs/HANDOFF_DEVELOPER.md.
    Officers only — gates behind member_type check.
    """
    if not can_read_developer_handoff(request.user):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    page = GUIDE_PAGES['guide_developer_handoff']
    content = _render_markdown_doc(_handoff_doc_path(page['doc']))
    context = {
        'page_title': page['page_title'],
        'doc_content': content,
        'back_url': 'guide_index',
        'back_label': 'Guide Index',
    }
    return render(request, 'guide/handoff.html', context)


# =============================================================================
# SHARED WITH THE APP (src/mobile/views_guide.py)
# =============================================================================

def visible_guide_pages(user, section=None):
    """The registry's pages ``user`` may open, in the index's order."""
    return [p for p in GUIDE_PAGES.values()
            if (section is None or p['section'] == section)
            and (not p['officer_only'] or can_read_developer_handoff(user))]


_BREADCRUMB = re.compile(r'<nav\b[^>]*aria-label="Breadcrumb"[^>]*>.*?</nav>', re.S | re.I)


def guide_page_html(request, url_name):
    """
    The HTML a registry page shows in its main column: a static page's
    ``{% block content %}`` rendered alone (no site chrome, breadcrumb removed),
    or a handoff doc's sanitized markdown. Raises KeyError for an unknown page.
    """
    page = GUIDE_PAGES[url_name]
    if page['doc']:
        return str(_render_markdown_doc(_handoff_doc_path(page['doc'])))
    from django.template import Context
    from django.template.loader import get_template
    from django.template.loader_tags import ExtendsNode
    template = get_template(page['template']).template
    extends = template.nodelist.get_nodes_by_type(ExtendsNode)[0]
    block = extends.blocks['content']
    context = Context({'request': request, 'user': request.user,
                       'page_title': page['page_title'], 'category': page['section']},
                      autoescape=template.engine.autoescape)
    with context.render_context.push_state(template), context.bind_template(template):
        html = block.nodelist.render(context)
    return _BREADCRUMB.sub('', html)


def guide_article_html(article):
    """A GuideArticle's content as the article page shows it (bleach-cleaned)."""
    return bleach.clean(article.content or '', tags=_GUIDE_ALLOWED_TAGS,
                        attributes=_GUIDE_ALLOWED_ATTRS, strip=True)
