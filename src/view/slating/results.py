"""
Slating Results Views

View and publish election results.
"""

from src.slating_rounds import position_states
from .tallies import current_slate_result, individual_tallies, round_history, slate_tally
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
    # 11-06-26: the whole slating committee sees the results (and which
    # positions "no" voters objected to) before they're published.
    can_view = period.status == 'results_published' or can_view_applications(request.user, period)

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
    # 11-05-26: a counted round's stored outcome (current_slate_result).
    current = current_slate_result(period, slate)
    slate_votes = SlatingVote.objects.filter(period=period, slate=slate,
                                             voting_attempt=period.current_voting_attempt)
    vote_tally = {
        'total': current['total'],
        'approve': current['approve'],
        'reject': current['reject'],
        'abstain': current['abstain'],
        'approval_percentage': current['percentage'] or 0,
        'passed': bool(current['passed']),
        'required': current['required'],
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
    overview = results_overview(period, slate, history)
    if overview['live']:
        total_ballots = overview['live']['voted']   # 11-05-26: this round, any kind of round

    # Check if user is committee member (can see rejection details)
    is_committee = can_view_applications(request.user, period)

    # Get rejection analysis for committee members
    rejection_analysis = rejection_breakdown(period, slate, candidates) if is_committee else None

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
        'can_publish_now': period.status in PUBLISHABLE,
        'is_committee': is_committee,
        'rejection_analysis': rejection_analysis,
        'history': history,
        # 10-29-26: each position's outcome (run-offs, floor nominations included).
        'outcomes': position_states(period, slate),
        # 11-05-26: the headline, each position's outcome and counts, the live count.
        'overview': overview,
        'live': overview['live'],
    }

    return render(request, 'slating/results.html', context)


def rejection_breakdown(period, slate, candidates=None, attempt=None):
    """
    Which positions "no" voters objected to on ``slate`` in a round (the
    current one by default), most objected-to first: [{'position',
    'position_id', 'candidate_name', 'objection_count'}], or None when no
    reject named a position. For the slating committee only (11-06-26: the
    website's results and the app share this).
    """
    attempt = period.current_voting_attempt if attempt is None else attempt
    rejection_votes = (SlatingVote.objects.filter(period=period, slate=slate, voting_attempt=attempt,
                                                  vote_choice='reject')
                       .exclude(rejected_positions=[]))
    position_counts = Counter()
    for vote in rejection_votes:
        for pos_id in vote.rejected_positions:
            position_counts[pos_id] += 1
    if not position_counts:
        return None
    positions = {p.id: p for p in SlatingPosition.objects.filter(id__in=position_counts.keys())}
    by_position = {}
    for c in (candidates if candidates is not None else slate.candidates.all()):
        by_position.setdefault(c.position_id, c)
    rows = []
    for pos_id, count in position_counts.most_common():
        pos = positions.get(pos_id)
        if pos:
            candidate = by_position.get(pos_id)
            rows.append({'position': pos.title, 'position_id': pos.id,
                         'candidate_name': candidate.candidate_name if candidate else 'Unknown',
                         'objection_count': count})
    return rows


def round_participation(period, attempt=None):
    """
    {voted, online, paper, present} for one round (default: the current one),
    any kind of round (11-05-26: run-off and floor rounds counted 0 before,
    as only slate ballots were counted). Paper ballots are members who voted
    on paper; ``present`` is who is marked present.
    """
    from src.models import SlatingAttendance
    from .tallies import paper_counts
    attempt = attempt or period.current_voting_attempt
    online = SlatingBallot.objects.filter(period=period, voting_attempt=attempt).values('voter').distinct().count()
    paper = max((p.get('ballots', 0) for p in paper_counts(period, attempt).values()), default=0)
    return {'voted': online + paper, 'online': online, 'paper': paper,
            'present': SlatingAttendance.objects.filter(period=period).count()}


