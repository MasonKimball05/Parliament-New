"""
Mobile plan, Phase 3c — My Attendance, Service Hours and Chapter Documents.

Each endpoint must give the app the numbers the web page shows (they share
attendance_overview / get_user_service_stats), under the same flags, toggles
and document visibility.

    python manage.py test src.tests.mobile.test_my_and_documents
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src.models import (
    Attendance, AttendanceExcuse, ChapterFolder, CommitteeDocument, Event, ServiceHoursAdjustment,
    ServiceHoursSubmission, ServicePeriod,
)
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag, restrict, toggle

API = '/api/mobile/v1/'


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('my-1')
        _, self.access, _ = make_device(self.user)
        self.officer = make_user('my-off', member_type='Officer')
        self.api = Client()

    def get(self, path, access=None, **params):
        return self.api.get(API + path, params, **bearer(access or self.access))


class AttendanceTests(_Base):

    def meeting(self, days_ago, status=None):
        e = Event.objects.create(title=f'Meeting {days_ago}', description='d', created_by=self.officer,
                                 requires_attendance=True, date_time=timezone.now() - timedelta(days=days_ago))
        if status:
            Attendance.objects.create(event=e, user=self.user, attendance_type='event', status=status,
                                      marked_by=self.officer)
        return e

    def test_counts_rate_and_history(self):
        self.meeting(1, 'present')
        self.meeting(3, 'late')
        absent = self.meeting(5, 'absent')
        self.meeting(7)                                     # not marked
        AttendanceExcuse.objects.create(event=absent, user=self.user, reason='Exam', status='pending')
        body = self.get('my/attendance/').json()
        self.assertEqual(body['stats']['total'], 3)
        self.assertEqual(body['stats']['attendance_rate'], 66.7)
        self.assertEqual([h['status'] for h in body['history']], ['present', 'late', 'absent', 'not_marked'])
        self.assertEqual(body['history'][2]['excuse'], {'status': 'pending'})

    def test_range(self):
        self.meeting(10, 'present')
        self.meeting(100, 'absent')
        self.assertEqual(self.get('my/attendance/', days=30).json()['stats']['total'], 1)
        self.assertEqual(self.get('my/attendance/', days=0).json()['stats']['total'], 2)
        self.assertEqual(self.get('my/attendance/', days='junk').json()['days'], 90)

    def test_flag_off(self):
        flag('event_attendance', False)
        self.assertEqual(self.get('my/attendance/').json()['error'], 'feature_disabled')


class ServiceHoursTests(_Base):

    def test_progress_submissions_and_adjustments(self):
        today = timezone.localdate()
        period = ServicePeriod.objects.create(name='Fall', start_date=today - timedelta(days=10),
                                              end_date=today + timedelta(days=10), is_active=True,
                                              default_hours_required=Decimal('10'))
        ServiceHoursSubmission.objects.create(period=period, submitted_by=self.user, hours=Decimal('4'),
                                              service_date=today, organization='Food bank',
                                              description='Sorting', status='approved')
        ServiceHoursSubmission.objects.create(period=period, submitted_by=self.user, hours=Decimal('2'),
                                              service_date=today, organization='Park',
                                              description='Cleanup', status='pending')
        ServiceHoursSubmission.objects.create(period=period, submitted_by=self.user, hours=Decimal('1'),
                                              service_date=today, organization='Draft', description='x',
                                              status='draft')
        ServiceHoursAdjustment.objects.create(member=self.user, period=period, hours=Decimal('1'),
                                              reason='Retreat', adjusted_by=self.officer)
        body = self.get('my/service-hours/').json()
        self.assertEqual(body['period']['name'], 'Fall')
        self.assertEqual(body['progress']['approved_hours'], 5.0)
        self.assertEqual(body['progress']['pending_hours'], 2.0)
        self.assertEqual(body['progress']['progress_percent'], 50)
        self.assertEqual([s['organization'] for s in body['submissions']], ['Park', 'Food bank'])
        self.assertEqual(body['adjustments'][0]['reason'], 'Retreat')

    def test_no_period(self):
        body = self.get('my/service-hours/').json()
        self.assertIsNone(body['period'])
        self.assertIsNone(body['progress'])


class DocumentTests(_Base):

    def doc(self, title, **kw):
        defaults = dict(title=title, uploaded_by=self.officer, published_to_chapter=True,
                        document=SimpleUploadedFile(f'{title}.pdf', b'%PDF-1.4 fake', content_type='application/pdf'))
        defaults.update(kw)
        return CommitteeDocument.objects.create(**defaults)

    def tearDown(self):
        for d in CommitteeDocument.objects.all():
            d.document.delete(save=False)

    def test_groups_folders_first(self):
        folder = ChapterFolder.objects.create(name='Bylaws', created_by=self.officer)
        self.doc('Constitution', chapter_folder=folder)
        self.doc('Minutes')
        self.doc('Draft', published_to_chapter=False)
        body = self.get('documents/').json()
        self.assertEqual([g['name'] for g in body['groups']], ['Bylaws', 'Chapter Documents'])
        self.assertEqual(body['groups'][0]['documents'][0]['extension'], 'pdf')

    def test_file_needs_a_published_document_and_the_bearer(self):
        d = self.doc('Minutes')
        hidden = self.doc('Draft', published_to_chapter=False)
        r = self.get(f'documents/{d.id}/file/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertEqual(b''.join(r.streaming_content), b'%PDF-1.4 fake')
        self.assertEqual(self.get(f'documents/{hidden.id}/file/').status_code, 404)
        self.assertEqual(self.api.get(API + f'documents/{d.id}/file/').status_code, 401)

    def test_page_toggle_and_pledge_restriction(self):
        toggle('chapter_documents', False)
        self.assertEqual(self.get('documents/').json()['error'], 'page_disabled')
        toggle('chapter_documents', True)
        pledge = make_user('my-pledge', member_type='Pledge')
        _, access, _ = make_device(pledge)
        restrict('chapter_documents', phases=())
        self.assertEqual(self.get('documents/', access=access).json()['error'], 'pledge_restricted')
