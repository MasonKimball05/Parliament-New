import logging

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from ..decorators import officer_required, log_function_call
from ..forms import LegislationForm
from src.models import Legislation, Role, ParliamentUser
from src.utils.file_validation import validate_uploaded_file
from src.notification_service import notify_all_active_members

logger = logging.getLogger(__name__)


@officer_required
@log_function_call
def upload_legislation(request):

    if request.method == 'POST':

        # --- Chair Appointment ---
        if request.POST.get('action_type') == 'create_appointment':
            return _create_appointment(request)

        # --- General Legislation ---
        if 'document' in request.FILES:
            try:
                validate_uploaded_file(request.FILES['document'])
            except ValidationError as e:
                messages.error(request, f'File upload error: {str(e)}')
                return redirect('upload_legislation')

        form = LegislationForm(request.POST, request.FILES)
        if form.is_valid():
            legislation = form.save(commit=False)
            legislation.posted_by = request.user
            legislation.save()

            _notify(legislation)
            return redirect('vote')
        else:
            messages.error(request, 'There was an error with your submission.')
    else:
        form = LegislationForm()

    appointment_roles = Role.objects.all().order_by('name')
    appointment_members = ParliamentUser.objects.filter(member_status='Active').order_by('name')

    return render(request, 'vote.html', {
        'form': form,
        'profile': request.user,
        'appointment_roles': appointment_roles,
        'appointment_members': appointment_members,
        'legislation': [],
        'vote_data': {},
        'can_vote': False,
    })


