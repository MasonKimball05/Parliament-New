"""
Slating Results Views

View and publish election results.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.timezone import localtime
from django.db.models import Count
from collections import Counter
from src.models import (
    SlatingPeriod, Slate, SlatingBallot, SlatingVote, SlatingActivity, SlatingPosition
)
from .permissions import slating_chair_required, can_view_applications
from src.decorators import exclude_pledges


@login_required
@exclude_pledges
def view_results(request, period_id):
    """
    View election results.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Check if results are published or user is committee/admin
    from .permissions import can_manage_period
    user_can_manage = can_manage_period(request.user, period)
    can_view = period.status == 'results_published' or user_can_manage

    if not can_view:
        messages.error(request, 'Results are not yet published.')
        return redirect('slating_dashboard')

    # Get primary slate
    slate = Slate.objects.filter(
        period=period,
        is_approved=True,
        slate_type='primary'
    ).first()

    if not slate:
        messages.error(request, 'No slate found.')
        return redirect('slating_dashboard')

    # Get candidates
    candidates = slate.candidates.select_related(
        'position', 'application__applicant', 'write_in_member'
    ).order_by('display_order')

    # Calculate final results
    total_ballots = SlatingBallot.objects.filter(
        period=period,
        voting_attempt=period.current_voting_attempt,
        vote_type='slate'
    ).count()

    # Slate vote tally
    slate_votes = SlatingVote.objects.filter(
        period=period,
        slate=slate
    )

    vote_tally = {
        'total': slate_votes.count(),
        'approve': slate_votes.filter(vote_choice='approve').count(),
        'reject': slate_votes.filter(vote_choice='reject').count(),
        'abstain': slate_votes.filter(vote_choice='abstain').count(),
    }

    # Calculate percentage
    counted_votes = vote_tally['approve'] + vote_tally['reject']
    if counted_votes > 0:
        vote_tally['approval_percentage'] = (vote_tally['approve'] / counted_votes) * 100
    else:
        vote_tally['approval_percentage'] = 0

    # Individual vote results (if applicable)
    individual_results = {}
    individual_summary = None
    if period.vote_type == 'individual':
        passed_count = 0
        failed_count = 0
        pending_count = 0
        for candidate in candidates:
            ind_votes = SlatingVote.objects.filter(slate_candidate=candidate)
            individual_results[candidate.id] = {
                'approve': ind_votes.filter(vote_choice='approve').count(),
                'reject': ind_votes.filter(vote_choice='reject').count(),
                'abstain': ind_votes.filter(vote_choice='abstain').count(),
            }
            if candidate.individual_passed is True:
                passed_count += 1
            elif candidate.individual_passed is False:
                failed_count += 1
            else:
                pending_count += 1
        individual_summary = {
            'passed': passed_count,
            'failed': failed_count,
            'pending': pending_count,
            'total': passed_count + failed_count + pending_count,
            'all_passed': failed_count == 0 and pending_count == 0,
        }

    # Check if user can publish/unpublish
    can_publish = user_can_manage

    # Check if user is committee member (can see rejection details)
    is_committee = can_view_applications(request.user, period)

    # Get rejection analysis for committee members
    rejection_analysis = None
    if is_committee:
        rejection_votes = slate_votes.filter(vote_choice='reject').exclude(rejected_positions=[])
        position_counts = Counter()
        for vote in rejection_votes:
            for pos_id in vote.rejected_positions:
                position_counts[pos_id] += 1

        # Map position IDs to names
        if position_counts:
            positions = {p.id: p for p in SlatingPosition.objects.filter(id__in=position_counts.keys())}
            rejection_analysis = []
            for pos_id, count in position_counts.most_common():
                pos = positions.get(pos_id)
                if pos:
                    # Find the candidate for this position
                    candidate = candidates.filter(position_id=pos_id).first()
                    rejection_analysis.append({
                        'position': pos.title,
                        'candidate_name': candidate.candidate_name if candidate else 'Unknown',
                        'objection_count': count,
                    })

    context = {
        'period': period,
        'slate': slate,
        'candidates': candidates,
        'total_ballots': total_ballots,
        'vote_tally': vote_tally,
        'individual_results': individual_results,
        'individual_summary': individual_summary,
        'can_publish': can_publish,
        'is_published': period.status == 'results_published',
        'is_committee': is_committee,
        'rejection_analysis': rejection_analysis,
    }

    return render(request, 'slating/results.html', context)


