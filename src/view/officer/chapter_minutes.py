"""
Chapter minutes management for officers.
Provides views for creating, editing, and publishing chapter meeting minutes
with attendance tracking and embedded motion/vote recording.
"""
import io
import json
import re
from datetime import date
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods
from django.core.files.base import ContentFile

from src.models import (
    ChapterMinutes, MinutesSection, MinutesMotion,
    ParliamentUser, Event, Attendance, AttendanceExcuse,
    CommitteeDocument, ActivityLog
)
from src.decorators import officer_required
from src.models.users import member_defer


def _linkable_events(limit=20, window_days=60):
    """
    Events eligible to link chapter minutes to, ordered by closeness to
    the current moment rather than raw chronological order.

    v3.29.3 — the previous query was `Event.objects.filter(requires_
    attendance=True).order_by('-date_time')[:20]`: no date window at all,
    sorted purely descending. `-date_time` puts the FARTHEST-future event
    first, and `generate_recurring_events` can pre-generate up to 52
    future instances of a weekly recurring meeting — so a chapter with
    one recurring meeting already schedules more than 20 events out into
    the future, and every one of them sorts ahead of an actual near-term
    event in that ordering. An officer taking minutes for tonight's
    meeting, an hour before it started, got a dropdown made entirely of
    instances of the same recurring series months out — the meeting they
    wanted wasn't in the top 20 at all. Reported by Mason as "I think
    it's a timezone thing"; it wasn't (TIME_ZONE/USE_TZ are configured
    correctly and `date_time` is stored/compared consistently) — it was
    this ordering, which reproduces regardless of timezone.

    Windowing to `window_days` on each side of now and then sorting by
    absolute distance from now (in Python — the candidate set here is
    small, same approach the sort-by-last-name helpers below already use)
    means the event that just happened and the one starting in an hour
    both surface near the top, and a meeting a year out no longer buries
    them.
    """
    now = timezone.now()
    window_start = now - timezone.timedelta(days=window_days)
    window_end = now + timezone.timedelta(days=window_days)
    candidates = list(Event.objects.filter(
        requires_attendance=True,
        date_time__gte=window_start,
        date_time__lte=window_end,
    ))
    candidates.sort(key=lambda e: abs((e.date_time - now).total_seconds()))
    return candidates[:limit]


@login_required
@officer_required
def chapter_minutes_list(request):
    """List all chapter minutes (drafts and published) - excludes committee minutes"""
    minutes_list = ChapterMinutes.objects.filter(
        committee__isnull=True
    ).order_by('-date', '-start_time')

    # Get recent/upcoming events for the create modal
    events = _linkable_events()

    context = {
        'minutes_list': minutes_list,
        'events': events,
    }
    return render(request, 'officer/chapter_minutes_list.html', context)


@login_required
@officer_required
def create_chapter_minutes(request):
    """Create a new chapter minutes session and redirect to the editor"""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        minutes_date = request.POST.get('date', '')
        start_time = request.POST.get('start_time', '')
        event_id = request.POST.get('event', '')

        if not title or not minutes_date or not start_time:
            messages.error(request, 'Title, date, and start time are required.')
            events = _linkable_events()
            return render(request, 'officer/chapter_minutes_list.html', {
                'minutes_list': ChapterMinutes.objects.all().order_by('-date', '-start_time'),
                'events': events,
                'show_create_modal': True,
            })

        event = None
        if event_id:
            try:
                event = Event.objects.get(id=event_id)
            except Event.DoesNotExist:
                pass

        minutes = ChapterMinutes.objects.create(
            title=title,
            date=minutes_date,
            start_time=start_time,
            event=event,
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
            description=f'Created chapter minutes: {title}',
            request=request,
            object_type='ChapterMinutes',
            object_id=minutes.id,
            object_repr=str(minutes),
        )

        messages.success(request, f'Minutes session "{title}" created.')
        return redirect('edit_chapter_minutes', minutes_id=minutes.id)

    return redirect('chapter_minutes_list')


