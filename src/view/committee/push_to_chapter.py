from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils.http import url_has_allowed_host_and_scheme
from src.models import Committee, CommitteeLegislation, Legislation
import logging
from django.utils import timezone

logger = logging.getLogger('function_calls')


@login_required
def committee_push_to_chapter(request, code):
    """Publish committee vote results to chapter documents page (no chapter vote created)"""
    committee = get_object_or_404(Committee, code=code)

    # Check permissions
    if not committee.is_chair(request.user):
        messages.error(request, 'Only committee chairs can publish results to chapter.')
        return redirect('committee_detail', code=code)

    if request.method == 'POST':
        legislation_id = request.POST.get('legislation_id')
        committee_leg = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

        # Check if already published
        if committee_leg.pushed_to_chapter:
            messages.error(request, 'This item has already been published to chapter.')
        else:
            # Just mark as published - don't create a chapter vote
            committee_leg.pushed_to_chapter = True
            committee_leg.save()

            logger.info(f"{request.user.username} published '{committee_leg.title}' from {committee.code} to chapter documents")
            messages.success(request, f"'{committee_leg.title}' results published to chapter documents.")

        # Redirect back to referring page if specified (validate to prevent open redirect)
        next_url = request.POST.get('next') or request.GET.get('next')
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
        ):
            return redirect(next_url)
        return redirect(f'/committee/{code}/vote/')

    # Get passed committee legislation
    passed_legislation = CommitteeLegislation.objects.filter(
        committee=committee,
        status='passed',
        voting_closed=True
    ).order_by('-created_at')

    return render(request, 'committee/push_to_chapter.html', {
        'committee': committee,
        'passed_legislation': passed_legislation,
    })


@login_required
def create_chapter_vote_from_committee(request, code, legislation_id):
    """Create a new chapter-wide vote based on a committee vote - with configuration options"""
    committee = get_object_or_404(Committee, code=code)
    committee_leg = get_object_or_404(CommitteeLegislation, id=legislation_id, committee=committee)

    # Check permissions
    if not committee.is_chair(request.user) and not request.user.is_admin:
        messages.error(request, 'Only committee chairs or admins can create chapter votes.')
        return redirect('committee_detail', code=code)

    # Check if chapter vote already exists
    if committee_leg.chapter_legislation:
        messages.error(request, 'A chapter vote already exists for this item.')
        return redirect(f'/committee/{code}/vote/')

    if request.method == 'POST':
        # Get form data
        title = request.POST.get('title', f"[{committee.code}] {committee_leg.title}")
        description = request.POST.get('description', committee_leg.description or '')
        vote_mode = request.POST.get('vote_mode', 'percentage')
        required_percentage = request.POST.get('required_percentage', '51')
        required_number = request.POST.get('required_number')
        anonymous_vote = request.POST.get('anonymous_vote') == 'on'
        allow_abstain = not (request.POST.get('remove_abstain') == 'on')
        show_committee_result = request.POST.get('show_committee_result') == 'on'

        # Parse available_at
        from django.utils.dateparse import parse_datetime
        from django.utils.timezone import make_aware
        raw_available_at = request.POST.get('available_at')
        if raw_available_at:
            parsed = parse_datetime(raw_available_at)
            available_at = make_aware(parsed) if parsed else timezone.now()
        else:
            available_at = timezone.now()

        # Handle plurality options
        plurality_options = []
        if vote_mode == 'plurality':
            for i in range(1, 6):
                val = request.POST.get(f'plurality_option_{i}')
                if val:
                    plurality_options.append(val.strip())
            if len(plurality_options) < 2:
                messages.error(request, "Plurality voting requires at least two options.")
                return redirect(request.path)

        # Handle piecewise required number
        if vote_mode == 'piecewise':
            if not required_number or int(required_number) < 1:
                messages.error(request, "Piecewise voting requires a valid number of required votes.")
                return redirect(request.path)
            required_number = int(required_number)
        else:
            required_number = None

        # Get manual committee vote result if provided
        committee_vote_summary = request.POST.get('committee_vote_summary', '')

        # Build description with committee result if requested
        final_description = description
        if show_committee_result and committee_vote_summary:
            final_description = f"{description}\n\n---\n<strong>Committee Vote Result ({committee.name}):</strong>\n{committee_vote_summary}"
        elif show_committee_result and committee_leg.voting_closed:
            # Auto-generate from actual votes
            from src.models import CommitteeVote
            votes = CommitteeVote.objects.filter(legislation=committee_leg)
            if committee_leg.vote_mode == 'plurality':
                tally = {opt: votes.filter(vote_choice=opt).count() for opt in (committee_leg.plurality_options or [])}
                result_text = ", ".join([f"{opt}: {count}" for opt, count in tally.items()])
            else:
                yes = votes.filter(vote_choice='yes').count()
                no = votes.filter(vote_choice='no').count()
                abstain = votes.filter(vote_choice='abstain').count()
                result_text = f"Yes: {yes}, No: {no}, Abstain: {abstain}"

            passed_text = "Passed" if committee_leg.passed else "Did Not Pass"
            final_description = f"{description}\n\n---\n<strong>Committee Vote Result ({committee.name}):</strong> {passed_text}\n{result_text}"

        # Create chapter legislation
        chapter_leg = Legislation.objects.create(
            title=title,
            description=final_description,
            document=committee_leg.document,
            posted_by=request.user,
            available_at=available_at,
            anonymous_vote=anonymous_vote,
            allow_abstain=allow_abstain,
            required_percentage=required_percentage,
            vote_mode=vote_mode,
            plurality_options=plurality_options if vote_mode == 'plurality' else None,
            required_number=required_number,
        )

        # Link to committee legislation
        committee_leg.chapter_legislation = chapter_leg
        committee_leg.save()

        logger.info(f"{request.user.username} created chapter vote for '{committee_leg.title}' from {committee.code}")
        messages.success(request, f"Chapter vote created! Members can now vote on the Vote page.")

        return redirect(f'/committee/{code}/vote/')

    # GET request - show the configuration form
    # Get existing vote data for defaults
    from src.models import CommitteeVote
    votes = CommitteeVote.objects.filter(legislation=committee_leg)

    if committee_leg.vote_mode == 'plurality':
        tally = {opt: votes.filter(vote_choice=opt).count() for opt in (committee_leg.plurality_options or [])}
        auto_summary = ", ".join([f"{opt}: {count}" for opt, count in tally.items()])
    else:
        yes = votes.filter(vote_choice='yes').count()
        no = votes.filter(vote_choice='no').count()
        abstain = votes.filter(vote_choice='abstain').count()
        auto_summary = f"Yes: {yes}, No: {no}, Abstain: {abstain}"

    passed_text = "Passed" if committee_leg.passed else "Did Not Pass"

    return render(request, 'committee/create_chapter_vote.html', {
        'committee': committee,
        'committee_leg': committee_leg,
        'auto_summary': auto_summary,
        'passed_text': passed_text,
        'has_digital_votes': votes.exists(),
    })