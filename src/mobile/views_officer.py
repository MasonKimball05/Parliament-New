"""
Officer tools in the app (mobile plan, Phase 3h): the parts of the officer
pages an officer reaches for on a phone. Officers, chairs and admins only
(``officer_required``'s rule, ``user_is_officer_or_chair``).

* ``GET officer/`` — the hub: pending excuses, scheduled announcements, and the
  recent events still waiting for attendance.
* Announcements (``manage_announcements``):
  ``GET/POST officer/announcements/`` — every announcement, newest first
  (``?page=``), and create one; ``GET/PATCH/DELETE officer/announcements/<id>/``.
  Through ``AnnouncementForm`` itself. ``send_email`` on create sends the same
  email ``send_announcement_notification`` sends (or, when scheduled, when it
  publishes). Polls, linked documents, GroupMe and the email recipient picker
  stay on the website, and an edit from the app leaves them as they were.
  ``GET officer/announcements/recipients/?visible_to=`` — how many members
  the email would reach, for the app's confirmation before posting.
* Events (``create_event`` / ``edit_event`` / ``delete_event``):
  ``POST officer/events/``; ``GET/PATCH/DELETE officer/events/<id>/``. Through
  ``EventForm``, starting from the event's own values, so the settings the app
  doesn't show (recurrence, reminders' second slot, the check-in area, GroupMe)
  stay as they are. A series edit or delete takes ``scope`` like the web form.
  The app creates single events; repeating ones are made on the website.
* Excuses (``review_excuses``): ``GET officer/excuses/?status=``,
  ``POST officer/excuses/<id>/`` ``{action: approve|deny, notes}`` —
  ``AttendanceExcuse.approve/deny``, which notify the member.
* Attendance (``mark_event_attendance``): ``GET/POST
  officer/events/<id>/attendance/`` — the roster, and
  ``{statuses: {user_id: present|late|absent}, excused: {user_id: reason},
  finalize}``. Read-only once finalized.
"""
from datetime import timedelta

from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.forms.announcements import AnnouncementForm
from src.forms.events import EventForm
from src.mobile.api import MobileAPIView
from src.mobile.views_directory import photo_path
from src.mobile.views_excuses_polls import BadRequest
from src.models import (
    ActivityLog, Announcement, Attendance, AttendanceExcuse, Committee, Event,
)
from src.models.users import member_defer
from src.permissions import user_is_officer_or_chair

PAGE_SIZE = 25
ATTENDANCE_LOOKBACK = timedelta(days=14)
MARKS = ('present', 'late', 'absent')
NOTES_MAX = 1000
REASON_MAX = 500


def _iso(dt):
    return dt.isoformat() if dt else None


def _form_data(form_class, instance=None):
    """The values a form would show for `instance` (or a new object), as form data.

    An edit from the app changes only the fields it sends; everything else is
    posted back as it was, so a field the app has no control for is not
    cleared by the ModelForm (an unticked checkbox, an empty multi-select).
    """
    form = form_class(instance=instance) if instance is not None else form_class()
    return {name: form[name].value() for name in form.fields}


def _form_error(form):
    fields = {name: [str(e) for e in errs] for name, errs in form.errors.items()}
    first = next(iter(fields.values()))[0] if fields else 'Check the form.'
    label = next(iter(fields), '__all__')
    if label != '__all__' and label in form.fields:
        first = f'{form.fields[label].label or label}: {first}'
    raise BadRequest(first, code='invalid')


def _apply(data, body, allowed):
    for key in allowed:
        if key in body:
            data[key] = body[key]


class _OfficerView(MobileAPIView):
    web_page_toggle = False   # the officer pages have no page toggles

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not user_is_officer_or_chair(request.user):
            raise exceptions.PermissionDenied('Officers and chairs only.', code='forbidden')


def _attendance_on():
    return check_feature_enabled('attendance_tracking') and check_feature_enabled('event_attendance')