@login_required
@officer_required
def edit_chapter_minutes(request, minutes_id):
    """Main editor page for chapter minutes"""
    minutes = get_object_or_404(ChapterMinutes, id=minutes_id, committee__isnull=True)

    # Get all active members for attendance (excluding advisors)
    # Sort: non-pledges first (by last name), then pledges (by last name)
    def get_last_name(user):
        """Extract last name from full name for sorting"""
        parts = user.name.strip().split()
        return parts[-1].lower() if parts else ''

    def get_user_id(user):
        """Extract user id for sorting"""
        return user.user_id

    all_members = ParliamentUser.objects.filter(
        member_status='Active'
    ).exclude(member_type='Advisor')

    # Separate non-pledges and pledges, sort members by id and by last name for pledges
    non_pledges = sorted(
        [m for m in all_members if m.member_type != 'Pledge'],
        key=get_user_id
    )
    pledges = sorted(
        [m for m in all_members if m.member_type == 'Pledge'],
        key=get_last_name
    )

    # Combine: non-pledges first, then pledges
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
            'title': section.title,  # For header sections
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

    # Get events for linking (recent + upcoming, closest to now first)
    events = _linkable_events()

    # Build excuse and event attendance maps when linked to an event
    excuse_map = {}  # user_id -> {status, reason}
    event_attendance_map = {}  # user_id -> status
    if minutes.event:
        # Get all excuse requests for this event
        excuses = AttendanceExcuse.objects.filter(
            event=minutes.event
        ).select_related('user').defer(*member_defer('user'))
        for excuse in excuses:
            excuse_map[str(excuse.user.user_id)] = {
                'status': excuse.status,
                'reason': excuse.reason,
            }

        # Get existing event attendance records
        event_records = Attendance.objects.filter(
            event=minutes.event,
            attendance_type='event',
        ).select_related('user').defer(*member_defer('user'))
        for record in event_records:
            event_attendance_map[str(record.user.user_id)] = record.status

    # Build member data for attendance
    attendance_data = minutes.attendance_data or []
    attendance_map = {str(a['user_id']): a['status'] for a in attendance_data}

    member_list = []
    for member in members:
        uid = str(member.user_id)
        # Determine status priority:
        # 1. Approved excuse overrides a saved 'absent' (excuse was approved after attendance saved)
        # 2. Otherwise: saved minutes data wins
        # 3. Fall back to event attendance record, then pending
        saved_status = attendance_map.get(uid)
        has_approved_excuse = uid in excuse_map and excuse_map[uid]['status'] == 'approved'

        if has_approved_excuse and saved_status == 'absent':
            # Excuse was approved after the officer marked absent — reflect the excuse
            status = 'excused'
        elif saved_status:
            status = saved_status
        elif uid in event_attendance_map:
            status = event_attendance_map[uid]
        elif has_approved_excuse:
            status = 'excused'
        else:
            status = 'pending'

        entry = {
            'user_id': member.user_id,
            'name': member.get_display_name(),
            'member_type': member.member_type,
            'status': status,
        }

        # Attach excuse info if present
        if uid in excuse_map:
            entry['excuse_status'] = excuse_map[uid]['status']
            entry['excuse_reason'] = excuse_map[uid]['reason']

        member_list.append(entry)

    # Get member choices for motion author dropdown
    member_choices = [
        {'user_id': m.user_id, 'name': m.get_display_name()}
        for m in members
    ]

    context = {
        'minutes': minutes,
        'sections_json': json.dumps(sections_data),
        'members_json': json.dumps(member_list),
        'member_choices_json': json.dumps(member_choices),
        'events': events,
        'has_linked_event': minutes.event is not None,
        'end_time_value': minutes.end_time.strftime('%H:%M') if minutes.end_time else '',
        'is_published': minutes.status == 'published',
        'motion_type_choices': MinutesMotion.MOTION_TYPE_CHOICES,
        'vote_method_choices': MinutesMotion.VOTE_METHOD_CHOICES,
        'result_choices': MinutesMotion.RESULT_CHOICES,
        'caucus_type_choices': MinutesMotion.CAUCUS_TYPE_CHOICES,
    }
    return render(request, 'officer/chapter_minutes_editor.html', context)


@login_required
@officer_required
@require_POST
def update_minutes_event(request, minutes_id):
    """
    AJAX endpoint to link, re-link, or unlink an existing minutes session
    to/from an event — v3.29.3, previously this was only settable at
    creation time via `create_chapter_minutes`, so an officer who forgot
    to link the event up front (or picked the wrong one) had no way to
    fix it short of deleting and recreating the whole minutes session.
    """
    minutes = get_object_or_404(ChapterMinutes, id=minutes_id, committee__isnull=True)

    try:
        data = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return JsonResponse({'error': 'Invalid data.'}, status=400)

    event_id = data.get('event_id')
    event = None
    if event_id:
        try:
            event = Event.objects.get(id=event_id)
        except Event.DoesNotExist:
            return JsonResponse({'error': 'That event no longer exists.'}, status=400)

    minutes.event = event

    # Same edit-tracking as save_minutes_data/save_minutes_attendance —
    # changing which event a published minutes record is linked to is a
    # substantive edit, not a formatting tweak.
    if minutes.status == 'published':
        minutes.edited_after_publish = True
        minutes.last_edit_at = timezone.now()
        minutes.last_edit_by = request.user
        minutes.last_edit_reason = data.get(
            'edit_reason',
            f'Linked event changed to "{event.title}"' if event else 'Event unlinked'
        )

    minutes.save()

    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=(
            f'Linked chapter minutes "{minutes.title}" to event: {event.title}'
            if event else
            f'Unlinked chapter minutes "{minutes.title}" from its event'
        ),
        request=request,
        object_type='ChapterMinutes',
        object_id=minutes.id,
        object_repr=str(minutes),
    )

    # If published and has a linked document, regenerate the PDF so the
    # "Event: ..." line on the document stays in sync.
    if minutes.status == 'published' and minutes.published_document:
        pdf_buffer = generate_minutes_pdf_buffer(minutes)
        ts = timezone.now().strftime('%Y%m%d%H%M%S')
        file_name = f"Chapter_Minutes_{minutes.date.strftime('%Y-%m-%d')}_{minutes.title.replace(' ', '_')}_{ts}.pdf"
        if minutes.published_document.document:
            minutes.published_document.document.delete(save=False)
        minutes.published_document.document.save(file_name, ContentFile(pdf_buffer.read()), save=True)

    return JsonResponse({
        'success': True,
        'message': f'Linked to "{event.title}".' if event else 'Event unlinked.',
        'event': {'id': event.id, 'title': event.title} if event else None,
    })


