"""
v3.19.8 — one disposition rule, both views that serve uploads.

WHY THIS FILE EXISTS
--------------------
v3.19.7 decided what an uploaded file is allowed to BECOME when a member opens
it, built `INLINE_SAFE_CONTENT_TYPES`, argued the `image/svg+xml` exclusion
correctly, and applied it to `src/view/serve_private_upload.py` — six private
directories, audience a Kai reviewer or a slating committee of four.

`serve_media` kept `as_attachment=False` for every content type, as it had since
v3.14.1. It serves **ten public prefixes to every logged-in member in the
chapter**. The fix went where the attention was rather than where the surface
was: CLAUDE.md's *"a rule stated correctly, a helper written to enforce it, then
something left outside the helper"*, now seven releases deep, and the first time
the thing left outside was the **bigger half**.

⚠️ **THIS WAS NEVER EXPLOITABLE AND THAT IS THE INTERESTING PART.**
`_reject_browser_executable` (v3.19.7, `src/storage.py`) refuses to store a
`.html`/`.svg`/`.js` from any writer including the public prefixes, and a `find`
over `media/` and `exportable_media/` returns zero such files. So the protection
was real and **incidental** — supplied by a blocklist of extensions somebody
thought of, one layer down, rather than by a decision about what this response
renders. The next content type a browser learns to execute would have arrived
already permitted.

> **A blocklist protects you from the files you named. An allowlist protects the
> response.** Both layers stay, and these tests assert the second one.

WHAT THESE TESTS ARE SHAPED AROUND
----------------------------------
Mostly the PROPERTY (*the two views answer the same question the same way*)
rather than a list of content types, because a list is the thing that just went
out of sync. `test_the_two_upload_views_share_one_rule` fails if someone
re-declares the set locally in either module — which is exactly how this
happened.
"""

import os
import tempfile
from unittest import mock

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from src.models import ParliamentUser
from src.utils.content_disposition import (
    INLINE_SAFE_CONTENT_TYPES,
    apply_disposition,
    should_download,
)

_TMP_MEDIA = tempfile.mkdtemp(prefix='parliament-test-disposition-')


class TheRuleItselfTests(TestCase):
    """No view, no request — just the classification."""

    def test_svg_is_not_inline_safe(self):
        """
        The single exclusion that justifies enumerating rather than
        pattern-matching. An SVG is an XML document that may contain `<script>`;
        it is an image everywhere except in the way that matters.
        """
        self.assertNotIn('image/svg+xml', INLINE_SAFE_CONTENT_TYPES)
        self.assertTrue(should_download('image/svg+xml'))

    def test_the_executable_web_types_all_download(self):
        for content_type in ('text/html', 'text/javascript', 'application/javascript',
                             'application/xhtml+xml', 'text/xml', 'image/svg+xml'):
            with self.subTest(content_type=content_type):
                self.assertTrue(should_download(content_type))

    def test_an_unidentifiable_type_downloads(self):
        """A type we could not identify is a type we have not reasoned about."""
        self.assertTrue(should_download('application/octet-stream'))
        self.assertTrue(should_download(None))

    def test_the_types_the_pages_actually_need_still_render(self):
        """
        The negative control. A rule that downloaded everything would pass every
        assertion above and break the bug-report screenshot, the PDF preview and
        every profile picture.
        """
        for content_type in ('application/pdf', 'image/png', 'image/jpeg', 'image/gif'):
            with self.subTest(content_type=content_type):
                self.assertFalse(should_download(content_type))

    def test_apply_disposition_always_sets_nosniff(self):
        """
        Whichever way the disposition goes. `nosniff` is what stops a browser
        disregarding a `Content-Type` it disagrees with, so a response that
        decided to render inline without it has decided nothing.
        """
        for content_type in ('application/pdf', 'text/html'):
            with self.subTest(content_type=content_type):
                response = {}
                apply_disposition(response, content_type, 'x.bin')
                self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_apply_disposition_survives_an_awkward_filename(self):
        """RFC 5987, not an f-string — v3.14.2 fixed this once already."""
        response = {}
        apply_disposition(response, 'application/pdf', 'a "quoted" ñame.pdf')
        header = response['Content-Disposition']
        self.assertIn('filename*=', header)


