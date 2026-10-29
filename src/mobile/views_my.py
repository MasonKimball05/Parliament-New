"""
The app's "about me" and documents screens (mobile plan, Phase 3c).

* ``GET my/attendance/?days=90`` — My Attendance: counts, rate, the chapter's
  rate over the same events, and one row per event with any excuse. ``days`` is
  30, 90, 180 or 365, or 0 for all time (the web page's range filter). Built by
  the web page's own ``attendance_overview``.
* ``GET my/service-hours/`` — the Service Hours dashboard: the current period's
  progress (``get_user_service_stats``), the member's submissions and manual
  adjustments.
* ``GET documents/`` — Chapter Documents: published documents grouped as the
  web page groups them (folders, then chapter-level, then by committee).
* ``GET documents/<id>/file/`` — the file itself, for the app's viewer.
  ``/media/`` needs a website session the app's requests don't have.
"""
import mimetypes
import os
from collections import defaultdict
from datetime import timedelta

from django.http import FileResponse
from django.utils import timezone
from rest_framework import exceptions
from rest_framework.response import Response

from src.feature_flag_decorators import check_feature_enabled
from src.mobile.api import MobileAPIView
from src.models import ChapterFolder, CommitteeDocument, ServiceHoursAdjustment, ServiceHoursSubmission
from src.models.users import member_defer
from src.utils.content_disposition import apply_disposition

DAYS = {30, 90, 180, 365, 0}
HISTORY_LIMIT = 200
SUBMISSION_LIMIT = 100


def _num(value):
    return float(value) if value is not None else None


class MyAttendanceView(MobileAPIView):
    web_page = 'my_attendance'
    web_page_toggle = False   # the web page has only the feature flags

    def get(self, request):
        from src.view.submit_excuse import attendance_overview
        if not (check_feature_enabled('attendance_tracking') and check_feature_enabled('event_attendance')):
            raise exceptions.PermissionDenied('Attendance tracking is off.', code='feature_disabled')
        try:
            days = int(request.query_params.get('days', 90))
        except ValueError:
            days = 90
        if days not in DAYS:
            days = 90
        now = timezone.now()
        overview = attendance_overview(request.user, now - timedelta(days=days) if days else None, now)
        history = []
        for row in overview['attendance_history'][:HISTORY_LIMIT]:
            event, excuse = row['event'], row['excuse']
            history.append({
                'event': {'id': event.id, 'title': event.title, 'date_time': event.date_time.isoformat()},
                'status': row['status'],
                'excuse': {'status': excuse.status} if excuse else None,
            })
        return Response({
            'days': days,
            'stats': overview['my_stats'],
            'chapter_average': overview['chapter_average'],
            'rate_difference': overview['rate_difference'],
            'history': history,
        })


class MyServiceHoursView(MobileAPIView):
    web_page = 'user_service_dashboard'
    web_page_toggle = False   # the web page has no @require_page_enabled

    def get(self, request):
        from src.view.service_user_dashboard import current_service_period, get_user_service_stats
        user = request.user
        period = current_service_period()
        progress = None
        adjustments = []
        if period:
            stats = get_user_service_stats(user, period)
            progress = {
                'expected_hours': _num(stats['expected_hours']),
                'approved_hours': _num(stats['approved_hours']),
                'pending_hours': _num(stats['pending_hours']),
                'remaining_hours': _num(stats['remaining_hours']),
                'progress_percent': stats['progress_percent'],
                'expectation_reason': (stats['expectation_override'] or {}).get('reason') or None,
            }
            adjustments = [{
                'hours': _num(a.hours),
                'reason': a.reason,
                'created_at': a.created_at.isoformat(),
            } for a in ServiceHoursAdjustment.objects.filter(member=user, period=period).order_by('-created_at')]
        submissions = (ServiceHoursSubmission.objects
                       .filter(submitted_by=user).exclude(status='draft')
                       .select_related('period', 'reviewed_by').defer(*member_defer('reviewed_by'))
                       .order_by('-submitted_at')[:SUBMISSION_LIMIT])
        return Response({
            'period': {
                'id': period.id, 'name': period.name,
                'start_date': period.start_date.isoformat(), 'end_date': period.end_date.isoformat(),
            } if period else None,
            'progress': progress,
            'adjustments': adjustments,
            'submissions': [{
                'id': s.id,
                'hours': _num(s.hours),
                'service_date': s.service_date.isoformat(),
                'organization': s.organization,
                'description': s.description,
                'status': s.status,
                'reviewer_notes': s.reviewer_notes or '',
                'period': s.period.name if s.period else '',
            } for s in submissions],
        })


def _document_json(doc):
    name = os.path.basename(doc.document.name or '')
    ext = os.path.splitext(name)[1].lstrip('.').lower()
    return {
        'id': doc.id,
        'title': doc.title,
        'description': doc.description or '',
        'type': doc.get_document_type_display(),
        'uploaded_at': doc.uploaded_at.isoformat() if doc.uploaded_at else None,
        'uploaded_by': doc.uploaded_by.get_display_name() if doc.uploaded_by else '',
        'file_name': name,
        'extension': ext,
        'file': f'documents/{doc.id}/file/',
    }


def _visible_documents(user):
    return [d for d in (CommitteeDocument.objects.filter(published_to_chapter=True)
                        .select_related('committee', 'uploaded_by', 'chapter_folder')
                        .defer(*member_defer('uploaded_by'))
                        .order_by('-uploaded_at'))
            if d.can_user_view(user)]


class DocumentListView(MobileAPIView):
    web_page = 'chapter_documents'

    def get(self, request):
        docs = _visible_documents(request.user)
        by_folder = defaultdict(list)
        chapter_level = []
        by_committee = defaultdict(list)
        for d in docs:
            if d.chapter_folder_id:
                by_folder[d.chapter_folder_id].append(d)
            elif d.committee_id:
                by_committee[d.committee].append(d)
            else:
                chapter_level.append(d)
        groups = []
        for folder in ChapterFolder.objects.order_by('name'):
            if by_folder.get(folder.id):
                groups.append({'name': folder.name, 'description': folder.description or '',
                               'documents': [_document_json(d) for d in by_folder[folder.id]]})
        if chapter_level:
            groups.append({'name': 'Chapter Documents', 'description': '',
                           'documents': [_document_json(d) for d in chapter_level]})
        for committee in sorted(by_committee, key=lambda c: c.name):
            groups.append({'name': committee.name, 'description': '',
                           'documents': [_document_json(d) for d in by_committee[committee]]})
        return Response({'groups': groups})


class DocumentFileView(MobileAPIView):
    web_page = 'chapter_documents'

    def get(self, request, document_id):
        doc = CommitteeDocument.objects.filter(pk=document_id, published_to_chapter=True).first()
        if doc is None or not doc.can_user_view(request.user) or not doc.document:
            raise exceptions.NotFound()
        try:
            handle = doc.document.open('rb')
        except (FileNotFoundError, OSError):
            raise exceptions.NotFound()
        name = os.path.basename(doc.document.name)
        content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        response = FileResponse(handle, content_type=content_type)
        apply_disposition(response, content_type, name)   # RFC 5987 name, nosniff
        response['Cache-Control'] = 'private, no-store'
        return response