@login_required
@officer_required
@require_POST
def save_minutes_data(request, minutes_id):
    """AJAX endpoint to save all sections (text + motions)"""
    minutes = get_object_or_404(ChapterMinutes, id=minutes_id, committee__isnull=True)

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
            title=s.get('title', ''),  # For header sections
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

    # If editing published minutes, track the edit and regenerate PDF
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
        ts = timezone.now().strftime('%Y%m%d%H%M%S')
        file_name = f"Chapter_Minutes_{minutes.date.strftime('%Y-%m-%d')}_{minutes.title.replace(' ', '_')}_{ts}.pdf"
        # Delete old file and save new one
        if minutes.published_document.document:
            minutes.published_document.document.delete(save=False)
        minutes.published_document.document.save(file_name, ContentFile(pdf_buffer.read()), save=True)

    return JsonResponse({'success': True, 'message': 'Minutes saved.', 'was_published': is_published})


@login_required
@officer_required
@require_POST
def save_minutes_attendance(request, minutes_id):
    """AJAX endpoint to save attendance data"""
    minutes = get_object_or_404(ChapterMinutes, id=minutes_id, committee__isnull=True)

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

    # Sync Attendance records so vote eligibility (3-hour window) is always current
    now = timezone.now()
    today = timezone.localdate()               # v3.17.4: was now.date() (UTC)

    if minutes.event:
        # Linked to an event — write event-type attendance records
        # Build a map of pending/approved excuses for this event
        excuses_by_user = {}
        for excuse in AttendanceExcuse.objects.filter(event=minutes.event).select_related('user').defer(*member_defer('user')):
            excuses_by_user[str(excuse.user.user_id)] = excuse

        for entry in attendance_list:
            user_id = entry.get('user_id')
            status = entry.get('status', 'pending')

            if status == 'pending':
                continue

            try:
                user = ParliamentUser.objects.get(user_id=user_id)
            except ParliamentUser.DoesNotExist:
                continue

            # Sync attendance record to the event
            # created_at is refreshed so the vote eligibility 3-hour window sees it
            Attendance.objects.update_or_create(
                event=minutes.event,
                user=user,
                attendance_type='event',
                defaults={
                    'status': status,
                    'created_at': now,
                    'marked_by': request.user,
                    'marked_at': now,
                    'notes': f'Marked via chapter minutes: {minutes.title}',
                }
            )

            # If marked excused and has a pending excuse, approve it
            uid = str(user_id)
            if status == 'excused' and uid in excuses_by_user:
                excuse = excuses_by_user[uid]
                if excuse.status == 'pending':
                    excuse.status = 'approved'
                    excuse.reviewed_by = request.user
                    excuse.reviewed_at = now
                    excuse.review_notes = f'Approved via chapter minutes: {minutes.title}'
                    excuse.save()

    else:
        # No linked event — write standalone committee attendance records
        # so vote eligibility still works during unlinked meetings
        for entry in attendance_list:
            user_id = entry.get('user_id')
            status = entry.get('status', 'pending')

            if status == 'pending':
                continue

            try:
                member = ParliamentUser.objects.get(user_id=user_id)
            except ParliamentUser.DoesNotExist:
                continue

            Attendance.objects.update_or_create(
                user=member,
                date=today,
                attendance_type='committee',
                committee=None,
                defaults={
                    'status': status,
                    'created_at': now,
                    'marked_by': request.user,
                    'marked_at': now,
                    'notes': f'Marked via chapter minutes: {minutes.title}',
                }
            )

    ActivityLog.log_activity(
        action_type='attendance_taken',
        user=request.user,
        description=f'Saved attendance for chapter minutes: {minutes.title}',
        request=request,
        object_type='ChapterMinutes',
        object_id=minutes.id,
        object_repr=str(minutes),
    )

    # If published and has a linked document, regenerate the PDF
    if minutes.status == 'published' and minutes.published_document:
        pdf_buffer = generate_minutes_pdf_buffer(minutes)
        ts = timezone.now().strftime('%Y%m%d%H%M%S')
        file_name = f"Chapter_Minutes_{minutes.date.strftime('%Y-%m-%d')}_{minutes.title.replace(' ', '_')}_{ts}.pdf"
        if minutes.published_document.document:
            minutes.published_document.document.delete(save=False)
        minutes.published_document.document.save(file_name, ContentFile(pdf_buffer.read()), save=True)

    return JsonResponse({'success': True, 'message': 'Attendance saved.'})