def _excuses_on():
    return check_feature_enabled('attendance_tracking') and check_feature_enabled('excuse_system')


def _awaiting_attendance(now):
    return (Event.objects
            .filter(requires_attendance=True, attendance_finalized=False, archived=False,
                    date_time__lte=now + timedelta(hours=2), date_time__gte=now - ATTENDANCE_LOOKBACK)
            .order_by('-date_time'))


class OfficerHubView(_OfficerView):
    web_page = 'officer_home'

    def get(self, request):
        now = timezone.now()
        attendance = list(_awaiting_attendance(now)[:10]) if _attendance_on() else []
        return Response({
            'pending_excuses': (AttendanceExcuse.objects.filter(status='pending').count()
                                if _excuses_on() else None),
            'scheduled_announcements': Announcement.objects.filter(publish_at__gt=now).count(),
            'attendance': [{'id': e.id, 'title': e.title, 'date_time': e.date_time.isoformat()}
                           for e in attendance],
            'features': {'attendance': _attendance_on(), 'excuses': _excuses_on()},
        })


# ── Announcements ────────────────────────────────────────────────────────────

ANNOUNCEMENT_FIELDS = ('title', 'content', 'publish_at', 'event_date', 'visible_to', 'is_active')


def _announcement_json(a, stats=None):
    now = timezone.now()
    status = ('inactive' if not a.is_active
              else 'scheduled' if a.publish_at and a.publish_at > now else 'live')
    out = {
        'id': a.id,
        'title': a.title,
        'content': a.content,
        'posted_at': a.posted_at.isoformat(),
        'posted_by': a.posted_by.get_display_name(),
        'publish_at': _iso(a.publish_at),
        'event_date': _iso(a.event_date),
        'visible_to': list(a.visible_to or []),
        'is_active': a.is_active,
        'status': status,
        'has_poll': hasattr(a, 'poll') and a.poll is not None,
        'web_path': reverse('edit_announcement', args=[a.id]),
    }
    if stats is not None:
        out['views'] = {'seen': stats['total_views'], 'audience': stats['target_audience']}
    return out


def _audiences():
    return [{'value': v, 'label': label} for v, label in Announcement.MEMBER_TYPES]


class OfficerAnnouncementListView(_OfficerView):
    web_page = 'manage_announcements'

    def get(self, request):
        qs = Announcement.annotate_view_stats(
            Announcement.objects.select_related('posted_by', 'poll').defer(*member_defer('posted_by'))
        ).order_by('-posted_at')
        page = Paginator(qs, PAGE_SIZE).get_page(request.query_params.get('page'))
        rows = list(page)
        counts = Announcement.active_counts_by_member_type()
        for a in rows:
            a._active_counts_by_type = counts
        Announcement.ensure_target_audience_snapshots(rows)
        return Response({
            'announcements': [_announcement_json(a, a.get_view_stats()) for a in rows],
            'page': page.number,
            'pages': page.paginator.num_pages,
            'audiences': _audiences(),
        })

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        data = _form_data(AnnouncementForm)
        data['is_active'] = True
        _apply(data, body, ANNOUNCEMENT_FIELDS)
        form = AnnouncementForm(data)
        if not form.is_valid():
            _form_error(form)
        send_email = bool(body.get('send_email'))
        announcement = form.save(commit=False)
        announcement.posted_by = request.user
        if send_email and not announcement.is_published():
            announcement.send_email_on_publish = True
        announcement.save()
        emailed = None
        if send_email and announcement.is_published():
            from src.notifications import send_announcement_notification
            emailed = send_announcement_notification(announcement, initiated_by=request.user)
        return Response({'announcement': _announcement_json(announcement), 'emailed': emailed}, status=201)


