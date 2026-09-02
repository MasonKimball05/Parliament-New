"""
v3.14.1 — authenticated /media/ serving (src/view/serve_media.py).

Locks in the fix for the 07-18-26 finding that nginx served all uploaded
legislation docs publicly. If any of these fail, assume the auth wall on
member uploads is broken.
"""
import os
import tempfile
from unittest import mock

from django.test import TestCase, Client, override_settings
from django.urls import reverse

from src.models import ParliamentUser

_TMP_MEDIA = tempfile.mkdtemp(prefix='parliament-test-media-')


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class ServeMediaTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.makedirs(os.path.join(_TMP_MEDIA, 'legislation_docs'), exist_ok=True)
        with open(os.path.join(_TMP_MEDIA, 'legislation_docs', 'doc.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 test payload')

    def setUp(self):
        self.client = Client()
        self.member = ParliamentUser.objects.create_user(
            user_id='sm1', name='Media Member', username='sm1',
            member_type='Member')
        self.url = reverse('serve_media',
                           kwargs={'path': 'legislation_docs/doc.pdf'})

    def test_anonymous_is_redirected_to_login(self):
        """THE regression test: /media/ must never serve without a session."""
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login/', resp['Location'])

    def test_logged_in_member_gets_file(self):
        self.client.force_login(self.member)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['Content-Type'], 'application/pdf')
        self.assertIn('private', resp['Cache-Control'])
        self.assertEqual(b''.join(resp.streaming_content),
                         b'%PDF-1.4 test payload')

    def test_traversal_is_blocked(self):
        self.client.force_login(self.member)
        resp = self.client.get(
            '/media/legislation_docs/%2e%2e/%2e%2e/etc/passwd')
        self.assertIn(resp.status_code, (404, 400))

    def test_missing_file_404s(self):
        self.client.force_login(self.member)
        resp = self.client.get(reverse('serve_media',
                                       kwargs={'path': 'nope/missing.pdf'}))
        self.assertEqual(resp.status_code, 404)

    def test_awkward_filename_headers(self):
        """v3.14.2: spaces/quotes/non-ASCII in an uploaded filename must
        yield a quoted X-Accel URI and an RFC 5987 Content-Disposition."""
        fname = 'Résolution "Fall" 2026.pdf'
        with open(os.path.join(_TMP_MEDIA, 'legislation_docs', fname), 'wb') as f:
            f.write(b'%PDF-1.4 awkward')
        self.client.force_login(self.member)
        url = reverse('serve_media',
                      kwargs={'path': f'legislation_docs/{fname}'})

        # FileResponse mode: header must parse (no raw quote/space breakage)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        cd = resp['Content-Disposition']
        self.assertTrue(cd.startswith('inline'))
        self.assertNotIn('"Fall"', cd)  # raw inner quotes would corrupt the header
        self.assertIn("filename*=utf-8''", cd)  # RFC 5987 form present

        # Accel mode: internal URI must be percent-quoted, no raw specials
        with mock.patch('src.view.serve_media.MEDIA_ACCEL_PREFIX',
                        '/internal_media'):
            resp = self.client.get(url)
        accel = resp['X-Accel-Redirect']
        self.assertTrue(accel.startswith('/internal_media/legislation_docs/'))
        for raw in (' ', '"'):
            self.assertNotIn(raw, accel)
        self.assertIn('%20', accel)

    def test_accel_redirect_mode(self):
        """With MEDIA_ACCEL_PREFIX set, Django delegates the byte-shoving to
        nginx via X-Accel-Redirect but still enforces auth first."""
        self.client.force_login(self.member)
        with mock.patch('src.view.serve_media.MEDIA_ACCEL_PREFIX',
                        '/internal_media'):
            resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp['X-Accel-Redirect'],
                         '/internal_media/legislation_docs/doc.pdf')
        # And anonymous still bounces even in accel mode
        self.client.logout()
        with mock.patch('src.view.serve_media.MEDIA_ACCEL_PREFIX',
                        '/internal_media'):
            resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 302)


