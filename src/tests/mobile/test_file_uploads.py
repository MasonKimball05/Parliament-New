"""
v4.4.2 — files from the app's file picker (build 19): committee document
upload and replace, song recordings, feedback PDFs, Kai attachments and excuse
documents. Each endpoint saves through the web view's own helper, so each test
class checks a good file, a wrong type, a file over the limit, who may, and
that the website takes the same path.

    python manage.py test src.tests.mobile.test_file_uploads
"""
import io
from datetime import timedelta
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from src.mobile import views_committee_documents, views_excuses_polls, views_support
from src.models import (
    AttendanceExcuse, ChapterFolder, Committee, CommitteeDocument, Event, FeedbackRequest, KaiCommendation,
    KaiReport, Song,
)
from src.models.documents import DocumentVersion
from src.tests.documents.test_upload_type_fixtures import _ooxml
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import flag
from src.view import feedback as web_feedback
from src.view import songbook as web_songbook
from src.view import submit_excuse as web_excuse
from src.view.committee import upload_document as web_upload

API = '/api/mobile/v1/'
DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def _pdf(name='doc.pdf', label=b'one', pad=0):
    """A small structurally real PDF (a bare header can sniff as text/plain)."""
    content = (b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n% ' + label + b'\n' + b'%' * pad
               + b'\ntrailer\n%%EOF\n')
    return SimpleUploadedFile(name, content, content_type='application/pdf')


def _docx(name='notes.docx'):
    return SimpleUploadedFile(name, _ooxml('wordprocessingml.document'), content_type=DOCX)


def _html(name='doc.pdf'):
    return SimpleUploadedFile(name, b'<html><script>alert(1)</script></html>', content_type='application/pdf')


def _png(name='photo.png'):
    buf = io.BytesIO()
    Image.new('RGB', (4, 4), 'blue').save(buf, format='PNG')
    return SimpleUploadedFile(name, buf.getvalue(), content_type='image/png')


def _mp3(name='take.mp3', size=None):
    body = b'ID3\x03\x00\x00\x00\x00\x00\x00' + b'\xff\xfb\x90\x00' * 64
    if size:
        body = body + b'\x00' * (size - len(body))
    return SimpleUploadedFile(name, body, content_type='audio/mpeg')


def _web_messages(response):
    return [str(m) for m in get_messages(response.wsgi_request)]


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'], REQUIRE_2FA_FOR_ADMINS=False,
                   REQUIRE_2FA_FOR_OFFICERS=False)
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.api = Client()
        self.tokens = {}

    def token(self, user):
        if user.pk not in self.tokens:
            self.tokens[user.pk] = make_device(user)[1]
        return self.tokens[user.pk]

    def get(self, user, path):
        return self.api.get(API + path, **bearer(self.token(user)))

    def upload(self, user, path, data):
        """A multipart POST, as the app sends a picked file."""
        return self.api.post(API + path, data, **bearer(self.token(user)))

    def web(self, user):
        c = Client()
        c.force_login(user)
        return c


# ── Committee documents ─────────────────────────────────────────────────────

