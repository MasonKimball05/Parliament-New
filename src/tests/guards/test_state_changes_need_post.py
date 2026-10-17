"""
Guard: a view that changes data must not do it on a GET.

WHY THIS EXISTS (v3.45.3, 10-13-26)
-----------------------------------
Django's CSRF check only runs on POST, PUT, PATCH and DELETE. A view that
deletes or changes something on a GET has no CSRF protection at all. The
session cookie is `SameSite=Lax`, and Lax cookies ARE sent when a person
follows a link from another site. So a link in an email, a text or any web
page could make a signed-in member's browser do the action for them.

Four views did that, although every page that uses them already sends a POST:

  * `delete_kai_template`      a Kai chair following a link deleted a template.
  * `toggle_document_publish`  a committee chair following a link published a
                               committee document to the whole chapter (or
                               took it down).
  * `cancel_excuse`            a member following a link deleted their own
                               pending excuse.
  * `recalculate_committee_vote` the one that a page reached with a link; it
                               is now a small form.

HOW IT WORKS
------------
1. `EveryStateChangingRouteRefusesGetTests` reads each view's source. A route
   whose name contains a word like delete / toggle / remove / cancel must have
   `require_POST`, `require_http_methods`, or its own `request.method` check.
   Routes that are allowed to act on a GET are in `GET_IS_FINE`, each with the
   reason. A stale entry there fails the test.
2. The other tests request the four views above with a GET (must be 405 and
   change nothing) and with a POST (must still work).

WHAT IT DOES NOT COVER
----------------------
A view whose route name has none of the words in `VERBS`. The word list is a
net, not a proof.

Run with: python manage.py test src.tests.guards.test_state_changes_need_post
"""
import inspect
import re
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import URLResolver, get_resolver, reverse
from django.utils import timezone

from src.models import (
    AttendanceExcuse, Committee, CommitteeDocument, CommitteeLegislation, Event,
    KaiReportTemplate, ParliamentUser,
)

#: A route name containing one of these words changes something.
VERBS = {
    'delete', 'toggle', 'remove', 'reset', 'destroy', 'clear', 'approve', 'publish',
    'unpush', 'push', 'revoke', 'regenerate', 'release', 'dismiss', 'bulk', 'cancel',
    'recalculate', 'reject', 'withdraw', 'recuse', 'appoint', 'archive', 'restore',
    'purge', 'rotate', 'sync', 'mark', 'set', 'update', 'add', 'copy', 'assign',
}

#: Routes that may act on a GET, keyed by URL name, each with the reason.
GET_IS_FINE = {
    'two_factor_dismiss': 'hides the 2FA setup prompt for this session for an hour',
    'admin_v2_push_subscriptions': 'a list page; "push" is in its name, not its action',
    'admin:logout': "Django admin's own",
}

VIEW_CHECKS = re.compile(r'require_POST|require_http_methods|request\.method')


def _routes(resolver=None, prefix=''):
    for entry in (resolver or get_resolver()).url_patterns:
        if isinstance(entry, URLResolver):
            ns = f'{entry.namespace}:' if entry.namespace else ''
            yield from _routes(entry, prefix + ns)
        elif entry.name:
            yield prefix + entry.name, entry


def _acts_on_name(name):
    words = set(re.split(r'[_:-]', name))
    return bool(words & VERBS)


def _view_source(pattern):
    """Source of the view function. `getsource` includes its decorators."""
    callback = pattern.callback
    if hasattr(callback, 'view_class'):
        return None                      # class-based: method handlers are separate
    func = inspect.unwrap(callback)
    # A decorator written without functools.wraps (admin v2's
    # `require_admin_v2_auth`) hides the view; take it from the closure.
    while func.__name__ == 'wrapper' and func.__closure__:
        inner = [c.cell_contents for c in func.__closure__ if inspect.isfunction(c.cell_contents)]
        if not inner:
            break
        func = inspect.unwrap(inner[0])
    try:
        return inspect.getsource(func)
    except (OSError, TypeError):
        return None