@login_required
@officer_required
@require_POST
def publish_chapter_minutes(request, minutes_id):
    """Publish minutes to the chapter documents page as a PDF"""
    minutes = get_object_or_404(ChapterMinutes, id=minutes_id, committee__isnull=True)

    if minutes.status == 'published':
        messages.warning(request, 'These minutes are already published.')
        return redirect('edit_chapter_minutes', minutes_id=minutes.id)

    visibility = request.POST.get('visibility', 'all_members')

    # Generate PDF using the shared helper
    pdf_buffer = generate_minutes_pdf_buffer(minutes)
    file_name = f"Chapter_Minutes_{minutes.date.strftime('%Y-%m-%d')}_{minutes.title.replace(' ', '_')}.pdf"
    pdf_file = ContentFile(pdf_buffer.read())

    # Create CommitteeDocument (chapter-level, committee=None)
    doc = CommitteeDocument.objects.create(
        committee=None,
        title=f"Chapter Minutes - {minutes.title} ({minutes.date.strftime('%m/%d/%Y')})",
        description=f"Chapter meeting minutes for {minutes.date.strftime('%B %d, %Y')}",
        uploaded_by=request.user,
        document_type='minutes',
        meeting_date=minutes.date,
        published_to_chapter=True,
        visibility=visibility,
    )
    doc.document.save(file_name, pdf_file, save=True)

    # Update minutes status
    minutes.status = 'published'
    minutes.published_document = doc
    minutes.publish_visibility = visibility
    minutes.save(update_fields=['status', 'published_document', 'publish_visibility'])

    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'Published chapter minutes: {minutes.title}',
        request=request,
        object_type='ChapterMinutes',
        object_id=minutes.id,
        object_repr=str(minutes),
    )

    messages.success(request, f'Minutes "{minutes.title}" published to chapter documents.')
    return redirect('edit_chapter_minutes', minutes_id=minutes.id)