class OfficerAnnouncementRecipientsView(_OfficerView):
    """How many members an announcement email would reach (`?visible_to=` repeated, none = everyone)."""
    web_page = 'create_announcement'

    def get(self, request):
        from src.notifications import announcement_email_recipients
        allowed = {v for v, _ in Announcement.MEMBER_TYPES}
        visible_to = [v for v in request.query_params.getlist('visible_to') if v in allowed]
        return Response({'count': announcement_email_recipients(visible_to).count()})


class OfficerAnnouncementView(_OfficerView):
    web_page = 'edit_announcement'

    def _get(self, announcement_id):
        return get_object_or_404(Announcement.objects.select_related('posted_by', 'poll'), pk=announcement_id)

    def get(self, request, announcement_id):
        a = self._get(announcement_id)
        return Response({'announcement': _announcement_json(a, a.get_view_stats()), 'audiences': _audiences()})

    def patch(self, request, announcement_id):
        a = self._get(announcement_id)
        body = request.data if isinstance(request.data, dict) else {}
        data = _form_data(AnnouncementForm, a)
        _apply(data, body, ANNOUNCEMENT_FIELDS)
        form = AnnouncementForm(data, instance=a)
        if not form.is_valid():
            _form_error(form)
        form.save()
        return Response({'announcement': _announcement_json(a)})

    def delete(self, request, announcement_id):
        self._get(announcement_id).delete()
        return Response({'ok': True})


# ── Events ───────────────────────────────────────────────────────────────────

EVENT_FIELDS = ('title', 'description', 'date_time', 'location', 'visible_to', 'is_active',
                'requires_attendance', 'allow_excuses', 'excuse_deadline',
                'requires_signup', 'max_signups', 'signups_open', 'allow_waitlist',
                'reminder_1_enabled', 'reminder_1_hours_before')


def _event_body(body):
    """The app's names → EventForm's (a committee goes by code)."""
    out = {k: body[k] for k in EVENT_FIELDS if k in body}
    if 'committee' in body:
        code = body['committee']
        if code:
            committee = Committee.objects.filter(code=code, is_active=True).first()
            if committee is None:
                raise BadRequest('That committee isn’t active.', code='invalid')
            out['committee'] = committee.pk
        else:
            out['committee'] = None
    return out


def _event_json(e):
    from src.event_series import scope_counts
    return {
        'id': e.id,
        'title': e.title,
        'description': e.description,
        'date_time': e.date_time.isoformat(),
        'location': e.location or '',
        'visible_to': list(e.visible_to or []),
        'is_active': e.is_active,
        'committee': e.committee.code if e.committee_id else None,
        'requires_attendance': e.requires_attendance,
        'allow_excuses': e.allow_excuses,
        'excuse_deadline': _iso(e.excuse_deadline),
        'requires_signup': e.requires_signup,
        'max_signups': e.max_signups,
        'signups_open': e.signups_open,
        'allow_waitlist': e.allow_waitlist,
        'reminder_1_enabled': e.reminder_1_enabled,
        'reminder_1_hours_before': e.reminder_1_hours_before,
        'attendance_finalized': e.attendance_finalized,
        'is_recurring': bool(e.is_recurring or e.parent_event_id),
        'scope_counts': scope_counts(e),
        'web_path': reverse('edit_event', args=[e.id]),
    }


def _event_options():
    return {
        'audiences': [{'value': v, 'label': label} for v, label in Event.MEMBER_TYPES],
        'committees': [{'code': c.code, 'name': c.name}
                       for c in Committee.objects.filter(is_active=True, code__gt='').order_by('name')],
    }


class OfficerEventCreateView(_OfficerView):
    web_page = 'create_event'

    def get(self, request):
        return Response(_event_options())

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        data = _form_data(EventForm)
        data.update(_event_body(body))
        data['is_recurring'] = False      # repeating events are made on the website
        form = EventForm(data)
        if not form.is_valid():
            _form_error(form)
        event = form.save(commit=False)
        event.created_by = request.user
        event.save()
        return Response({'event': _event_json(event)}, status=201)