class CommitteeDocumentUploadTests(_Base):

    def setUp(self):
        super().setUp()
        self.chair = make_user('fu-chair')
        self.member = make_user('fu-member')
        self.outsider = make_user('fu-out')
        self.c = Committee.objects.create(name='Finance', code='FIN', is_active=True)
        self.other = Committee.objects.create(name='Social', code='SOC', is_active=True)
        self.c.chairs.add(self.chair)
        self.c.members.add(self.chair, self.member)
        self.folder = ChapterFolder.objects.create(name='FU Minutes', created_by=self.chair)

    def test_form_choices(self):
        flag('document_versioning', False)
        r = self.get(self.chair, 'committees/FIN/documents/upload/')
        self.assertEqual(r.status_code, 200, r.content)
        b = r.json()
        self.assertIn({'value': 'agenda', 'label': 'Meeting Agenda'}, b['types'])
        self.assertEqual(b['extensions'], ['.pdf', '.docx', '.doc', '.txt'])
        self.assertIn({'id': self.folder.id, 'name': 'FU Minutes'}, b['folders'])
        self.assertEqual(b['max_bytes'], 20 * 1024 * 1024)
        self.assertFalse(b['can_replace'])
        self.assertEqual(self.get(self.member, 'committees/FIN/documents/upload/').status_code, 403)
        self.assertEqual(self.get(self.chair, 'committees/NOPE/documents/upload/').status_code, 404)

    def test_good_file(self):
        r = self.upload(self.chair, 'committees/FIN/documents/upload/', {
            'file': _pdf(), 'title': 'Budget', 'description': 'Spring', 'document_type': 'minutes',
            'meeting_date': '2026-10-30', 'publish_to_chapter': 'true', 'chapter_folder': self.folder.id})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['message'], 'Document uploaded and published to chapter successfully.')
        doc = CommitteeDocument.objects.get(pk=r.json()['document']['id'])
        self.assertEqual((doc.committee, doc.uploaded_by, doc.document_type, str(doc.meeting_date),
                          doc.published_to_chapter, doc.chapter_folder),
                         (self.c, self.chair, 'minutes', '2026-10-30', True, self.folder))
        self.assertEqual(r.json()['document']['extension'], 'pdf')
        # A Word file too; a folder without publishing is dropped, as on the website.
        r = self.upload(self.chair, 'committees/FIN/documents/upload/',
                        {'file': _docx(), 'title': 'Notes', 'chapter_folder': self.folder.id})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(r.json()['message'], 'Document uploaded successfully.')
        doc = CommitteeDocument.objects.get(pk=r.json()['document']['id'])
        self.assertEqual((doc.published_to_chapter, doc.chapter_folder, doc.document_type), (False, None, 'general'))

    def test_wrong_type(self):
        r = self.upload(self.chair, 'committees/FIN/documents/upload/', {'file': _html(), 'title': 'T'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertIn('File upload error', r.json()['detail'])
        bad = SimpleUploadedFile('x.html', b'<script>', content_type='text/html')
        r = self.upload(self.chair, 'committees/FIN/documents/upload/', {'file': bad, 'title': 'T'})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(CommitteeDocument.objects.exists())

    def test_too_big(self):
        with patch('src.utils.file_validation.MAX_FILE_SIZE', 64):
            r = self.upload(self.chair, 'committees/FIN/documents/upload/', {'file': _pdf(), 'title': 'T'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertIn('exceeds maximum allowed size', r.json()['detail'])
        self.assertFalse(CommitteeDocument.objects.exists())

    def test_fields(self):
        for body, words in (({'title': 'No file'}, 'both a file and a title'),
                            ({'file': _pdf(), 'title': ''}, 'both a file and a title'),
                            ({'file': _pdf(), 'title': 'T' * 201}, 'too long'),
                            ({'file': _pdf(), 'title': 'T', 'document_type': 'nope'}, 'document type'),
                            ({'file': _pdf(), 'title': 'T', 'meeting_date': '30/10/2026'}, 'YYYY-MM-DD'),
                            ({'file': _pdf(), 'title': 'T', 'meeting_date': '2026-13-40'}, 'YYYY-MM-DD')):
            r = self.upload(self.chair, 'committees/FIN/documents/upload/', body)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'), body)
            self.assertIn(words, r.json()['detail'])
        self.assertFalse(CommitteeDocument.objects.exists())

    def test_permissions(self):
        for user in (self.member, self.outsider):
            r = self.upload(user, 'committees/FIN/documents/upload/', {'file': _pdf(), 'title': 'T'})
            self.assertEqual((r.status_code, r.json()['error']), (403, 'no_access'))
        # Another committee's chair can't file under this one.
        r = self.upload(self.chair, 'committees/SOC/documents/upload/', {'file': _pdf(), 'title': 'T'})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.api.post(API + 'committees/FIN/documents/upload/', {}).status_code, 401)
        self.assertFalse(CommitteeDocument.objects.exists())
        # A member a chair has let manage documents may.
        from src.models import CommitteePermissions
        CommitteePermissions.objects.create(committee=self.c, user=self.member, can_upload_docs=True)
        r = self.upload(self.member, 'committees/FIN/documents/upload/', {'file': _pdf(), 'title': 'T'})
        self.assertEqual(r.status_code, 201, r.content)

    def test_the_website_takes_the_same_path(self):
        self.assertIs(views_committee_documents.create_committee_document, web_upload.create_committee_document)
        with patch.object(web_upload, 'create_committee_document', wraps=web_upload.create_committee_document) as w:
            resp = self.web(self.chair).post(reverse('upload_document', args=['FIN']),
                                             {'file': _html(), 'title': 'T'})
        self.assertEqual(w.call_count, 1)
        self.assertEqual(resp.status_code, 200)
        app = self.upload(self.chair, 'committees/FIN/documents/upload/', {'file': _html(), 'title': 'T'})
        self.assertEqual(_web_messages(resp), [app.json()['detail']])
        resp = self.web(self.chair).post(reverse('upload_document', args=['FIN']), {
            'file': _pdf(), 'title': 'Web', 'document_type': 'agenda', 'meeting_date': '2026-11-01'})
        self.assertEqual(resp.status_code, 302)
        doc = CommitteeDocument.objects.get(title='Web')
        self.assertEqual((doc.document_type, str(doc.meeting_date)), ('agenda', '2026-11-01'))
        # v4.4.2: a bad type or date is refused there too instead of failing at the database.
        resp = self.web(self.chair).post(reverse('upload_document', args=['FIN']),
                                         {'file': _pdf(), 'title': 'Web 2', 'meeting_date': 'soon'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(CommitteeDocument.objects.filter(title='Web 2').exists())

    def test_list_says_what_the_app_may_do(self):
        CommitteeDocument.objects.create(committee=self.c, title='Old', document=_pdf(), uploaded_by=self.chair)
        flag('committee_system', True)
        flag('document_versioning', False)
        b = self.get(self.chair, 'committees/FIN/documents/').json()
        self.assertEqual((b['can_manage'], b['can_replace']), (True, False))
        self.assertEqual(b['documents'][0]['version'], 'v1.0')
        self.assertEqual(b['upload_path'], reverse('upload_document', args=['FIN']))   # older builds
        flag('document_versioning', True)
        self.assertTrue(self.get(self.chair, 'committees/FIN/documents/').json()['can_replace'])


class CommitteeDocumentReplaceTests(_Base):

    def setUp(self):
        super().setUp()
        self.chair = make_user('fr-chair')
        self.member = make_user('fr-member')
        self.c = Committee.objects.create(name='Finance', code='FIN', is_active=True)
        self.other = Committee.objects.create(name='Social', code='SOC', is_active=True)
        self.c.chairs.add(self.chair)
        self.other.chairs.add(self.chair)
        self.c.members.add(self.chair, self.member)
        self.doc = CommitteeDocument.objects.create(committee=self.c, title='Bylaws', document=_pdf('v1.pdf'),
                                                    uploaded_by=self.chair)
        self.path = f'committees/FIN/documents/{self.doc.id}/replace/'
        flag('document_versioning', True)

    def test_flag_off(self):
        flag('document_versioning', False)
        r = self.upload(self.chair, self.path, {'file': _pdf('v2.pdf')})
        self.assertEqual((r.status_code, r.json()['error']), (403, 'feature_disabled'))
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.version_number, 1)

    def test_good_file(self):
        old_name = self.doc.document.name
        r = self.upload(self.chair, self.path, {'file': _docx('v2.docx'), 'change_notes': ' Fixed typos '})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['message'], 'Uploaded a new version of "Bylaws" (v2.0). '
                                              'The previous version is kept in its history.')
        self.assertEqual((r.json()['document']['version'], r.json()['document']['extension']), ('v2.0', 'docx'))
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.version_number, 2)
        v = DocumentVersion.objects.get(document=self.doc)
        self.assertEqual((v.version_number, v.file.name, v.change_notes), (1, old_name, 'Fixed typos'))

    def test_wrong_type_and_no_file(self):
        r = self.upload(self.chair, self.path, {'file': _html('v2.pdf')})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertIn('File upload error', r.json()['detail'])
        r = self.upload(self.chair, self.path, {'change_notes': 'x'})
        self.assertEqual(r.status_code, 400)
        self.assertIn('choose a file', r.json()['detail'])
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.version_number, 1)
        self.assertFalse(DocumentVersion.objects.exists())

    def test_too_big(self):
        with patch('src.utils.file_validation.MAX_FILE_SIZE', 64):
            r = self.upload(self.chair, self.path, {'file': _pdf('v2.pdf')})
        self.assertEqual(r.status_code, 400)
        self.assertIn('exceeds maximum allowed size', r.json()['detail'])
        self.assertFalse(DocumentVersion.objects.exists())

    def test_permissions(self):
        r = self.upload(self.member, self.path, {'file': _pdf('v2.pdf')})
        self.assertEqual((r.status_code, r.json()['error']), (403, 'no_access'))
        # The document under another committee's code is not found.
        r = self.upload(self.chair, f'committees/SOC/documents/{self.doc.id}/replace/', {'file': _pdf('v2.pdf')})
        self.assertEqual(r.status_code, 404)
        self.assertFalse(DocumentVersion.objects.exists())

    def test_the_website_takes_the_same_path(self):
        self.assertIs(views_committee_documents.replace_committee_document, web_upload.replace_committee_document)
        with patch.object(web_upload, 'replace_committee_document', wraps=web_upload.replace_committee_document) as w:
            resp = self.web(self.chair).post(reverse('committee_replace_document', args=['FIN', self.doc.id]),
                                             {'file': _html('v2.pdf')})
        self.assertEqual(w.call_count, 1)
        app = self.upload(self.chair, self.path, {'file': _html('v2.pdf')})
        self.assertEqual(_web_messages(resp), [app.json()['detail']])
        resp = self.web(self.chair).post(reverse('committee_replace_document', args=['FIN', self.doc.id]),
                                         {'file': _pdf('v2.pdf'), 'change_notes': 'web'})
        self.assertEqual(resp.status_code, 302)
        self.doc.refresh_from_db()
        self.assertEqual(self.doc.version_number, 2)


# ── Song recordings ─────────────────────────────────────────────────────────

class SongRecordingTests(_Base):

    def setUp(self):
        super().setUp()
        self.officer = make_user('fs-officer', member_type='Officer')
        self.member = make_user('fs-member')
        self.song = Song.objects.create(title='Old Song', lyrics='La la', created_by=self.officer)

    def edit(self, body, user=None):
        return self.upload(user or self.officer, f'songbook/songs/{self.song.id}/edit/',
                           {'title': 'Old Song', 'lyrics': 'La la', **body})

    def test_add_with_a_recording(self):
        r = self.upload(self.officer, 'songbook/songs/', {'title': 'New', 'lyrics': 'x', 'audio_file': _mp3()})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()['song']['has_audio'])
        self.assertTrue(r.json()['song']['audio_name'].endswith('.mp3'))
        self.assertTrue(Song.objects.get(pk=r.json()['song']['id']).audio_file)

    def test_replace_and_remove(self):
        self.assertEqual(self.edit({'audio_file': _mp3('first.mp3')}).status_code, 200)
        self.song.refresh_from_db()
        first = self.song.audio_file.name
        r = self.edit({'audio_file': _mp3('second.mp3')})
        self.assertEqual(r.status_code, 200, r.content)
        self.song.refresh_from_db()
        self.assertNotEqual(self.song.audio_file.name, first)
        self.assertIn('second', r.json()['song']['audio_name'])
        # No file and no remove keeps it.
        self.assertTrue(self.edit({}).json()['song']['has_audio'])
        # remove_audio takes it off; with a new file the new one wins.
        r = self.edit({'remove_audio': 'true', 'audio_file': _mp3('third.mp3')})
        self.assertIn('third', r.json()['song']['audio_name'])
        r = self.edit({'remove_audio': 'true'})
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['song']['has_audio'], r.json()['song']['audio_name']), (False, ''))
        self.song.refresh_from_db()
        self.assertFalse(self.song.audio_file)

    def test_wrong_type(self):
        self.edit({'audio_file': _mp3()})
        r = self.edit({'audio_file': SimpleUploadedFile('take.txt', b'hello', content_type='text/plain'),
                       'title': 'Changed'})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertIn('Invalid file type. Allowed types: MP3, WAV, M4A, OGG, FLAC.', r.json()['detail'])
        self.song.refresh_from_db()
        self.assertEqual(self.song.title, 'Old Song')
        self.assertTrue(self.song.audio_file)

    def test_a_file_that_is_not_audio_is_refused(self):
        # v4.4.2: SongForm's content check used to swallow its own refusal.
        fake = SimpleUploadedFile('take.mp3', b'%PDF-1.4 not audio at all ' * 50, content_type='audio/mpeg')
        r = self.upload(self.officer, 'songbook/songs/', {'title': 'Fake', 'lyrics': 'x', 'audio_file': fake})
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertIn('Expected audio file', r.json()['detail'])
        self.assertFalse(Song.objects.filter(title='Fake').exists())

    def test_too_big(self):
        r = self.upload(self.officer, 'songbook/songs/',
                        {'title': 'Big', 'lyrics': 'x', 'audio_file': _mp3(size=50 * 1024 * 1024 + 1)})
        self.assertEqual(r.status_code, 400)
        self.assertIn('Audio file size must not exceed 50 MB.', r.json()['detail'])
        self.assertFalse(Song.objects.filter(title='Big').exists())

    def test_permissions(self):
        r = self.edit({'audio_file': _mp3()}, user=self.member)
        self.assertEqual(r.status_code, 403)
        r = self.edit({'remove_audio': 'true'}, user=self.member)
        self.assertEqual(r.status_code, 403)
        self.song.refresh_from_db()
        self.assertFalse(self.song.audio_file)

    def test_the_website_takes_the_same_path(self):
        from src.mobile import views_songbook_manage
        self.assertIs(views_songbook_manage.update_song, web_songbook.update_song)
        self.edit({'audio_file': _mp3()})
        with patch.object(web_songbook, 'update_song', wraps=web_songbook.update_song) as w:
            resp = self.web(self.officer).post(reverse('song_edit', args=[self.song.id]),
                                               {'title': 'Web title', 'lyrics': 'La la', 'remove_audio': 'true'})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(w.call_count, 1)
        self.song.refresh_from_db()
        # The web form has no remove control, so the recording stays there.
        self.assertEqual(self.song.title, 'Web title')
        self.assertTrue(self.song.audio_file)
        resp = self.web(self.officer).post(reverse('song_create'), {
            'title': 'Web new', 'lyrics': 'x', 'audio_file': SimpleUploadedFile('a.txt', b'x')})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(Song.objects.filter(title='Web new').exists())


