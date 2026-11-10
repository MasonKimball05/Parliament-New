"""
Slating Applications Review Views

Committee review of applications.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse
from django.db.models import Count, F, Q
from src.models import (
    SlatingPeriod, SlatingApplication, SlatingPosition, SlatingActivity,
    SlateCandidate, SlatingNominee,
)
from .permissions import can_manage_period, can_view_applications, slating_committee_required, slating_chair_required
from src.models.users import member_defer, member_prefetch
from .apply import tiered_position_lists


# ── Shared with the app (src/mobile/views_slating_committee.py) ─────────────

def filter_applications(period, status_filter='', position_id=None, gpa_level=None, search=''):
    """
    The review list: every application but drafts, newest submission first,
    narrowed by status, a wanted position (any tier), GPA level and a name /
    roll number / user id search.
    """
    # v3.17.3: applications_review.html:130 renders an "Interviewed" marker per
    # row from `app.interviews`, which was a query per application. Prefetching
    # is the whole fix — `.exists()` on a prefetched relation costs nothing (see
    # PrefetchCacheBehaviourTests; CLAUDE.md's checklist is wrong about this).
    applications = period.applications.exclude(
        status='draft'
    ).select_related('applicant').defer(*member_defer('applicant')).prefetch_related(
        'interviews'
    ).order_by('-submitted_at')

    # Apply filters
    if status_filter:
        applications = applications.filter(status=status_filter)

    if position_id is not None:
        # Filter by position preference (handles both legacy list and new tiered dict format)
        filtered_apps = []
        for app in applications:
            if position_id in app.get_preferred_positions():
                filtered_apps.append(app.id)
        applications = applications.filter(id__in=filtered_apps)

    if gpa_level is not None:
        applications = applications.filter(gpa_level=gpa_level)

    if search:
        # v3.25.0 — `role_number` added: see `global_search`. Slating is the
        # surface where an officer is most likely to be working from a written
        # list of roll numbers rather than names.
        applications = applications.filter(
            Q(applicant__name__icontains=search) |
            Q(applicant__role_number__icontains=search) |
            Q(applicant__user_id__icontains=search)
        )
    return applications


def application_status_counts(period):
    """
    ``{status: {label, count}}`` for every status but draft.

    v3.17.3: was one COUNT per status choice — eight round trips over the same
    table, found by the detail-route sweep (this page takes a period_id, so the
    zero-argument N+1 test never reached it). One GROUP BY answers all of them;
    statuses with no applications keep their explicit 0 rather than vanishing,
    which is what the previous loop produced.
    """
    _counts = dict(
        period.applications.exclude(status='draft')
        .values_list('status')
        .annotate(n=Count('pk'))
    )
    return {
        status: {'label': label, 'count': _counts.get(status, 0)}
        for status, label in SlatingApplication.STATUS_CHOICES
        if status != 'draft'
    }


def review_sections(application, user):
    """
    The application's answers by section, in display order. Fields not shown
    in review are left out unless ``user`` is a site admin.
    """
    sections = {}
    for response in application.responses.select_related('field').all():
        if not response.field.show_in_review and not user.is_admin:
            # Skip fields not shown in review (unless admin)
            continue
        section = response.field.section or 'General'
        if section not in sections:
            sections[section] = []
        sections[section].append(response)

    # Sort each section by display order
    for section in sections:
        sections[section].sort(key=lambda r: r.field.display_order)
    return sections


def can_review_application(user, period):
    """
    Whether the detail page shows the review panel: the people ``submit_review``
    lets save one (``can_manage_period``: the slating manager, the committee's
    admin and chairs, a site admin while the period isn't locked). 11-05-26:
    was a site admin or a committee chair, which left out the slating manager
    and showed a locked-out admin a panel the server refused.
    """
    return can_manage_period(user, period)


TIER_LABELS = (('first_choice', '1st'), ('second_choice', '2nd'), ('third_choice', '3rd'))


def ranked_positions(application, titles):
    """
    The applicant's wanted positions by preference, for the list:
    ``[{'tier': '1st', 'positions': ['EVP']}, ...]`` (tiers with none left out).
    ``titles`` is ``{position id: title}`` for the period's active positions.
    """
    prefs = application.position_preferences or {}
    if isinstance(prefs, list):   # legacy: all first choice
        prefs = {'first_choice': prefs}
    out = []
    for tier, label in TIER_LABELS:
        names = [titles[pid] for pid in prefs.get(tier, []) or [] if pid in titles]
        if names:
            out.append({'tier': label, 'positions': names})
    return out


def save_review(period, application, user, new_status, review_notes, verify_gpa=False, unverify_gpa=False):
    """
    The review panel's save: a new status (logged when it changes), the
    confidential review notes (the reviewer becomes ``user`` when they
    change) and GPA verification.
    """
    if new_status and new_status in dict(SlatingApplication.STATUS_CHOICES):
        old_status = application.status
        application.status = new_status

        # Log status change
        if old_status != new_status:
            SlatingActivity.objects.create(
                period=period,
                user=user,
                action='application_reviewed',
                details=f'Changed {application.applicant.name} status from {old_status} to {new_status}',
                metadata={'application_id': application.id, 'old_status': old_status, 'new_status': new_status}
            )

    # Update review notes
    if review_notes != application.review_notes:
        application.review_notes = review_notes
        application.reviewer = user

    # Verify GPA
    if verify_gpa:
        application.gpa_verified = True
    elif unverify_gpa:
        application.gpa_verified = False

    application.save()


@login_required
@slating_committee_required
def applications_list(request, period_id):
    """
    View all applications for a period.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Filter options
    status_filter = request.GET.get('status', '')
    position_filter = request.GET.get('position', '')
    gpa_level_filter = request.GET.get('gpa_level', '')
    search = request.GET.get('search', '')

    applications = filter_applications(
        period, status_filter,
        int(position_filter) if position_filter else None,
        int(gpa_level_filter) if gpa_level_filter else None,
        search)

    # Get positions for filter dropdown
    positions = period.positions.filter(is_active=True).order_by('display_order')

    status_counts = application_status_counts(period)

    # 11-05-26: each row shows the applicant's ranked positions.
    titles = {p.id: p.title for p in positions}
    applications = list(applications)
    for app in applications:
        app.ranked_positions = ranked_positions(app, titles)

    context = {
        'period': period,
        'applications': applications,
        'positions': positions,
        'status_filter': status_filter,
        'position_filter': position_filter,
        'gpa_level_filter': gpa_level_filter,
        'search': search,
        'status_counts': status_counts,
        'status_choices': [s for s in SlatingApplication.STATUS_CHOICES if s[0] != 'draft'],
        'gpa_level_choices': SlatingApplication.GPA_LEVEL_CHOICES,
        # v4.0.0: the Import button is for the people who manage the period.
        'can_manage': can_manage_period(request.user, period),
    }

    return render(request, 'slating/applications_review.html', context)


WANTED_TIERS = ('first_choice', 'second_choice', 'third_choice')

#: What the applicant's history card lists (SlatingActivity rows that name the application).
PROFILE_ACTIVITY_LIMIT = 25


def applicant_profile(application, tiered_positions):
    """
    Everything about one applicant in their period, for the application page
    (11-10-26: it was the application alone, with a 50-word snippet of the
    interview notes). Only for people who may read interview notes (the
    page is ``slating_committee_required``, the same check as the notes form).

    * ``interviews``: every interview, newest first, with its panel and
      recommended positions (prefetched: one query each, not one per row).
    * ``recommended_ids``: positions any completed interview recommended
      them for; ``any_positions_marked`` is False until one marks any (so a
      wanted position missing from the set only reads "not recommended"
      once someone has actually marked positions).
    * ``extra_recommended``: positions an interviewer recommended that they
      didn't ask for.
    * ``latest_interview`` / ``latest_recommendation``: for the summary card.
    * ``slate_spots``, ``nominations``: where they've been placed this period.
    * ``history``: the period's activity log entries about this application.
    """
    interviews = list(
        application.interviews.prefetch_related(
            member_prefetch('interviewers'), 'recommended_positions'
        ).order_by(F('scheduled_at').desc(nulls_last=True), '-created_at')
    )
    completed = [i for i in interviews if i.completed_at]
    marked = {p.id: p for i in completed for p in i.recommended_positions.all()}
    any_marked = bool(marked)

    wanted_ids = {p.id for tier in WANTED_TIERS for p in (tiered_positions.get(tier) or [])}
    extra = sorted((p for pid, p in marked.items() if pid not in wanted_ids),
                   key=lambda p: (p.display_order, p.title))

    # Scheduled ones first (nulls last on every database), so [0] is the one to show.
    latest = interviews[0] if interviews else None
    latest_rec = next((i for i in completed if i.recommendation), None)

    slate_spots = list(
        SlateCandidate.objects.filter(application=application)
        .select_related('slate', 'position').order_by('slate__created_at', 'display_order')
    )
    nominations = list(
        SlatingNominee.objects.filter(period_id=application.period_id, member_id=application.applicant_id)
        .select_related('position')
    )
    history = list(
        SlatingActivity.objects.filter(period_id=application.period_id,
                                       metadata__application_id=application.id)
        .select_related('user').defer(*member_defer('user'))
        .order_by('-timestamp')[:PROFILE_ACTIVITY_LIMIT]
    )
    return {
        'interviews': interviews,
        'completed_interviews': len(completed),
        'recommended_ids': set(marked),
        'recommended_positions_list': sorted(marked.values(), key=lambda p: (p.display_order, p.title)),
        'any_positions_marked': any_marked,
        'extra_recommended': extra,
        'do_not_want_ids': {p.id for p in (tiered_positions.get('do_not_want') or [])},
        'latest_interview': latest,
        'latest_recommendation': latest_rec,
        'slate_spots': slate_spots,
        'nominations': nominations,
        'history': history,
    }


@login_required
@slating_committee_required
def application_detail(request, period_id, app_id):
    """
    View a single application in detail.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    application = get_object_or_404(
        SlatingApplication,
        id=app_id,
        period=period
    )

    sections = review_sections(application, request.user)

    # Get positions
    positions = period.positions.filter(is_active=True)
    position_dict = {p.id: p for p in positions}

    tiered_positions, selected_positions = tiered_position_lists(application, position_dict)
    profile = applicant_profile(application, tiered_positions)

    can_review = can_review_application(request.user, period)

    context = {
        'period': period,
        'application': application,
        'sections': sections,
        'selected_positions': selected_positions,
        'tiered_positions': tiered_positions,
        **profile,
        'can_review': can_review,
        'can_schedule': can_view_applications(request.user, period),   # 11-06-26: the whole committee
        'can_manage': can_manage_period(request.user, period),
        'status_choices': SlatingApplication.STATUS_CHOICES,
    }

    return render(request, 'slating/application_detail.html', context)


@login_required
@slating_chair_required
def submit_review(request, period_id, app_id):
    """
    Submit review notes and status update for an application.
    """
    if request.method != 'POST':
        return redirect('slating_app_detail', period_id=period_id, app_id=app_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)
    application = get_object_or_404(SlatingApplication, id=app_id, period=period)

    save_review(period, application, request.user, request.POST.get('status'),
                request.POST.get('review_notes', '').strip(),
                verify_gpa=request.POST.get('verify_gpa') == 'on',
                unverify_gpa=request.POST.get('unverify_gpa') == 'on')

    messages.success(request, 'Application review saved.')
    return redirect('slating_app_detail', period_id=period_id, app_id=app_id)


@login_required
@slating_chair_required
def bulk_update_status(request, period_id):
    """
    Bulk update status for multiple applications.
    """
    if request.method != 'POST':
        return redirect('slating_applications', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    app_ids = request.POST.getlist('application_ids')
    new_status = request.POST.get('status')

    if not app_ids or not new_status:
        messages.error(request, 'No applications selected or status not specified.')
        return redirect('slating_applications', period_id=period_id)

    if new_status not in dict(SlatingApplication.STATUS_CHOICES):
        messages.error(request, 'Invalid status.')
        return redirect('slating_applications', period_id=period_id)

    # Update applications
    updated = SlatingApplication.objects.filter(
        id__in=app_ids,
        period=period
    ).exclude(status='draft').update(status=new_status)

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='application_reviewed',
        details=f'Bulk updated {updated} applications to {new_status}',
        metadata={'application_ids': app_ids, 'new_status': new_status}
    )

    messages.success(request, f'Updated {updated} applications to {new_status}.')
    return redirect('slating_applications', period_id=period_id)
