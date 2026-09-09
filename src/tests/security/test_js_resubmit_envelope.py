"""
v3.29.33 — `JsResubmitEnvelopeMiddleware` (src/middleware/js_resubmit.py).

Context: the 09-08-26 mobile-CSRF saga's actual fix (v3.29.30,
`changelogs/v3.29.28.md`'s Addendum 4) replaced a second native
`form.submit()` — proven to silently serialize an empty body in WebKit —
with `fetch()` + `FormData` in `templates/base.html`. That fix was
correct about the WebKit bug, and introduced a different one: `fetch()`'s
default `redirect: 'follow'` makes the browser silently execute the
redirect TARGET request *inside* the fetch call, before the calling JS
ever sees a response — and that target request is a real page render
that consumes Django's one-shot session flash message (every page
extends `base.html`, which renders `{% for message in messages %}`).
So the message was gone by the time the JS's own, separate
`window.location.href` navigation requested the page a second time —
found in the 09-09-26 auto-run review, verified below with the real
Django test client (not just reasoned about).

This file proves, behaviorally, against REAL views (not a synthetic
url_patterns fixture) that tagging a POST with the `X-Parliament-Resubmit`
header now makes the flash message arrive in the JSON envelope instead —
survives exactly once, not zero times (the bug) and not twice (the
naive "just don't consume it" non-fix, which would double-show it on
whatever page the browser's own subsequent navigation lands on).
"""
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse

from src.models import ParliamentUser

RESUBMIT_HEADER = {'HTTP_X_PARLIAMENT_RESUBMIT': '1'}


def make_user(user_id, member_type='Member', **kwargs):
    defaults = dict(name=f'User {user_id}', username=f'user_{user_id}', member_type=member_type)
    defaults.update(kwargs)
    return ParliamentUser.objects.create_user(user_id=user_id, password='testpass123', **defaults)


def _pdf(name='doc.pdf', label=b'x'):
    content = b'%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n% ' + label + b'\ntrailer\n%%EOF\n'
    return SimpleUploadedFile(name, content, content_type='application/pdf')


class TheMiddlewareOrderingTests(TestCase):
    """
    The class docstring on JsResubmitEnvelopeMiddleware explains why this
    matters: Django calls process_response in REVERSE of
    settings.MIDDLEWARE, so this middleware must be listed AFTER
    MessageMiddleware to run BEFORE it in the response phase — otherwise
    MessageMiddleware persists "unread" messages forward before this
    middleware gets a chance to consume them, and the fix regresses into
    showing every message TWICE instead of the zero times it showed
    before this file existed.
    """

    def test_js_resubmit_middleware_is_listed_after_message_middleware(self):
        middleware = list(settings.MIDDLEWARE)
        message_idx = middleware.index('django.contrib.messages.middleware.MessageMiddleware')
        js_resubmit_idx = middleware.index('src.middleware.js_resubmit.JsResubmitEnvelopeMiddleware')
        self.assertGreater(
            js_resubmit_idx, message_idx,
            'JsResubmitEnvelopeMiddleware must be listed AFTER MessageMiddleware '
            'in settings.MIDDLEWARE — response-phase processing runs in reverse, '
            'so this is what makes it run BEFORE MessageMiddleware when a response '
            'comes back through the stack.',
        )