# ── Feedback attachments ────────────────────────────────────────────────────

class FeedbackAttachmentTests(_Base):

    def setUp(self):
        super().setUp()
        self.user = make_user('ff-1')

    def send(self, attachment):
        return self.upload(self.user, 'support/feedback/', {'title': 'Mockup', 'description': 'See it',
                                                            'attachment': attachment})

    def test_form_says_pdf(self):
        b = self.get(self.user, 'support/feedback/').json()
        self.assertEqual(b['attachment']['extensions'], ['.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf'])
        self.assertEqual(b['attachment']['max_bytes'], 10 * 1024 * 1024)

    def test_good_pdf_and_photo(self):
        for upload in (_pdf('mock.pdf'), _png()):
            r = self.send(upload)
            self.assertEqual(r.status_code, 201, r.content)
            self.assertIsNone(r.json()['attachment_problem'])
            self.assertTrue(FeedbackRequest.objects.get(pk=r.json()['id']).attachment)

    def test_wrong_type(self):
        for upload, problem in (
                (_html('mock.pdf'), "That file isn't a readable PDF."),
                (SimpleUploadedFile('notes.txt', b'hello'), 'Attachments must be a photo (PNG, JPEG, WebP or GIF) '
                                                            'or a PDF.'),
                (SimpleUploadedFile('shot.png', b'not a png'), "That file isn't a readable image.")):
            r = self.send(upload)
            self.assertEqual(r.status_code, 201, r.content)   # the request isn't lost
            self.assertEqual(r.json()['attachment_problem'], problem)
            self.assertFalse(FeedbackRequest.objects.get(pk=r.json()['id']).attachment)

    def test_too_big(self):
        r = self.send(_pdf('big.pdf', pad=10 * 1024 * 1024))
        self.assertEqual(r.json()['attachment_problem'], 'Attachments can be at most 10 MB.')
        self.assertFalse(FeedbackRequest.objects.get(pk=r.json()['id']).attachment)

    def test_signed_out(self):
        self.assertEqual(self.api.post(API + 'support/feedback/', {'attachment': _pdf()}).status_code, 401)
        self.assertFalse(FeedbackRequest.objects.exists())

    def test_the_website_takes_the_same_path(self):
        self.assertIs(views_support.attachment_error, web_feedback.attachment_error)
        web = self.web(self.user)
        resp = web.post(reverse('feedback_request'), {'title': 'Web', 'description': 'd', 'attachment': _pdf()})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(FeedbackRequest.objects.get(title='Web').attachment)
        # v4.4.2: the website checks it too (it saved any file before), and keeps the request.
        resp = web.post(reverse('feedback_request'), {'title': 'Web 2', 'description': 'd',
                                                      'attachment': _html('mock.pdf')})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(FeedbackRequest.objects.get(title='Web 2').attachment)
        self.assertIn("It was sent without the attachment. That file isn't a readable PDF.", _web_messages(resp))