class TheTwoUploadViewsShareOneRuleTests(TestCase):
    """
    The structural guard, and the one that would have caught v3.19.7's gap.

    A per-module copy of this set is how the private views came to be protected
    and the public ones not.
    """

    def test_the_two_upload_views_share_one_rule(self):
        from src.view import serve_media, serve_private_upload

        self.assertIs(
            serve_private_upload.INLINE_SAFE_CONTENT_TYPES,
            INLINE_SAFE_CONTENT_TYPES,
            'serve_private_upload has its own copy of the inline allowlist again',
        )
        self.assertIs(
            serve_media.apply_disposition,
            apply_disposition,
            'serve_media is not using the shared disposition helper',
        )

    def test_neither_view_module_declares_its_own_allowlist(self):
        """
        Reads the source, because an assignment beats an import and the `is`
        check above would still pass if the name were re-bound *before* use.
        """
        import inspect

        from src.view import serve_media, serve_private_upload

        for module in (serve_media, serve_private_upload):
            source = inspect.getsource(module)
            with self.subTest(module=module.__name__):
                self.assertNotIn(
                    'INLINE_SAFE_CONTENT_TYPES = frozenset', source,
                    f'{module.__name__} re-declares the allowlist locally; it must '
                    f'import it from src.utils.content_disposition so the two '
                    f'upload-serving views cannot drift apart.',
                )


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class ServeMediaHonoursTheAllowlistTests(TestCase):
    """`/media/` — ten public prefixes, audience every logged-in member."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        docs = os.path.join(_TMP_MEDIA, 'legislation_docs')
        os.makedirs(docs, exist_ok=True)
        with open(os.path.join(docs, 'bill.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 a real bill')
        # Written directly to disk, bypassing the storage layer, ON PURPOSE:
        # `_reject_browser_executable` would refuse this through the normal
        # write path, and the point of this test is what happens to a file that
        # is nonetheless present — a pre-v3.19.7 upload, a restored backup, a
        # file placed by hand on the server. Testing only what the blocklist
        # lets through would test the blocklist.
        with open(os.path.join(docs, 'notice.html'), 'wb') as f:
            f.write(b'<!DOCTYPE html><html><body>login please</body></html>')
        with open(os.path.join(docs, 'seal.svg'), 'wb') as f:
            f.write(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')

    def setUp(self):
        self.client = Client()
        self.member = ParliamentUser.objects.create_user(
            user_id='sd1', name='Disposition Member', username='sd1',
            member_type='Member')
        self.client.force_login(self.member)

    def _get(self, name):
        return self.client.get(reverse('serve_media',
                                       kwargs={'path': f'legislation_docs/{name}'}))

    def test_html_on_a_public_prefix_is_downloaded_not_rendered(self):
        resp = self._get('notice.html')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp['Content-Disposition'].startswith('attachment'))
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')

    def test_svg_on_a_public_prefix_is_downloaded_not_rendered(self):
        resp = self._get('seal.svg')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp['Content-Disposition'].startswith('attachment'))

    def test_the_control_a_pdf_still_renders_inline(self):
        """
        Without this, a guard that attached everything would pass both tests
        above while breaking every document preview in the application.
        """
        resp = self._get('bill.pdf')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp['Content-Disposition'].startswith('inline'))
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')

    def test_the_cache_control_contract_is_unchanged(self):
        """v3.19.8 touched the disposition; `private` here is v3.14.1's and stays."""
        resp = self._get('bill.pdf')
        self.assertIn('private', resp['Cache-Control'])

    @mock.patch('src.view.serve_media.MEDIA_ACCEL_PREFIX', '/protected-media')
    def test_accel_mode_gets_the_same_disposition(self):
        """
        ⚠️ THE PRODUCTION PATH, AND THE ONE THAT WOULD GO UNCHECKED.

        Under X-Accel nginx streams the body and Django supplies only headers,
        so this branch is the only place the disposition can be set at all. It
        is also the branch developers never run locally — which is precisely why
        putting the call under the `else` would have looked correct forever.
        """
        resp = self._get('notice.html')
        self.assertEqual(resp['X-Accel-Redirect'],
                         '/protected-media/legislation_docs/notice.html')
        self.assertTrue(resp['Content-Disposition'].startswith('attachment'))
        self.assertEqual(resp['X-Content-Type-Options'], 'nosniff')
