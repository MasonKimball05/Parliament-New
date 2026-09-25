"""
Meeting agendas → minutes drafts (09-25-26, "meeting mode" first slice).

Officers (the same `officer_required` gate as chapter minutes) create and edit
an agenda, publish it for members, and start the minutes from it. Members can
read PUBLISHED agendas only. Every write is a plain form POST that redirects
back (no JS, nothing for CSP to block).

Behind feature flag `meeting_agendas` (seeded enabled; turn it off at
/admin-v2/ to hide the whole feature).
"""
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Max
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from src.decorators import officer_required
from src.permissions import user_is_officer_or_chair
from src.feature_flag_decorators import require_feature_flag
from src.models import ActivityLog, AgendaItem, ChapterMinutes, Event, MeetingAgenda, MinutesSection, ParliamentUser
from src.models.users import member_defer
from src.view.officer.chapter_minutes import _linkable_events

FLAG = 'meeting_agendas'
MAX_TITLE = 200
MAX_NOTES = 4000


def _parse(fn, value):
    """`fn(value.strip())`, or None for a blank or malformed value."""
    try:
        return fn((value or '').strip()) if (value or '').strip() else None
    except (TypeError, ValueError):
        return None


def _is_officer(user):
    return user_is_officer_or_chair(user)


def _items(agenda):
    return agenda.items.select_related('presenter').defer(*member_defer('presenter'))


@login_required
@require_feature_flag(FLAG)
def agenda_list(request):
    """Officers see every agenda (drafts too); members see published ones."""
    officer = _is_officer(request.user)
    qs = MeetingAgenda.objects.select_related('event', 'minutes')
    if not officer:
        qs = qs.filter(status='published')
    today = timezone.localdate()
    agendas = list(qs.order_by('-date', '-start_time')[:50])
    return render(request, 'meetings/agenda_list.html', {
        'upcoming': [a for a in agendas if a.date >= today][::-1],
        'past': [a for a in agendas if a.date < today],
        'is_officer': officer,
        # Same helper as the minutes create modal — it already fixed recurring
        # instances crowding tonight's meeting out of the dropdown (v3.29.3).
        'events': _linkable_events() if officer else [],
    })


@login_required
@require_feature_flag(FLAG)
def agenda_detail(request, agenda_id):
    agenda = get_object_or_404(MeetingAgenda.objects.select_related('event', 'minutes'), pk=agenda_id)
    officer = _is_officer(request.user)
    if agenda.status != 'published' and not officer:
        raise Http404
    return render(request, 'meetings/agenda_detail.html', {
        'agenda': agenda,
        'items': _items(agenda),
        'is_officer': officer,
    })


@login_required
@officer_required
@require_feature_flag(FLAG)
@require_POST
def create_agenda(request):
    title = (request.POST.get('title') or '').strip()[:MAX_TITLE]
    # 09-25-26 (auto-run finding) — date/time/event were passed to .create()
    # raw, so a malformed value raised ValidationError/ValueError → 500 (and,
    # since v3.35.0, an alert email). Parse them and reuse the form error.
    date = _parse(datetime.date.fromisoformat, request.POST.get('date'))
    start_time = _parse(datetime.time.fromisoformat, request.POST.get('start_time'))
    if not (title and date and start_time):
        messages.error(request, 'Title, a valid date and a valid start time are required.')
        return redirect('agenda_list')
    event = None
    event_id = _parse(int, request.POST.get('event'))
    if event_id:
        event = Event.objects.filter(pk=event_id).first()
    with transaction.atomic():
        agenda = MeetingAgenda.objects.create(title=title, date=date, start_time=start_time,
                                              event=event, created_by=request.user)
        if request.POST.get('standard_order') == 'on':
            labels = dict(AgendaItem.ITEM_TYPES)
            AgendaItem.objects.bulk_create([
                AgendaItem(agenda=agenda, order=i, item_type=t, title=labels[t])
                for i, t in enumerate(AgendaItem.STANDARD_ORDER)
            ])
    ActivityLog.log_activity(action_type='other', user=request.user, request=request,
                             description=f'Created meeting agenda: {title}',
                             object_type='MeetingAgenda', object_id=agenda.pk, object_repr=str(agenda))
    return redirect('edit_agenda', agenda_id=agenda.pk)


@login_required
@officer_required
@require_feature_flag(FLAG)
def edit_agenda(request, agenda_id):
    agenda = get_object_or_404(MeetingAgenda.objects.select_related('event', 'minutes'), pk=agenda_id)
    return render(request, 'meetings/agenda_edit.html', {
        'agenda': agenda,
        'items': _items(agenda),
        'item_types': AgendaItem.ITEM_TYPES,
        'members': ParliamentUser.objects.filter(is_active=True).defer(*member_defer()).order_by('name'),
    })


