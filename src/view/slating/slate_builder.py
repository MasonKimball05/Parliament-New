"""
Slating Slate Builder Views

Build and manage the officer slate.

11-04-26: each view's work is a helper below it (``draft_slate``,
``build_slate_context``, ``assign_candidate``, ``approve_slate_for_voting``,
``copy_slate_to_draft``, ``add_write_in``, ``replace_candidate``,
``record_manual_results`` …) that the app's screens (src/mobile/
views_slating_slate.py) call too, so both make the same checks, log the same
SlatingActivity and say the same thing. A refusal is a ``SlateError``: the web
shows it as a message and redirects where it always did.
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.http import Http404, JsonResponse
from src.models import (
    SlatingPeriod, SlatingApplication, SlatingPosition,
    Slate, SlateCandidate, SlatingActivity, ParliamentUser
)
from .permissions import slating_chair_required, slating_committee_required, voting_member_required
from src.models.users import member_defer

#: When any voting member may see the approved slate (view_approved_slate).
PUBLIC_STATUSES = ('voting_open', 'voting_closed', 'results_published')
#: Write-ins go on blank positions of the approved slate during these.
WRITE_IN_STATUSES = ('deliberation', 'voting_open')
#: Manual results can't be recorded once these are reached.
MANUAL_RESULTS_CLOSED = ('voting_closed', 'results_published', 'archived')


class SlateError(Exception):
    """A refusal the web shows as ``messages.error``; ``code`` / ``status`` are the app's."""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.status = status


def eligible_members():
    """
    Members a write-in, a replacement or a manual winner may be (the web's
    dropdowns). 11-05-26: the applicants' rule (permissions.applicant_
    ineligibility: active members, pledges included, no advisors); it listed
    inactive members and left pledges out. The committee rule is checked on
    save (``write_in_refusal``).
    """
    return list(
        ParliamentUser.objects.filter(member_status='Active').exclude(member_type='Advisor').order_by('name')
    )


def write_in_refusal(period, member):
    """
    Why ``member`` can't go on the slate as a write-in (or a replacement, or
    an in-person winner), or None (11-05-26): the applicants' rules and not
    on the period's slating committee (``nominee_ineligibility``). Any user
    id was accepted before.
    """
    from .permissions import nominee_ineligibility
    return nominee_ineligibility(member, period)


ROUND_OPEN_SWAP = 'Pause or close voting before changing the slate.'


def swap_refusal(period, position):
    """
    11-05-26: the nominee of a position being voted on can't be swapped while
    the round is open (members would vote on different people, and the votes
    already cast went with the deleted candidate). Pausing first is fine; a
    position not on an open position round's ballot may still be changed.
    """
    if period.status != 'voting_open':
        return None
    if period.vote_type != 'individual':
        return ROUND_OPEN_SWAP
    from src.slating_rounds import ballot_states
    if any(s.position.id == position.id for s in ballot_states(period)):
        return ROUND_OPEN_SWAP
    return None


def approved_primary_slate(period):
    return Slate.objects.filter(period=period, is_approved=True, slate_type='primary').first()


def slate_candidate_rows(slate):
    """A slate's candidates in ballot order, with what the preview shows."""
    return slate.candidates.select_related(
        'position', 'application', 'application__applicant'
    ).defer(*member_defer('application__applicant')).order_by('display_order')


# ── Build ────────────────────────────────────────────────────────────────────

def _newest_draft(period):
    return Slate.objects.filter(period=period, slate_type='draft').order_by('-created_at', '-id').first()


def builder_slate(period, user):
    """
    (slate, read_only) for the Slate Builder (11-05-26): the draft being
    built; once it has been approved (no draft left), the approved slate,
    read-only, rather than a new empty draft with a live Approve button.
    Copy starts a new draft from it; Edit approved slate changes it.
    """
    if _newest_draft(period) is None:
        approved = approved_primary_slate(period)
        if approved is not None:
            return approved, True
    return draft_slate(period, user), False


APPROVED_READ_ONLY = ('This slate is approved and on the ballot. Use Edit approved slate to change it, '
                      'or Copy it to start a new draft.')