# ── Kai attachments ─────────────────────────────────────────────────────────

class KaiAttachmentTests(_Base):

    def setUp(self):
        super().setUp()
        self.reporter = make_user('fk-reporter', email='reporter@example.com')
        self.accused = make_user('fk-accused')
        self.chair = make_user('fk-chair', email='chair@example.com')
        self.kai = Committee.objects.create(name='Kai', code='KAI', is_kai_committee=True)
        self.kai.members.add(self.chair)
        self.kai.chairs.add(self.chair)

    def report(self, **kw):
        return {'category': 'behavioral', 'title': 'Loud party', 'description': 'It was loud.',
                'targeted_to': str(self.accused.pk), **kw}

    def test_documents(self):
        for upload in (_pdf('notes.pdf'), _docx(), _png()):
            r = self.upload(self.reporter, 'kai/reports/', self.report(attachment=upload))
            self.assertEqual(r.status_code, 201, r.content)
            self.assertTrue(KaiReport.objects.get(pk=r.json()['id']).attachment.name.endswith(
                upload.name.rsplit('.', 1)[1]))
        r = self.upload(self.reporter, 'kai/commendations/', {
            'commended_member': str(self.accused.pk), 'title': 'Great', 'description': 'Ran it.',
            'attachment': _docx('thanks.docx')})
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(KaiCommendation.objects.get(pk=r.json()['id']).attachment)

    def test_wrong_type(self):
        for upload in (_html('notes.pdf'), SimpleUploadedFile('notes.txt', b'hello')):
            r = self.upload(self.reporter, 'kai/reports/', self.report(attachment=upload))
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertFalse(KaiReport.objects.exists())
        r = self.upload(self.reporter, 'kai/commendations/', {
            'commended_member': str(self.accused.pk), 'title': 'Great', 'description': 'Ran it.',
            'attachment': _html('thanks.pdf')})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(KaiCommendation.objects.exists())

    def test_too_big(self):
        with patch('src.utils.file_validation.MAX_FILE_SIZE', 64):
            r = self.upload(self.reporter, 'kai/reports/', self.report(attachment=_pdf()))
        self.assertEqual(r.status_code, 400)
        self.assertIn('exceeds maximum allowed size', r.json()['detail'])
        self.assertFalse(KaiReport.objects.exists())

    def test_kai_off(self):
        flag('kai_reports', False)
        r = self.upload(self.reporter, 'kai/reports/', self.report(attachment=_pdf()))
        self.assertEqual((r.status_code, r.json()['error']), (403, 'feature_disabled'))

    def test_the_website_takes_the_same_path(self):
        # Both views bind KaiReportForm; a Word file now passes there too (the
        # 2 KB sniff refused every real .docx).
        resp = self.web(self.reporter).post(reverse('submit_kai_report'), self.report(attachment=_docx()))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(KaiReport.objects.get().attachment.name.endswith('.docx'))
        resp = self.web(self.reporter).post(reverse('submit_kai_report'),
                                            self.report(title='Bad', attachment=_html('notes.pdf')))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(KaiReport.objects.filter(title='Bad').exists())


