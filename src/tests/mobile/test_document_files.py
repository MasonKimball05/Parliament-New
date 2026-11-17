"""The document files the app's viewer shows (v4.4.14, src/mobile/views_document_files.py)."""
from datetime import timedelta

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from src.models import AttendanceExcuse, Event, Legislation
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag, restrict

API = '/api/mobile/v1/'


def _pdf(name):
    return SimpleUploadedFile(name, b'%PDF-1.4 fake', content_type='application/pdf')


def _body(r):
    return b''.join(r.streaming_content) if r.streaming else r.content


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('df-1')
        _, self.access, _ = make_device(self.user)
        self.api = Client()

    def get(self, path, access=None):
        return self.api.get(API + path, **bearer(access or self.access))


class LegislationDocumentTests(_Base):

    def setUp(self):
        super().setUp()
        now = timezone.now()
        self.bill = Legislation.objects.create(
            title='Budget', description='d', posted_by=self.user, document=_pdf('budget.pdf'),
            available_at=now - timedelta(hours=1), voting_starts_at=now - timedelta(hours=1))
        self.addCleanup(self.bill.document.delete, save=False)

    def test_file_streams_for_any_member(self):
        r = self.get(f'legislation/{self.bill.id}/document/file/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertEqual(r['Cache-Control'], 'private, no-store')
        self.assertEqual(_body(r), b'%PDF-1.4 fake')
        self.assertEqual(self.api.get(API + f'legislation/{self.bill.id}/document/file/').status_code, 401)

    def test_no_document_is_404(self):
        bare = Legislation.objects.create(title='Bare', description='d', posted_by=self.user,
                                          available_at=timezone.now())
        self.assertEqual(self.get(f'legislation/{bare.id}/document/file/').status_code, 404)
        self.assertEqual(self.get('legislation/999999/document/file/').status_code, 404)

    def test_vote_detail_carries_the_file(self):
        doc = self.get(f'votes/{self.bill.id}/').json()['vote']['document']
        self.assertEqual(doc['file'], f'legislation/{self.bill.id}/document/file/')
        self.assertTrue(doc['name'].endswith('.pdf'))

    def test_pledge_restriction_applies(self):
        pledge = make_user('df-pl', member_type='Pledge')
        _, pa, _ = make_device(pledge)
        restrict('view_document', phases=())
        self.assertEqual(self.get(f'legislation/{self.bill.id}/document/file/', pa).status_code, 403)


class ExcuseDocumentTests(_Base):

    def setUp(self):
        super().setUp()
        flag('attendance_tracking', True)
        flag('excuse_system', True)
        self.officer = make_user('df-off', member_type='Officer')
        _, self.officer_access, _ = make_device(self.officer)
        self.other = make_user('df-2')
        _, self.other_access, _ = make_device(self.other)
        event = Event.objects.create(title='Meeting', description='d', created_by=self.officer,
                                     requires_attendance=True, date_time=timezone.now() + timedelta(days=1))
        self.excuse = AttendanceExcuse.objects.create(event=event, user=self.user, reason='Doctor',
                                                      supporting_document=_pdf('note.pdf'))
        self.addCleanup(self.excuse.supporting_document.delete, save=False)
        self.path = f'excuses/{self.excuse.id}/document/file/'

    def test_author_and_officer_only(self):
        self.assertEqual(self.get(self.path).status_code, 200)
        r = self.get(self.path, self.officer_access)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(_body(r), b'%PDF-1.4 fake')
        self.assertEqual(self.get(self.path, self.other_access).status_code, 404)

    def test_excuse_system_off_is_404(self):
        flag('excuse_system', False)
        self.assertEqual(self.get(self.path).status_code, 404)

    def test_officer_list_carries_the_file(self):
        rows = self.get('officer/excuses/', self.officer_access).json()['excuses']
        row = next(x for x in rows if x['id'] == self.excuse.id)
        self.assertEqual(row['document']['file'], self.path)


class CnbPdfTests(_Base):

    def test_pdf_for_any_member(self):
        r = self.get('cnb/pdf/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r['Content-Type'], 'application/pdf')
        self.assertTrue(_body(r).startswith(b'%PDF'))
        self.assertEqual(r['Cache-Control'], 'private, no-store')
        self.assertEqual(self.api.get(API + 'cnb/pdf/').status_code, 401)

    def test_cnb_payload_carries_the_file(self):
        self.assertEqual(self.get('cnb/').json()['web']['pdf_file'], 'cnb/pdf/')
