"""
v3.44.5 — the document viewer must not render PDF pages inside its own request.

WHY THIS EXISTS. v3.44.4 capped the size of each preview page after the
10-04-26 outage, but left the shape that made the outage possible: the viewer
rendered every page (up to 50) before it answered, for every visitor, on every
load, on the one thread Daphne runs ordinary views on. Desktop visitors paid
for it too, and they are shown the PDF in an iframe and never see the images.

Now the viewer only lists the pages (`pdf_preview_manifest`), and each page is
rendered when the browser asks for it (`pdf_preview_page`). The page link is a
signed token tied to the member the viewer issued it to, because the page view
does not repeat the viewer's permission check.
"""

import os
import tempfile
from pathlib import Path
from unittest import mock

import fitz
from django.core import signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from src.models import CommitteeDocument, ParliamentUser
from src.view import view_document


def _pdf_bytes(page_sizes):
    doc = fitz.open()
    for width, height in page_sizes:
        page = doc.new_page(width=width, height=height)
        page.insert_text((72, 72), 'Page text')
    data = doc.tobytes()
    doc.close()
    return data


class _PreviewTestCase(TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(MEDIA_ROOT=tmp.name)
        override.enable()
        self.addCleanup(override.disable)

        self.member = ParliamentUser.objects.create_user(
            user_id='pvw-member', password='testpass123',
            name='Preview Member', username='pvw_member', member_type='Member',
        )
        self.other = ParliamentUser.objects.create_user(
            user_id='pvw-other', password='testpass123',
            name='Other Member', username='pvw_other', member_type='Member',
        )
        self.client = Client()
        self.client.login(username='pvw_member', password='testpass123')

    def _document(self, page_sizes, name='handbook.pdf'):
        return CommitteeDocument.objects.create(
            title='Handbook',
            document=SimpleUploadedFile(name, _pdf_bytes(page_sizes), content_type='application/pdf'),
            uploaded_by=self.member, published_to_chapter=True, visibility='all_members',
        )

    def _view(self, doc, client=None):
        return (client or self.client).get(reverse('view_chapter_document', args=[doc.id]))


class ViewerDoesNotRenderTests(_PreviewTestCase):

    def test_the_viewer_request_renders_no_pages(self):
        """The regression this release exists for."""
        doc = self._document([(612, 792)] * 3)
        with mock.patch.object(view_document, '_render_pdf_page_jpeg') as render:
            response = self._view(doc)
        self.assertEqual(response.status_code, 200)
        render.assert_not_called()

    def test_the_viewer_page_carries_links_not_image_data(self):
        doc = self._document([(612, 792)] * 3)
        response = self._view(doc)
        html = response.content.decode()
        self.assertNotIn('data:image/jpeg;base64', html)
        self.assertNotIn('data:image/png;base64', html)
        pages = response.context['pdf_images']['images']
        self.assertEqual([p['page'] for p in pages], [1, 2, 3])
        for page in pages:
            self.assertIn(f'src="{page["url"]}"', html)

    def test_a_failed_page_is_retried(self):
        """Each page is its own request, so one can fail alone. The retry
        script must be a nonce'd inline script (CSP) placed before the images."""
        html = self._view(self._document([(612, 792)])).content.decode()
        self.assertIn('data-preview-page="1"', html)
        script_at = html.index("addEventListener('error'")
        self.assertLess(script_at, html.index('data-preview-page="1"'))
        self.assertIn("img.hasAttribute('data-preview-page')", html)

    def test_each_image_reserves_its_space(self):
        """Without width/height every lazy image is 0px tall, so all of them
        are on screen at once and all of them load at once."""
        doc = self._document([(612, 792)])
        response = self._view(doc)
        page = response.context['pdf_images']['images'][0]
        self.assertEqual((page['width'], page['height']), (1275, 1650))
        self.assertIn('width="1275" height="1650"', response.content.decode())
        self.assertIn('loading="lazy"', response.content.decode())

    def test_the_announced_size_respects_the_pixel_cap(self):
        doc = self._document([(2550, 3300)])
        page = self._view(doc).context['pdf_images']['images'][0]
        self.assertLessEqual(page['width'], view_document.PDF_PREVIEW_MAX_WIDTH_PX)
        self.assertLessEqual(page['height'], view_document.PDF_PREVIEW_MAX_HEIGHT_PX)

    def test_a_long_pdf_lists_only_the_first_pages(self):
        doc = self._document([(612, 792)] * 4)
        with mock.patch.object(view_document, 'PDF_PREVIEW_MAX_PAGES', 2):
            manifest = view_document.pdf_preview_manifest(doc.document.path, self.member, max_pages=2)
        self.assertEqual(len(manifest['images']), 2)
        self.assertEqual(manifest['total_pages'], 4)
        self.assertTrue(manifest['truncated'])

    def test_an_unreadable_pdf_falls_back(self):
        doc = CommitteeDocument.objects.create(
            title='Broken',
            document=SimpleUploadedFile('broken.pdf', b'%PDF-1.4\nnot really a pdf', content_type='application/pdf'),
            uploaded_by=self.member, published_to_chapter=True, visibility='all_members',
        )
        Path(doc.document.path).write_bytes(b'this is not a pdf')
        response = self._view(doc)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context['pdf_images'])
        self.assertContains(response, "Open the PDF in your device")

    def test_a_file_outside_media_root_gets_no_links(self):
        with tempfile.TemporaryDirectory() as elsewhere:
            path = os.path.join(elsewhere, 'x.pdf')
            Path(path).write_bytes(_pdf_bytes([(612, 792)]))
            self.assertIsNone(view_document.pdf_preview_manifest(path, self.member))