def approve_confirm_message(period, slate):
    """The Approve button's confirmation: how full the slate is (11-05-26)."""
    positions = list(period.positions.filter(is_active=True).order_by('display_order'))
    filled = set(slate.candidates.filter(is_runoff=False).values_list('position_id', flat=True))
    missing = [p for p in positions if p.id not in filled]
    detail = f'{len(positions) - len(missing)} of {len(positions)} filled'
    if missing:
        detail += '; ' + ', '.join(p.code or p.title for p in missing) + ' unfilled'
    return f'Approve this slate ({detail})? It goes on the ballot.'


def draft_slate(period, user):
    """
    The period's draft slate, made on first visit. 11-04-26: the newest draft
    when there are several — Copy makes another one and sends the chair here,
    and ``get_or_create`` then failed (MultipleObjectsReturned) every time.
    """
    slate = _newest_draft(period)
    if slate is None:
        slate = Slate.objects.create(period=period, slate_type='draft', name='Draft Slate', created_by=user)
    return slate


def build_slate_context(period, slate):
    """What the slate builder page shows."""
    positions = period.positions.filter(is_active=True).order_by('display_order')

    # Get current slate assignments keyed by (position_id, is_runoff)
    assignments = {}  # position_id -> {'primary': sc, 'runoff': sc|None}
    for sc in slate.candidates.select_related('application', 'application__applicant').defer(*member_defer('application__applicant')):
        entry = assignments.setdefault(sc.position_id, {'primary': None, 'runoff': None})
        if sc.is_runoff:
            entry['runoff'] = sc
        else:
            entry['primary'] = sc

    # All positions have a primary candidate assigned?
    all_primary_filled = all(
        assignments.get(p.id, {}).get('primary') is not None
        for p in positions
    )

    # v3.17.5: the "N of M positions filled" line in slate_builder.html was
    #
    #   {{ slate.candidates.filter.is_runoff.False.count|default:slate.candidates.count }}
    #
    # which is not a valid expression. Django resolves `candidates.filter` to
    # the bound method, CALLS IT WITH NO ARGUMENTS (returning the unfiltered
    # queryset), then fails to find `.is_runoff` on it — so the whole chain
    # resolved to the empty string and `|default:` silently fell through to
    # `slate.candidates.count`. **It was therefore showing the total candidate
    # count, runoffs included, not the number of primary slots filled** — a
    # wrong number, not merely a slow one, and it cost a COUNT per render.
    #
    # `assignments` is already built from `slate.candidates` above, so both
    # numbers are free.
    primary_filled_count = sum(
        1 for entry in assignments.values() if entry['primary'] is not None
    )

    # Get available applications (any status except draft/withdrawn, not yet slated on THIS slate)
    available_apps = period.applications.exclude(
        status__in=['draft', 'withdrawn']
    ).exclude(
        slate_assignments__slate=slate
    ).select_related('applicant').defer(*member_defer('applicant')).prefetch_related('interviews').order_by('applicant__name')

    # Organize applications by first choice position preference
    apps_by_position = {p.id: [] for p in positions}
    apps_by_position['other'] = []

    for app in available_apps:
        first_choices = app.get_first_choice_positions()
        if first_choices:
            # Place in first matched position bucket
            placed = False
            for first_pref in first_choices:
                if first_pref in apps_by_position:
                    apps_by_position[first_pref].append(app)
                    placed = True
                    break
            if not placed:
                apps_by_position['other'].append(app)
        else:
            apps_by_position['other'].append(app)

    # Get existing slates for reference
    existing_slates = period.slates.exclude(id=slate.id).filter(
        slate_type__in=['primary', 'alternative']
    )

    return {
        'approve_confirm': approve_confirm_message(period, slate),
        'period': period,
        'slate': slate,
        'positions': positions,
        'primary_filled_count': primary_filled_count,
        'assignments': assignments,
        'apps_by_position': apps_by_position,
        'available_apps': available_apps,
        'existing_slates': existing_slates,
        'all_primary_filled': all_primary_filled,
    }