@override_settings(MEDIA_ROOT=_TMP_MEDIA)
class PrivateMediaDirectoriesAreNotServedHere(TestCase):
    """
    ⚠️ v3.19.5 — `/media/` MUST NOT SERVE A LEGISLATION DRAFT, and this class is
    the regression test for a finding that was reported FIXED and was not.

    v3.19.3 found that any authenticated member could fetch any member's private
    draft attachment at `/media/legislation_drafts/<name>`. It fixed that by
    building `serve_legislation_draft_document` (author-scoped, via
    `_get_own_draft`) and repointing both templates at it — and the 08-08 review
    closed the finding after confirming no template still referenced
    `draft.document.url`.

    **Removing the link is not removing the route.** `media/<path:path>` was
    untouched and still resolved anything under `MEDIA_ROOT`, so the only thing
    standing between a draft and any logged-in member was the uuid filename that
    v3.19.3 labelled, in four places, *"defence in depth, explicitly NOT the
    access control"*. Files predating migration `0016` did not even have that:
    their names are `slugify()` of the uploaded filename.

    So these tests are deliberately about the ROUTE and not about the fix. They
    do not construct a `LegislationDraft` at all — a file sitting in the
    directory is enough, which is the point: **orphaned draft files, of which
    this feature has produced several, have no row to be scoped by.**
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        os.makedirs(os.path.join(_TMP_MEDIA, 'legislation_drafts'), exist_ok=True)
        # Two names: the post-0016 uuid shape, and the pre-0016 slug shape that
        # is guessable from a bill's title. Both must be refused.
        for name in ('deadbeefdeadbeefdeadbeefdeadbeef.pdf',
                     'dues-restructuring-amendment.pdf'):
            with open(os.path.join(_TMP_MEDIA, 'legislation_drafts', name), 'wb') as f:
                f.write(b'%PDF-1.4 private draft')

        # The control's fixture is built HERE and not borrowed from
        # `ServeMediaTests`, which writes the same file. Test class execution
        # order is not guaranteed, and a control that only exists when another
        # class happened to run first is a control that reports "refused"
        # (correct-looking) when it should report "served".
        os.makedirs(os.path.join(_TMP_MEDIA, 'legislation_docs'), exist_ok=True)
        with open(os.path.join(_TMP_MEDIA, 'legislation_docs', 'doc.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 test payload')

        # Near-miss: a public directory whose name starts with a private one.
        # `legislation_drafts` must not match `legislation_drafts_public` — a
        # `startswith` check would fail this and nothing else would notice.
        os.makedirs(os.path.join(_TMP_MEDIA, 'legislation_drafts_public'), exist_ok=True)
        with open(os.path.join(_TMP_MEDIA, 'legislation_drafts_public', 'ok.pdf'), 'wb') as f:
            f.write(b'%PDF-1.4 public')

    def setUp(self):
        self.client = Client()
        self.member = ParliamentUser.objects.create_user(
            user_id='pm1', name='Nosy Member', username='pm1',
            member_type='Member')
        self.client.force_login(self.member)

    def test_a_member_cannot_fetch_a_draft_by_its_uuid_name(self):
        resp = self.client.get(reverse('serve_media', kwargs={
            'path': 'legislation_drafts/deadbeefdeadbeefdeadbeefdeadbeef.pdf'}))
        self.assertEqual(resp.status_code, 404)

    def test_a_member_cannot_fetch_a_pre_0016_draft_by_its_guessable_name(self):
        """The population `0016` deliberately declined to rename."""
        resp = self.client.get(reverse('serve_media', kwargs={
            'path': 'legislation_drafts/dues-restructuring-amendment.pdf'}))
        self.assertEqual(resp.status_code, 404)

    def test_the_prefix_check_reads_the_resolved_path_not_the_request(self):
        """
        ⚠️ The assertion that makes the guard worth having.

        `legislation_docs/../legislation_drafts/x.pdf` has a first segment of
        `legislation_docs` and resolves into the private directory. A check
        written against `path` rather than against what `path` resolves to would
        pass this and serve the file — which is the same "checked the input, not
        the value" shape as the finding this class exists for.
        """
        resp = self.client.get(
            '/media/legislation_docs/../legislation_drafts/'
            'deadbeefdeadbeefdeadbeefdeadbeef.pdf')
        self.assertIn(resp.status_code, (404, 400))

        # Same thing with the separators percent-encoded, so the check cannot be
        # satisfied by Django's URL resolver normalising the path for us.
        resp = self.client.get(
            '/media/legislation_docs/%2e%2e/legislation_drafts/'
            'deadbeefdeadbeefdeadbeefdeadbeef.pdf')
        self.assertIn(resp.status_code, (404, 400))

    def test_it_is_refused_in_accel_mode_too(self):
        """
        The X-Accel path hands the URI to nginx and never touches the bytes, so a
        guard placed after the response is built would leak here and nowhere
        else. Nothing about this may depend on which serving mode is configured.
        """
        with mock.patch('src.view.serve_media.MEDIA_ACCEL_PREFIX',
                        '/internal_media'):
            resp = self.client.get(reverse('serve_media', kwargs={
                'path': 'legislation_drafts/deadbeefdeadbeefdeadbeefdeadbeef.pdf'}))
        self.assertEqual(resp.status_code, 404)
        self.assertNotIn('X-Accel-Redirect', resp)

    def test_the_control_a_public_media_directory_still_works(self):
        """
        The negative control, and it is not decoration: a guard that refused
        everything would pass every assertion above. `legislation_docs/` is the
        directory `/media/`'s promise is actually correct for.
        """
        resp = self.client.get(reverse('serve_media', kwargs={
            'path': 'legislation_docs/doc.pdf'}))
        self.assertEqual(resp.status_code, 200)

    def test_the_control_a_directory_whose_name_merely_starts_with_a_private_one(self):
        """
        ⚠️ The second control, and the reason the check splits on the path
        separator instead of using `startswith`.

        `legislation_drafts_public/` is not `legislation_drafts/`. A prefix
        comparison would refuse it, and the failure would be invisible — refusing
        to serve a file looks exactly like the guard working.
        """
        resp = self.client.get(reverse('serve_media', kwargs={
            'path': 'legislation_drafts_public/ok.pdf'}))
        self.assertEqual(resp.status_code, 200)

    def test_the_private_set_names_only_directories_that_have_their_own_view(self):
        """
        ⚠️ The property, not the instance — the fifth time this codebase has had
        to make that move (a call site, a branch, a column, a resource, a route).

        An entry in `PRIVATE_MEDIA_PREFIXES` says "this is served somewhere
        else". If it is not served anywhere else, the entry is not a redaction,
        it is a feature deletion — and the failure would be invisible, because
        refusing to serve a file looks exactly like the fix working.
        """
        from django.urls import get_resolver

        from src.view.serve_media import PRIVATE_MEDIA_PREFIXES

        self.assertTrue(PRIVATE_MEDIA_PREFIXES, 'The set must not be empty.')

        # Every prefix needs a named route that is NOT serve_media.
        #
        # v3.19.6 — five more entries. The set grew from one to six because
        # nothing had ever enumerated the population it is drawn from; see
        # `src/test_media_classification.py`, which now does, and note that THIS
        # test could not have caught the gap — it guards entries that are in the
        # set and is blind to a directory that was never added.
        route_names = set(get_resolver().reverse_dict.keys())
        expected = {
            'legislation_drafts': 'legislation_draft_document',
            'kai_reports': 'kai_report_attachment',
            # v3.28.9 — commendations, same rule one line up.
            'kai_commendations': 'kai_commendation_attachment',
            'slating': 'slating_gpa_screenshot',
            'excuse_documents': 'excuse_document',
            'service_hours': 'service_hours_attachment',
            'bug_reports': 'bug_report_screenshot',
        }
        for prefix in PRIVATE_MEDIA_PREFIXES:
            self.assertIn(
                prefix, expected,
                f'{prefix!r} was added to PRIVATE_MEDIA_PREFIXES without '
                f'recording which view serves it instead. Add it here.')
            self.assertIn(
                expected[prefix], route_names,
                f'{prefix!r} is refused by /media/ but {expected[prefix]!r} is '
                f'not routed — the files are now unreachable by anyone.')
