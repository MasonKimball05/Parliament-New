"""
Guard: request every route signed out. Each one must refuse, or be on the
short list of pages that are public on purpose. None may crash.

WHY THIS EXISTS (v3.45.1, 10-09-26)
-----------------------------------
v3.45.0 added a helper to `src/view/officer/cnb.py` directly above
`add_amendment`, between that view and its first decorator:

    @login_required
    def _propose_section_title(request, resolution, section):    # new helper
        ...

    @require_POST
    def add_amendment(request, resolution_id):                   # lost its gate

`@login_required` ended up on the helper and `add_amendment` had none. Nothing
failed: every test of that view signs in first, and a signed-in request goes
through `login_required` on the helper unchanged. A signed-out POST reached
the view body and crashed on `AnonymousUser.has_cnb_permission` (a 500;
nothing was saved, by luck of which line came first).

The same sweep found two more views that crashed signed out
(`committee_manage_members`, `csp_report`). Both are in the tests below.

HOW IT WORKS
------------
The database is empty. A gated view answers a signed-out request before it
looks anything up, so it redirects to the login page (or answers 401 / 403 /
405). A view with no gate goes on to its body: it returns a page, or a 404
for the row that is not there, or it crashes. Any of those fails here unless
the route is listed in PUBLIC with the reason.

WHAT IT DOES NOT COVER
----------------------
  * Whether the WRONG signed-in member is refused. That is each view's own
    permission test.
  * The DRF router and the Django admin's regex routes (skipped; both have
    their own authentication).
  * The honeypot routes (skipped: they ban the caller, which is their job).

Run with: python manage.py test src.tests.guards.test_anonymous_access
"""
import json
import re
import uuid

from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import URLResolver, get_resolver, reverse
from django.views.generic import RedirectView

from src.models import (
    Article, Committee, GoverningDocument, IPBlacklist, ParliamentUser, Resolution,
    ResolutionAmendment, ResolutionStructureChange, Section,
)

#: Routes a signed-out visitor is meant to reach, keyed by the route as
#: written in the URLconf, each with the reason. `test_public_list_has_no_
#: stale_entries` fails when an entry no longer exists or has become gated, so
#: this cannot turn into a list of exemptions nobody revisits.
PUBLIC = {
    '': 'landing page',
    'contact/submit/': 'public contact form (rate-limited; see CLAUDE.md)',
    'login/': 'the login page',
    'accounts/login/': 'the login page (LOGIN_URL)',
    'admin/login/': "Django admin's own login page",
    'admin/logout/': "Django admin's own; signed out it redirects to /admin/",
    'password-reset/': 'password reset',
    'password-reset/done/': 'password reset',
    'password-reset-confirm/<uidb64>/<token>/': 'password reset; the token is the credential',
    'password-reset-complete/': 'password reset',
    'accounts/two-factor/recovery-confirm/<str:uidb64>/<str:token>/': '2FA recovery link from email; the token is the credential',
    'accounts/passkeys/authenticate/begin/': 'passkey sign-in, step 1',
    'accounts/passkeys/authenticate/complete/': 'passkey sign-in, step 2',
    'internal/csrf-token/': 'hands a fresh CSRF token to a page (v3.29.x)',
    'songbook/public/': 'public songbook',
    'songbook/public/<int:pk>/': 'public songbook',
    'changelog/': 'public changelog',
    'changelog/<str:version>/': 'public changelog',
    'roadmap/': 'public roadmap',
    'service-worker.js': 'fetched by the browser without a session',
    'calendar/feed/<str:token>/': 'calendar subscription; the token is the credential',
    'events/<int:event_id>/checkin/embed/<str:embed_token>/qr.svg': 'QR image; the token is the credential',
    'announcements/<int:announcement_id>/poll/qr/embed/<str:embed_token>/qr.svg': 'QR image; the token is the credential',
    'track/announcement/<int:announcement_id>/user/<str:user_id>/': 'email open pixel, loaded by a mail client',
    'track/event-reminder/<int:log_id>/user/<str:user_id>/': 'email open pixel, loaded by a mail client',
    'kai/track-email/<int:report_id>.gif': 'email open pixel; only a signed URL records anything (v3.38.2)',
    'kai/track-submitter-email/<int:report_id>.gif': 'email open pixel; only a signed URL records anything (v3.38.2)',
    'csp-report/': 'browsers post CSP reports with no session (see CLAUDE.md)',
    'api/health-check/': 'latency probe; returns a timestamp only',
    'site-monitor/static-assets/': 'go-sentinel; needs the site-monitor token (v3.35.3)',
    'api/v1/<drf_format_suffix:format>': 'DRF router root with a format suffix; 404 for the sample value',
}

#: Statuses that mean "refused" without a redirect.
REFUSED = {401, 403, 405}

SAMPLES = {'int': '1', 'str': 'x', 'slug': 'x', 'path': 'x', 'uuid': str(uuid.UUID(int=1))}


def _routes(resolver=None, prefix=''):
    """Every (route, pattern) in the URLconf, includes flattened."""
    for entry in (resolver or get_resolver()).url_patterns:
        if isinstance(entry, URLResolver):
            yield from _routes(entry, prefix + str(entry.pattern))
        else:
            yield prefix + str(entry.pattern), entry


def _skipped(route, pattern):
    callback = pattern.callback
    if getattr(callback, '__module__', '') == 'src.view.honeypot':
        return True
    if any(char in route for char in '(^\\'):           # regex routes: DRF router, admin
        return True
    # A legacy alias that only redirects to the real (gated) route.
    return getattr(callback, 'view_class', None) is RedirectView


