"""
Committee minutes editor - full-featured minutes system for committees.
Adapts the chapter minutes editor for committee-scoped use with
committee-based attendance and chair/secretary permissions.
"""
import json

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods
from django.core.files.base import ContentFile

from src.models import (
    ChapterMinutes, MinutesSection, MinutesMotion,
    ParliamentUser, Committee, CommitteePermissions,
    CommitteeDocument, ActivityLog
)
from src.view.officer.chapter_minutes import generate_minutes_pdf_buffer


def is_committee_member_or_above(user, committee):
    """Check if user is a committee member, chair, officer, or admin"""
    return (committee.is_member(user) or committee.is_chair(user)
            or user.member_type == 'Officer' or user.is_admin)


def can_edit_committee_minutes(user, committee):
    """Check if user is a committee chair, admin, or designated secretary.
    These users can edit ANY committee minutes (not just their own)."""
    if user.is_admin:
        return True
    if committee.is_chair(user):
        return True
    return CommitteePermissions.objects.filter(
        committee=committee, user=user, can_take_minutes=True
    ).exists()


def can_edit_specific_minutes(user, committee, minutes):
    """Check if user can edit a specific minutes record.
    - Chairs, admins, and secretaries can edit any minutes.
    - The original creator can edit their own draft minutes.
    """
    if can_edit_committee_minutes(user, committee):
        return True
    # Creator can edit their own draft
    if minutes.created_by == user and minutes.status == 'draft':
        return True
    return False


@login_required
def committee_minutes_list(request, code):
    """List all committee minutes (drafts and published)"""
    committee = get_object_or_404(Committee, code=code)

    if not is_committee_member_or_above(request.user, committee):
        messages.error(request, 'You do not have permission to view this committee\'s minutes.')
        return redirect('committee_home', code=code)

    minutes_list = ChapterMinutes.objects.filter(
        committee=committee
    ).order_by('-date', '-start_time')

    # Any committee member can create minutes
    can_create = is_committee_member_or_above(request.user, committee)
    # Only chairs/admins/secretaries can edit any minutes
    can_edit = can_edit_committee_minutes(request.user, committee)

    context = {
        'committee': committee,
        'minutes_list': minutes_list,
        'can_create': can_create,
        'can_edit': can_edit,
    }
    return render(request, 'committee/minutes_list.html', context)


@login_required
@require_http_methods(["POST"])
def create_committee_minutes(request, code):
    """Create a new committee minutes session and redirect to the editor"""
    committee = get_object_or_404(Committee, code=code)

    if not is_committee_member_or_above(request.user, committee):
        messages.error(request, 'You do not have permission to create minutes for this committee.')
        return redirect('committee_minutes_list', code=code)

    title = request.POST.get('title', '').strip()
    minutes_date = request.POST.get('date', '')
    start_time = request.POST.get('start_time', '')

    if not title or not minutes_date or not start_time:
        messages.error(request, 'Title, date, and start time are required.')
        return redirect('committee_minutes_list', code=code)

    minutes = ChapterMinutes.objects.create(
        title=title,
        date=minutes_date,
        start_time=start_time,
        committee=committee,
        created_by=request.user,
        status='draft',
    )

    # Create initial empty text section
    MinutesSection.objects.create(
        minutes=minutes,
        section_type='text',
        order=0,
        content='',
    )

    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'Created committee minutes for {committee.code}: {title}',
        request=request,
        object_type='ChapterMinutes',
        object_id=minutes.id,
        object_repr=str(minutes),
    )

    messages.success(request, f'Minutes session "{title}" created.')
    return redirect('edit_committee_minutes', code=code, minutes_id=minutes.id)


