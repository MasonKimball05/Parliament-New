"""
Recruitment committee dashboard — event management, RSVP tracking, permission management.
"""
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.db.models import Count, Q
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from src.models import (
    Committee, Event, ParliamentUser,
    RecruitmentCandidate, RecruitmentCandidateNote, RecruitmentEvent, RecruitmentEventRSVP, RecruitmentMemberPermission,
    ActivityLog,
)
from src.feature_flag_decorators import require_page_enabled
from src.models.users import member_defer

RECRUIT_PERM_FIELDS = ['can_manage_events', 'can_view_private', 'can_take_attendance']


def _get_committee(code):
    return get_object_or_404(Committee, code=code, is_recruitment_committee=True)


def _user_access(committee, user):
    """Return (has_access, is_chair, perm_obj_or_None)."""
    is_chair = committee.is_chair(user) or user.is_admin
    is_member = committee.members.filter(pk=user.pk).exists()
    is_advisor = committee.advisors.filter(pk=user.pk).exists()
    is_vp = committee.role and user.roles.filter(id=committee.role.id).exists()

    has_access = is_chair or is_member or is_advisor or is_vp

    perm = None
    if has_access and not is_chair:
        perm = RecruitmentMemberPermission.objects.filter(
            committee=committee, user=user
        ).first()

    return has_access, is_chair, perm


def _group_candidates_by_assignee(candidates):
    """
    Group an already-evaluated candidates queryset by `assigned_to`, for
    the dashboard's "Assignments" tab.

    Requested by Mason: "Currently I only see a way to assign, but not a
    good way to really view who has who and whatnot." The flat
    "Candidates" tab already shows an Assigned To column, but answering
    "how many prospects does each of us actually have" meant scrolling
    and counting by eye.

    Takes `candidates` rather than re-querying: `recruitment_dashboard`
    already builds that queryset with `select_related('assigned_to')`, and
    iterating it here (instead of a second `RecruitmentCandidate.objects
    .filter(...)`) evaluates and caches it once — the flat tab's own
    `{% for c in candidates %}` then reads from that same cache instead of
    re-querying. This function must never itself trigger a query per
    candidate; it only reads attributes already joined in.
    """
    from collections import defaultdict

    groups = defaultdict(list)
    for candidate in candidates:
        groups[candidate.assigned_to_id].append(candidate)

    rows = [
        {
            'assignee': group_candidates[0].assigned_to,
            'candidates': group_candidates,
        }
        for group_candidates in groups.values()
    ]
    # Assigned groups alphabetically by name; "Unassigned" (assignee is
    # None) always last, regardless of how many candidates are in it —
    # it's the one group that isn't really "someone's", so it shouldn't
    # compete for the top of the list just because it's often the biggest.
    rows.sort(key=lambda row: (row['assignee'] is None, (row['assignee'].name if row['assignee'] else '')))
    return rows


def _can_manage(committee, user, perm=None):
    """Can the user create/edit/delete recruitment events?

    Pass the perm object already fetched by _user_access to avoid a redundant query.
    """
    if committee.is_chair(user) or user.is_admin:
        return True
    if perm is None:
        perm = RecruitmentMemberPermission.objects.filter(
            committee=committee, user=user
        ).first()
    return perm is not None and perm.can_manage_events


def _can_view_private(committee, user, perm=None):
    """Can the user see committee-only notes and candidate lists?

    Pass the perm object already fetched by _user_access to avoid a redundant query.
    """
    if committee.is_chair(user) or user.is_admin:
        return True
    if perm is None:
        perm = RecruitmentMemberPermission.objects.filter(
            committee=committee, user=user
        ).first()
    return perm is not None and perm.can_view_private


