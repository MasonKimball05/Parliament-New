"""
Mason: "currently if you link a document to an announcement it will make
you download the document if you click on it, can instead we make it a
hyperlink to open the document from where it is saved and they have the
option to download it then?"

`download_chapter_document` (`src/view/view_document.py`) — the view both
`templates/announcements.html`'s doc chip and
`templates/emails/announcement_notification.html`'s new document link
point at — used to unconditionally `FileResponse(..., as_attachment=True)`
regardless of content type, forcing a save dialog even for a PDF a member
would rather just read. It's now wired onto the same
`src/utils/content_disposition.py` convention `serve_media`/
`serve_private_upload` already use for exactly this decision: a PDF or
image opens inline (the browser's own viewer has its own download
button — that's "the option to download it then"), anything else
(`.docx`/`.xlsx`/etc., which browsers can't render anyway) still
downloads. The access check itself — `@login_required` +
`published_to_chapter=True` + `document.can_user_view()` — is unchanged;
this only touches what the response tells the browser to do with the
bytes once that check passes.
"""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from src.models import CommitteeDocument, ParliamentUser


def _pdf(name='handbook.pdf'):
    """A minimal but structurally real PDF — see
    `test_document_versioning.py`'s `_pdf()` for why a bare string payload
    isn't reliable against libmagic's content-type sniff."""
    content = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n% real\ntrailer\n%%EOF\n'
    return SimpleUploadedFile(name, content, content_type='application/pdf')


def _docx(name='handbook.docx'):
    """Real minimal DOCX bytes (a zip) — not inline-safe, must still download."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types/>')
    return SimpleUploadedFile(
        name, buf.getvalue(),
        content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    )


class DownloadChapterDocumentDispositionTests(TestCase):
    def setUp(self):
        self.member = ParliamentUser.objects.create_user(
            user_id='cdd-member', password='testpass123',
            name='Doc Member', username='cdd_member', member_type='Member',
        )
        self.client = Client()
        self.client.login(username='cdd_member', password='testpass123')

    def _download(self, doc):
        return self.client.get(reverse('download_chapter_document', args=[doc.id]))

    def test_pdf_opens_inline_not_as_a_forced_download(self):
        doc = CommitteeDocument.objects.create(
            title='Handbook', document=_pdf(), uploaded_by=self.member,
            published_to_chapter=True, visibility='all_members',
        )
        response = self._download(doc)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Disposition'].startswith('inline'))
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_docx_still_downloads(self):
        """Browsers can't render a .docx — must still prompt to save, same
        as before this change."""
        doc = CommitteeDocument.objects.create(
            title='Handbook', document=_docx(), uploaded_by=self.member,
            published_to_chapter=True, visibility='all_members',
        )
        response = self._download(doc)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response['Content-Disposition'].startswith('attachment'))

    def test_cache_control_is_private(self):
        """Same reasoning as `serve_media`: a visibility-restricted document
        must never land in a shared/CDN cache."""
        doc = CommitteeDocument.objects.create(
            title='Handbook', document=_pdf(), uploaded_by=self.member,
            published_to_chapter=True, visibility='all_members',
        )
        response = self._download(doc)
        self.assertIn('private', response['Cache-Control'])

    def test_unpublished_document_is_a_404(self):
        """Unchanged from before: `published_to_chapter=False` is excluded
        by the lookup itself, not just the permission check."""
        doc = CommitteeDocument.objects.create(
            title='Draft', document=_pdf('draft.pdf'), uploaded_by=self.member,
            published_to_chapter=False, visibility='all_members',
        )
        response = self._download(doc)
        self.assertEqual(response.status_code, 404)