@login_required
def edit_committee_minutes(request, code, minutes_id):
    """Main editor page for committee minutes"""
    committee = get_object_or_404(Committee, code=code)

    minutes = get_object_or_404(ChapterMinutes, id=minutes_id, committee=committee)

    if not can_edit_specific_minutes(request.user, committee, minutes):
        messages.error(request, 'You do not have permission to edit this committee\'s minutes.')
        return redirect('committee_minutes_list', code=code)

    # Get committee members for attendance (members + chairs, deduplicated)
    # Exclude advisors and sort by last name
    member_ids = set(
        committee.members.filter(member_status='Active').exclude(member_type='Advisor').values_list('pk', flat=True)
    ) | set(
        committee.chairs.filter(member_status='Active').exclude(member_type='Advisor').values_list('pk', flat=True)
    )

    def get_last_name(user):
        """Extract last name from full name for sorting"""
        parts = user.name.strip().split()
        return parts[-1].lower() if parts else ''

    all_members = ParliamentUser.objects.filter(pk__in=member_ids)

    # Sort: non-pledges first (by last name), then pledges (by last name)
    non_pledges = sorted(
        [m for m in all_members if m.member_type != 'Pledge'],
        key=get_last_name
    )
    pledges = sorted(
        [m for m in all_members if m.member_type == 'Pledge'],
        key=get_last_name
    )
    members = non_pledges + pledges

    # Get existing sections with related motions
    sections = minutes.sections.all().select_related('motion').order_by('order')

    # Build sections data for the template/JS
    sections_data = []
    for section in sections:
        s = {
            'id': section.id,
            'type': section.section_type,
            'order': section.order,
            'content': section.content,
            'title': section.title,
        }
        if section.section_type == 'motion' and hasattr(section, 'motion'):
            m = section.motion
            s['motion'] = {
                'id': m.id,
                'motion_type': m.motion_type,
                'motion_text': m.motion_text,
                'context_notes': m.context_notes,
                'author_id': m.author_id or '',
                'author_text': m.author_text,
                'received_second': m.received_second,
                'seconded_by_text': m.seconded_by_text,
                'vote_method': m.vote_method,
                'result': m.result,
                'votes_for': m.votes_for,
                'votes_against': m.votes_against,
                'votes_abstain': m.votes_abstain,
                'caucus_held': m.caucus_held,
                'caucus_duration': m.caucus_duration,
                'caucus_type': m.caucus_type,
                'speaker_time': m.speaker_time,
            }
        sections_data.append(s)

    # Build member data for attendance (no event/excuse integration for committees)
    attendance_data = minutes.attendance_data or []
    attendance_map = {str(a['user_id']): a['status'] for a in attendance_data}

    member_list = []
    for member in members:
        uid = str(member.user_id)
        status = attendance_map.get(uid, 'pending')
        entry = {
            'user_id': member.user_id,
            'name': member.get_display_name(),
            'member_type': member.member_type,
            'status': status,
        }
        member_list.append(entry)

    # Member choices for motion author dropdown (committee members only)
    member_choices = [
        {'user_id': m.user_id, 'name': m.get_display_name()}
        for m in members
    ]

    context = {
        'minutes': minutes,
        'sections_json': json.dumps(sections_data),
        'members_json': json.dumps(member_list),
        'member_choices_json': json.dumps(member_choices),
        'events': [],  # No event linking for committees
        'has_linked_event': False,
        'end_time_value': minutes.end_time.strftime('%H:%M') if minutes.end_time else '',
        'is_published': minutes.status == 'published',
        'motion_type_choices': MinutesMotion.MOTION_TYPE_CHOICES,
        'vote_method_choices': MinutesMotion.VOTE_METHOD_CHOICES,
        'result_choices': MinutesMotion.RESULT_CHOICES,
        'caucus_type_choices': MinutesMotion.CAUCUS_TYPE_CHOICES,
        # Committee-specific context
        'is_committee_minutes': True,
        'committee': committee,
    }
    return render(request, 'officer/chapter_minutes_editor.html', context)