class EveryStateChangingRouteRefusesGetTests(TestCase):
    def _offenders(self):
        found = {}
        for name, pattern in _routes():
            if name.split(':')[0] in ('admin', 'parliament_admin') or not _acts_on_name(name):
                continue
            source = _view_source(pattern)
            if source is None:
                continue
            if VIEW_CHECKS.search(source):
                continue
            found[name] = pattern
        return found

    def test_the_scan_finds_routes(self):
        checked = [n for n, _ in _routes() if _acts_on_name(n) and ':' not in n]
        self.assertGreater(len(checked), 150, 'the scan found far fewer routes than the site has')

    def test_state_changing_views_refuse_get(self):
        offenders = sorted(set(self._offenders()) - set(GET_IS_FINE))
        self.assertEqual(
            offenders, [],
            'These views change data and accept a GET, which has no CSRF check. '
            'Add @require_POST (and send a POST from the page), or list the route in '
            'GET_IS_FINE with the reason:\n  ' + '\n  '.join(offenders))

    def test_get_is_fine_has_no_stale_entries(self):
        names = {n for n, _ in _routes()}
        offenders = set(self._offenders())
        stale = sorted(n for n in GET_IS_FINE
                       if not n.startswith('admin:') and (n not in names or n not in offenders))
        self.assertEqual(stale, [], 'GET_IS_FINE entries that are gone or now refuse GET:\n  '
                         + '\n  '.join(stale))