class OfficerEventView(_OfficerView):
    web_page = 'edit_event'

    def _get(self, event_id):
        return get_object_or_404(Event.objects.select_related('committee'), pk=event_id)

    def get(self, request, event_id):
        return Response({'event': _event_json(self._get(event_id)), **_event_options()})

    def patch(self, request, event_id):
        from src.event_series import SCOPE_THIS, affected_events, apply_series_edit, clean_scope, scope_counts
        event = self._get(event_id)
        body = request.data if isinstance(request.data, dict) else {}
        scope = clean_scope(body.get('scope')) if scope_counts(event) else SCOPE_THIS
        before = {'date_time': event.date_time, 'excuse_deadline': event.excuse_deadline}
        data = _form_data(EventForm, event)
        data.update(_event_body(body))
        form = EventForm(data, instance=event)
        if not form.is_valid():
            _form_error(form)
        with transaction.atomic():
            form.save()
            # edit_event's own steps, minus the GroupMe boxes (left as they were).
            affected_ids = list(affected_events(event, scope, pivot=before['date_time'])
                                .values_list('pk', flat=True).distinct())
            others = apply_series_edit(event, before, set(form.changed_data), scope)
            if event.date_time != before['date_time']:
                Event.objects.filter(pk__in=affected_ids, date_time__gt=timezone.now()).update(
                    reminder_1_sent_at=None, reminder_2_sent_at=None, groupme_reminded_at=None)
        event.refresh_from_db()
        return Response({'event': _event_json(event), 'others_updated': others})

    def delete(self, request, event_id):
        from src.event_series import SCOPE_THIS, clean_scope, delete_series_events, scope_counts
        event = self._get(event_id)
        scope = clean_scope(request.query_params.get('scope')) if scope_counts(event) else SCOPE_THIS
        deleted, kept = delete_series_events(event, scope)
        return Response({'deleted': deleted, 'kept_finalized': kept})


# ── Excuses ──────────────────────────────────────────────────────────────────

def _excuse_json(x):
    return {
        'id': x.id,
        'status': x.status,
        'reason': x.reason,
        'submitted_at': x.submitted_at.isoformat(),
        'member': {'user_id': x.user.user_id, 'name': x.user.get_display_name(), 'photo': photo_path(x.user)},
        'event': {'id': x.event_id, 'title': x.event.title, 'date_time': x.event.date_time.isoformat()},
        'reviewed_by': x.reviewed_by.get_display_name() if x.reviewed_by_id else None,
        'review_notes': x.review_notes or '',
        'document_path': reverse('excuse_document', args=[x.id]) if x.supporting_document else None,
    }


class OfficerExcuseListView(_OfficerView):
    web_page = 'review_excuses'

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _excuses_on():
            raise exceptions.PermissionDenied('Excuses are off.', code='feature_disabled')

    def get(self, request):
        status = request.query_params.get('status', 'pending')
        qs = (AttendanceExcuse.objects.select_related('user', 'event', 'reviewed_by')
              .defer(*member_defer('user'), *member_defer('reviewed_by')))
        if status in ('pending', 'approved', 'denied'):
            qs = qs.filter(status=status)
        order = 'event__date_time' if status == 'pending' else '-submitted_at'
        counts = {s: AttendanceExcuse.objects.filter(status=s).count() for s in ('pending', 'approved', 'denied')}
        return Response({'excuses': [_excuse_json(x) for x in qs.order_by(order)[:100]], 'counts': counts})


class OfficerExcuseReviewView(OfficerExcuseListView):

    def post(self, request, excuse_id):
        excuse = get_object_or_404(AttendanceExcuse.objects.select_related('user', 'event'), pk=excuse_id)
        body = request.data if isinstance(request.data, dict) else {}
        action = body.get('action')
        notes = str(body.get('notes') or '')[:NOTES_MAX]
        if action not in ('approve', 'deny'):
            raise BadRequest('Approve or deny.', code='invalid')
        (excuse.approve if action == 'approve' else excuse.deny)(request.user, notes)
        ActivityLog.log_activity(
            action_type='other', user=request.user,
            description=f'{"Approved" if action == "approve" else "Denied"} excuse for '
                        f'{excuse.user.name} for {excuse.event.title}',
            request=request, object_type='AttendanceExcuse', object_id=excuse.id, object_repr=str(excuse))
        excuse.refresh_from_db()
        return Response({'excuse': _excuse_json(excuse)})