def _can_take_attendance(committee, user, perm=None):
    """Can the user mark attendees as checked-in?

    Pass the perm object already fetched by _user_access to avoid a redundant query.
    """
    if committee.is_chair(user) or user.is_admin:
        return True
    if perm is None:
        perm = RecruitmentMemberPermission.objects.filter(
            committee=committee, user=user
        ).first()
    return perm is not None and perm.can_take_attendance


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@login_required
@require_page_enabled('committee_home')
def recruitment_dashboard(request, code):
    committee = _get_committee(code)
    has_access, is_chair, perm = _user_access(committee, request.user)

    if not has_access:
        messages.error(request, 'You do not have access to the recruitment dashboard.')
        return redirect('committee_home', code=code)

    can_manage = _can_manage(committee, request.user, perm=perm)
    can_view_private = _can_view_private(committee, request.user, perm=perm)

    now = timezone.now()
    _signup_annotation = Count(
        'event__signups',
        filter=Q(event__signups__is_cancelled=False),
    )
    upcoming = (
        RecruitmentEvent.objects
        .filter(committee=committee, event__date_time__gte=now)
        # v3.17.3: created_by joined but never rendered by recruitment_dashboard.html
        .select_related('event')
        .annotate(signup_count=_signup_annotation)
        .order_by('event__date_time')
    )
    past = (
        RecruitmentEvent.objects
        .filter(committee=committee, event__date_time__lt=now)
        .select_related('event')
        .annotate(signup_count=_signup_annotation)
        .order_by('-event__date_time')
    )

    # Filter out committee-only events for non-privileged members.
    # ⚠️ Must happen BEFORE `past` is sliced below — `.filter()` on an
    # already-sliced queryset raises `TypeError: Cannot filter a query
    # once a slice has been taken`, unconditionally, the moment this
    # branch runs. Found while adding the Assignments tab: every
    # non-privileged member of a recruitment committee got a 500 on this
    # dashboard, always, regardless of whether there was any private past
    # event to actually filter out.
    if not can_view_private:
        upcoming = upcoming.filter(visibility='public')
        past = past.filter(visibility='public')

    past = past[:20]

    # Candidates (only visible to members with private access)
    candidates = None
    candidates_by_assignee = None
    if can_view_private:
        candidates = (
            RecruitmentCandidate.objects
            .filter(committee=committee)
            .select_related('assigned_to', 'source_event__event').defer(*member_defer('assigned_to'))
            .order_by('status', 'name')
        )
        # Evaluates `candidates` once (list()), so the flat "Candidates"
        # tab below reads from the same cached result instead of
        # re-querying — see _group_candidates_by_assignee's docstring.
        candidates_by_assignee = _group_candidates_by_assignee(list(candidates))

    active_tab = request.GET.get('tab', 'events')

    context = {
        'committee': committee,
        'upcoming': upcoming,
        'past': past,
        'can_manage': can_manage,
        'can_view_private': can_view_private,
        'is_chair': is_chair,
        'candidates': candidates,
        'candidates_by_assignee': candidates_by_assignee,
        'status_choices': RecruitmentCandidate.STATUS_CHOICES,
        'active_tab': active_tab,
    }
    return render(request, 'committee/recruitment_dashboard.html', context)


# ---------------------------------------------------------------------------
# Create / Edit
# ---------------------------------------------------------------------------

