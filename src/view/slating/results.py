"""
Slating Results Views

View and publish election results.
"""

from src.slating_rounds import position_states
from .tallies import individual_tallies, round_history, slate_tally
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.utils.timezone import localtime
from django.db.models import Count, Q
from collections import Counter
from src.models import (
    SlatingPeriod, Slate, SlatingBallot, SlatingVote, SlatingActivity, SlatingPosition
)
from .permissions import slating_chair_required, can_view_applications
from src.decorators import pledge_page_allowed
from src.models.users import member_defer


@login_required
@pledge_page_allowed('slating_results')
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
    ).defer(*member_defer('application__applicant', 'write_in_member')).order_by('display_order')

    # Slate vote tally — v4.0.0: the CURRENT round (it counted every round
    # together, so a re-vote's results included the failed round), plus the
    # paper ballots the committee entered for it.
    current = slate_tally(period, slate, period.current_voting_attempt)
    slate_votes = SlatingVote.objects.filter(period=period, slate=slate,
                                             voting_attempt=period.current_voting_attempt)
    vote_tally = {
        'total': current['total'],
        'approve': current['approve'],
        'reject': current['reject'],
        'abstain': current['abstain'],
        'approval_percentage': current['percentage'] or 0,
        'online': current['online'],
        'paper': current['paper'],
        'paper_ballots': current['ballots_paper'],
    }
    total_ballots = current['ballots_online'] + current['ballots_paper']
    history = round_history(period, slate)

    # Individual vote results (if applicable)
    individual_results = {}
    individual_summary = None
    if period.vote_type == 'individual':
        passed_count = 0
        failed_count = 0
        pending_count = 0
        # Each position's counts from the round that decided it (v4.0.0):
        # the latest non-voided round it was on the ballot in.
        decided_in = {}
        for rnd in history:
            if rnd['status'] == 'voided':
                continue
            for row in rnd.get('positions', []):
                decided_in[(row['position'], row['candidate'])] = row
        for candidate in candidates:
            row = decided_in.get((candidate.position.title, candidate.candidate_name))
            individual_results[candidate.id] = row or {'approve': 0, 'reject': 0, 'abstain': 0}
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
            # v4.0.0: the slate's candidates are already loaded; this was a
            # query per objected-to position.
            by_position = {}
            for c in candidates:
                by_position.setdefault(c.position_id, c)
            rejection_analysis = []
            for pos_id, count in position_counts.most_common():
                pos = positions.get(pos_id)
                if pos:
                    candidate = by_position.get(pos_id)
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
        'history': history,
        # 10-29-26: each position's outcome (run-offs, floor nominations included).
        'outcomes': position_states(period, slate),
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
    period.save(update_fields=['status', 'results_publish_at'])

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=request.user,
        action='results_published',
        details='Election results published'
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
    ).defer(*member_defer('application__applicant', 'write_in_member')).order_by('display_order')

    # v4.0.0: built from the round history (every round kept, online and
    # paper ballots counted separately). It used to count every round's slate
    # votes together, so a re-vote's "official" results included the round
    # that failed.
    history = round_history(period, slate)
    final = slate_tally(period, slate, period.current_voting_attempt)

    def fmt_time(dt):
        return localtime(dt).strftime('%b %d, %Y %I:%M %p') if dt else 'unknown'

    def counts(c, paper=None, ballots_paper=0):
        line = f"Approve {c['approve']}, Reject {c['reject']}, Abstain {c['abstain']}"
        if paper and any(paper.values()):
            line += (f"  (includes paper: Approve {paper['approve']}, Reject {paper['reject']}, "
                     f"Abstain {paper['abstain']}"
                     + (f"; {ballots_paper} paper ballot(s)" if ballots_paper else '') + ")")
        return line

    # Generate document content
    content_lines = [
        f"OFFICER ELECTION RESULTS",
        f"========================",
        f"",
        f"Election: {period.name}",
        f"Academic Term: {period.academic_term}",
        f"Published: {localtime(timezone.now()).strftime('%B %d, %Y at %I:%M %p %Z')}",
        f"Required for passage: {period.required_approval_percentage}% of votes cast (abstentions not counted)",
        f"",
    ]

    if period.vote_type == 'individual':
        passed_count = sum(1 for c in candidates if c.individual_passed is True)
        failed_count = sum(1 for c in candidates if c.individual_passed is False)
        total_count = len(list(candidates))
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
            content_lines.append(f"  {candidate.position.title}: {candidate.candidate_name} — {status}")
        content_lines.append("")
    else:
        pct = final['percentage']
        content_lines.extend([
            f"VOTING MODE: Full Slate Vote",
            f"",
            f"FINAL ROUND (round {period.current_voting_attempt})",
            f"------------",
            f"Total Votes Cast: {final['total']}",
            f"  {counts(final, final['paper'], final['ballots_paper'])}",
            f"Approval Rate: {pct:.1f}%" if pct is not None else "Approval Rate: n/a",
            f"",
            f"RESULT: {'SLATE APPROVED' if final['passed'] else 'SLATE DID NOT PASS'}",
            f"",
        ])
        content_lines.extend([f"ELECTED OFFICERS", f"----------------"] if final['passed']
                             else [f"PROPOSED SLATE (Not Approved)", f"-----------------------------"])
        for candidate in candidates:
            content_lines.append(f"  {candidate.position.title}: {candidate.candidate_name}")
        content_lines.append("")

    content_lines.extend([
        f"VOTING HISTORY (every round, including failed and voided ones)",
        f"--------------------------------------------------------------",
    ])
    if not history:
        content_lines.append("  No online voting rounds (results recorded manually).")
    for rnd in history:
        label = {'passed': 'PASSED', 'failed': 'FAILED', 'voided': 'VOIDED (not counted)',
                 'open': 'IN PROGRESS', 'closed': 'CLOSED'}.get(rnd['status'], rnd['status'].upper())
        kind = 'individual positions' if rnd['vote_type'] == 'individual' else 'full slate'
        content_lines.append(f"Round {rnd['attempt']} — {kind} — {label}")
        content_lines.append(f"  Opened {fmt_time(rnd['opened_at'])}; closed {fmt_time(rnd['closed_at'])}")
        if 'slate' in rnd:
            sl = rnd['slate']
            pct = sl.get('percentage')
            content_lines.append(f"  {counts(sl, sl.get('paper'), sl.get('ballots_paper', 0))}"
                                 + (f" — {pct:.1f}% approval" if pct is not None else ""))
            content_lines.append(f"  Online ballots: {sl.get('ballots_online', 0)}")
        for row in rnd.get('positions', []):
            verdict = 'passed' if row['passed'] else ('failed' if row['passed'] is False else 'no votes')
            content_lines.append(f"  {row['position']}: {row['candidate']} — "
                                 f"{counts(row, row.get('paper'), row.get('ballots_paper', 0))} — {verdict}")
        for ev in rnd.get('events', []):
            if ev.get('event') == 'candidate_replaced':
                content_lines.append(f"  Candidate replaced mid-round: {ev['position']} — {ev['candidate']} "
                                     f"had Approve {ev['approve']}, Reject {ev['reject']}, Abstain {ev['abstain']}")
        content_lines.append("")
    content_lines.append("Paper ballots are counted by the slating committee for members who couldn't vote "
                         "online, and are included in the totals above.")

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

    # Voting history — v4.0.0: every round from the round history (failed
    # and voided rounds included, paper ballots counted and shown), one row
    # per slate round or per position in an individual round.
    voting_history = []
    primary = slates.filter(slate_type='primary').first()
    for rnd in round_history(period, primary):
        rows = [('', rnd['slate'])] if 'slate' in rnd else [
            (f"{r['position']} — {r['candidate']}", r) for r in rnd.get('positions', [])]
        for label, r in rows:
            voting_history.append({
                'attempt': rnd['attempt'],
                'label': label,
                'status': rnd['status'] if 'slate' in rnd or rnd['status'] in ('voided', 'open')
                else ('passed' if r.get('passed') else 'failed'),
                'total': r['approve'] + r['reject'] + r['abstain'],
                'approve': r['approve'],
                'reject': r['reject'],
                'abstain': r['abstain'],
                'paper': sum((r.get('paper') or {}).values()),
                'percentage': r.get('percentage') or 0,
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

    # Application stats — v3.17.3: five COUNTs over one table became one pass.
    app_stats = period.applications.aggregate(
        total=Count('pk'),
        submitted=Count('pk', filter=Q(status='submitted')),
        interviewed=Count('pk', filter=Q(status='interviewed')),
        slated=Count('pk', filter=Q(status='slated')),
        withdrawn=Count('pk', filter=Q(status='withdrawn')),
    )

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