@login_required
@slating_chair_required
def publish_results(request, period_id):
    """
    Publish election results.
    """
    if request.method != 'POST':
        return redirect('slating_results', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    if period.status not in ['voting_closed', 'voting_open']:
        messages.error(request, 'Cannot publish results at this time.')
        return redirect('slating_results', period_id=period_id)

    # Update status
    period.status = 'results_published'
    period.results_publish_at = timezone.now()
    period.save()

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='results_published',
        details='Election results published',
        ip_address=request.META.get('REMOTE_ADDR')
    )

    # Save to chapter documents if requested
    if request.POST.get('save_to_documents'):
        try:
            _save_results_to_documents(period, request.user)
            messages.info(request, 'Results saved to Chapter Documents.')
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f'Failed to save results to documents: {e}', exc_info=True)
            messages.warning(request, f'Could not save to Chapter Documents: {e}')

    # Send notifications
    try:
        from src.notification_service import notify_all_active_members
        notify_all_active_members(
            'slating_results',  # Use correct notification type for slating preferences
            f'Election Results: {period.name}',
            message='The officer election results have been published.',
            link=f'/slating/period/{period.id}/results/',
            source_type='SlatingPeriod',
            source_id=period.id,
            exclude_user=request.user
        )
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f'Failed to send results published notification: {e}')

    messages.success(request, 'Results published successfully!')
    return redirect('slating_results', period_id=period_id)


def _save_results_to_documents(period, uploaded_by):
    """
    Generate and save election results as a document to chapter documents.
    """
    from django.core.files.base import ContentFile
    from src.models import CommitteeDocument, ChapterFolder

    # Get vote data
    slate = Slate.objects.filter(
        period=period,
        is_approved=True,
        slate_type='primary'
    ).first()

    if not slate:
        return

    # Get candidates
    candidates = slate.candidates.select_related(
        'position', 'application__applicant', 'write_in_member'
    ).order_by('display_order')

    # Calculate results
    slate_votes = SlatingVote.objects.filter(
        period=period,
        slate=slate
    )

    # Generate document content
    content_lines = [
        f"OFFICER ELECTION RESULTS",
        f"========================",
        f"",
        f"Election: {period.name}",
        f"Academic Term: {period.academic_term}",
        f"Published: {localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')}",
        f"",
    ]

    if period.vote_type == 'individual':
        # Per-position voting summary
        passed_count = sum(1 for c in candidates if c.individual_passed is True)
        failed_count = sum(1 for c in candidates if c.individual_passed is False)
        total_count = candidates.count()
        all_passed = failed_count == 0
        content_lines.extend([
            f"VOTING MODE: Individual Position Votes",
            f"",
            f"RESULT: {passed_count} of {total_count} POSITIONS PASSED",
            f"",
            f"POSITION RESULTS",
            f"----------------",
        ])
        for candidate in candidates:
            status = "PASSED" if candidate.individual_passed else ("FAILED" if candidate.individual_passed is False else "PENDING")
            content_lines.append(
                f"  {candidate.position.title}: {candidate.candidate_name} — {status}"
            )
    else:
        approve_count = slate_votes.filter(vote_choice='approve').count()
        reject_count = slate_votes.filter(vote_choice='reject').count()
        abstain_count = slate_votes.filter(vote_choice='abstain').count()
        total_votes = approve_count + reject_count + abstain_count
        counted_votes = approve_count + reject_count
        approval_percentage = (approve_count / counted_votes * 100) if counted_votes > 0 else 0
        passed = approval_percentage >= period.required_approval_percentage
        content_lines.extend([
            f"VOTING MODE: Full Slate Vote",
            f"",
            f"VOTING SUMMARY",
            f"--------------",
            f"Total Votes Cast: {total_votes}",
            f"Approve: {approve_count}",
            f"Reject: {reject_count}",
            f"Abstain: {abstain_count}",
            f"",
            f"Approval Rate: {approval_percentage:.1f}%",
            f"Required for Passage: {period.required_approval_percentage}%",
            f"",
            f"RESULT: {'SLATE APPROVED' if passed else 'SLATE DID NOT PASS'}",
            f"",
        ])
        if passed:
            content_lines.extend([f"ELECTED OFFICERS", f"----------------"])
        else:
            content_lines.extend([f"PROPOSED SLATE (Not Approved)", f"-----------------------------"])
        for candidate in candidates:
            content_lines.append(
                f"  {candidate.position.title}: {candidate.candidate_name}"
            )

    content_lines.extend([
        f"",
        f"---",
        f"This document was automatically generated by the Parliament system.",
    ])

    content = "\n".join(content_lines)

    # Get or create elections folder
    folder, _ = ChapterFolder.objects.get_or_create(
        name='Elections',
        defaults={
            'description': 'Officer election results and related documents',
            'created_by': uploaded_by
        }
    )

    # File under the chapter committee so it appears in chapter documents
    from src.models import Committee as CommitteeModel
    try:
        filing_committee = CommitteeModel.objects.get(is_chapter_committee=True)
    except CommitteeModel.DoesNotExist:
        filing_committee = period.slating_committee  # fallback

    # Create or update the document for this period
    filename = f"election_results_{period.academic_term.replace(' ', '_').replace('/', '-')}_{period.id}.txt"

    existing = CommitteeDocument.objects.filter(
        title=f"Election Results - {period.name}",
        committee=filing_committee,
    ).first()

    if existing:
        doc = existing
        doc.chapter_folder = folder
        doc.published_to_chapter = True
        doc.visibility = 'all_members'
    else:
        doc = CommitteeDocument(
            committee=filing_committee,
            title=f"Election Results - {period.name}",
            description=f"Official election results for {period.name} ({period.academic_term})",
            uploaded_by=uploaded_by,
            published_to_chapter=True,
            chapter_folder=folder,
            document_type='report',
            visibility='all_members',
        )

    # Save the content as a file (overwrites if exists)
    doc.document.save(filename, ContentFile(content.encode('utf-8')))
    doc.save()

    return doc