@login_required
@require_page_enabled('committee_home')
def create_recruitment_event(request, code):
    committee = _get_committee(code)
    has_access, is_chair, perm = _user_access(committee, request.user)

    if not has_access or not _can_manage(committee, request.user, perm=perm):
        messages.error(request, 'You do not have permission to create recruitment events.')
        return redirect('recruitment_dashboard', code=code)

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        date_time_str = request.POST.get('date_time', '').strip()
        location = request.POST.get('location', '').strip()
        description = request.POST.get('description', '').strip()
        event_type = request.POST.get('event_type', 'other')
        visibility = request.POST.get('visibility', 'public')
        status = request.POST.get('status', 'planned')
        notes = request.POST.get('notes', '').strip()
        notes_visibility = request.POST.get('notes_visibility', 'committee_only')
        rsvp_reminder_enabled = request.POST.get('rsvp_reminder_enabled') == 'on'
        try:
            rsvp_reminder_hours = max(1, int(request.POST.get('rsvp_reminder_hours_before', 24) or 24))
        except (ValueError, TypeError):
            rsvp_reminder_hours = 24
        attendance_type = request.POST.get('attendance_type', 'none')

        # Max signups (only relevant when attendance_type == 'signup')
        max_signups = None
        if attendance_type == 'signup':
            raw_max = request.POST.get('max_signups', '').strip()
            if raw_max:
                try:
                    max_signups = int(raw_max)
                    if max_signups <= 0:
                        max_signups = None
                except ValueError:
                    pass

        # Expected attendees / visible_to (only relevant when attendance_type == 'attendance')
        expected_types = request.POST.getlist('expected_member_types')
        visible_to = expected_types if attendance_type == 'attendance' and expected_types else None

        errors = []
        if not title:
            errors.append('Title is required.')
        if not date_time_str:
            errors.append('Date and time are required.')

        date_time = None
        if date_time_str:
            try:
                from django.utils.dateparse import parse_datetime
                naive = parse_datetime(date_time_str)
                if naive is None:
                    errors.append('Invalid date/time format.')
                else:
                    # The <input type="datetime-local"> this comes from has no
                    # timezone of its own — it's the officer's local wall-clock
                    # time (America/Chicago for this chapter). This used to
                    # localize as UTC, which silently stored every recruitment
                    # event 5-6 hours off from what was actually entered.
                    # make_aware() uses the active/configured timezone, same as
                    # every other date_time field in the app (e.g. EventForm,
                    # which gets this for free via the ModelForm/ORM layer).
                    date_time = timezone.make_aware(naive) if timezone.is_naive(naive) else naive
            except Exception:
                errors.append('Invalid date/time format.')

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'committee/recruitment_event_form.html', {
                'committee': committee,
                'post': request.POST,
                'event_type_choices': RecruitmentEvent.EVENT_TYPE_CHOICES,
                'visibility_choices': RecruitmentEvent.VISIBILITY_CHOICES,
                'status_choices': RecruitmentEvent.STATUS_CHOICES,
                'notes_visibility_choices': RecruitmentEvent.NOTES_VISIBILITY_CHOICES,
                'member_type_choices': Event.MEMBER_TYPES,
                'editing': False,
            })

        event = Event.objects.create(
            title=title,
            description=description,
            date_time=date_time,
            location=location,
            created_by=request.user,
            requires_attendance=(attendance_type == 'attendance'),
            requires_signup=(attendance_type == 'signup'),
            max_signups=max_signups,
            visible_to=visible_to,
            allow_excuses=False,
            is_active=True,
            archived=False,
        )

        re = RecruitmentEvent.objects.create(
            event=event,
            committee=committee,
            event_type=event_type,
            visibility=visibility,
            status=status,
            notes=notes,
            notes_visibility=notes_visibility,
            rsvp_reminder_enabled=rsvp_reminder_enabled,
            rsvp_reminder_hours_before=rsvp_reminder_hours,
            created_by=request.user,
        )

        ActivityLog.log_activity(
            action_type='other',
            user=request.user,
            description=f'Created recruitment event "{title}" ({re.get_event_type_display()})',
            request=request,
            object_type='RecruitmentEvent',
            object_id=re.id,
            object_repr=str(re),
        )

        messages.success(request, f'Recruitment event "{title}" created.')
        return redirect('recruitment_event_detail', code=code, recruitment_event_id=re.id)

    return render(request, 'committee/recruitment_event_form.html', {
        'committee': committee,
        'post': {
            'title': '', 'date_time': '', 'location': '', 'description': '',
            'event_type': 'other', 'visibility': 'public', 'status': 'planned',
            'notes': '', 'notes_visibility': 'committee_only',
            'attendance_type': 'none',
            'max_signups': '',
            'expected_member_types': [],
        },
        'event_type_choices': RecruitmentEvent.EVENT_TYPE_CHOICES,
        'visibility_choices': RecruitmentEvent.VISIBILITY_CHOICES,
        'status_choices': RecruitmentEvent.STATUS_CHOICES,
        'notes_visibility_choices': RecruitmentEvent.NOTES_VISIBILITY_CHOICES,
        'member_type_choices': Event.MEMBER_TYPES,
        'editing': False,
    })


