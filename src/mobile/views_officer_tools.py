"""
More of the officer portal in the app (10-30-26), each the website page's own
data and rules:

* ``GET officer/attendance-events/`` — ``event_attendance_list``: events that
  take attendance, upcoming (not finalized) and the last 20 past, with counts.
  Opens the existing roster (``officer/events/<id>/attendance/``).
* ``GET officer/stats/?days=`` — ``chapter_stats``: attendance, voting,
  service hours and recruitment, from ``chapter_stats_context``.
* ``GET officer/members/`` — ``user_list``: everyone (not removed) with status,
  type, roll number, email, phone, roles and last sign-in. The app filters and
  sorts on the phone, as the web page does with its query string.
* ``GET officer/contact/`` — ``contact_submissions_view``: the website's
  contact form messages; ``POST officer/contact/<id>/read/`` and
  ``POST officer/contact/read-all/`` mark them read.
* ``GET officer/reports/`` — ``view_all_reports``: every committee document,
  published or not, newest first; ``GET officer/reports/<id>/file/`` serves one.

Contact messages and reports follow ``officer_or_advisor_required`` (advisors
too), like the web; the rest ``officer_required`` (officers and chairs).
"""
import mimetypes
import os

from django.db.models import Count
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView
from src.mobile.views_directory import photo_path
from src.models import CommitteeDocument, ContactSubmission, Event, ParliamentUser
from src.models.users import member_defer
from src.permissions import user_is_officer_or_chair
from src.utils.content_disposition import apply_disposition


class _Officers(MobileAPIView):
    web_page_toggle = False

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not user_is_officer_or_chair(request.user):
            raise exceptions.PermissionDenied('Officers and chairs only.', code='forbidden')


class _OfficersAndAdvisors(MobileAPIView):
    web_page_toggle = False

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.can_view_officer_pages:
            raise exceptions.PermissionDenied('Officers and advisors only.', code='forbidden')


# ── Attendance events ────────────────────────────────────────────────────────

def _stats_json(stats):
    if not stats:
        return None
    return {k: v for k, v in stats.items() if isinstance(v, (int, float, str, bool)) or v is None}


class AttendanceEventsView(_Officers):
    web_page = 'event_attendance_list'

    def get(self, request):
        if not (check_feature_enabled('attendance_tracking') and check_feature_enabled('event_attendance')):
            raise exceptions.PermissionDenied('Attendance tracking is off.', code='feature_disabled')
        from django.db.models import Q
        now = timezone.now()
        events = (Event.objects.filter(requires_attendance=True)
                  .select_related('committee').order_by('-date_time'))
        upcoming = list(events.filter(date_time__gte=now, attendance_finalized=False).order_by('date_time'))
        past = Event.prime_attendance_stats(events.filter(Q(date_time__lt=now) | Q(attendance_finalized=True))[:20])

        def row(e, stats=True):
            return {
                'id': e.id,
                'title': e.title,
                'date_time': e.date_time.isoformat(),
                'committee': e.committee.name if e.committee_id else None,
                'finalized': e.attendance_finalized,
                'timed': bool(e.checkin_min_minutes),
                'stats': _stats_json(e.get_attendance_stats()) if stats else None,
            }
        return Response({'upcoming': [row(e, stats=False) for e in upcoming], 'past': [row(e) for e in past]})


# ── Chapter stats ────────────────────────────────────────────────────────────

class ChapterStatsView(_Officers):
    web_page = 'chapter_stats'

    def get(self, request):
        from src.view.officer.chapter_stats import STATS_DAY_OPTIONS, chapter_stats_context, clean_days
        c = chapter_stats_context(clean_days(request.query_params.get('days')))
        service = c['service_stats']
        recruitment = c['recruitment_stats']
        return Response({
            'days': c['days'],
            'day_options': list(STATS_DAY_OPTIONS),
            'active_count': c['active_count'],
            'attendance': {
                'meeting_count': c['meeting_count'],
                'rate': c['attendance_rate'],
                'meetings': [{
                    'id': m['event'].id, 'title': m['event'].title,
                    'date_time': m['event'].date_time.isoformat(),
                    'present': m['present'], 'excused': m['excused'], 'absent': m['absent'],
                    'total': m['total'], 'rate': m['rate'],
                } for m in c['meeting_breakdown']],
                'at_risk': [{
                    'user_id': r['member'].user_id, 'name': r['member'].get_display_name(),
                    'present': r['present'], 'total': r['total'], 'rate': r['rate'],
                } for r in c['at_risk_attendance']],
            },
            'voting': {
                'count': c['legislation_count'],
                'rate': c['overall_vote_rate'],
                'votes': [{
                    'id': v['legislation'].id, 'title': v['legislation'].title,
                    'cast': v['cast'], 'possible': v['possible'], 'rate': v['rate'],
                } for v in c['vote_breakdown']],
            },
            'service': {
                'period': service['period'].name,
                'required_hours': service['required_hours'],
                'members_met': service['members_met'],
                'members_total': service['members_total'],
                'rate': service['completion_rate'],
                'members': [{
                    'user_id': r['member'].user_id, 'name': r['member'].get_display_name(),
                    'hours': r['hours'], 'required': r['required'], 'met': r['met'],
                } for r in c['service_breakdown']],
            } if service else None,
            'recruitment': recruitment,
        })


