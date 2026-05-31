"""
Guide System Views

Static guide pages and article views for user documentation.
"""

import os
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
    'a':   ['href', 'target', 'rel'],
    'img': ['src', 'alt', 'class', 'width', 'height'],
    'span': ['class'],
    'div':  ['class'],
    'code': ['class'],
    'td':   ['colspan', 'rowspan'],
    'th':   ['colspan', 'rowspan'],
}

from src.models import GuideTour, GuideTourStep, UserTourProgress, GuideArticle

logger = logging.getLogger('function_calls')


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
    safe_content = mark_safe(
        bleach.clean(article.content or '', tags=_GUIDE_ALLOWED_TAGS,
                     attributes=_GUIDE_ALLOWED_ATTRS, strip=True)
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
    context = {
        'page_title': 'Events Management Guide',
        'category': 'officer',
    }
    return render(request, 'guide/officers/events.html', context)


@login_required
def guide_announcements(request):
    """
    Announcements guide for officers.
    """
    context = {
        'page_title': 'Announcements Guide',
        'category': 'officer',
    }
    return render(request, 'guide/officers/announcements.html', context)


@login_required
def guide_attendance(request):
    """
    Attendance management guide for officers.
    """
    context = {
        'page_title': 'Attendance Management Guide',
        'category': 'officer',
    }
    return render(request, 'guide/officers/attendance.html', context)


@login_required
def guide_chapter_minutes(request):
    """
    Chapter minutes guide for officers.
    """
    context = {
        'page_title': 'Chapter Minutes Guide',
        'category': 'officer',
    }
    return render(request, 'guide/officers/chapter_minutes.html', context)


@login_required
def guide_managing_members(request):
    """
    Managing members guide for officers.
    """
    context = {
        'page_title': 'Managing Members Guide',
        'category': 'officer',
    }
    return render(request, 'guide/officers/managing_members.html', context)


@login_required
def guide_slating(request):
    """
    Slating and elections guide for officers.
    """
    context = {
        'page_title': 'Slating & Elections Guide',
        'category': 'officer',
    }
    return render(request, 'guide/officers/slating.html', context)


@login_required
def guide_kai(request):
    """
    Kai reports guide for officers.
    """
    context = {
        'page_title': 'Kai Reports Guide',
        'category': 'officer',
    }
    return render(request, 'guide/officers/kai.html', context)


@login_required
def guide_legislation(request):
    """
    Legislation guide for all members.
    """
    context = {
        'page_title': 'Legislation Guide',
        'category': 'member',
    }
    return render(request, 'guide/members/legislation.html', context)


@login_required
def guide_committees(request):
    """
    Committees guide for all members.
    """
    context = {
        'page_title': 'Committees Guide',
        'category': 'member',
    }
    return render(request, 'guide/members/committees.html', context)


# =============================================================================
# MEMBER GUIDE PAGES (Additional)
# =============================================================================

@login_required
def guide_profile(request):
    """
    Profile management guide for all members.
    """
    context = {
        'page_title': 'Profile Management Guide',
        'category': 'member',
    }
    return render(request, 'guide/members/profile.html', context)


@login_required
def guide_calendar(request):
    """
    Calendar and event subscription guide for all members.
    """
    context = {
        'page_title': 'Calendar Guide',
        'category': 'member',
    }
    return render(request, 'guide/members/calendar.html', context)


@login_required
def guide_notifications(request):
    """
    Notifications center guide for all members.
    """
    context = {
        'page_title': 'Notifications Guide',
        'category': 'member',
    }
    return render(request, 'guide/members/notifications.html', context)


@login_required
def guide_excuses(request):
    """
    Submitting excuses guide for all members.
    """
    context = {
        'page_title': 'Submitting Excuses Guide',
        'category': 'member',
    }
    return render(request, 'guide/members/excuses.html', context)


@login_required
def guide_2fa(request):
    """
    Two-factor authentication guide for all members.
    """
    context = {
        'page_title': 'Two-Factor Authentication Guide',
        'category': 'member',
    }
    return render(request, 'guide/members/2fa.html', context)


@login_required
def guide_directory(request):
    """
    Member directory guide for all members.
    """
    context = {
        'page_title': 'Member Directory Guide',
        'category': 'member',
    }
    return render(request, 'guide/members/directory.html', context)


@login_required
def guide_search(request):
    """
    Global search guide for all members.
    """
    context = {
        'page_title': 'Global Search Guide',
        'category': 'member',
    }
    return render(request, 'guide/members/search.html', context)


# =============================================================================
# OFFICER GUIDE PAGES (Additional)
# =============================================================================

@login_required
def guide_resolutions(request):
    """
    Resolution management guide for officers.
    """
    context = {
        'page_title': 'Resolution Management Guide',
        'category': 'officer',
    }
    return render(request, 'guide/officers/resolutions.html', context)


@login_required
def guide_activity_logs(request):
    """
    Activity logs guide for officers.
    """
    context = {
        'page_title': 'Activity Logs Guide',
        'category': 'officer',
    }
    return render(request, 'guide/officers/activity_logs.html', context)


@login_required
def guide_kai_forms(request):
    """
    Kai form customization guide for officers.
    """
    context = {
        'page_title': 'Kai Form Customization Guide',
        'category': 'officer',
    }
    return render(request, 'guide/officers/kai_forms.html', context)


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
        progress.save()

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
        progress.save()

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
    progress.save()

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
    return mark_safe(bleach.clean(html, tags=_HANDOFF_ALLOWED_TAGS,
                                  attributes=_HANDOFF_ALLOWED_ATTRS, strip=True))


@login_required
def guide_officer_handoff(request):
    """
    Officer & Admin Guide rendered from docs/OFFICER_GUIDE.md.
    """
    doc_path = os.path.join(os.path.dirname(__file__), '..', '..', 'docs', 'OFFICER_GUIDE.md')
    content = _render_markdown_doc(os.path.abspath(doc_path))
    context = {
        'page_title': 'Officer & Admin Guide',
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
    from src.models import ParliamentUser
    user = request.user
    if not (user.is_staff or getattr(user, 'member_type', None) == 'Officer'):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied

    doc_path = os.path.join(os.path.dirname(__file__), '..', '..', 'docs', 'HANDOFF_DEVELOPER.md')
    content = _render_markdown_doc(os.path.abspath(doc_path))
    context = {
        'page_title': 'Developer Handoff Guide',
        'doc_content': content,
        'back_url': 'guide_index',
        'back_label': 'Guide Index',
    }
    return render(request, 'guide/handoff.html', context)