def _url(route):
    return '/' + re.sub(r'<(?:(\w+):)?\w+>', lambda m: SAMPLES.get(m.group(1) or 'str', 'x'), route)


def _signed_out(method, url, **kwargs):
    """One signed-out request from a clean slate (no rate-limit or ban carried over)."""
    cache.clear()
    IPBlacklist.objects.all().delete()
    return getattr(Client(raise_request_exception=False), method)(url, **kwargs)


def _is_refusal(response):
    return _refuses(response.status_code, response.get('Location', ''))


def _refuses(status, location):
    if status in REFUSED:
        return True
    return status in (301, 302) and 'login' in location


class EveryRouteSignedOutTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        # (route, method, status, Location). Plain values: Django copies
        # class-level test data for each test, and a response cannot be copied.
        cls.results = []
        for route, pattern in _routes():
            if _skipped(route, pattern):
                continue
            for method in ('get', 'post'):
                response = _signed_out(method, _url(route))
                cls.results.append((route, method, response.status_code, response.get('Location', '')))

    def test_the_sweep_reaches_the_routes(self):
        # A walk that finds nothing passes everything.
        self.assertGreater(len(self.results), 2000, 'the sweep requested far fewer routes than the site has')

    def test_no_route_crashes_signed_out(self):
        crashed = sorted({f'{method.upper()} /{route}' for route, method, status, _ in self.results
                          if status >= 500})
        self.assertEqual(crashed, [], 'These routes return a 5xx to a signed-out request:\n  ' + '\n  '.join(crashed))

    def test_every_route_refuses_or_is_listed_as_public(self):
        open_routes = sorted({
            f'{method.upper()} /{route} -> {status}'
            for route, method, status, location in self.results
            if route not in PUBLIC and not _refuses(status, location)
        })
        self.assertEqual(
            open_routes, [],
            'These routes answered a signed-out request without sending it to the login page. '
            'Add the missing gate (check the decorator is on the VIEW, not on a helper above it), '
            'or list the route in PUBLIC with the reason:\n  ' + '\n  '.join(open_routes))

    def test_public_list_has_no_stale_entries(self):
        by_route = {}
        for route, method, status, location in self.results:
            by_route.setdefault(route, []).append(_refuses(status, location))
        stale = sorted(route for route in PUBLIC if route not in by_route or all(by_route[route]))
        self.assertEqual(stale, [], 'These PUBLIC entries are gone or now refuse signed-out requests; remove them:\n  '
                         + '\n  '.join(repr(route) for route in stale))


class ViewsThatCrashedSignedOutTests(TestCase):
    """The three found on 10-09-26, each with the row its view looks up present."""

    def test_add_amendment_sends_a_signed_out_post_to_login(self):
        chair = ParliamentUser.objects.create(
            user_id='ANON-1', name='Chair', username='anon1', member_type='Member',
            member_status='Active', is_admin=True, email='anon1@example.com')
        resolution = Resolution.objects.create(title='T', created_by=chair)
        document = GoverningDocument.objects.filter(doc_type='bylaws').first() \
            or GoverningDocument.objects.create(doc_type='bylaws', title='Bylaws')
        article = Article.objects.create(document=document, number='XCIX', title='Test', display_order=999)
        section = Section.objects.create(article=article, number='1', title='Dues', content='One two.',
                                         display_order=1)
        response = _signed_out('post', reverse('cnb_add_amendment', args=[resolution.pk]), data={
            'section_id': section.pk, 'proposed_text': 'One two three.', 'section_title': 'Fees'})
        self.assertTrue(_is_refusal(response), response.status_code)
        self.assertFalse(ResolutionAmendment.objects.exists())
        self.assertFalse(ResolutionStructureChange.objects.exists())

    def test_committee_chair_pages_send_a_signed_out_visitor_to_login(self):
        committee = Committee.objects.first() or Committee.objects.create(name='Anon Test', code='ANONT')
        for method in ('get', 'post'):
            response = _signed_out(method, reverse('manage_members', args=[committee.code]))
            self.assertTrue(_is_refusal(response), f'{method}: {response.status_code}')

    def test_csp_report_answers_400_to_a_body_that_is_not_a_report(self):
        bodies = [
            ('form-encoded', {'data': {'a': 'b'}}),
            ('a JSON list', {'data': '[]', 'content_type': 'application/reports+json'}),
            ('a JSON string', {'data': '"a"', 'content_type': 'application/json'}),
            ('a JSON number', {'data': '5', 'content_type': 'application/json'}),
        ]
        for label, kwargs in bodies:
            response = _signed_out('post', reverse('csp_report'), **kwargs)
            self.assertEqual(response.status_code, 400, label)

    def test_csp_report_accepts_fields_that_are_not_text(self):
        from src.models import CSPViolation
        body = json.dumps({'csp-report': {'blocked-uri': 5, 'violated-directive': None, 'line-number': 12}})
        response = _signed_out('post', reverse('csp_report'), data=body, content_type='application/csp-report')
        self.assertEqual(response.status_code, 204)
        violation = CSPViolation.objects.get()
        self.assertEqual((violation.blocked_uri, violation.violated_directive, violation.line_number), ('5', '', '12'))

    def test_csp_report_still_records_an_ordinary_report(self):
        from src.models import CSPViolation
        body = json.dumps({'csp-report': {'blocked-uri': 'https://example.com/x.js',
                                          'violated-directive': 'script-src'}})
        response = _signed_out('post', reverse('csp_report'), data=body, content_type='application/csp-report')
        self.assertEqual(response.status_code, 204)
        self.assertEqual(CSPViolation.objects.get().blocked_uri, 'https://example.com/x.js')