@login_required
@require_POST
def save_committee_minutes_data(request, code, minutes_id):
    """AJAX endpoint to save all sections (text + motions) for committee minutes"""
    committee = get_object_or_404(Committee, code=code)
    minutes = get_object_or_404(ChapterMinutes, id=minutes_id, committee=committee)

    if not can_edit_specific_minutes(request.user, committee, minutes):
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    try:
        data = json.loads(request.body)
        sections_data = data.get('sections', [])
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid data.'}, status=400)

    # Track edits to published minutes
    is_published = minutes.status == 'published'
    edit_reason = data.get('edit_reason', '')

    # Save end_time if provided
    end_time_str = data.get('end_time')
    if end_time_str:
        from datetime import datetime as dt
        try:
            minutes.end_time = dt.strptime(end_time_str, '%H:%M').time()
        except (ValueError, TypeError):
            pass
    elif end_time_str == '':
        minutes.end_time = None

    # Delete existing sections and motions
    minutes.sections.all().delete()

    # Recreate sections from submitted data
    for i, s in enumerate(sections_data):
        section = MinutesSection.objects.create(
            minutes=minutes,
            section_type=s.get('type', 'text'),
            order=i,
            content=s.get('content', ''),
            title=s.get('title', ''),
        )

        if s.get('type') == 'motion' and s.get('motion'):
            m = s['motion']
            author = None
            author_id = m.get('author_id')
            if author_id:
                try:
                    author = ParliamentUser.objects.get(user_id=author_id)
                except ParliamentUser.DoesNotExist:
                    pass

            MinutesMotion.objects.create(
                section=section,
                motion_type=m.get('motion_type', 'custom'),
                motion_text=m.get('motion_text', ''),
                context_notes=m.get('context_notes', ''),
                author=author,
                author_text=m.get('author_text', ''),
                received_second=m.get('received_second', False),
                seconded_by_text=m.get('seconded_by_text', ''),
                vote_method=m.get('vote_method', 'voice'),
                result=m.get('result', 'passed'),
                votes_for=m.get('votes_for') or None,
                votes_against=m.get('votes_against') or None,
                votes_abstain=m.get('votes_abstain') or None,
                caucus_held=m.get('caucus_held', False),
                caucus_duration=m.get('caucus_duration') or None,
                caucus_type=m.get('caucus_type', ''),
                speaker_time=m.get('speaker_time') or None,
            )

    minutes.updated_at = timezone.now()

    if is_published:
        minutes.edited_after_publish = True
        minutes.last_edit_at = timezone.now()
        minutes.last_edit_by = request.user
        if edit_reason:
            minutes.last_edit_reason = edit_reason

    minutes.save()

    # If published and has a linked document, regenerate the PDF
    if is_published and minutes.published_document:
        pdf_buffer = generate_minutes_pdf_buffer(minutes)
        file_name = f"Committee_Minutes_{committee.code}_{minutes.date.strftime('%Y-%m-%d')}_{minutes.title.replace(' ', '_')}.pdf"
        if minutes.published_document.document:
            minutes.published_document.document.delete(save=False)
        minutes.published_document.document.save(file_name, ContentFile(pdf_buffer.read()), save=True)

    return JsonResponse({'success': True, 'message': 'Minutes saved.', 'was_published': is_published})


@login_required
@require_POST
def save_committee_minutes_attendance(request, code, minutes_id):
    """AJAX endpoint to save attendance data for committee minutes"""
    committee = get_object_or_404(Committee, code=code)
    minutes = get_object_or_404(ChapterMinutes, id=minutes_id, committee=committee)

    if not can_edit_specific_minutes(request.user, committee, minutes):
        return JsonResponse({'error': 'Permission denied.'}, status=403)

    try:
        data = json.loads(request.body)
        attendance_list = data.get('attendance', [])
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid data.'}, status=400)

    # Track edits to published minutes
    if minutes.status == 'published':
        minutes.edited_after_publish = True
        minutes.last_edit_at = timezone.now()
        minutes.last_edit_by = request.user
        minutes.last_edit_reason = data.get('edit_reason', 'Attendance updated')

    # Store attendance snapshot on minutes
    minutes.attendance_data = attendance_list
    minutes.attendance_taken = True
    minutes.save()

    ActivityLog.log_activity(
        action_type='attendance_taken',
        user=request.user,
        description=f'Saved attendance for committee minutes ({committee.code}): {minutes.title}',
        request=request,
        object_type='ChapterMinutes',
        object_id=minutes.id,
        object_repr=str(minutes),
    )

    # If published and has a linked document, regenerate the PDF
    if minutes.status == 'published' and minutes.published_document:
        pdf_buffer = generate_minutes_pdf_buffer(minutes)
        file_name = f"Committee_Minutes_{committee.code}_{minutes.date.strftime('%Y-%m-%d')}_{minutes.title.replace(' ', '_')}.pdf"
        if minutes.published_document.document:
            minutes.published_document.document.delete(save=False)
        minutes.published_document.document.save(file_name, ContentFile(pdf_buffer.read()), save=True)

    return JsonResponse({'success': True, 'message': 'Attendance saved.'})