@login_required
@require_page_enabled('committee_home')
def edit_recruitment_event(request, code, recruitment_event_id):
    committee = _get_committee(code)
    re = get_object_or_404(RecruitmentEvent, id=recruitment_event_id, committee=committee)

    has_access, is_chair, perm = _user_access(committee, request.user)
    if not has_access or not _can_manage(committee, request.user, perm=perm):
        messages.error(request, 'You do not have permission to edit recruitment events.')
        return redirect('recruitment_event_detail', code=code, recruitment_event_id=re.id)

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        date_time_str = request.POST.get('date_time', '').strip()
        location = request.POST.get('location', '').strip()
        description = request.POST.get('description', '').strip()
        event_type = request.POST.get('event_type', re.event_type)
        visibility = request.POST.get('visibility', re.visibility)
        status = request.POST.get('status', re.status)
        notes = request.POST.get('notes', '').strip()
        notes_visibility = request.POST.get('notes_visibility', re.notes_visibility)
        rsvp_reminder_enabled = request.POST.get('rsvp_reminder_enabled') == 'on'
        try:
            rsvp_reminder_hours = max(1, int(request.POST.get('rsvp_reminder_hours_before', 24) or 24))
        except (ValueError, TypeError):
            rsvp_reminder_hours = 24
        attendance_type = request.POST.get('attendance_type', 'none')

        max_signups = None
        if attendance_type == 'signup':
            raw_max = request.POST.get('max_signups', '').strip()
            if raw_max:
                try:
                    max_signups = int(raw_max)
                    if max_signups <= 0:
                        max_signups = None
                except ValueError:
                    pass

        expected_types = request.POST.getlist('expected_member_types')
        visible_to = expected_types if attendance_type == 'attendance' and expected_types else None

        errors = []
        if not title:
            errors.append('Title is required.')
        if not date_time_str:
            errors.append('Date and time are required.')

        date_time = None
        if date_time_str:
            try:
                from django.utils.dateparse import parse_datetime
                naive = parse_datetime(date_time_str)
                if naive is None:
                    errors.append('Invalid date/time format.')
                else:
                    # See the matching comment in create_recruitment_event —
                    # same bug, same fix: this was localizing the officer's
                    # local wall-clock input as UTC instead of the chapter's
                    # actual timezone.
                    date_time = timezone.make_aware(naive) if timezone.is_naive(naive) else naive
            except Exception:
                errors.append('Invalid date/time format.')

        if errors:
            for e in errors:
                messages.error(request, e)
            return render(request, 'committee/recruitment_event_form.html', {
                'committee': committee,
                're': re,
                'post': request.POST,
                'event_type_choices': RecruitmentEvent.EVENT_TYPE_CHOICES,
                'visibility_choices': RecruitmentEvent.VISIBILITY_CHOICES,
                'status_choices': RecruitmentEvent.STATUS_CHOICES,
                'notes_visibility_choices': RecruitmentEvent.NOTES_VISIBILITY_CHOICES,
                'member_type_choices': Event.MEMBER_TYPES,
                'editing': True,
            })

        re.event.title = title
        re.event.description = description
        re.event.date_time = date_time
        re.event.location = location
        re.event.requires_attendance = (attendance_type == 'attendance')
        re.event.requires_signup = (attendance_type == 'signup')
        re.event.max_signups = max_signups
        re.event.visible_to = visible_to
        re.event.save(update_fields=['title', 'description', 'date_time', 'location', 'requires_attendance', 'requires_signup', 'max_signups', 'visible_to'])

        re.event_type = event_type
        re.visibility = visibility
        re.status = status
        re.notes = notes
        re.notes_visibility = notes_visibility
        # Reset sent_at if reminder is re-enabled or lead time changed
        if rsvp_reminder_enabled and (
            not re.rsvp_reminder_enabled
            or rsvp_reminder_hours != re.rsvp_reminder_hours_before
        ):
            re.rsvp_reminder_sent_at = None
        re.rsvp_reminder_enabled = rsvp_reminder_enabled
        re.rsvp_reminder_hours_before = rsvp_reminder_hours
        re.save()

        messages.success(request, 'Recruitment event updated.')
        return redirect('recruitment_event_detail', code=code, recruitment_event_id=re.id)

    # Pre-fill date/time as local datetime-local string
    local_dt = timezone.localtime(re.event.date_time)
    dt_str = local_dt.strftime('%Y-%m-%dT%H:%M')

    if re.event.requires_attendance:
        current_attendance_type = 'attendance'
    elif re.event.requires_signup:
        current_attendance_type = 'signup'
    else:
        current_attendance_type = 'none'

    return render(request, 'committee/recruitment_event_form.html', {
        'committee': committee,
        're': re,
        'post': {
            'title': re.event.title,
            'date_time': dt_str,
            'location': re.event.location or '',
            'description': re.event.description or '',
            'event_type': re.event_type,
            'visibility': re.visibility,
            'status': re.status,
            'notes': re.notes,
            'notes_visibility': re.notes_visibility,
            'rsvp_reminder_enabled': re.rsvp_reminder_enabled,
            'rsvp_reminder_hours_before': re.rsvp_reminder_hours_before,
            'attendance_type': current_attendance_type,
            'max_signups': re.event.max_signups or '',
            'expected_member_types': re.event.visible_to or [],
        },
        'event_type_choices': RecruitmentEvent.EVENT_TYPE_CHOICES,
        'visibility_choices': RecruitmentEvent.VISIBILITY_CHOICES,
        'status_choices': RecruitmentEvent.STATUS_CHOICES,
        'notes_visibility_choices': RecruitmentEvent.NOTES_VISIBILITY_CHOICES,
        'member_type_choices': Event.MEMBER_TYPES,
        'editing': True,
    })


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