def generate_minutes_pdf_buffer(minutes):
    """
    Generate a PDF of chapter minutes and return a BytesIO buffer.
    Used by both download_minutes_pdf and publish_chapter_minutes.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_LEFT, TA_CENTER
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
        HRFlowable, KeepTogether, Indenter, ListFlowable, ListItem, Preformatted
    )

    # Build the PDF in memory
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    # Define styles
    styles = getSampleStyleSheet()
    style_title = ParagraphStyle(
        'MinutesTitle',
        parent=styles['Title'],
        fontSize=20,
        leading=24,
        spaceAfter=4,
        textColor=HexColor('#1a1a1a'),
    )
    style_subtitle = ParagraphStyle(
        'MinutesSubtitle',
        parent=styles['Normal'],
        fontSize=11,
        leading=14,
        spaceAfter=2,
        textColor=HexColor('#555555'),
    )
    style_section_header = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontSize=14,
        leading=18,
        spaceBefore=16,
        spaceAfter=8,
        textColor=HexColor('#1e3a5f'),
        borderWidth=0,
    )
    style_body = ParagraphStyle(
        'MinutesBody',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        spaceAfter=8,
    )
    style_motion_header = ParagraphStyle(
        'MotionHeader',
        parent=styles['Heading3'],
        fontSize=11,
        leading=14,
        spaceBefore=4,
        spaceAfter=4,
        textColor=HexColor('#1e40af'),
    )
    style_motion_detail = ParagraphStyle(
        'MotionDetail',
        parent=styles['Normal'],
        fontSize=9,
        leading=12,
        leftIndent=12,
        spaceAfter=2,
        textColor=HexColor('#374151'),
    )
    style_footer = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=9,
        leading=12,
        textColor=HexColor('#888888'),
        alignment=TA_CENTER,
    )
    style_attendance_name = ParagraphStyle(
        'AttendanceName',
        parent=styles['Normal'],
        fontSize=9,
        leading=11,
    )

    # Markdown-specific styles (used for formatted text blocks in the minutes body)
    style_md_h1 = ParagraphStyle(
        'MdH1',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=17,
        spaceBefore=10,
        spaceAfter=4,
        textColor=HexColor('#111827'),
    )
    style_md_h2 = ParagraphStyle(
        'MdH2',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15,
        spaceBefore=8,
        spaceAfter=3,
        textColor=HexColor('#374151'),
    )
    style_md_h3 = ParagraphStyle(
        'MdH3',
        parent=styles['Normal'],
        fontName='Helvetica-BoldOblique',
        fontSize=10,
        leading=14,
        spaceBefore=6,
        spaceAfter=2,
        textColor=HexColor('#4b5563'),
    )
    style_md_blockquote = ParagraphStyle(
        'MdBlockquote',
        parent=styles['Normal'],
        fontSize=10,
        leading=14,
        leftIndent=20,
        spaceAfter=6,
        textColor=HexColor('#6b7280'),
    )
    style_md_code_block = ParagraphStyle(
        'MdCodeBlock',
        parent=styles['Code'],
        fontName='Courier',
        fontSize=9,
        leading=12,
        leftIndent=12,
        rightIndent=12,
        spaceBefore=4,
        spaceAfter=4,
    )

    # ---- Inline Markdown + Markdown-to-flowables helpers ----

    def _esc(text):
        """Escape XML special characters in plain text."""
        return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    def _inline_md(text):
        """
        Convert inline Markdown syntax to ReportLab XML tags.
        Call AFTER _esc() so user-typed < > & are already safe.
        Supports: ***bold-italic***, **bold**, *italic*, ~~strike~~, `code`
        """
        # Bold + italic must be checked before bold/italic individually
        text = re.sub(r'\*{3}(.+?)\*{3}', r'<b><i>\1</i></b>', text)
        text = re.sub(r'\*{2}(.+?)\*{2}', r'<b>\1</b>', text)
        text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
        # __bold__ and _italic_ variants
        text = re.sub(r'_{2}(.+?)_{2}', r'<b>\1</b>', text)
        text = re.sub(r'_([^_]+?)_', r'<i>\1</i>', text)
        # Strikethrough
        text = re.sub(r'~~(.+?)~~', r'<strike>\1</strike>', text)
        # Inline code
        text = re.sub(r'`([^`]+)`', lambda m: '<font name="Courier">' + m.group(1) + '</font>', text)
        return text

    def _render_markdown(text, target_list):
        """
        Parse a plain-text block as Markdown and append ReportLab flowables
        to target_list.

        Supported block elements:
          # H1  ## H2  ### H3
          - / * / + unordered list
          1. ordered list
          > blockquote
          ``` fenced code block
          --- / *** / ___ horizontal rule
          blank line (paragraph break)

        Supported inline elements:
          **bold**  *italic*  ***bold-italic***
          __bold__  _italic_
          ~~strikethrough~~  `inline code`
        """
        lines = text.split('\n')
        i = 0
        pending = []  # accumulate consecutive plain-text lines into one paragraph

        def flush_pending():
            if not pending:
                return
            combined = '<br/>'.join(_inline_md(_esc(ln)) for ln in pending)
            target_list.append(Paragraph(combined, style_body))
            pending.clear()

        while i < len(lines):
            line = lines[i]

            # — Blank line → paragraph break
            if line.strip() == '':
                flush_pending()
                i += 1
                continue

            # — Horizontal rule: three or more -, *, or _ on their own line
            if re.match(r'^[-*_]{3,}\s*$', line.strip()):
                flush_pending()
                target_list.append(HRFlowable(width='100%', thickness=0.5, color=HexColor('#9ca3af')))
                target_list.append(Spacer(1, 4))
                i += 1
                continue

            # — ATX headings: #, ##, ###
            h_match = re.match(r'^(#{1,3})\s+(.*)', line)
            if h_match:
                flush_pending()
                level = len(h_match.group(1))
                content = _inline_md(_esc(h_match.group(2).strip()))
                style = {1: style_md_h1, 2: style_md_h2, 3: style_md_h3}[level]
                target_list.append(Paragraph(content, style))
                i += 1
                continue

            # — Fenced code block: ```
            if line.strip().startswith('```'):
                flush_pending()
                i += 1
                code_lines = []
                while i < len(lines) and not lines[i].strip().startswith('```'):
                    code_lines.append(_esc(lines[i]))
                    i += 1
                i += 1  # skip closing ```
                if code_lines:
                    target_list.append(Preformatted('\n'.join(
                        ln.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') for ln in code_lines
                    ), style_md_code_block))
                continue

            # — Blockquote: lines starting with >
            if line.startswith('>'):
                flush_pending()
                quote_lines = []
                while i < len(lines) and lines[i].startswith('>'):
                    stripped = lines[i][1:].lstrip(' ')
                    quote_lines.append(stripped)
                    i += 1
                combined = '<br/>'.join(_inline_md(_esc(ln)) for ln in quote_lines)
                target_list.append(Paragraph(combined, style_md_blockquote))
                continue

            # — Unordered list: -, *, or + followed by a space
            if re.match(r'^[-*+]\s', line):
                flush_pending()
                items = []
                while i < len(lines):
                    m = re.match(r'^[-*+]\s+(.*)', lines[i])
                    if m:
                        items.append(m.group(1))
                        i += 1
                    else:
                        break
                list_items = [
                    ListItem(Paragraph(_inline_md(_esc(it)), style_body), leftIndent=10)
                    for it in items
                ]
                target_list.append(ListFlowable(
                    list_items, bulletType='bullet', leftIndent=16, bulletFontSize=8,
                    spaceBefore=2, spaceAfter=4,
                ))
                continue

            # — Ordered list: digit(s) followed by . and a space
            if re.match(r'^\d+\.\s', line):
                flush_pending()
                items = []
                while i < len(lines):
                    m = re.match(r'^\d+\.\s+(.*)', lines[i])
                    if m:
                        items.append(m.group(1))
                        i += 1
                    else:
                        break
                list_items = [
                    ListItem(Paragraph(_inline_md(_esc(it)), style_body), leftIndent=10)
                    for it in items
                ]
                target_list.append(ListFlowable(
                    list_items, bulletType='1', leftIndent=16, bulletFontSize=8,
                    spaceBefore=2, spaceAfter=4,
                ))
                continue

            # — Plain text line: accumulate
            pending.append(line)
            i += 1

        flush_pending()

    elements = []

    # === TITLE BLOCK ===
    if minutes.committee:
        title_prefix = f"Committee Minutes: {minutes.committee.name}"
    else:
        title_prefix = "Chapter Minutes"
    elements.append(Paragraph(f"{title_prefix}: {minutes.title}", style_title))
    time_line = f"{minutes.date.strftime('%B %d, %Y')} &mdash; Called to Order: {minutes.start_time.strftime('%I:%M %p')}"
    if minutes.end_time:
        time_line += f" &mdash; Adjourned: {minutes.end_time.strftime('%I:%M %p')}"
    elements.append(Paragraph(time_line, style_subtitle))
    if minutes.committee:
        elements.append(Paragraph(f"Committee: {minutes.committee.name}", style_subtitle))
    if minutes.event:
        elements.append(Paragraph(f"Event: {minutes.event.title}", style_subtitle))
    elements.append(Spacer(1, 12))
    elements.append(HRFlowable(width="100%", thickness=1, color=HexColor('#d1d5db')))
    elements.append(Spacer(1, 8))

    # === ATTENDANCE SECTION ===
    if minutes.attendance_data:
        elements.append(Paragraph("ATTENDANCE", style_section_header))

        present = [a for a in minutes.attendance_data if a.get('status') == 'present']
        late = [a for a in minutes.attendance_data if a.get('status') == 'late']
        absent = [a for a in minutes.attendance_data if a.get('status') == 'absent']
        excused = [a for a in minutes.attendance_data if a.get('status') == 'excused']

        # Summary line
        summary_parts = [f"<b>Present:</b> {len(present)}"]
        if late:
            summary_parts.append(f"<b>Late:</b> {len(late)}")
        summary_parts.append(f"<b>Absent:</b> {len(absent)}")
        if excused:
            summary_parts.append(f"<b>Excused:</b> {len(excused)}")
        total = len(present) + len(late) + len(absent) + len(excused)
        summary_parts.append(f"<b>Total:</b> {total}")
        elements.append(Paragraph(" &nbsp;|&nbsp; ".join(summary_parts), style_body))

        # Attendance table - build columns
        att_columns = []
        att_headers = []
        if present:
            att_headers.append(Paragraph(f"<b>Present ({len(present)})</b>", style_attendance_name))
            att_columns.append([Paragraph(a.get('name', ''), style_attendance_name) for a in present])
        if late:
            att_headers.append(Paragraph(f"<b>Late ({len(late)})</b>", style_attendance_name))
            att_columns.append([Paragraph(a.get('name', ''), style_attendance_name) for a in late])
        if absent:
            att_headers.append(Paragraph(f"<b>Absent ({len(absent)})</b>", style_attendance_name))
            att_columns.append([Paragraph(a.get('name', ''), style_attendance_name) for a in absent])
        if excused:
            att_headers.append(Paragraph(f"<b>Excused ({len(excused)})</b>", style_attendance_name))
            att_columns.append([Paragraph(a.get('name', ''), style_attendance_name) for a in excused])

        if att_columns:
            # Pad columns to equal length
            max_rows = max(len(col) for col in att_columns)
            for col in att_columns:
                while len(col) < max_rows:
                    col.append(Paragraph("", style_attendance_name))

            # Build table data: header row + name rows
            table_data = [att_headers]
            for row_idx in range(max_rows):
                table_data.append([col[row_idx] for col in att_columns])

            num_cols = len(att_columns)
            col_width = (doc.width) / num_cols
            att_table = Table(table_data, colWidths=[col_width] * num_cols)
            att_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, 0), HexColor('#f3f4f6')),
                ('TEXTCOLOR', (0, 0), (-1, 0), HexColor('#1a1a1a')),
                ('FONTSIZE', (0, 0), (-1, -1), 9),
                ('TOPPADDING', (0, 0), (-1, -1), 3),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('GRID', (0, 0), (-1, 0), 0.5, HexColor('#d1d5db')),
                ('LINEBELOW', (0, 0), (-1, 0), 0.5, HexColor('#d1d5db')),
            ]))
            elements.append(att_table)

        elements.append(Spacer(1, 8))
        elements.append(HRFlowable(width="100%", thickness=1, color=HexColor('#d1d5db')))
        elements.append(Spacer(1, 8))

    # === MINUTES BODY ===
    elements.append(Paragraph("MINUTES", style_section_header))

    # Style for custom section headers (officer reports, etc.)
    style_custom_header = ParagraphStyle(
        'CustomSectionHeader',
        parent=styles['Heading3'],
        fontSize=12,
        leading=16,
        spaceBefore=4,
        spaceAfter=6,
        textColor=HexColor('#6b21a8'),
        borderWidth=0,
    )

    # Helper function to render a single section's content
    def render_section_content(section, target_list):
        if section.section_type == 'text':
            text = section.content.strip()
            if text:
                _render_markdown(text, target_list)

        elif section.section_type == 'motion' and hasattr(section, 'motion'):
            m = section.motion
            # Use indented paragraphs instead of a table wrapper to allow page breaks
            style_motion_header_indented = ParagraphStyle(
                'MotionHeaderIndented',
                parent=style_motion_header,
                leftIndent=12,
                borderPadding=0,
                borderColor=HexColor('#3b82f6'),
                borderWidth=0,
            )
            style_motion_detail_indented = ParagraphStyle(
                'MotionDetailIndented',
                parent=style_motion_detail,
                leftIndent=12,
            )

            target_list.append(Spacer(1, 4))
            # Add a visual indicator line for the motion block
            target_list.append(HRFlowable(width="100%", thickness=2, color=HexColor('#3b82f6')))
            target_list.append(Spacer(1, 4))
            target_list.append(Paragraph(f"MOTION: {m.get_motion_type_display()}", style_motion_header_indented))
            safe_text = m.motion_text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            target_list.append(Paragraph(f"<i>{safe_text}</i>", style_motion_detail_indented))
            target_list.append(Paragraph(f"<b>Author:</b> {m.get_author_display()}", style_motion_detail_indented))
            if m.received_second:
                target_list.append(Paragraph(f"<b>Seconded by:</b> {m.seconded_by_text}", style_motion_detail_indented))
            target_list.append(Paragraph(
                f"<b>Vote Method:</b> {m.get_vote_method_display()} &nbsp;&nbsp; <b>Result:</b> {m.get_result_display()}",
                style_motion_detail_indented
            ))
            if m.votes_for is not None:
                target_list.append(Paragraph(
                    f"<b>Votes:</b> {m.votes_for} for, {m.votes_against or 0} against, {m.votes_abstain or 0} abstain",
                    style_motion_detail_indented
                ))
            if m.caucus_held:
                caucus_info = f"<b>Caucus:</b> {m.get_caucus_type_display()}, {m.caucus_duration} minutes"
                if m.caucus_type == 'moderated' and m.speaker_time:
                    caucus_info += f", {m.speaker_time}s per speaker"
                target_list.append(Paragraph(caucus_info, style_motion_detail_indented))
            if m.context_notes:
                safe_notes = m.context_notes.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                target_list.append(Paragraph(f"<b>Notes:</b> {safe_notes}", style_motion_detail_indented))
            target_list.append(Spacer(1, 4))

    # Helper function to create a boxed section - returns list of flowables
    # that can flow across pages (no table wrapper)
    def create_section_box(header_title, box_content):
        box_elements = []
        # Header with visual indicator
        safe_title = header_title.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        box_elements.append(HRFlowable(width="100%", thickness=2, color=HexColor('#a855f7')))
        box_elements.append(Spacer(1, 4))

        # Create indented style for section header
        style_section_header_indented = ParagraphStyle(
            'SectionHeaderIndented',
            parent=style_custom_header,
            leftIndent=12,
        )

        box_elements.append(Paragraph(f"<b>{safe_title}</b>", style_section_header_indented))

        # Add indented content using Indenter flowable
        if box_content:
            box_elements.append(Indenter(left=12))
            box_elements.extend(box_content)
            box_elements.append(Indenter(left=-12))

        box_elements.append(Spacer(1, 4))
        return box_elements

    # Process sections with header/section_end pairing
    sections_list = list(minutes.sections.all().select_related('motion').order_by('order'))
    i = 0
    while i < len(sections_list):
        section = sections_list[i]

        if section.section_type == 'header':
            # Start collecting content for this section box
            header_title = section.title if hasattr(section, 'title') and section.title else section.content
            box_content = []
            i += 1

            # Collect content until we hit section_end or another header
            while i < len(sections_list):
                next_section = sections_list[i]
                if next_section.section_type == 'section_end':
                    i += 1  # Skip the section_end marker
                    break
                elif next_section.section_type == 'header':
                    # Another header - don't consume it, let the outer loop handle it
                    break
                else:
                    render_section_content(next_section, box_content)
                    i += 1

            # Render the boxed section
            if header_title:
                elements.append(Spacer(1, 8))
                elements.extend(create_section_box(header_title, box_content))
                elements.append(Spacer(1, 8))

        elif section.section_type == 'section_end':
            # Orphan section_end - skip it
            i += 1

        else:
            # Regular content outside a section box
            render_section_content(section, elements)
            i += 1

    # === EDIT HISTORY (if edited after publication) ===
    if minutes.edited_after_publish and minutes.last_edit_at:
        style_edit_notice = ParagraphStyle(
            'EditNotice',
            parent=styles['Normal'],
            fontSize=9,
            leading=12,
            textColor=HexColor('#b45309'),
        )
        elements.append(Spacer(1, 16))
        elements.append(HRFlowable(width="100%", thickness=1, color=HexColor('#fbbf24')))
        elements.append(Spacer(1, 8))
        elements.append(Paragraph(
            "<b>DOCUMENT EDITED AFTER PUBLICATION</b>",
            ParagraphStyle('EditHeader', parent=style_edit_notice, fontSize=10, textColor=HexColor('#92400e'))
        ))
        elements.append(Spacer(1, 4))

        edit_info = f"Last edited: {minutes.last_edit_at.strftime('%B %d, %Y at %I:%M %p')}"
        if minutes.last_edit_by:
            edit_info += f" by {minutes.last_edit_by.get_display_name()}"
        elements.append(Paragraph(edit_info, style_edit_notice))

        if minutes.last_edit_reason:
            safe_reason = minutes.last_edit_reason.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            elements.append(Paragraph(f"<b>Reason:</b> {safe_reason}", style_edit_notice))

    # === FOOTER ===
    elements.append(Spacer(1, 20))
    elements.append(HRFlowable(width="100%", thickness=1, color=HexColor('#d1d5db')))
    elements.append(Spacer(1, 8))
    elements.append(Paragraph(
        f"Minutes recorded by: {minutes.created_by.get_display_name()}",
        style_footer
    ))
    central_time = timezone.now().astimezone(ZoneInfo('America/Chicago'))
    elements.append(Paragraph(
        f"Downloaded: {central_time.strftime('%B %d, %Y at %I:%M %p')} CT",
        style_footer
    ))

    # Build PDF
    doc.build(elements)
    buf.seek(0)
    return buf