def assign_candidate(period, slate, position_id, app_id, is_runoff=False):
    """Put an application in a position's primary (or runoff) slot. Returns the message."""
    position = get_object_or_404(SlatingPosition, id=position_id, period=period)
    application = get_object_or_404(SlatingApplication, id=app_id, period=period)

    # Remove existing assignment for this slot only
    SlateCandidate.objects.filter(slate=slate, position=position, is_runoff=is_runoff).delete()

    # Create new assignment
    SlateCandidate.objects.create(
        slate=slate,
        position=position,
        application=application,
        is_runoff=is_runoff,
        display_order=position.display_order
    )

    # Update application status
    application.status = 'slated'
    application.save(update_fields=['status'])

    label = 'runoff candidate' if is_runoff else 'primary candidate'
    return f'{application.applicant.name} assigned as {label} for {position.title}.'


def unassign_candidate(slate, assignment_id):
    """Take a candidate off the slate (their application goes back to interviewed). Returns the message."""
    assignment = get_object_or_404(SlateCandidate, id=assignment_id, slate=slate)
    applicant_name = assignment.candidate_name
    position_title = assignment.position.title

    # Reset application status (11-04-26: a write-in copied onto the draft has
    # no application; removing one used to fail here)
    if assignment.application_id:
        assignment.application.status = 'interviewed'
        assignment.application.save(update_fields=['status'])

    assignment.delete()

    return f'{applicant_name} removed from {position_title}.'


def save_candidate_notes(slate, assignment_id, notes):
    assignment = get_object_or_404(SlateCandidate, id=assignment_id, slate=slate)
    assignment.notes = notes
    assignment.save(update_fields=['notes'])
    return assignment