@login_required
@require_page_enabled('committee_home')
def recruitment_event_detail(request, code, recruitment_event_id):
    committee = _get_committee(code)
    re = get_object_or_404(
        RecruitmentEvent.objects.select_related('event', 'created_by').defer(*member_defer('created_by')),
        id=recruitment_event_id,
        committee=committee,
    )

    has_access, is_chair, perm = _user_access(committee, request.user)
    if not has_access:
        messages.error(request, 'You do not have access to this event.')
        return redirect('committee_home', code=code)

    can_view_priv = _can_view_private(committee, request.user, perm=perm)
    can_manage = _can_manage(committee, request.user, perm=perm)
    can_attend = _can_take_attendance(committee, request.user, perm=perm)

    # Block non-privileged from committee-only events
    if re.visibility == 'committee_only' and not can_view_priv:
        messages.error(request, 'This event is visible to recruitment committee staff only.')
        return redirect('recruitment_dashboard', code=code)

    uses_signup = re.event.requires_signup

    # For sign-up events use EventSignup; for legacy RSVP events use RecruitmentEventRSVP
    if uses_signup:
        from src.models import EventSignup
        signups = list(
            EventSignup.objects
            .filter(event=re.event, is_cancelled=False)
            .select_related('user').defer(*member_defer('user'))
            .order_by('signed_up_at')
        )
        signup_count = len(signups)
        rsvps = None
        user_rsvp = None
        going_count = signup_count
        checked_in_count = 0
    else:
        signups = None
        signup_count = 0
        rsvps = list(re.rsvps.select_related('user').defer(*member_defer('user')).order_by('user__name'))
        user_rsvp = next((r for r in rsvps if r.user_id == request.user.pk), None)
        going_count = sum(1 for r in rsvps if r.status == 'going')
        checked_in_count = sum(1 for r in rsvps if r.checked_in)

    # Handle POST actions
    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'rsvp' and not uses_signup:
            rsvp_status = request.POST.get('rsvp_status', 'going')
            valid_statuses = {c[0] for c in RecruitmentEventRSVP.RSVP_STATUS_CHOICES}
            if rsvp_status not in valid_statuses:
                rsvp_status = 'going'
            RecruitmentEventRSVP.objects.update_or_create(
                recruitment_event=re,
                user=request.user,
                defaults={'status': rsvp_status},
            )
            messages.success(request, 'RSVP updated.')
            return redirect('recruitment_event_detail', code=code, recruitment_event_id=re.id)

        elif action == 'check_in' and can_attend and not uses_signup:
            user_id = request.POST.get('user_id')
            checked = request.POST.get('checked_in') == 'true'
            try:
                rsvp = RecruitmentEventRSVP.objects.get(recruitment_event=re, user_id=user_id)
                rsvp.checked_in = checked
                rsvp.save(update_fields=['checked_in'])
                return JsonResponse({'ok': True})
            except RecruitmentEventRSVP.DoesNotExist:
                return JsonResponse({'error': 'RSVP not found'}, status=404)

    context = {
        'committee': committee,
        're': re,
        'uses_signup': uses_signup,
        'signups': signups,
        'signup_count': signup_count,
        'rsvps': rsvps,
        'user_rsvp': user_rsvp,
        'can_manage': can_manage,
        'can_view_private': can_view_priv,
        'can_take_attendance': can_attend,
        'is_chair': is_chair,
        'going_count': going_count,
        'checked_in_count': checked_in_count,
    }
    return render(request, 'committee/recruitment_event_detail.html', context)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@login_required
@require_page_enabled('committee_home')
@require_http_methods(['POST'])
def delete_recruitment_event(request, code, recruitment_event_id):
    committee = _get_committee(code)
    re = get_object_or_404(RecruitmentEvent, id=recruitment_event_id, committee=committee)

    if not _can_manage(committee, request.user):
        messages.error(request, 'You do not have permission to delete recruitment events.')
        return redirect('recruitment_event_detail', code=code, recruitment_event_id=re.id)

    title = re.event.title
    re.event.delete()  # cascades to RecruitmentEvent
    messages.success(request, f'Recruitment event "{title}" deleted.')
    return redirect('recruitment_dashboard', code=code)


# ---------------------------------------------------------------------------
# Permission management
# ---------------------------------------------------------------------------

@login_required
@require_page_enabled('committee_home')
def manage_recruitment_permissions(request, code):
    committee = _get_committee(code)

    if not (committee.is_chair(request.user) or request.user.is_admin):
        messages.error(request, 'Only recruitment chairs can manage permissions.')
        return redirect('committee_home', code=code)

    chairs_pks = set(committee.chairs.values_list('pk', flat=True))
    members_pks = set(committee.members.values_list('pk', flat=True))
    all_pks = chairs_pks | members_pks

    committee_members = ParliamentUser.objects.filter(pk__in=all_pks).order_by('name')

    existing_perms = {
        p.user_id: p
        for p in RecruitmentMemberPermission.objects.filter(committee=committee).select_related('user').defer(*member_defer('user'))
    }

    member_rows = []
    for member in committee_members:
        is_chair = member.pk in chairs_pks
        perm = existing_perms.get(member.pk)
        row = {'member': member, 'is_chair': is_chair}
        for field in RECRUIT_PERM_FIELDS:
            row[field] = True if is_chair else (getattr(perm, field) if perm else False)
        member_rows.append(row)

    context = {
        'committee': committee,
        'member_rows': member_rows,
        'perm_fields': RECRUIT_PERM_FIELDS,
    }
    return render(request, 'committee/manage_recruitment_permissions.html', context)