# ── Attendance ───────────────────────────────────────────────────────────────

class OfficerAttendanceView(_OfficerView):
    web_page = 'mark_event_attendance'

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _attendance_on():
            raise exceptions.PermissionDenied('Attendance is off.', code='feature_disabled')

    def _event(self, event_id):
        return get_object_or_404(Event.objects.select_related('committee'), pk=event_id, requires_attendance=True)

    def _roster(self, event):
        members = list(event.required_members().order_by('name'))
        records = {a.user_id: a for a in Attendance.objects.filter(event=event, attendance_type='event')}
        excuses = {x.user_id: x for x in AttendanceExcuse.objects.filter(event=event)}
        rows = []
        for m in members:
            record, excuse = records.get(m.user_id), excuses.get(m.user_id)
            approved = excuse is not None and excuse.status == 'approved'
            rows.append({
                'user_id': m.user_id,
                'name': m.get_display_name(),
                'photo': photo_path(m),
                'status': record.status if record else ('excused' if approved else 'pending'),
                'note': (record.notes or '') if record else '',
                'excuse': ({'status': excuse.status, 'reason': excuse.reason} if excuse else None),
                'locked': approved,          # an approved excuse is never overwritten here
            })
        return members, rows

    def _response(self, event):
        _, rows = self._roster(event)
        if hasattr(event, '_attendance_stats_cache'):
            del event._attendance_stats_cache
        return Response({
            'event': {'id': event.id, 'title': event.title, 'date_time': event.date_time.isoformat(),
                      'finalized': event.attendance_finalized},
            'members': rows,
            'stats': event.get_attendance_stats(),
        })

    def get(self, request, event_id):
        return self._response(self._event(event_id))

    def post(self, request, event_id):
        event = self._event(event_id)
        if event.attendance_finalized:
            raise BadRequest('Attendance for this event is finalized.', code='finalized')
        body = request.data if isinstance(request.data, dict) else {}
        statuses = body.get('statuses') or {}
        excused = body.get('excused') or {}
        if not isinstance(statuses, dict) or not isinstance(excused, dict):
            raise BadRequest('statuses and excused are maps of user id.', code='invalid')
        members, rows = self._roster(event)
        locked = {r['user_id'] for r in rows if r['locked']}
        now = timezone.now()
        updated = 0
        with transaction.atomic():
            for m in members:
                if m.user_id in locked:
                    continue
                reason = str(excused.get(m.user_id) or '').strip()[:REASON_MAX]
                if reason:
                    defaults = {'status': 'excused', 'notes': f'Officer excused: {reason}'}
                elif statuses.get(m.user_id) in MARKS:
                    defaults = {'status': statuses[m.user_id]}
                else:
                    continue
                Attendance.objects.update_or_create(
                    event=event, user=m, attendance_type='event',
                    defaults={**defaults, 'marked_by': request.user, 'marked_at': now})
                updated += 1
            if updated:
                ActivityLog.log_activity(
                    action_type='attendance_taken', user=request.user,
                    description=f'Marked attendance for {event.title} ({updated} members)',
                    request=request, object_type='Event', object_id=event.id, object_repr=str(event))
            if body.get('finalize'):
                event.attendance_finalized = True
                event.finalized_by = request.user
                event.finalized_at = now
                event.save()
                ActivityLog.log_activity(
                    action_type='attendance_taken', user=request.user,
                    description=f'Finalized attendance for {event.title}',
                    request=request, object_type='Event', object_id=event.id, object_repr=str(event))
        return self._response(event)