@login_required
@slating_committee_required
def build_slate(request, period_id):
    """
    Build the officer slate from applications.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # The draft, or (11-05-26) the approved slate read-only once approved.
    slate, read_only = builder_slate(period, request.user)

    if request.method == 'POST' and read_only:
        messages.error(request, APPROVED_READ_ONLY)
        return redirect('slating_build_slate', period_id=period_id)

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'assign':
            return _handle_assign_candidate(request, period, slate, is_runoff=False)
        elif action == 'assign_runoff':
            return _handle_assign_candidate(request, period, slate, is_runoff=True)
        elif action == 'unassign':
            return _handle_unassign_candidate(request, period, slate)
        elif action == 'save_notes':
            return _handle_save_notes(request, slate)

        return redirect('slating_build_slate', period_id=period_id)

    # GET - show slate builder
    from .permissions import can_manage_period
    return render(request, 'slating/slate_builder.html',
                  {**build_slate_context(period, slate), 'read_only': read_only,
                   # 11-06-26: the committee builds; approving stays with the managers.
                   'can_approve': can_manage_period(request.user, period)})


def _handle_assign_candidate(request, period, slate, is_runoff=False):
    """Assign a candidate to a position on the slate."""
    message = assign_candidate(period, slate, request.POST.get('position_id'),
                               request.POST.get('application_id'), is_runoff)
    messages.success(request, message)
    return redirect('slating_build_slate', period_id=period.id)


def _handle_unassign_candidate(request, period, slate):
    """Remove a candidate from a position."""
    messages.success(request, unassign_candidate(slate, request.POST.get('assignment_id')))
    return redirect('slating_build_slate', period_id=period.id)


def _handle_save_notes(request, slate):
    """Save notes for a slate candidate."""
    save_candidate_notes(slate, request.POST.get('assignment_id'), request.POST.get('notes', '').strip())
    return JsonResponse({'status': 'success'})


# ── Approve, preview, copy ───────────────────────────────────────────────────

def slate_to_approve(period, slate_id=None):
    if slate_id:
        return get_object_or_404(Slate, id=slate_id, period=period)
    # Get draft slate (the one the builder shows; see draft_slate)
    slate = _newest_draft(period)
    if slate is None:
        raise Http404('No draft slate.')
    return slate


def approve_slate_for_voting(period, slate, user, data):
    """
    Approve ``slate`` (``data``: the posted ``slate_type``, ``name``,
    ``description``). Returns (warning or None, success message).
    """
    # 11-05-26: an empty slate can't go on the ballot.
    if not slate.candidates.exists():
        raise SlateError('empty_slate', 'This slate has no candidates yet. Assign at least one before approving it.')

    # Warn about unfilled positions but allow approval (committee may discuss in person)
    warning = None
    positions = period.positions.filter(is_active=True)
    primary_position_ids = slate.candidates.filter(is_runoff=False).values_list('position_id', flat=True)

    missing_positions = positions.exclude(id__in=primary_position_ids)
    if missing_positions.exists():
        missing_names = ', '.join(p.title for p in missing_positions)
        warning = f'Slate approved with {missing_positions.count()} unfilled position(s): {missing_names}. These will be decided in person.'

    # Check if there's already an approved primary slate
    existing_primary = period.slates.filter(
        slate_type='primary',
        is_approved=True
    ).exclude(id=slate.id).first()

    slate_type = data.get('slate_type', 'primary')

    if existing_primary and slate_type == 'primary':
        # Demote existing primary to alternative
        existing_primary.slate_type = 'alternative'
        existing_primary.save(update_fields=['slate_type'])

    # Approve the slate
    slate.slate_type = slate_type
    slate.is_approved = True
    slate.approved_at = timezone.now()
    slate.approved_by = user
    slate.name = data.get('name', f'{slate_type.title()} Slate')
    slate.description = data.get('description', '')
    slate.save(update_fields=['slate_type', 'is_approved', 'approved_at', 'approved_by', 'name', 'description'])

    # Log activity
    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='slate_created',
        details=f'Approved {slate_type} slate: {slate.name}',
        metadata={'slate_id': slate.id, 'slate_type': slate_type}
    )

    return warning, f'Slate "{slate.name}" approved.'


@login_required
@slating_chair_required
def approve_slate(request, period_id, slate_id=None):
    """
    Approve a slate for voting.
    """
    if request.method != 'POST':
        return redirect('slating_build_slate', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)
    slate = slate_to_approve(period, slate_id)

    try:
        warning, message = approve_slate_for_voting(period, slate, request.user, request.POST)
    except SlateError as e:
        messages.error(request, str(e))
        return redirect('slating_build_slate', period_id=period_id)
    if warning:
        messages.warning(request, warning)
    messages.success(request, message)
    return redirect('slating_period_setup', period_id=period_id)


@login_required
@slating_committee_required
def slate_preview(request, period_id, slate_id):
    """
    Preview a slate before approval.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)
    slate = get_object_or_404(Slate, id=slate_id, period=period)

    context = {
        'period': period,
        'slate': slate,
        'candidates': slate_candidate_rows(slate),
    }

    return render(request, 'slating/slate_preview.html', context)


def public_slate(period):
    """The approved primary slate a voting member may see, or a SlateError (view_approved_slate)."""
    if period.status not in PUBLIC_STATUSES:
        raise SlateError('not_public', 'The slate is not yet publicly available.', 409)
    slate = approved_primary_slate(period)
    if not slate:
        raise SlateError('no_slate', 'No approved slate found.', 404)
    return slate


@login_required
@voting_member_required
def view_approved_slate(request, period_id):
    """
    Let any voting member see the approved slate once voting is open.
    Available during: voting_open, voting_closed, results_published.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    try:
        slate = public_slate(period)
    except SlateError as e:
        messages.error(request, str(e))
        return redirect('slating_dashboard')

    context = {
        'period': period,
        'slate': slate,
        'candidates': slate_candidate_rows(slate),
        'public_view': True,
    }

    return render(request, 'slating/slate_preview.html', context)


def copy_slate_to_draft(period, source_slate, user):
    """A new draft with ``source_slate``'s candidates. Returns (new slate, message)."""
    # Create new draft slate
    new_slate = Slate.objects.create(
        period=period,
        name=f'Copy of {source_slate.name}',
        slate_type='draft',
        description=source_slate.description,
        created_by=user
    )

    # Copy candidates. 11-04-26: runoffs stay runoffs (a slate with one
    # failed on the second "primary" for that position) and write-ins keep
    # their member (they were copied as a nameless candidate).
    for candidate in source_slate.candidates.all():
        SlateCandidate.objects.create(
            slate=new_slate,
            position=candidate.position,
            application=candidate.application,
            write_in_member=candidate.write_in_member,
            is_runoff=candidate.is_runoff,
            display_order=candidate.display_order,
            notes=candidate.notes
        )

    return new_slate, f'Created new draft from "{source_slate.name}".'