# ── Member list ──────────────────────────────────────────────────────────────

class MemberListView(_Officers):
    web_page = 'user_list'
    web_page_toggle = True

    def get(self, request):
        users = (ParliamentUser.objects.exclude(member_status='Removed')
                 .prefetch_related('roles').order_by('name'))
        return Response({
            'members': [{
                'user_id': u.user_id,
                'name': u.get_display_name(),
                'username': u.username,
                'role_number': u.role_number or '',
                'member_type': u.member_type,
                'member_status': u.member_status,
                'email': u.email or '',
                'phone': u.phone_number or '',
                'roles': [r.name for r in u.roles.all()],
                'photo': photo_path(u),
                'last_login': u.last_login.isoformat() if u.last_login else None,
            } for u in users],
            'types': [t for t, _ in ParliamentUser.MEMBER_TYPES],
            'statuses': [s for s, _ in ParliamentUser.MEMBER_STATUS if s != 'Removed'],
        })


# ── Contact messages ─────────────────────────────────────────────────────────

def _contact_json(c):
    return {'id': c.id, 'name': c.name, 'email': c.email, 'topic': c.topic, 'message': c.message,
            'submitted_at': c.submitted_at.isoformat(), 'is_read': c.is_read}


class ContactListView(_OfficersAndAdvisors):
    web_page = 'officer_home'
    web_page_toggle = True

    def get(self, request):
        rows = ContactSubmission.objects.order_by('-submitted_at')[:200]
        return Response({'messages': [_contact_json(c) for c in rows],
                         'unread': ContactSubmission.objects.filter(is_read=False).count()})


class ContactReadView(_OfficersAndAdvisors):
    web_page = 'officer_home'

    def post(self, request, pk):
        c = get_object_or_404(ContactSubmission, pk=pk)
        if not c.is_read:
            c.is_read = True
            c.save(update_fields=['is_read'])
        return Response({'message': _contact_json(c)})


class ContactReadAllView(_OfficersAndAdvisors):
    web_page = 'officer_home'

    def post(self, request):
        return Response({'marked': ContactSubmission.objects.filter(is_read=False).update(is_read=True)})


# ── Reports and documents ────────────────────────────────────────────────────

#: Same ceiling as the website page (view_all_reports.DOCUMENT_FETCH_LIMIT).
REPORT_LIMIT = 500


class ReportListView(_OfficersAndAdvisors):
    web_page = 'view_all_reports'

    def get(self, request):
        docs = (CommitteeDocument.objects.select_related('committee', 'uploaded_by')
                .defer(*member_defer('uploaded_by')).order_by('-uploaded_at')[:REPORT_LIMIT])
        totals = dict(CommitteeDocument.objects.values_list('document_type').annotate(n=Count('pk')))
        return Response({
            'documents': [{
                'id': d.id,
                'title': d.title,
                'description': d.description or '',
                'type': d.document_type,
                'type_label': d.get_document_type_display(),
                'committee': d.committee.name if d.committee_id else None,
                'uploaded_at': d.uploaded_at.isoformat() if d.uploaded_at else None,
                'uploaded_by': d.uploaded_by.get_display_name() if d.uploaded_by_id else '',
                'published': d.published_to_chapter,
                'file_name': os.path.basename(d.document.name) if d.document else '',
                'extension': os.path.splitext(d.document.name)[1].lstrip('.').lower() if d.document else '',
                'file': f'officer/reports/{d.id}/file/',
            } for d in docs],
            'totals': totals,
            'truncated': sum(totals.values()) > len(docs),
        })


class ReportFileView(_OfficersAndAdvisors):
    web_page = 'view_all_reports'

    def get(self, request, document_id):
        doc = CommitteeDocument.objects.filter(pk=document_id).first()
        if doc is None or not doc.document:
            raise exceptions.NotFound()
        try:
            handle = doc.document.open('rb')
        except (FileNotFoundError, OSError):
            raise exceptions.NotFound()
        name = os.path.basename(doc.document.name)
        content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        response = FileResponse(handle, content_type=content_type)
        apply_disposition(response, content_type, name)
        response['Cache-Control'] = 'private, no-store'
        return response