@login_required
@officer_required
def download_minutes_pdf(request, minutes_id):
    """Generate and download a formatted PDF of chapter minutes"""
    minutes = get_object_or_404(ChapterMinutes, id=minutes_id, committee__isnull=True)
    buf = generate_minutes_pdf_buffer(minutes)

    file_name = f"Chapter_Minutes_{minutes.date.strftime('%Y-%m-%d')}_{minutes.title.replace(' ', '_')}.pdf"
    response = HttpResponse(buf.read(), content_type='application/pdf')
    disposition = 'inline' if request.GET.get('preview') else 'attachment'
    response['Content-Disposition'] = f'{disposition}; filename="{file_name}"'
    return response


@login_required
@officer_required
@require_POST
def delete_chapter_minutes(request, minutes_id):
    """Delete chapter minutes (officers can delete drafts, admins can delete any)"""
    minutes = get_object_or_404(ChapterMinutes, id=minutes_id, committee__isnull=True)

    # Check permissions: officers can only delete drafts, admins can delete any
    is_admin = request.user.is_admin
    if minutes.status == 'published' and not is_admin:
        messages.error(request, 'Only administrators can delete published minutes.')
        return redirect('chapter_minutes_list')

    title = minutes.title
    minutes_date = minutes.date

    # If there's a linked published document, delete it too
    if minutes.published_document:
        minutes.published_document.delete()

    # Delete all related sections (cascades to motions)
    minutes.sections.all().delete()

    # Log the deletion
    ActivityLog.log_activity(
        action_type='other',
        user=request.user,
        description=f'Deleted chapter minutes: {title} ({minutes_date})',
        request=request,
        object_type='ChapterMinutes',
        object_id=minutes_id,
        object_repr=f'{title} ({minutes_date})',
    )

    # Delete the minutes
    minutes.delete()

    messages.success(request, f'Minutes "{title}" deleted successfully.')
    return redirect('chapter_minutes_list')
