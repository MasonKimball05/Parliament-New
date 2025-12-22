from django.shortcuts import render
from django.contrib.auth.decorators import login_required, user_passes_test
from src.models import Legislation, CommitteeLegislation, CommitteeDocument

@login_required
@user_passes_test(lambda u: u.member_type in ['Chair', 'Officer'])
def view_all_activity(request):
    """View all recent member activity for officers"""

    # Get all chapter legislation
    all_chapter_legislation = Legislation.objects.select_related(
        'posted_by'
    ).order_by('-created_at')

    # Get all committee legislation
    all_committee_legislation = CommitteeLegislation.objects.select_related(
        'committee', 'posted_by'
    ).order_by('-created_at')

    # Get all committee documents
    all_committee_docs = CommitteeDocument.objects.select_related(
        'committee', 'uploaded_by'
    ).order_by('-uploaded_at')

    # Filter by status
    draft_chapter_leg = all_chapter_legislation.filter(status='draft')
    passed_chapter_leg = all_chapter_legislation.filter(status='passed')
    removed_chapter_leg = all_chapter_legislation.filter(status='removed')

    draft_committee_leg = all_committee_legislation.filter(status='draft')
    passed_committee_leg = all_committee_legislation.filter(status='passed')
    removed_committee_leg = all_committee_legislation.filter(status='removed')

    context = {
        'all_chapter_legislation': all_chapter_legislation,
        'all_committee_legislation': all_committee_legislation,
        'all_committee_docs': all_committee_docs,
        'draft_chapter_leg': draft_chapter_leg,
        'passed_chapter_leg': passed_chapter_leg,
        'removed_chapter_leg': removed_chapter_leg,
        'draft_committee_leg': draft_committee_leg,
        'passed_committee_leg': passed_committee_leg,
        'removed_committee_leg': removed_committee_leg,
    }

    return render(request, 'officer/view_all_activity.html', context)