@login_required
@slating_committee_required
def copy_slate(request, period_id, slate_id):
    """
    Copy a slate to create a new draft.
    """
    if request.method != 'POST':
        return redirect('slating_build_slate', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)
    source_slate = get_object_or_404(Slate, id=slate_id, period=period)

    _, message = copy_slate_to_draft(period, source_slate, request.user)
    messages.success(request, message)
    return redirect('slating_build_slate', period_id=period_id)


# ── Write-ins ────────────────────────────────────────────────────────────────

def write_in_slate(period):
    """The approved primary slate write-ins go on, or a SlateError."""
    if period.status not in WRITE_IN_STATUSES:
        raise SlateError('not_allowed', 'Write-in assignments are only allowed during deliberation or voting.', 409)
    slate = approved_primary_slate(period)
    if not slate:
        raise SlateError('no_slate', 'No approved primary slate found.', 409)
    return slate


def write_in_options(period):
    """
    The period setup page's Blank Positions panel: the positions with no
    applicant in their primary slot, the write-ins already assigned, and (when
    a position is blank) the members to choose from, with who applied for
    which position and who is already on the slate.
    """
    out = {'blank_positions': [], 'write_in_candidates': [], 'active_members': [],
           'pos_applicants': {}, 'slated_ids': []}
    if period.status not in WRITE_IN_STATUSES:
        return out
    approved_slate = approved_primary_slate(period)
    if not approved_slate:
        return out
    filled_ids = approved_slate.candidates.filter(is_runoff=False, write_in_member__isnull=True).values_list('position_id', flat=True)
    out['blank_positions'] = blank_positions = list(period.positions.filter(is_active=True).exclude(id__in=filled_ids))
    out['write_in_candidates'] = list(approved_slate.candidates.filter(write_in_member__isnull=False).select_related('position', 'write_in_member').defer(*member_defer('write_in_member')))
    if not blank_positions:
        return out
    out['active_members'] = eligible_members()

    # Who is already on the slate in any capacity
    slated_user_ids = []
    for sc in approved_slate.candidates.select_related('application__applicant', 'write_in_member').defer(*member_defer('application__applicant', 'write_in_member')):
        if sc.application_id:
            slated_user_ids.append(sc.application.applicant.user_id)
        elif sc.write_in_member_id:
            slated_user_ids.append(sc.write_in_member.user_id)
    out['slated_ids'] = slated_user_ids

    # Applicants by position (non-withdrawn)
    apps = list(period.applications.exclude(
        status__in=['draft', 'withdrawn']
    ).select_related('applicant').defer(*member_defer('applicant')))

    pos_applicants = {}
    for pos in blank_positions:
        entries = []
        for app in apps:
            tier = app.get_position_tier(pos.id)
            if tier and tier != 'do_not_want':
                entries.append({'id': app.applicant.user_id, 'tier': tier})
        pos_applicants[str(pos.id)] = entries
    out['pos_applicants'] = pos_applicants
    return out


def add_write_in(period, user, position_id, member_id):
    """Assign any active member to a blank primary slot of the approved slate. Returns the message."""
    slate = write_in_slate(period)

    position = get_object_or_404(SlatingPosition, id=position_id, period=period)
    member = get_object_or_404(ParliamentUser, user_id=member_id)

    refusal = write_in_refusal(period, member)
    if refusal:
        raise SlateError('ineligible', refusal)

    # Only allow assigning to blank primary slots
    if slate.candidates.filter(position=position, is_runoff=False).exists():
        raise SlateError('position_filled', f'{position.title} already has a primary candidate. Remove them first.', 409)

    SlateCandidate.objects.create(
        slate=slate,
        position=position,
        write_in_member=member,
        application=None,
        is_runoff=False,
        display_order=position.display_order
    )

    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='position_added',
        details=f'Write-in: {member.name} assigned to {position.title}'
    )

    return f'{member.name} added as write-in candidate for {position.title}. They will be voted on individually.'