def _item_fields(request, item):
    t = request.POST.get('item_type') or item.item_type
    item.item_type = t if t in dict(AgendaItem.ITEM_TYPES) else 'custom'
    # Type first: a blank title falls back to the type's label.
    item.title = ((request.POST.get('title') or '').strip() or item.get_item_type_display())[:MAX_TITLE]
    item.notes = (request.POST.get('notes') or '').strip()[:MAX_NOTES]
    item.presenter_text = (request.POST.get('presenter_text') or '').strip()[:200]
    presenter_id = request.POST.get('presenter') or ''
    item.presenter = (ParliamentUser.objects.filter(pk=presenter_id, is_active=True).first()
                      if presenter_id else None)
    try:
        d = int(request.POST.get('duration_minutes') or 0)
        item.duration_minutes = d if 0 < d <= 600 else None
    except ValueError:
        item.duration_minutes = None


@login_required
@officer_required
@require_feature_flag(FLAG)
@require_POST
def add_agenda_item(request, agenda_id):
    agenda = get_object_or_404(MeetingAgenda, pk=agenda_id)
    nxt = (agenda.items.aggregate(m=Max('order'))['m'] or 0) + 1
    item = AgendaItem(agenda=agenda, order=nxt)
    _item_fields(request, item)
    item.save()
    return redirect('edit_agenda', agenda_id=agenda.pk)


@login_required
@officer_required
@require_feature_flag(FLAG)
@require_POST
def update_agenda_item(request, agenda_id, item_id):
    item = get_object_or_404(AgendaItem, pk=item_id, agenda_id=agenda_id)
    action = request.POST.get('action', 'save')
    if action == 'delete':
        item.delete()
    elif action in ('up', 'down'):
        siblings = list(item.agenda.items.order_by('order', 'pk'))
        i = siblings.index(item)
        j = i - 1 if action == 'up' else i + 1
        if 0 <= j < len(siblings):
            siblings[i], siblings[j] = siblings[j], siblings[i]
            for n, s in enumerate(siblings):
                if s.order != n:
                    s.order = n
                    s.save(update_fields=['order'])
    else:
        _item_fields(request, item)
        item.save()
    return redirect('edit_agenda', agenda_id=agenda_id)


@login_required
@officer_required
@require_feature_flag(FLAG)
@require_POST
def set_agenda_status(request, agenda_id):
    agenda = get_object_or_404(MeetingAgenda, pk=agenda_id)
    status = request.POST.get('status')
    if status in dict(MeetingAgenda.STATUS_CHOICES):
        agenda.status = status
        agenda.save(update_fields=['status', 'updated_at'])
        messages.success(request, 'Agenda published — members can now see it.' if status == 'published'
                         else 'Agenda moved back to draft.')
    return redirect('edit_agenda', agenda_id=agenda.pk)


@login_required
@officer_required
@require_feature_flag(FLAG)
@require_POST
def start_minutes_from_agenda(request, agenda_id):
    """
    Create a ChapterMinutes draft laid out like the agenda — one header per
    item, then a text block holding the presenter and notes — and open it in
    the existing minutes editor. Idempotent: a second click opens the same
    minutes rather than making another.
    """
    get_object_or_404(MeetingAgenda, pk=agenda_id)
    with transaction.atomic():
        # 09-25-26 (auto-run finding) — lock the row before the idempotency
        # check. Unlocked, a double-click could pass `if agenda.minutes_id`
        # twice and create two drafts, one orphaned.
        agenda = MeetingAgenda.objects.select_for_update().get(pk=agenda_id)
        if agenda.minutes_id:
            return redirect('edit_chapter_minutes', minutes_id=agenda.minutes_id)
        minutes = ChapterMinutes.objects.create(
            title=agenda.title, date=agenda.date, start_time=agenda.start_time,
            event=agenda.event, created_by=request.user, status='draft',
        )
        order = 0
        sections = []
        for item in _items(agenda):
            sections.append(MinutesSection(minutes=minutes, section_type='header', order=order, title=item.title))
            order += 1
            lines = []
            if item.presenter_display:
                lines.append(f'Presented by {item.presenter_display}.')
            if item.notes:
                lines.append(item.notes)
            sections.append(MinutesSection(minutes=minutes, section_type='text', order=order,
                                           content='\n'.join(lines)))
            order += 1
        if not sections:
            sections.append(MinutesSection(minutes=minutes, section_type='text', order=0, content=''))
        MinutesSection.objects.bulk_create(sections)
        agenda.minutes = minutes
        agenda.save(update_fields=['minutes', 'updated_at'])
    ActivityLog.log_activity(action_type='other', user=request.user, request=request,
                             description=f'Started minutes from agenda: {agenda.title}',
                             object_type='ChapterMinutes', object_id=minutes.pk, object_repr=str(minutes))
    messages.success(request, 'Minutes started from the agenda.')
    return redirect('edit_chapter_minutes', minutes_id=minutes.pk)