@login_required
@slating_chair_required
def results_summary(request, period_id):
    """
    Detailed results summary for committee.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Get all slates
    slates = period.slates.filter(is_approved=True).order_by('-created_at')

    # Get voting history by attempt
    voting_history = []
    for attempt in range(1, period.current_voting_attempt + 1):
        votes = SlatingVote.objects.filter(
            period=period,
            voting_attempt=attempt,
            slate__isnull=False
        )

        approve = votes.filter(vote_choice='approve').count()
        reject = votes.filter(vote_choice='reject').count()
        abstain = votes.filter(vote_choice='abstain').count()
        total = approve + reject + abstain
        counted = approve + reject

        voting_history.append({
            'attempt': attempt,
            'total': total,
            'approve': approve,
            'reject': reject,
            'abstain': abstain,
            'percentage': (approve / counted * 100) if counted > 0 else 0,
            'passed': (approve / counted * 100) >= period.required_approval_percentage if counted > 0 else False,
        })

    # Get participation stats
    from src.models import ParliamentUser
    eligible_voters = ParliamentUser.objects.filter(
        member_status='Active',
        member_type__in=['Member', 'Chair', 'Officer']
    ).count()

    total_unique_voters = SlatingBallot.objects.filter(
        period=period
    ).values('voter').distinct().count()

    participation_rate = (total_unique_voters / eligible_voters * 100) if eligible_voters > 0 else 0

    # Application stats
    app_stats = {
        'total': period.applications.count(),
        'submitted': period.applications.filter(status='submitted').count(),
        'interviewed': period.applications.filter(status='interviewed').count(),
        'slated': period.applications.filter(status='slated').count(),
        'withdrawn': period.applications.filter(status='withdrawn').count(),
    }

    context = {
        'period': period,
        'slates': slates,
        'voting_history': voting_history,
        'eligible_voters': eligible_voters,
        'total_unique_voters': total_unique_voters,
        'participation_rate': participation_rate,
        'app_stats': app_stats,
    }

    return render(request, 'slating/results_summary.html', context)
