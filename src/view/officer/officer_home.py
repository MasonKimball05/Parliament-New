from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from src.models import (
    CommitteeDocument, Event, Legislation, CommitteeLegislation, ContactSubmission,
    SlatingPeriod, SlatingBallot, Committee, ParliamentUser,
)
from src.decorators import officer_or_advisor_required
from src.feature_flag_decorators import require_page_enabled

@login_required
@officer_or_advisor_required
@require_page_enabled('officer_home')
def officer_home(request):
    now = timezone.now()

    # Get recent reports (last 5)
    recent_reports = CommitteeDocument.objects.filter(
        document_type='report'
    ).select_related('committee', 'uploaded_by').order_by('-uploaded_at')[:5]

    # Get upcoming meetings/events (next 5, exclude archived)
    upcoming_events = Event.objects.filter(
        date_time__gte=now,
        is_active=True,
        archived=False
    ).select_related('created_by').order_by('date_time')[:5]

    # Get recent member actions - recent legislation and committee documents (last 5)
    recent_legislation = Legislation.objects.filter(
        status='draft'
    ).select_related('posted_by').order_by('-created_at')[:3]

    recent_committee_docs = CommitteeDocument.objects.select_related(
        'committee', 'uploaded_by'
    ).order_by('-uploaded_at')[:3]

    # Get recent committee legislation
    recent_committee_legislation = CommitteeLegislation.objects.filter(
        status='draft'
    ).select_related('committee', 'posted_by').order_by('-created_at')[:2]

    # Contact submissions — most recent 10, with unread count
    contact_submissions = ContactSubmission.objects.all()[:10]
    unread_contact_count = ContactSubmission.objects.filter(is_read=False).count()

    # === SLATING ===
    active_slating_period = SlatingPeriod.objects.filter(
        status__in=['nominations_open', 'voting_open', 'results_published']
    ).first()

    # Officers always have slating visibility; check active period's committee for management actions
    slating_committee = active_slating_period.slating_committee if active_slating_period else None
    has_slating_access = request.user.is_admin or bool(
        slating_committee and (
            slating_committee.admin == request.user or
            slating_committee.members.filter(pk=request.user.pk).exists() or
            slating_committee.chairs.filter(pk=request.user.pk).exists()
        )
    )

    slating_positions = []
    slating_total_applications = 0
    slating_pending_review = 0
    officer_has_voted = False
    slating_ballots_cast = 0
    slating_total_voters = 0
    slating_passed_slate = None
    slating_slate_candidates = []

    if active_slating_period:
        slating_positions = list(
            active_slating_period.positions.filter(is_active=True).order_by('display_order', 'title')
        )
        if active_slating_period.status == 'nominations_open':
            slating_total_applications = active_slating_period.applications.exclude(
                status='withdrawn'
            ).count()
            slating_pending_review = active_slating_period.applications.filter(
                status='submitted'
            ).count()
        elif active_slating_period.status == 'voting_open':
            officer_has_voted = SlatingBallot.objects.filter(
                period=active_slating_period,
                voter=request.user,
            ).exists()
            slating_ballots_cast = SlatingBallot.objects.filter(
                period=active_slating_period,
                voting_attempt=active_slating_period.current_voting_attempt,
            ).values('voter').distinct().count()
            slating_total_voters = ParliamentUser.objects.filter(member_status='Active').count()
        elif active_slating_period.status == 'results_published':
            slating_passed_slate = active_slating_period.slates.filter(passed=True).first()
            if slating_passed_slate:
                slating_slate_candidates = list(
                    slating_passed_slate.candidates.select_related(
                        'position', 'application__applicant'
                    ).order_by('display_order')
                )

    context = {
        'recent_reports': recent_reports,
        'upcoming_events': upcoming_events,
        'recent_legislation': recent_legislation,
        'recent_committee_docs': recent_committee_docs,
        'recent_committee_legislation': recent_committee_legislation,
        'contact_submissions': contact_submissions,
        'unread_contact_count': unread_contact_count,
        # Slating
        'active_slating_period': active_slating_period,
        'has_slating_access': has_slating_access,
        'slating_positions': slating_positions,
        'slating_total_applications': slating_total_applications,
        'slating_pending_review': slating_pending_review,
        'officer_has_voted': officer_has_voted,
        'slating_ballots_cast': slating_ballots_cast,
        'slating_total_voters': slating_total_voters,
        'slating_passed_slate': slating_passed_slate,
        'slating_slate_candidates': slating_slate_candidates,
    }

    return render(request, 'officer_home.html', context)