def delete_write_in(period, candidate_id):
    """Remove a write-in candidate. Returns the message."""
    candidate = get_object_or_404(SlateCandidate, id=candidate_id, slate__period=period, write_in_member__isnull=False)

    name = candidate.write_in_member.name
    position_title = candidate.position.title
    candidate.delete()

    return f'{name} removed from {position_title}.'


@login_required
@slating_committee_required
def assign_write_in(request, period_id):
    """
    Assign any active member to a blank position on the approved slate.
    Available during deliberation and voting_open.
    """
    if request.method != 'POST':
        return redirect('slating_period_setup', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)

    try:
        message = add_write_in(period, request.user, request.POST.get('position_id'), request.POST.get('member_id'))
    except SlateError as e:
        messages.error(request, str(e))
        return redirect('slating_period_setup', period_id=period_id)

    messages.success(request, message)
    return redirect('slating_period_setup', period_id=period_id)


@login_required
@slating_committee_required
def remove_write_in(request, period_id):
    """Remove a write-in candidate from a blank position."""
    if request.method != 'POST':
        return redirect('slating_period_setup', period_id=period_id)

    period = get_object_or_404(SlatingPeriod, id=period_id)
    messages.success(request, delete_write_in(period, request.POST.get('candidate_id')))
    return redirect('slating_period_setup', period_id=period_id)


# ── Edit the approved slate ──────────────────────────────────────────────────

def voting_paused(period):
    return period.status == 'deliberation' and period.current_voting_attempt > 0


def editable_slate(period):
    """The approved primary slate while it may be edited (voting open or paused), or a SlateError."""
    if period.status not in ('voting_open',) and not voting_paused(period):
        raise SlateError('not_voting', 'Slate editing is only available during voting.', 409)
    slate = approved_primary_slate(period)
    if not slate:
        raise SlateError('no_slate', 'No approved primary slate found.', 409)
    return slate


def replace_candidate(period, slate, user, data):
    """
    Replace a position's primary candidate (``data``: ``position_id``,
    ``candidate_type`` applicant | write_in, ``application_id`` or
    ``member_id``). Returns the message; an invalid choice is a SlateError.
    """
    position_id = data.get('position_id')
    candidate_type = data.get('candidate_type')  # 'applicant' or 'write_in'
    application_id = data.get('application_id')
    member_id = data.get('member_id')

    position = get_object_or_404(SlatingPosition, id=position_id, period=period)
    refusal = swap_refusal(period, position)
    if refusal:
        raise SlateError('round_open', refusal, 409)

    # Resolve the replacement before touching the current candidate, so a bad
    # choice leaves the position as it was rather than vacant.
    application = member = None
    if candidate_type == 'applicant' and application_id:
        application = get_object_or_404(SlatingApplication, id=application_id, period=period)
        new_name = application.applicant.name
    elif candidate_type == 'write_in' and member_id:
        member = get_object_or_404(ParliamentUser, user_id=member_id)
        new_name = member.name
        refusal = write_in_refusal(period, member)
        if refusal:
            raise SlateError('ineligible', refusal)
    else:
        raise SlateError('invalid_input', 'Invalid replacement — select an applicant or a member.')

    # Remove existing primary candidate for this position
    old_candidate = slate.candidates.filter(position=position, is_runoff=False).first()
    old_name = old_candidate.candidate_name if old_candidate else '(vacant)'
    if old_candidate:
        # v4.0.0: deleting the candidate cascades their votes; keep this
        # round's counts for them in the round history first.
        from .tallies import note_candidate_replaced
        note_candidate_replaced(period, old_candidate)
        old_candidate.delete()

    SlateCandidate.objects.create(
        slate=slate,
        position=position,
        application=application,
        write_in_member=member,
        is_runoff=False,
        display_order=position.display_order,
    )

    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='slate_edited',
        details=f'{position.title}: replaced {old_name} with {new_name}'
    )

    return f'{position.title} updated: {old_name} → {new_name}.'