class RedirectCaseTests(TestCase):
    """
    `bug_report` (submit_bug_report) answers a validation failure with
    `messages.error(...)` + `redirect('bug_report')` — the "success or
    failure both redirect" shape most of the ~20 file-upload views share.
    """

    def setUp(self):
        self.user = make_user('resubmit-1')
        self.client = Client()
        self.client.login(username='user_resubmit-1', password='testpass123')

    def test_without_the_header_behaves_exactly_as_before(self):
        """Control: an ordinary (non-fetch) POST must be completely untouched."""
        response = self.client.post(reverse('bug_report'), {'description': ''})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Content-Type'].split(';')[0], 'text/html')
        self.assertEqual(response.url, reverse('bug_report'))

    def test_with_the_header_the_redirect_becomes_a_json_envelope(self):
        response = self.client.post(
            reverse('bug_report'), {'description': ''}, **RESUBMIT_HEADER,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'].split(';')[0], 'application/json')
        data = response.json()
        self.assertEqual(data['redirect'], reverse('bug_report'))
        self.assertEqual(len(data['messages']), 1)
        self.assertIn('Please provide a description', data['messages'][0]['text'])
        self.assertEqual(data['messages'][0]['level'], 'error')

    def test_the_message_is_not_delivered_a_second_time(self):
        """
        THE ACTUAL BUG THIS FIXES, proven end to end: the message must
        show up exactly once — in the envelope — and NOT again on a
        later, ordinary request to the same URL (which is what a naive
        "read the messages but don't mark them used" non-fix would do,
        and what the original bug's opposite failure mode — reading them
        via fetch()'s invisible redirect-follow and then never showing
        them at all — also was not).
        """
        self.client.post(reverse('bug_report'), {'description': ''}, **RESUBMIT_HEADER)
        response = self.client.get(reverse('bug_report'))
        self.assertNotIn(b'Please provide a description', response.content)

    def test_a_successful_submission_also_arrives_as_an_envelope(self):
        response = self.client.post(
            reverse('bug_report'),
            {'description': 'Something is broken on the events page.'},
            **RESUBMIT_HEADER,
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['redirect'])
        self.assertEqual(len(data['messages']), 1)
        self.assertIn('Thank you', data['messages'][0]['text'])
        self.assertEqual(data['messages'][0]['level'], 'success')


class RenderInPlaceCaseTests(TestCase):
    """
    `upload_chapter_document` — the actual view this whole saga is
    about — answers a validation failure with `messages.error(...)` +
    `render(...)` at 200, NOT a redirect. This is the worse of the two
    original failure modes: `response.ok` was true for this 200, so the
    old code took the SUCCESS branch and navigated away, discarding the
    rendered error and leaving the member with zero explanation.
    """

    def setUp(self):
        self.officer = make_user('resubmit-officer', member_type='Officer')
        self.client = Client()
        self.client.login(username='user_resubmit-officer', password='testpass123')

    def test_a_validation_error_arrives_as_an_envelope_with_no_redirect(self):
        response = self.client.post(
            reverse('upload_chapter_document'),
            {'title': '', 'description': ''},  # no file, no title
            **RESUBMIT_HEADER,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'].split(';')[0], 'application/json')
        data = response.json()
        self.assertIsNone(data['redirect'])
        self.assertEqual(len(data['messages']), 1)
        self.assertIn('provide both a file and a title', data['messages'][0]['text'])
        self.assertEqual(data['messages'][0]['level'], 'error')

    def test_without_the_header_the_same_error_renders_html_at_200(self):
        """Control: confirms the untagged request is untouched — this really is a 200 render, not a redirect, for this view."""
        response = self.client.post(
            reverse('upload_chapter_document'), {'title': '', 'description': ''},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'].split(';')[0], 'text/html')
        self.assertIn(b'Please provide both a file and a title', response.content)

    def test_a_successful_upload_still_produces_a_redirect_in_the_envelope(self):
        response = self.client.post(
            reverse('upload_chapter_document'),
            {
                'title': 'Bylaws Draft', 'description': '', 'document_type': 'general',
                'file': _pdf(),
            },
            **RESUBMIT_HEADER,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'].split(';')[0], 'application/json')
        data = response.json()
        self.assertEqual(data['redirect'], reverse('manage_chapter_documents'))
        self.assertEqual(len(data['messages']), 1)
        self.assertEqual(data['messages'][0]['level'], 'success')


class PassthroughTests(TestCase):
    """Everything this middleware must NOT touch."""

    def setUp(self):
        self.user = make_user('resubmit-passthrough')
        self.client = Client()
        self.client.login(username='user_resubmit-passthrough', password='testpass123')

    def test_get_requests_are_never_wrapped_even_with_the_header(self):
        response = self.client.get(reverse('bug_report'), **RESUBMIT_HEADER)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'].split(';')[0], 'text/html')

    def test_a_403_is_not_wrapped(self):
        """
        A non-officer hitting an officer-only upload view with the
        header still gets the app's ordinary 403 (`officer_required`
        returns HttpResponseForbidden for an authenticated non-officer,
        not a redirect) — the JS's existing `!response.ok` branch already
        handles this with a generic toast, and there's no view-specific
        message to relay.
        """
        response = self.client.post(
            reverse('upload_chapter_document'), {'title': 'x'}, **RESUBMIT_HEADER,
        )
        self.assertEqual(response.status_code, 403)
        self.assertNotEqual(response.get('Content-Type', '').split(';')[0], 'application/json')