@login_required
@require_POST
def publish_committee_minutes(request, code, minutes_id):
    """Publish committee minutes as a PDF to committee documents (and optionally chapter documents)"""
    committee = get_object_or_404(Committee, code=code)

    if not can_edit_committee_minutes(request.user, committee):
        messages.error(request, 'You do not have permission to publish minutes for this committee.')
        return redirect('committee_minutes_list', code=code)

    minutes = get_object_or_404(ChapterMinutes, id=minutes_id, committee=committee)

    if minutes.status == 'published':
        messages.warning(request, 'These minutes are already published.')
        return redirect('edit_committee_minutes', code=code, minutes_id=minutes.id)

    visibility = request.POST.get('visibility', 'committee_only')
    also_publish_to_chapter = request.POST.get('publish_to_chapter') == 'on'

    # Generate PDF
    pdf_buffer = generate_minutes_pdf_buffer(minutes)
    file_name = f"Committee_Minutes_{committee.code}_{minutes.date.strftime('%Y-%m-%d')}_{minutes.title.replace(' ', '_')}.pdf"
    pdf_file = ContentFile(pdf_buffer.read())

    # Create CommitteeDocument linked to the committee
    doc = CommitteeDocument.objects.create(
        committee=committee,
        title=f"Committee Minutes - {committee.name} - {minutes.title} ({minutes.date.strftime('%m/%d/%Y')})",
        description=f"Committee meeting minutes for {committee.name} on {minutes.date.strftime('%B %d, %Y')}",
        uploaded_by=request.user,
        document_type='minutes',
        meeting_date=minutes.date,
        published_to_chapter=also_publish_to_chapter,
        visibility=visibility,
    )
    doc.document.save(file_name, pdf_file, save=True)

    # Update minutes status
    minutes.status = 'published'
    minutes.published_document = doc
    minutes.publish_visibility = visibility
    minutes.save()

    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'Published committee minutes ({committee.code}): {minutes.title}',
        request=request,
        object_type='ChapterMinutes',
        object_id=minutes.id,
        object_repr=str(minutes),
    )

    msg = f'Minutes "{minutes.title}" published to committee documents.'
    if also_publish_to_chapter:
        msg += ' Also published to chapter documents.'
    messages.success(request, msg)
    return redirect('edit_committee_minutes', code=code, minutes_id=minutes.id)


@login_required
def download_committee_minutes_pdf(request, code, minutes_id):
    """Generate and download a formatted PDF of committee minutes"""
    committee = get_object_or_404(Committee, code=code)

    # Allow any committee member or editor to download
    if not (committee.is_member(request.user) or committee.is_chair(request.user)
            or request.user.member_type == 'Officer' or request.user.is_admin):
        messages.error(request, 'You do not have permission to download this PDF.')
        return redirect('committee_minutes_list', code=code)

    minutes = get_object_or_404(ChapterMinutes, id=minutes_id, committee=committee)
    buf = generate_minutes_pdf_buffer(minutes)

    file_name = f"Committee_Minutes_{committee.code}_{minutes.date.strftime('%Y-%m-%d')}_{minutes.title.replace(' ', '_')}.pdf"
    response = HttpResponse(buf.read(), content_type='application/pdf')
    disposition = 'inline' if request.GET.get('preview') else 'attachment'
    response['Content-Disposition'] = f'{disposition}; filename="{file_name}"'
    return response


@login_required
@require_POST
def delete_committee_minutes(request, code, minutes_id):
    """Delete committee minutes (editors can delete drafts, admins can delete any)"""
    committee = get_object_or_404(Committee, code=code)

    if not can_edit_committee_minutes(request.user, committee):
        messages.error(request, 'You do not have permission to delete minutes for this committee.')
        return redirect('committee_minutes_list', code=code)

    minutes = get_object_or_404(ChapterMinutes, id=minutes_id, committee=committee)

    # Only admins can delete published minutes
    if minutes.status == 'published' and not request.user.is_admin:
        messages.error(request, 'Only administrators can delete published minutes.')
        return redirect('committee_minutes_list', code=code)

    title = minutes.title
    minutes_date = minutes.date

    # Delete linked published document
    if minutes.published_document:
        minutes.published_document.delete()

    # Delete all related sections (cascades to motions)
    minutes.sections.all().delete()

    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'Deleted committee minutes ({committee.code}): {title} ({minutes_date})',
        request=request,
        object_type='ChapterMinutes',
        object_id=minutes_id,
        object_repr=f'{title} ({minutes_date})',
    )

    minutes.delete()

    messages.success(request, f'Minutes "{title}" deleted successfully.')
    return redirect('committee_minutes_list', code=code)