@login_required
@require_page_enabled('committee_home')
@require_http_methods(['POST'])
def update_recruitment_permission(request, code, user_id):
    committee = _get_committee(code)

    if not (committee.is_chair(request.user) or request.user.is_admin):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    try:
        member = ParliamentUser.objects.get(user_id=user_id)
    except ParliamentUser.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)

    if committee.is_chair(member):
        return JsonResponse({'error': 'Chairs always have full access'}, status=400)

    defaults = {field: request.POST.get(field) == 'true' for field in RECRUIT_PERM_FIELDS}
    defaults['granted_by'] = request.user

    perm, _ = RecruitmentMemberPermission.objects.update_or_create(
        committee=committee,
        user=member,
        defaults=defaults,
    )

    return JsonResponse({'success': True, 'user_id': user_id, **defaults})


@login_required
@require_page_enabled('committee_home')
@require_http_methods(['POST'])
def reset_recruitment_permissions(request, code):
    committee = _get_committee(code)

    if not (committee.is_chair(request.user) or request.user.is_admin):
        return JsonResponse({'error': 'Permission denied'}, status=403)

    deleted_count, _ = RecruitmentMemberPermission.objects.filter(committee=committee).delete()
    return JsonResponse({'success': True, 'deleted': deleted_count})


# ---------------------------------------------------------------------------
# Candidate tracking
# ---------------------------------------------------------------------------

@login_required
@require_page_enabled('committee_home')
def candidate_list(request, code):
    """Redirect to the recruitment dashboard candidates tab (kept for backwards-compat)."""
    from django.urls import reverse
    return redirect(reverse('recruitment_dashboard', kwargs={'code': code}) + '?tab=candidates')


def _candidate_list_legacy(request, code):
    committee = _get_committee(code)
    has_access, is_chair, perm = _user_access(committee, request.user)

    if not has_access or not _can_view_private(committee, request.user, perm=perm):
        messages.error(request, 'You do not have access to candidate tracking.')
        return redirect('recruitment_dashboard', code=code)

    status_filter = request.GET.get('status', '')
    assigned_filter = request.GET.get('assigned_to', '')

    from django.db.models import Prefetch
    candidates = (
        RecruitmentCandidate.objects
        .filter(committee=committee)
        .select_related('assigned_to', 'source_event__event', 'added_by').defer(*member_defer('assigned_to', 'added_by'))
        .prefetch_related(
            Prefetch('note_entries', queryset=RecruitmentCandidateNote.objects.select_related('author').defer(*member_defer('author')).order_by('created_at'))
        )
        .order_by('name')
    )
    if status_filter:
        candidates = candidates.filter(status=status_filter)
    if assigned_filter:
        if assigned_filter == 'me':
            candidates = candidates.filter(assigned_to=request.user)
        elif assigned_filter == 'unassigned':
            candidates = candidates.filter(assigned_to__isnull=True)

    committee_members = ParliamentUser.objects.filter(
        pk__in=set(committee.chairs.values_list('pk', flat=True)) |
               set(committee.members.values_list('pk', flat=True))
    ).order_by('name')

    return render(request, 'committee/candidate_list.html', {
        'committee': committee,
        'candidates': candidates,
        'status_choices': RecruitmentCandidate.STATUS_CHOICES,
        'status_filter': status_filter,
        'assigned_filter': assigned_filter,
        'committee_members': committee_members,
        'can_manage': _can_manage(committee, request.user, perm=perm),
        'is_chair': is_chair,
    })