def results_overview(period, slate, history=None):
    """
    What the results pages lead with (11-05-26), shared by the web and the app:

    * ``headline``: "6 elected · 1 left unfilled" from who holds each position;
    * ``positions``: one entry per position — its outcome and how it was
      decided (the full slate, a position round, the chapter's decision, or in
      person), with that round's per-candidate counts only when a position
      round decided it (a position elected by the slate has no counts of its
      own: it showed "0 approve 0 reject" before). Counts are the closed
      rounds' stored snapshots;
    * ``live``: while a round is open, the ballots cast in it (any kind of
      round, paper included) against the members marked present.
    """
    from src.slating_rounds import KIND_LABELS
    history = history if history is not None else round_history(period, slate)
    states = position_states(period, slate)

    # The latest counted (not voided, not open) position round each position was on.
    in_round = {}
    for rnd in history:
        if rnd['status'] in ('voided', 'open') or 'positions' not in rnd:
            continue
        rows = {}
        for row in rnd['positions']:
            rows.setdefault(row['position'], []).append(row)
        for title, rws in rows.items():
            in_round[title] = {'attempt': rnd['attempt'], 'rows': rws}
    slate_passed_round = next((r['attempt'] for r in history if r['status'] == 'passed' and 'slate' in r), None)
    manual = (SlatingActivity.objects.filter(period=period, action='voting_closed', metadata__manual=True,
                                             metadata__has_key='vote_mode').order_by('-timestamp').first())
    manual_counts = (manual.metadata or {}).get('counts', {}) if manual else {}
    chapter = {a.metadata.get('position'): a.details for a in SlatingActivity.objects.filter(
        period=period, action='voting_closed', metadata__manual=True, metadata__has_key='reason')}

    positions = []
    for st in states:
        entry = {'position': st.position, 'title': st.position.title, 'kind': st.kind,
                 'kind_label': KIND_LABELS[st.kind],
                 'winner': st.winner.name if st.winner else None, 'unfilled': st.unfilled,
                 'decided': st.decided, 'how': None, 'round': None, 'tallies': []}
        decided_round = in_round.get(st.position.title)
        if st.position.title in chapter and st.decided:
            entry['how'] = 'chapter'
        elif decided_round and (st.decided or st.failed_last):
            entry['how'] = 'round'
            entry['round'] = decided_round['attempt']
            entry['tallies'] = [{
                'candidate': r['candidate'], 'approve': r['approve'], 'reject': r['reject'],
                'abstain': r['abstain'], 'percentage': r['percentage'], 'passed': r['passed'],
                'threshold': r.get('threshold'), 'pick_one': r.get('kind') != 'approve',
            } for r in decided_round['rows']]
        elif str(st.position.id) in manual_counts and st.decided:
            entry['how'] = 'in_person'
            c = manual_counts[str(st.position.id)]
            entry['tallies'] = [{'candidate': st.winner.name if st.winner else '', **c,
                                 'percentage': (c['approve'] / (c['approve'] + c['reject']) * 100)
                                 if (c['approve'] + c['reject']) else None,
                                 'passed': True, 'threshold': period.required_approval_percentage,
                                 'pick_one': False}]
        elif manual and st.decided:
            entry['how'] = 'in_person'
        elif st.decided and slate_passed_round:
            entry['how'] = 'slate'
            entry['round'] = slate_passed_round
        positions.append(entry)

    elected = sum(1 for p in positions if p['winner'])
    unfilled = sum(1 for p in positions if p['unfilled'] and not p['winner'])
    undecided = sum(1 for p in positions if not p['decided'])
    parts = [f'{elected} elected']
    if unfilled:
        parts.append(f'{unfilled} left unfilled')
    if undecided:
        parts.append(f'{undecided} not yet decided')

    live = round_participation(period) if period.status == 'voting_open' else None
    return {'headline': ' · '.join(parts), 'elected': elected, 'unfilled': unfilled, 'undecided': undecided,
            'positions': positions, 'live': live}