def edit_slate_context(period, slate):
    """What the Edit Approved Slate page shows."""
    positions = period.positions.filter(is_active=True).order_by('display_order')
    candidate_map = {
        sc.position_id: sc
        for sc in slate.candidates.filter(is_runoff=False).select_related(
            'position', 'application__applicant', 'write_in_member'
        ).defer(*member_defer('application__applicant', 'write_in_member'))
    }

    # All non-withdrawn applications for this period
    applications = list(
        period.applications.exclude(status__in=['draft', 'withdrawn'])
        .select_related('applicant').defer(*member_defer('applicant'))
        .order_by('applicant__name')
    )

    rows = []
    for pos in positions:
        current = candidate_map.get(pos.id)
        rows.append({
            'position': pos,
            'current': current,
        })

    return {
        'period': period,
        'slate': slate,
        'rows': rows,
        'applications': applications,
        # All active/inactive members for write-in
        'active_members': eligible_members(),
        'voting_paused': voting_paused(period),
    }


@login_required
@slating_chair_required
def edit_approved_slate(request, period_id):
    """
    Let chairs/admins replace any candidate on the approved slate.
    Available during voting_open or paused voting (deliberation + attempt > 0).
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    try:
        slate = editable_slate(period)
    except SlateError as e:
        messages.error(request, str(e))
        return redirect('slating_period_setup', period_id=period_id)

    if request.method == 'POST':
        try:
            messages.success(request, replace_candidate(period, slate, request.user, request.POST))
        except SlateError as e:
            messages.error(request, str(e))
        return redirect('slating_edit_approved_slate', period_id=period_id)

    # GET — build context
    return render(request, 'slating/edit_approved_slate.html', edit_slate_context(period, slate))


# ── Manual results ───────────────────────────────────────────────────────────

class ManualResultsError(SlateError):
    """The form's errors, one per position (``errors``)."""

    def __init__(self, errors):
        super().__init__('invalid_input', ' '.join(errors))
        self.errors = errors


MANUAL_AFTER_ONLINE = ('Voting has already started online, so the results can’t be recorded by hand. '
                       'Close the round and use the results page.')


def manual_results_refusal(period):
    if period.status in MANUAL_RESULTS_CLOSED:
        return 'Results have already been recorded.'
    # 11-05-26: replacing the slate's candidates cascades their online votes
    # away, so recording by hand is only for an election that never voted online.
    if period.current_voting_attempt or period.rounds.exists():
        return MANUAL_AFTER_ONLINE
    return None