# ── Excuse documents ────────────────────────────────────────────────────────

class ExcuseDocumentTests(_Base):

    def setUp(self):
        super().setUp()
        self.user = make_user('fe-1')
        self.officer = make_user('fe-off', member_type='Officer')
        self.event = Event.objects.create(title='Meeting', description='d', created_by=self.officer,
                                          requires_attendance=True, allow_excuses=True,
                                          date_time=timezone.now() + timedelta(days=2))

    def send(self, document, user=None):
        return self.upload(user or self.user, 'my/excuses/', {
            'event_id': self.event.id, 'reason': 'Doctor appointment', 'supporting_document': document})

    def test_good_file(self):
        b = self.get(self.user, 'my/excuses/').json()
        self.assertEqual(b['document'], {'extensions': ['.pdf', '.doc', '.docx', '.jpg', '.jpeg', '.png'],
                                         'max_bytes': 20 * 1024 * 1024})
        r = self.send(_pdf('note.pdf'))
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.json()['has_document'])
        self.assertTrue(AttendanceExcuse.objects.get(pk=r.json()['id']).supporting_document)
        # Without a file (and as JSON) it works as before.
        other = Event.objects.create(title='Other', description='d', created_by=self.officer, allow_excuses=True,
                                     date_time=timezone.now() + timedelta(days=3))
        r = self.api.post(API + 'my/excuses/', {'event_id': other.id, 'reason': 'Doctor appointment'},
                          content_type='application/json', **bearer(self.token(self.user)))
        self.assertEqual((r.status_code, r.json()['has_document']), (201, False))

    def test_wrong_type(self):
        r = self.send(_html('note.pdf'))
        self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_input'))
        self.assertIn('File upload error', r.json()['detail'])
        self.assertFalse(AttendanceExcuse.objects.exists())

    def test_too_big(self):
        with patch('src.utils.file_validation.MAX_FILE_SIZE', 64):
            r = self.send(_pdf('note.pdf'))
        self.assertEqual(r.status_code, 400)
        self.assertIn('exceeds maximum allowed size', r.json()['detail'])
        self.assertFalse(AttendanceExcuse.objects.exists())

    def test_permissions(self):
        self.assertEqual(self.api.post(API + 'my/excuses/', {'event_id': self.event.id}).status_code, 401)
        flag('excuse_system', False)
        r = self.send(_pdf('note.pdf'))
        self.assertEqual((r.status_code, r.json()['error']), (403, 'feature_disabled'))
        self.assertFalse(AttendanceExcuse.objects.exists())

    def test_the_website_takes_the_same_path(self):
        self.assertIs(views_excuses_polls.excuse_document_error, web_excuse.excuse_document_error)
        url = reverse('submit_excuse', args=[self.event.id])
        resp = self.web(self.user).post(url, {'reason': 'Doctor appointment', 'supporting_document': _html('n.pdf')})
        app = self.send(_html('n.pdf'))
        self.assertIn(app.json()['detail'], _web_messages(resp))
        resp = self.web(self.user).post(url, {'reason': 'Doctor appointment', 'supporting_document': _pdf('n.pdf')})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(AttendanceExcuse.objects.get(user=self.user).supporting_document)