@login_required
@require_page_enabled('committee_home')
def create_candidate(request, code):
    """Create a new candidate."""
    committee = _get_committee(code)
    has_access, is_chair, perm = _user_access(committee, request.user)

    if not has_access or not _can_view_private(committee, request.user, perm=perm):
        messages.error(request, 'You do not have permission to add candidates.')
        return redirect('recruitment_dashboard', code=code)

    committee_members = ParliamentUser.objects.filter(
        pk__in=set(committee.chairs.values_list('pk', flat=True)) |
               set(committee.members.values_list('pk', flat=True))
    ).order_by('name')
    recruitment_events = RecruitmentEvent.objects.filter(committee=committee).select_related('event').order_by('-event__date_time')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        status = request.POST.get('status', 'prospect')
        assigned_to_id = request.POST.get('assigned_to', '').strip()
        notes = request.POST.get('notes', '').strip()
        last_contacted_str = request.POST.get('last_contacted', '').strip()
        source_event_id = request.POST.get('source_event', '').strip()

        if not name:
            messages.error(request, 'Name is required.')
            return render(request, 'committee/candidate_form.html', {
                'committee': committee, 'post': request.POST,
                'status_choices': RecruitmentCandidate.STATUS_CHOICES,
                'committee_members': committee_members,
                'recruitment_events': recruitment_events,
            })

        valid_statuses = {c[0] for c in RecruitmentCandidate.STATUS_CHOICES}
        if status not in valid_statuses:
            status = 'prospect'

        assigned_to = None
        if assigned_to_id:
            try:
                assigned_to = ParliamentUser.objects.get(user_id=assigned_to_id)
            except ParliamentUser.DoesNotExist:
                pass

        last_contacted = None
        if last_contacted_str:
            from django.utils.dateparse import parse_date
            last_contacted = parse_date(last_contacted_str)

        source_event = None
        if source_event_id:
            try:
                source_event = RecruitmentEvent.objects.get(id=source_event_id, committee=committee)
            except RecruitmentEvent.DoesNotExist:
                pass

        candidate = RecruitmentCandidate.objects.create(
            committee=committee,
            name=name,
            email=email,
            phone=phone,
            status=status,
            assigned_to=assigned_to,
            notes=notes,
            last_contacted=last_contacted,
            source_event=source_event,
            added_by=request.user,
        )

        ActivityLog.log_activity(
            action_type='other',
            user=request.user,
            description=f'Added recruitment candidate "{name}" ({candidate.get_status_display()}) to {committee.code}',
            request=request,
            object_type='RecruitmentCandidate',
            object_id=candidate.pk,
            object_repr=name,
        )

        messages.success(request, f'Candidate "{name}" added.')
        return redirect('candidate_list', code=code)

    return render(request, 'committee/candidate_form.html', {
        'committee': committee,
        'post': {
            'name': '', 'email': '', 'phone': '', 'status': 'prospect',
            'assigned_to': '', 'notes': '', 'last_contacted': '', 'source_event': '',
        },
        'status_choices': RecruitmentCandidate.STATUS_CHOICES,
        'committee_members': committee_members,
        'recruitment_events': recruitment_events,
    })


@login_required
@require_page_enabled('committee_home')
def edit_candidate(request, code, candidate_id):
    """Edit an existing candidate."""
    committee = _get_committee(code)
    candidate = get_object_or_404(RecruitmentCandidate, id=candidate_id, committee=committee)

    has_access, is_chair, perm = _user_access(committee, request.user)
    if not has_access or not _can_view_private(committee, request.user, perm=perm):
        messages.error(request, 'You do not have permission to edit candidates.')
        return redirect('candidate_list', code=code)

    committee_members = ParliamentUser.objects.filter(
        pk__in=set(committee.chairs.values_list('pk', flat=True)) |
               set(committee.members.values_list('pk', flat=True))
    ).order_by('name')
    recruitment_events = RecruitmentEvent.objects.filter(committee=committee).select_related('event').order_by('-event__date_time')

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        status = request.POST.get('status', candidate.status)
        assigned_to_id = request.POST.get('assigned_to', '').strip()
        notes = request.POST.get('notes', '').strip()
        last_contacted_str = request.POST.get('last_contacted', '').strip()
        source_event_id = request.POST.get('source_event', '').strip()

        if not name:
            messages.error(request, 'Name is required.')
            return render(request, 'committee/candidate_form.html', {
                'committee': committee, 'candidate': candidate, 'post': request.POST,
                'status_choices': RecruitmentCandidate.STATUS_CHOICES,
                'committee_members': committee_members,
                'recruitment_events': recruitment_events,
                'editing': True,
            })

        valid_statuses = {c[0] for c in RecruitmentCandidate.STATUS_CHOICES}
        if status not in valid_statuses:
            status = candidate.status

        assigned_to = None
        if assigned_to_id:
            try:
                assigned_to = ParliamentUser.objects.get(user_id=assigned_to_id)
            except ParliamentUser.DoesNotExist:
                pass

        last_contacted = None
        if last_contacted_str:
            from django.utils.dateparse import parse_date
            last_contacted = parse_date(last_contacted_str)

        source_event = None
        if source_event_id:
            try:
                source_event = RecruitmentEvent.objects.get(id=source_event_id, committee=committee)
            except RecruitmentEvent.DoesNotExist:
                pass

        candidate.name = name
        candidate.email = email
        candidate.phone = phone
        candidate.status = status
        candidate.assigned_to = assigned_to
        candidate.notes = notes
        candidate.last_contacted = last_contacted
        candidate.source_event = source_event
        candidate.save()

        messages.success(request, f'Candidate "{name}" updated.')
        return redirect('candidate_list', code=code)

    return render(request, 'committee/candidate_form.html', {
        'committee': committee,
        'candidate': candidate,
        'post': {
            'name': candidate.name,
            'email': candidate.email,
            'phone': candidate.phone,
            'status': candidate.status,
            'assigned_to': candidate.assigned_to.user_id if candidate.assigned_to else '',
            'notes': candidate.notes,
            'last_contacted': candidate.last_contacted.isoformat() if candidate.last_contacted else '',
            'source_event': candidate.source_event_id or '',
        },
        'status_choices': RecruitmentCandidate.STATUS_CHOICES,
        'committee_members': committee_members,
        'recruitment_events': recruitment_events,
        'editing': True,
    })


