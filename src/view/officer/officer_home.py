from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from src.models import CommitteeDocument, Event, Legislation, CommitteeLegislation
from src.decorators import officer_or_advisor_required

@login_required
@officer_or_advisor_required
def officer_home(request):
    # Get recent reports (last 5)
    recent_reports = CommitteeDocument.objects.filter(
        document_type='report'
    ).select_related('committee', 'uploaded_by').order_by('-uploaded_at')[:5]

    # Get upcoming meetings/events (next 5, exclude archived)
    now = timezone.now()
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

    context = {
        'recent_reports': recent_reports,
        'upcoming_events': upcoming_events,
        'recent_legislation': recent_legislation,
        'recent_committee_docs': recent_committee_docs,
        'recent_committee_legislation': recent_committee_legislation,
    }

    return render(request, 'officer_home.html', context)