def _create_appointment(request):
    """Handle chair appointment legislation creation."""
    role_id = request.POST.get('appointment_role_id', '').strip()
    member_id = request.POST.get('appointment_member_id', '').strip()
    vote_mode = request.POST.get('appt_vote_mode', 'percentage')
    available_at_str = request.POST.get('appt_available_at', '').strip()
    voting_starts_at_str = request.POST.get('appt_voting_starts_at', '').strip()
    voting_ends_at_str = request.POST.get('appt_voting_ends_at', '').strip()
    required_percentage = request.POST.get('appt_required_percentage', '51')
    required_number_str = request.POST.get('appt_required_number', '').strip()
    anonymous = 'appt_anonymous' in request.POST
    allow_abstain = 'appt_remove_abstain' not in request.POST

    # Validate role — may be an existing role ID or the sentinel "__new__"
    if not role_id:
        messages.error(request, 'Please select a role for the appointment.')
        return redirect('vote')

    if role_id == '__new__':
        new_role_name = request.POST.get('new_role_name', '').strip()
        new_role_code = request.POST.get('new_role_code', '').strip().upper()
        if not new_role_name or not new_role_code:
            messages.error(request, 'Please provide both a name and a code for the new role.')
            return redirect('vote')
        role, created = Role.objects.get_or_create(
            code=new_role_code,
            defaults={'name': new_role_name},
        )
        if not created and role.name != new_role_name:
            # Code already exists under a different name — just use the existing role
            messages.warning(request, f'A role with code "{new_role_code}" already exists ({role.name}). Using the existing role.')
    else:
        try:
            role = Role.objects.get(id=role_id)
        except Role.DoesNotExist:
            messages.error(request, 'Selected role not found.')
            return redirect('vote')

    # Validate available_at
    # v3.13.3: make_aware() added — these were saved naive, and the DB layer
    # interprets naive datetimes as UTC, so appointment vote open times were
    # skewed by the UTC offset (~5-6 h for America/Chicago). The chapter
    # upload path already converted correctly; this path was missed.
    available_at = parse_datetime(available_at_str) if available_at_str else None
    if not available_at:
        messages.error(request, 'Please set an available date for the appointment vote.')
        return redirect('vote')
    if timezone.is_naive(available_at):
        available_at = timezone.make_aware(available_at)
    # v3.14.0: "Now" button — server-resolved, immune to device-clock skew
    if request.POST.get('appt_available_at_is_now') == '1':
        available_at = timezone.now()

    voting_starts_at = parse_datetime(voting_starts_at_str) if voting_starts_at_str else None
    if voting_starts_at and timezone.is_naive(voting_starts_at):
        voting_starts_at = timezone.make_aware(voting_starts_at)
    voting_ends_at = parse_datetime(voting_ends_at_str) if voting_ends_at_str else None
    if voting_ends_at and timezone.is_naive(voting_ends_at):
        voting_ends_at = timezone.make_aware(voting_ends_at)

    # Validate appointment_member for non-plurality votes
    appointment_member = None
    if vote_mode != 'plurality':
        if not member_id:
            messages.error(request, 'Please select a nominee for the appointment.')
            return redirect('vote')
        try:
            appointment_member = ParliamentUser.objects.get(user_id=member_id, member_status='Active')
        except ParliamentUser.DoesNotExist:
            messages.error(request, 'Selected nominee not found or is not active.')
            return redirect('vote')

    # Build plurality options for plurality appointments
    plurality_options = None
    plurality_votes_allowed = 1
    plurality_runoff_enabled = False
    plurality_runoff_count = 2
    if vote_mode == 'plurality':
        opts = []
        for i in range(1, 11):
            val = request.POST.get(f'appt_plurality_option_{i}', '').strip()
            if val:
                opts.append(val)
        if len(opts) < 2:
            messages.error(request, 'Plurality appointment votes require at least 2 candidate options.')
            return redirect('vote')
        plurality_options = opts
        try:
            plurality_votes_allowed = int(request.POST.get('appt_plurality_votes_allowed', 1))
        except (ValueError, TypeError):
            plurality_votes_allowed = 1
        plurality_runoff_enabled = 'appt_plurality_runoff_enabled' in request.POST
        try:
            plurality_runoff_count = int(request.POST.get('appt_plurality_runoff_count', 2))
        except (ValueError, TypeError):
            plurality_runoff_count = 2

    required_number = None
    if vote_mode == 'piecewise':
        try:
            required_number = int(required_number_str)
        except (ValueError, TypeError):
            messages.error(request, 'Please enter the required number of yes votes.')
            return redirect('vote')

    # Auto-generate title
    title = request.POST.get('appt_title', '').strip()
    if not title:
        if appointment_member:
            title = f'Chair Appointment: {appointment_member.name} as {role.name}'
        else:
            title = f'Chair Appointment: {role.name}'

    description = request.POST.get('appt_description', '').strip()
    if not description:
        if appointment_member:
            description = f'Chapter vote on the appointment of {appointment_member.name} as {role.name}.'
        else:
            description = f'Chapter vote on the appointment of a new {role.name}.'

    legislation = Legislation(
        title=title,
        description=description,
        posted_by=request.user,
        legislation_type='appointment',
        appointment_role=role,
        appointment_member=appointment_member,
        available_at=available_at,
        voting_starts_at=voting_starts_at,
        voting_ends_at=voting_ends_at,
        vote_mode=vote_mode,
        required_percentage=required_percentage,
        required_number=required_number,
        anonymous_vote=anonymous,
        allow_abstain=allow_abstain,
        plurality_options=plurality_options,
        plurality_votes_allowed=plurality_votes_allowed,
        plurality_runoff_enabled=plurality_runoff_enabled,
        plurality_runoff_count=plurality_runoff_count,
    )
    legislation.save()

    _notify(legislation)

    messages.success(request, f'Appointment vote for {role.name} created successfully.')
    return redirect('vote')


def _notify(legislation):
    """
    Announce a newly uploaded bill — but only if it is actually available.

    ⚠️ v3.19.0 CHANGED THE TIMING HERE, and it is a deliberate behaviour change.

    This used to fire unconditionally the moment the row was saved. `available_at`
    is a required field that officers routinely set in the future, so a bill dated
    three weeks out pushed "New Legislation: …" to every active member for
    something none of them could open — and nothing announced it again when it
    *did* become available. The notification was either premature or absent.

    Now: available already → announce inline (unchanged from the member's point
    of view). Dated in the future → say nothing, and let
    `tasks.notify_available_legislation` announce it the minute it lands.

    Both paths go through `announce_legislation_availability`, which stamps
    `availability_notified_at` as its claim, so the inline call and the periodic
    task cannot both send. Do not re-add a bare `notify_all_active_members` call
    here: it would bypass the stamp and the task would announce the bill a
    second time when `available_at` passed.
    """
    from src.tasks.votes import announce_legislation_availability

    try:
        if not legislation.is_available():
            logger.info(
                'Legislation id=%s ("%s") is dated %s — deferring the chapter '
                'notification to tasks.notify_available_legislation.',
                legislation.pk, legislation.title, legislation.available_at,
            )
            return
        announce_legislation_availability(legislation)
    except Exception as e:
        logger.error('Failed to send legislation notifications: %s', e, exc_info=True)