@login_required
@require_http_methods(['POST'])
@require_page_enabled('committee_home')
def delete_candidate(request, code, candidate_id):
    """Delete a candidate."""
    committee = _get_committee(code)
    candidate = get_object_or_404(RecruitmentCandidate, id=candidate_id, committee=committee)

    has_access, is_chair, perm = _user_access(committee, request.user)
    if not has_access or not _can_manage(committee, request.user, perm=perm):
        messages.error(request, 'You do not have permission to delete candidates.')
        return redirect('candidate_list', code=code)

    name = candidate.name
    candidate.delete()
    messages.success(request, f'Candidate "{name}" removed.')
    return redirect('candidate_list', code=code)


@login_required
@require_page_enabled('committee_home')
@require_http_methods(['POST'])
def candidate_update_status(request, code, candidate_id):
    """
    Inline status update for the candidate list badge popover.
    POST body: status=<new_status>
    Returns JSON: {status, status_display, badge_class}
    """
    committee = _get_committee(code)
    candidate = get_object_or_404(RecruitmentCandidate, id=candidate_id, committee=committee)

    has_access, _, perm = _user_access(committee, request.user)
    if not has_access or not _can_manage(committee, request.user, perm=perm):
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    valid_statuses = {s[0] for s in RecruitmentCandidate.STATUS_CHOICES}
    new_status = request.POST.get('status', '').strip()
    if new_status not in valid_statuses:
        return JsonResponse({'error': 'Invalid status.'}, status=400)

    candidate.status = new_status
    candidate.save(update_fields=['status', 'updated_at'])

    # Badge colour classes — mirrors the template
    badge_classes = {
        'accepted': 'bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300',
        'declined': 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',
        'rejected': 'bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-300',
        'bid':      'bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300',
        'invited':  'bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300',
        'contacted':'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300',
        'prospect': 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300',
    }

    return JsonResponse({
        'status': candidate.status,
        'status_display': candidate.get_status_display(),
        'badge_class': badge_classes.get(new_status, badge_classes['prospect']),
    })


@login_required
@require_page_enabled('committee_home')
@require_http_methods(['POST'])
def add_candidate_note(request, code, candidate_id):
    """
    Add a note to a candidate's note thread.
    POST body: body=<text>
    Returns JSON: {note_id, author, body, created_at}
    """
    committee = _get_committee(code)
    candidate = get_object_or_404(RecruitmentCandidate, id=candidate_id, committee=committee)

    has_access, _, perm = _user_access(committee, request.user)
    if not has_access or not _can_view_private(committee, request.user, perm=perm):
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    body = request.POST.get('body', '').strip()
    if not body:
        return JsonResponse({'error': 'Note cannot be empty.'}, status=400)

    note = RecruitmentCandidateNote.objects.create(
        candidate=candidate,
        author=request.user,
        body=body,
    )

    return JsonResponse({
        'note_id': note.pk,
        'author': request.user.get_display_name(),
        'body': note.body,
        'created_at': note.created_at.strftime('%b %-d, %Y %-I:%M %p'),
    })


@login_required
@require_page_enabled('committee_home')
@require_http_methods(['POST'])
def delete_candidate_note(request, code, candidate_id, note_id):
    """
    Delete a candidate note.
    Authors can delete their own notes; chairs can delete any note.
    """
    committee = _get_committee(code)
    candidate = get_object_or_404(RecruitmentCandidate, id=candidate_id, committee=committee)
    note = get_object_or_404(RecruitmentCandidateNote, id=note_id, candidate=candidate)

    is_chair = committee.is_chair(request.user) or request.user.is_admin
    if note.author_id != request.user.pk and not is_chair:
        return JsonResponse({'error': 'You can only delete your own notes.'}, status=403)

    note.delete()
    return JsonResponse({'deleted': True, 'note_id': note_id})