def record_manual_results(period, user, data):
    """
    Bypass voting (``data``: ``vote_mode`` none | overall | per_position,
    ``winner_<position id>``, and the counts). Moves the period to
    voting_closed. A missing or unknown winner is a ManualResultsError.
    """
    positions = period.positions.filter(is_active=True).order_by('display_order')
    vote_mode = data.get('vote_mode', 'none')  # none | overall | per_position

    refusal = manual_results_refusal(period)
    if refusal:
        raise ManualResultsError([refusal])

    errors = []
    assignments = []  # (position, member) tuples to create
    # 11-05-26: a winner must be one of the form's members (any user id was
    # accepted), and meet the candidates' rules (write_in_refusal).
    allowed = {str(m.pk): m for m in eligible_members()}

    for pos in positions:
        member_id = data.get(f'winner_{pos.id}')
        if not member_id:
            errors.append(f'Select a winner for {pos.title}.')
            continue
        member = allowed.get(str(member_id))
        if member is None:
            errors.append(f'Invalid member for {pos.title}.')
            continue
        refusal = write_in_refusal(period, member)
        if refusal:
            errors.append(f'{pos.title}: {refusal}')
            continue
        assignments.append((pos, member))

    if errors:
        raise ManualResultsError(errors)

    # Get or create the primary slate. After the winners are checked, so a form
    # missing one leaves no empty approved slate behind.
    slate, _ = Slate.objects.get_or_create(
        period=period,
        slate_type='primary',
        defaults={'name': f'{period.name} Slate', 'created_by': user}
    )
    # Ensure it is approved
    if not slate.is_approved:
        slate.is_approved = True
        slate.approved_at = timezone.now()
        slate.approved_by = user
        slate.save(update_fields=['is_approved', 'approved_at', 'approved_by'])

    # Overall vote counts (optional)
    overall_approve = overall_reject = overall_abstain = None
    if vote_mode == 'overall':
        try:
            overall_approve = int(data.get('overall_approve', 0))
            overall_reject = int(data.get('overall_reject', 0))
            overall_abstain = int(data.get('overall_abstain', 0))
        except (ValueError, TypeError):
            pass

    # Build slate candidates and record results
    in_person_counts = {}   # 11-05-26: kept for the results page (abstains have no column)
    for pos, member in assignments:
        # Remove any existing primary candidate for this position
        SlateCandidate.objects.filter(slate=slate, position=pos, is_runoff=False).delete()

        approve = reject = abstain = 0
        passed = None

        if vote_mode == 'per_position':
            try:
                approve = int(data.get(f'approve_{pos.id}', 0))
                reject = int(data.get(f'reject_{pos.id}', 0))
                abstain = int(data.get(f'abstain_{pos.id}', 0))
            except (ValueError, TypeError):
                pass
            counted = approve + reject
            if counted > 0:
                passed = (approve / counted * 100) >= period.required_approval_percentage
        elif vote_mode == 'overall' and overall_approve is not None:
            approve = overall_approve
            reject = overall_reject
            abstain = overall_abstain
            counted = approve + reject
            if counted > 0:
                passed = (approve / counted * 100) >= period.required_approval_percentage

        if vote_mode != 'none':
            in_person_counts[str(pos.id)] = {'approve': approve, 'reject': reject, 'abstain': abstain}
        SlateCandidate.objects.create(
            slate=slate,
            position=pos,
            write_in_member=member,
            display_order=pos.display_order,
            individual_passed=passed if vote_mode != 'none' else True,
            individual_votes_for=approve,
            individual_votes_against=reject,
        )

    # Update slate-level vote totals for overall mode
    if vote_mode == 'overall' and overall_approve is not None:
        counted = overall_approve + overall_reject
        slate.approval_votes = overall_approve
        slate.rejection_votes = overall_reject
        slate.abstain_votes = overall_abstain
        slate.total_votes = overall_approve + overall_reject + overall_abstain
        slate.approval_percentage = (overall_approve / counted * 100) if counted > 0 else None
        slate.passed = (overall_approve / counted * 100) >= period.required_approval_percentage if counted > 0 else None
        slate.save(update_fields=['approval_votes', 'rejection_votes', 'abstain_votes', 'total_votes', 'approval_percentage', 'passed'])

    period.status = 'voting_closed'
    period.vote_type = 'individual' if vote_mode == 'per_position' else period.vote_type
    period.save(update_fields=['status', 'vote_type'])

    SlatingActivity.objects.create(
        period=period,
        user=user,
        action='voting_closed',
        details=f'Manual results recorded by {user.name}. Vote mode: {vote_mode}.',
        metadata={'manual': True, 'vote_mode': vote_mode, **({'counts': in_person_counts} if in_person_counts else {})}
    )

    return 'Results recorded. You can now publish them.'


@login_required
@slating_chair_required
def manual_results(request, period_id):
    """
    Bypass voting entirely — record who won each position and optionally
    log vote counts (overall or per-position). Moves period to voting_closed.
    Available from any pre-voting-closed status.
    """
    period = get_object_or_404(SlatingPeriod, id=period_id)

    # Not available once results are published or archived
    refusal = manual_results_refusal(period)
    if refusal:
        messages.error(request, refusal)
        return redirect('slating_period_setup', period_id=period_id)

    if request.method == 'POST':
        try:
            message = record_manual_results(period, request.user, request.POST)
        except ManualResultsError as e:
            for error in e.errors:
                messages.error(request, error)
            return redirect('slating_manual_results', period_id=period_id)

        messages.success(request, message)
        return redirect('slating_results', period_id=period_id)

    # GET
    positions = period.positions.filter(is_active=True).order_by('display_order')
    rows = [{'position': pos} for pos in positions]
    context = {
        'period': period,
        'rows': rows,
        'all_members': eligible_members(),
    }
    return render(request, 'slating/manual_results.html', context)