class PreviewPageTests(_PreviewTestCase):

    def setUp(self):
        super().setUp()
        self.doc = self._document([(612, 792), (2550, 3300)])
        self.pages = self._view(self.doc).context['pdf_images']['images']

    def test_a_page_is_served_as_a_jpeg(self):
        response = self.client.get(self.pages[0]['url'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/jpeg')
        self.assertEqual(response.content[:3], b'\xff\xd8\xff')
        image = fitz.Pixmap(response.content)
        self.assertEqual((image.width, image.height), (1275, 1650))

    def test_a_page_is_rendered_within_the_pixel_cap(self):
        response = self.client.get(self.pages[1]['url'])
        image = fitz.Pixmap(response.content)
        self.assertLessEqual(image.width, view_document.PDF_PREVIEW_MAX_WIDTH_PX)
        self.assertLessEqual(image.height, view_document.PDF_PREVIEW_MAX_HEIGHT_PX)
        self.assertEqual(
            (image.width, image.height),
            (self.pages[1]['width'], self.pages[1]['height']),
            'the size announced on the <img> is the size rendered',
        )

    def test_a_page_is_cacheable_by_the_member_only(self):
        response = self.client.get(self.pages[0]['url'])
        self.assertEqual(response['Cache-Control'], 'private, max-age=3600')

    def test_one_request_renders_one_page(self):
        with mock.patch.object(
            view_document, '_render_pdf_page_jpeg', wraps=view_document._render_pdf_page_jpeg,
        ) as render:
            self.client.get(self.pages[0]['url'])
        self.assertEqual(render.call_count, 1)

    def test_anonymous_is_sent_to_login(self):
        response = Client().get(self.pages[0]['url'])
        self.assertEqual(response.status_code, 302)
        self.assertIn('/accounts/login/', response['Location'])

    def test_another_members_link_does_not_work(self):
        """The token is the only access check on this view, so it must be
        worthless to anyone but the member the viewer issued it to."""
        other = Client()
        other.login(username='pvw_other', password='testpass123')
        self.assertEqual(other.get(self.pages[0]['url']).status_code, 404)

    def test_a_tampered_token_is_refused(self):
        token = self.pages[0]['url'].split('/')[-3]
        forged = signing.dumps(
            {'u': str(self.member.pk), 'p': '../../etc/passwd', 'm': 0},
            salt='some-other-salt', compress=True,
        )
        for bad in (token[:-2] + 'xx', forged, 'not-a-token'):
            url = reverse('pdf_preview_page', args=[bad, 1])
            self.assertEqual(self.client.get(url).status_code, 404, bad)

    def test_an_expired_token_is_refused(self):
        with mock.patch.object(view_document, 'PDF_PREVIEW_TOKEN_MAX_AGE', -1):
            self.assertEqual(self.client.get(self.pages[0]['url']).status_code, 404)

    def test_a_page_past_the_end_is_a_404(self):
        token = self.pages[0]['url'].split('/')[-3]
        for page in (0, 3, view_document.PDF_PREVIEW_MAX_PAGES + 1):
            url = reverse('pdf_preview_page', args=[token, page])
            self.assertEqual(self.client.get(url).status_code, 404, page)

    def test_a_correctly_signed_path_outside_media_root_is_still_refused(self):
        """Belt and braces: the path is signed, so this needs the secret key."""
        token = signing.dumps(
            {'u': str(self.member.pk), 'p': '../outside.pdf', 'm': 0},
            salt=view_document._PDF_PREVIEW_SALT, compress=True,
        )
        url = reverse('pdf_preview_page', args=[token, 1])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_a_deleted_file_is_a_404(self):
        os.remove(self.doc.document.path)
        self.assertEqual(self.client.get(self.pages[0]['url']).status_code, 404)

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post(self.pages[0]['url']).status_code, 405)


class OtherViewersTests(_PreviewTestCase):
    """Every viewer goes through the same two helpers; this pins that none of
    them was left calling the render-everything function."""

    def test_no_viewer_calls_the_render_everything_function(self):
        source = Path(view_document.__file__).read_text(encoding='utf-8')
        self.assertEqual(source.count('convert_pdf_to_images('), 1, 'only its own def may remain')
        self.assertEqual(source.count('pdf_preview_manifest('), 3, 'its def + the two call sites')

    def test_a_document_the_member_may_not_see_issues_no_links(self):
        doc = self._document([(612, 792)])
        CommitteeDocument.objects.filter(pk=doc.pk).update(published_to_chapter=False)
        with mock.patch.object(view_document, 'pdf_preview_manifest') as manifest:
            response = self._view(doc)
        self.assertEqual(response.status_code, 404)
        manifest.assert_not_called()