class _Base(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.chair = ParliamentUser.objects.create(
            user_id='POST-1', name='Chair', username='post1', member_type='Officer',
            member_status='Active', is_admin=True, email='post1@example.com')
        cls.member = ParliamentUser.objects.create(
            user_id='POST-2', name='Member', username='post2', member_type='Member',
            member_status='Active', email='post2@example.com')
        cls.committee = Committee.objects.create(name='Post Test', code='POSTT', is_active=True)
        cls.committee.members.add(cls.chair)
        cls.committee.chairs.add(cls.chair)

    def client_for(self, user):
        client = Client()
        client.force_login(user)
        return client


class DeleteKaiTemplateTests(_Base):
    def setUp(self):
        kai = Committee.objects.filter(is_kai_committee=True).first() \
            or Committee.objects.create(name='Kai', code='KAIPT', is_kai_committee=True)
        kai.chairs.add(self.chair)
        self.template = KaiReportTemplate.objects.create(
            name='T', description='d', title_template='t', description_template='d')

    def test_get_is_refused_and_deletes_nothing(self):
        response = self.client_for(self.chair).get(reverse('delete_kai_template', args=[self.template.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(KaiReportTemplate.objects.filter(pk=self.template.pk).exists())

    def test_post_still_deletes(self):
        response = self.client_for(self.chair).post(reverse('delete_kai_template', args=[self.template.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(KaiReportTemplate.objects.filter(pk=self.template.pk).exists())


class ToggleDocumentPublishTests(_Base):
    def setUp(self):
        self.document = CommitteeDocument.objects.create(
            committee=self.committee, title='Private', uploaded_by=self.chair, document='x.pdf')

    def test_get_is_refused_and_publishes_nothing(self):
        response = self.client_for(self.chair).get(
            reverse('toggle_document_publish', args=[self.committee.code, self.document.pk]))
        self.assertEqual(response.status_code, 405)
        self.document.refresh_from_db()
        self.assertFalse(self.document.published_to_chapter)

    def test_post_still_publishes(self):
        self.client_for(self.chair).post(
            reverse('toggle_document_publish', args=[self.committee.code, self.document.pk]))
        self.document.refresh_from_db()
        self.assertTrue(self.document.published_to_chapter)


class CancelExcuseTests(_Base):
    def setUp(self):
        event = Event.objects.create(title='Meeting', description='d', created_by=self.chair,
                                     date_time=timezone.now() + timedelta(days=2), is_active=True)
        self.excuse = AttendanceExcuse.objects.create(event=event, user=self.member, reason='Exam')

    def test_get_is_refused_and_keeps_the_excuse(self):
        response = self.client_for(self.member).get(reverse('cancel_excuse', args=[self.excuse.pk]))
        self.assertEqual(response.status_code, 405)
        self.assertTrue(AttendanceExcuse.objects.filter(pk=self.excuse.pk).exists())

    def test_post_still_cancels(self):
        self.client_for(self.member).post(reverse('cancel_excuse', args=[self.excuse.pk]))
        self.assertFalse(AttendanceExcuse.objects.filter(pk=self.excuse.pk).exists())


class RecalculateCommitteeVoteTests(_Base):
    def setUp(self):
        now = timezone.now()
        self.vote = CommitteeLegislation.objects.create(
            committee=self.committee, title='V', description='d', posted_by=self.chair,
            available_at=now - timedelta(days=1), voting_closed=True, voting_ended_at=now)

    def test_get_is_refused(self):
        response = self.client_for(self.chair).get(reverse('recalculate_committee_vote', args=[self.vote.pk]))
        self.assertEqual(response.status_code, 405)

    def test_post_still_runs(self):
        response = self.client_for(self.chair).post(
            reverse('recalculate_committee_vote', args=[self.vote.pk]),
            {'next': reverse('chapter_documents')})
        self.assertRedirects(response, reverse('chapter_documents'), fetch_redirect_response=False)

    def test_chapter_documents_page_sends_a_post(self):
        from pathlib import Path
        source = Path('templates/chapter_documents.html').read_text()
        self.assertNotIn("href=\"{% url 'recalculate_committee_vote'", source)
        self.assertIn("action=\"{% url 'recalculate_committee_vote' vote.id %}\"", source)


class DismissAnnouncementTests(_Base):
    def test_missing_announcement_is_404_not_500(self):
        response = self.client_for(self.member).post(reverse('dismiss_announcement_api', args=[999999]))
        self.assertEqual(response.status_code, 404)


class LogoutTakesPostTests(_Base):
    """v3.45.3 (Mason, 10-13-26: "Let's update the logout")."""

    def test_get_shows_the_page_and_keeps_the_session(self):
        client = self.client_for(self.member)
        response = client.get(reverse('logout'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Log out?')
        self.assertIn('_auth_user_id', client.session)

    def test_post_signs_out(self):
        client = self.client_for(self.member)
        response = client.post(reverse('logout'))
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', client.session)

    def test_post_needs_the_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.member)
        response = client.post(reverse('logout'))
        self.assertEqual(response.status_code, 403)
        self.assertIn('_auth_user_id', client.session)

    def test_signed_out_get_goes_to_login(self):
        response = Client().get(reverse('logout'))
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)

    def test_the_nav_sends_a_post(self):
        from pathlib import Path
        source = Path('templates/base.html').read_text()
        self.assertNotIn("href=\"{% url 'logout' %}\"", source)
        self.assertEqual(source.count("action=\"{% url 'logout' %}\""), 2)


class AmendmentRefsMatchBySectionTests(TestCase):
    """
    v3.45.3 (Mason, 10-13-26): tracked amendment refs "randomly breaking".

    The edit page and the print preview looked for the exact text of a ref,
    "[Bylaws Art. II § 3 (Addition) - 2]". The number is the amendment's place
    in a list sorted by section, so adding an amendment on an earlier section
    renumbered every later ref; the type and scope parts change on save too.
    Both pages now match a ref by its section (cnb/_amendment_ref_match.html).
    Reproduced and checked in Chromium; this pins the wiring.
    """

    def read(self, name):
        from pathlib import Path
        return Path('templates/cnb/' + name).read_text()

    def test_both_pages_use_the_shared_matcher(self):
        for name in ('_ref_inserter.html', 'resolution_print.html'):
            source = self.read(name)
            self.assertIn('{% include "cnb/_amendment_ref_match.html" %}', source, name)
            self.assertIn('cnbAmendmentRefPattern(', source, name)

    def test_neither_page_matches_the_exact_text_any_more(self):
        self.assertNotIn('indexOf(code)', self.read('_ref_inserter.html'))
        self.assertNotIn('function buildMarker', self.read('resolution_print.html'))