@login_required
@slating_chair_required
def publish_results(request, period_id):
    """
    Publish election results.
    """
    if request.method != 'POST':
        return redirect('slating_results', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    refusal = publish_refusal(period)
    if refusal:
        messages.error(request, refusal)
        return redirect('slating_results', period_id=period_id)

    for level, text in publish_period_results(period, request.user, bool(request.POST.get('save_to_documents'))):
        getattr(messages, level)(request, text)

    messages.success(request, 'Results published successfully!')
    return redirect('slating_results', period_id=period_id)


#: 11-05-26: only once voting is closed (close_round counted it). Publishing
#: while a round was open showed a live, uncounted tally as the result.
PUBLISHABLE = ('voting_closed',)


def publish_refusal(period):
    """
    Why ``period``'s results can't be published now, in words, or None
    (11-05-26): voting must be closed, and the refusal names what is still
    undecided. Both Publish buttons (results page, setup page) and the app use it.
    """
    if period.status == 'results_published':
        return 'The results are already published.'
    if period.status in PUBLISHABLE:
        return None
    undecided = [s.position.title for s in position_states(period) if not s.decided]
    if period.status == 'voting_open':
        why = 'Voting is still open: close it first.'
    elif period.status == 'deliberation' and period.current_voting_attempt:
        why = 'Voting isn’t finished.'
    else:
        why = 'Voting hasn’t happened yet.'
    if undecided and period.current_voting_attempt:
        why += ' Still undecided: ' + ', '.join(undecided) + '.'
    return 'Can’t publish the results yet. ' + why


def publish_period_results(period, user, save_to_documents=False):
    """
    Publish ``period``'s results (the caller checks ``PUBLISHABLE``): logged,
    optionally filed in Chapter Documents, and every active member notified.
    Returns ``[(level, message)]`` notes about the filing, for the caller to
    show. Shared with the app (``src/mobile/views_slating_committee.py``).
    """
    notes = []
    # Update status
    period.status = 'results_published'
    period.results_publish_at = timezone.now()
    period.save(update_fields=['status', 'results_publish_at'])

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='results_published',
        details='Election results published'
    )

    # Save to chapter documents if requested
    if save_to_documents:
        try:
            from django.db import transaction
            with transaction.atomic():   # a failed filing mustn't break the caller's transaction
                _save_results_to_documents(period, user)
            notes.append(('info', 'Results saved to Chapter Documents.'))
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f'Failed to save results to documents: {e}', exc_info=True)
            notes.append(('warning', f'Could not save to Chapter Documents: {e}'))

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
            exclude_user=user
        )
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f'Failed to send results published notification: {e}')
    return notes


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
    final = current_slate_result(period, slate)   # 11-05-26: the counted round's stored outcome

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
        f"Required for passage: {final['required']}% of votes cast (abstentions not counted)",
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

    context = {'period': period, **results_summary_data(period)}

    return render(request, 'slating/results_summary.html', context)


def results_summary_data(period):
    """
    The committee's summary: approved slates, every voting round (failed and
    voided ones too, paper ballots counted), turnout and application counts.
    Shared with the app (``src/mobile/views_slating_committee.py``).
    """
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
    from src.app_review import exclude_review_accounts
    eligible_voters = exclude_review_accounts(ParliamentUser.objects.filter(
        member_status='Active',
        member_type__in=['Member', 'Chair', 'Officer']
    )).count()

    total_unique_voters = SlatingBallot.objects.filter(
        period=period
    ).values('voter').distinct().count()

    # 11-05-26: participation is of the members marked present (who may vote),
    # paper ballots included — the latest round's turnout. It was every voter
    # ever over every active member, present or not.
    from src.models import SlatingAttendance
    present = SlatingAttendance.objects.filter(period=period).count()
    latest = round_participation(period) if period.current_voting_attempt else {'voted': 0}
    participation_rate = min(latest['voted'] / present * 100, 100) if present else 0

    # Application stats — v3.17.3: five COUNTs over one table became one pass.
    app_stats = period.applications.aggregate(
        total=Count('pk'),
        submitted=Count('pk', filter=Q(status='submitted')),
        interviewed=Count('pk', filter=Q(status='interviewed')),
        slated=Count('pk', filter=Q(status='slated')),
        withdrawn=Count('pk', filter=Q(status='withdrawn')),
    )

    return {
        'slates': slates,
        'voting_history': voting_history,
        'eligible_voters': eligible_voters,
        'total_unique_voters': total_unique_voters,
        'participation_rate': participation_rate,
        'present': present,
        'latest_round_voted': latest['voted'],
        'app_stats': app_stats,
    }
