"""
Geo-restricted exports, and the two event sign-up views that were 500ing.

WHAT WENT WRONG (found 07-30-26, fixed v3.17.5)
-----------------------------------------------
Two findings, both landing on `event_signup_export`.

**1. `GeoRestrictionMiddleware` could not express the route.** Its list was a
tuple of path *prefixes* matched with `path.startswith(...)`. Every entry in it
happened to have a static prefix, which hid the fact that a route with a
parameter in the MIDDLE of it cannot be written as a prefix at all.
`event_signup_export` is `/calendar/event/<int:event_id>/signups/export/`, and
it writes Name + Email for every member signed up — the same class of bulk
member data as the directory and user-list exports already in the list. It
could not be added. The list is now keyed on resolved **URL name**, which is
parameter-agnostic and survives a route being re-pathed.

**2. The gate admitted Officers but not Chairs.** v3.17.3 revived both sign-up
views from a year-long `ModuleNotFoundError` (they imported
`src.utils.officer_check`, a module that has never existed) and reached for
`request.user.is_officer` — a real property, but NOT what the rest of the app
means by "officer": it is `member_type == 'Officer' or is_admin`, so a Chair got
a 403 on the sign-up list for an event they run. Every other officer view uses
`@officer_required`, which admits officers, chairs and admins. Because the views
500'd before reaching the check, this would have shipped as a first-appearance
bug the first time anyone loaded the page in production.

Using the decorator also routes the denial through `_gate()`, so it shows up in
dev mode's Perms tab and the authz log instead of raising a bare
`PermissionDenied`.
"""

from django.test import Client, TestCase
from django.urls import get_resolver, reverse
from django.utils import timezone

from src.middleware.geo_restriction import RESTRICTED_EXPORT_VIEWS
from src.models import Event, ParliamentUser


def make_user(uid, member_type, is_admin=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=f'User {uid}', username=uid,
        member_type=member_type, member_status='Active', is_admin=is_admin,
    )
    user.set_password('geo-test-pass-12345!')
    user.save()
    return user


def flag_session_as_foreign(client):
    """Mark the session the way a non-US login does."""
    session = client.session
    session['login_geo_suspicious'] = True
    session['login_geo_country'] = 'France'
    session['login_geo_city'] = 'Paris'
    session.save()


class RestrictedExportViewsAreRealRoutesTests(TestCase):
    """
    A name-keyed list is only as good as its names. A typo, or a route renamed
    out from under it, silently disables the control — so assert every entry
    resolves to something that exists.
    """

    def test_every_restricted_name_is_a_real_url_name(self):
        known = set()

        def walk(patterns):
            for pattern in patterns:
                nested = getattr(pattern, 'url_patterns', None)
                if nested is not None:
                    walk(nested)
                elif getattr(pattern, 'name', None):
                    known.add(pattern.name)

        walk(get_resolver().url_patterns)
        missing = sorted(RESTRICTED_EXPORT_VIEWS - known)
        self.assertEqual(
            missing, [],
            'geo-restricted view names that no longer exist — the restriction '
            'is silently off for these',
        )

    def test_the_bulk_member_data_exports_are_all_listed(self):
        """
        The specific gap that prompted v3.17.5. `event_signup_export` is a
        Name + Email roster; it belongs with the directory and user-list
        exports, and it is the one the prefix list could not reach.
        """
        for name in ('export_directory', 'export_user_list', 'event_signup_export'):
            self.assertIn(name, RESTRICTED_EXPORT_VIEWS, name)


class GeoRestrictionBlocksParameterisedExportsTests(TestCase):
    """The behavioural half — a path prefix could not have done this."""

    def setUp(self):
        self.officer = make_user('geo-officer', 'Officer')
        self.event = Event.objects.create(
            title='Founders Day',
            date_time=timezone.now() + timezone.timedelta(days=7),
            is_active=True,
            requires_signup=True,
            created_by=self.officer,
        )
        self.url = reverse('event_signup_export', args=[self.event.pk])

    def test_export_is_blocked_from_a_flagged_session(self):
        client = Client()
        client.force_login(self.officer)
        flag_session_as_foreign(client)
        response = client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_export_is_allowed_from_an_ordinary_session(self):
        client = Client()
        client.force_login(self.officer)
        response = client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])

    def test_the_signup_list_page_is_not_geo_blocked(self):
        """
        Only the *export* is bulk data. Blocking the page itself would be
        collateral damage, and asserting it keeps the list honest about what it
        is for.
        """
        client = Client()
        client.force_login(self.officer)
        flag_session_as_foreign(client)
        response = client.get(reverse('event_signup_list', args=[self.event.pk]))
        self.assertEqual(response.status_code, 200)

    def test_a_static_prefix_could_not_have_matched_this_route(self):
        """
        Documents *why* the list changed shape. If someone reverts to prefixes,
        this is the assertion that explains the failure.
        """
        path = reverse('event_signup_export', args=[self.event.pk])
        self.assertIn('/signups/export/', path)
        self.assertRegex(path, r'^/calendar/event/\d+/signups/export/$')


class EventSignupViewsAdmitChairsTests(TestCase):
    """
    The gate must match `@officer_required` — officers, chairs and admins — not
    the narrower `is_officer` property.
    """

    def setUp(self):
        self.officer = make_user('sg-officer', 'Officer')
        self.event = Event.objects.create(
            title='Rush Kickoff',
            date_time=timezone.now() + timezone.timedelta(days=3),
            is_active=True,
            requires_signup=True,
            created_by=self.officer,
        )

    def _get(self, user, name):
        client = Client()
        client.force_login(user)
        return client.get(reverse(name, args=[self.event.pk]))

    def test_chair_may_view_the_signup_list(self):
        chair = make_user('sg-chair', 'Chair')
        self.assertEqual(self._get(chair, 'event_signup_list').status_code, 200)

    def test_chair_may_export_the_roster(self):
        chair = make_user('sg-chair-x', 'Chair')
        self.assertEqual(self._get(chair, 'event_signup_export').status_code, 200)

    def test_officer_may_still_view_and_export(self):
        self.assertEqual(self._get(self.officer, 'event_signup_list').status_code, 200)
        self.assertEqual(self._get(self.officer, 'event_signup_export').status_code, 200)

    def test_ordinary_member_is_refused_both(self):
        member = make_user('sg-member', 'Member')
        self.assertEqual(self._get(member, 'event_signup_list').status_code, 403)
        self.assertEqual(self._get(member, 'event_signup_export').status_code, 403)

    def test_pledge_is_refused_both(self):
        pledge = make_user('sg-pledge', 'Pledge')
        self.assertEqual(self._get(pledge, 'event_signup_list').status_code, 403)
        self.assertEqual(self._get(pledge, 'event_signup_export').status_code, 403)

    def test_the_inline_is_officer_check_has_not_come_back(self):
        """
        A static guard. The inline check is easy to re-add and looks harmless;
        it is not, because it silently drops Chairs and skips `_gate()` logging.
        """
        import pathlib

        source = (pathlib.Path(__file__).resolve().parent.parent.parent / 'view' / 'calendar.py')
        text = source.read_text(encoding='utf-8')
        for line_no, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith('#'):
                continue
            self.assertNotIn(
                'if not request.user.is_officer', stripped,
                f'src/view/calendar.py:{line_no} — use @officer_required, which '
                f'admits Chairs and logs the denial',
            )


# ═══════════════════════════════════════════════════════════════════════════
#  v3.17.7 — the exports that are a MODE of a page, not a route
# ═══════════════════════════════════════════════════════════════════════════
#
# v3.17.5 moved this control from path prefixes to resolved URL names, which
# fixed "the parameter is in the middle of the path". It left the other shape
# standing: **an export triggered by a query parameter on an ordinary page.**
# Three existed, all carrying bulk member or security data:
#
#   * `poll_results`       + `?export=csv` → respondent names and every answer
#   * `admin_v2_audit_log` + `?export=csv` → actor, target, detail, IP address
#   * `bulk_actions_kai_reports` POST `bulk_action=export_csv`
#
# Only the third could join RESTRICTED_EXPORT_VIEWS — its URL name IS the
# export. Listing the other two would geo-block the poll results screen and the
# audit log viewer entirely, so those are guarded inside the view against
# `request.geo_suspicious` via `geo_export_blocked()`.
#
# WHY THE OLD COMPLETENESS TEST DID NOT NOTICE
# --------------------------------------------
# `test_the_bulk_member_data_exports_are_all_listed` asserts three hardcoded
# names are present. That is a regression guard for the gap v3.17.5 closed, not
# a completeness check — **it cannot fail for an export nobody thought of**, and
# the v3.17.5 changelog's claim that a sweep had found the only missing one was
# true only of routes *named* like exports. Searching for `text/csv` instead of
# for `name='…export…'` finds all three. `test_every_csv_export_is_geo_guarded`
# below does that, so the fourth one fails the suite instead of shipping.


class QueryParamExportsAreGeoGuardedTests(TestCase):
    """The behavioural half — a URL-name list could not have done this."""

    def setUp(self):
        from src.models import Announcement, AnnouncementPoll

        self.admin = make_user('geo-qp-admin', 'Officer', is_admin=True)
        self.announcement = Announcement.objects.create(
            title='Chapter vote', content='Body', posted_by=self.admin,
        )
        self.poll = AnnouncementPoll.objects.create(
            announcement=self.announcement, title='Chapter vote poll',
        )

    def _client(self, flagged):
        client = Client()
        client.force_login(self.admin)
        if flagged:
            flag_session_as_foreign(client)
        return client

    def _admin_v2_client(self, flagged):
        """
        `audit_log` sits behind `require_admin_v2_auth`, a two-factor gate: an
        env allowlist plus a separate authenticated session. Both halves have to
        be satisfied or the request 302s to the admin-v2 login before ever
        reaching the geo guard — which is what a first pass at this test found.
        """
        from unittest import mock

        from src.view import admin_v2

        patcher = mock.patch.object(
            admin_v2, 'ALLOWED_USER_IDS', {self.admin.user_id})
        patcher.start()
        self.addCleanup(patcher.stop)

        client = Client()
        client.force_login(self.admin)
        session = client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        if flagged:
            session['login_geo_suspicious'] = True
            session['login_geo_country'] = 'France'
            session['login_geo_city'] = 'Paris'
        session.save()
        return client

    def test_poll_results_export_is_blocked_from_a_flagged_session(self):
        url = reverse('poll_results', args=[self.announcement.id])
        response = self._client(flagged=True).get(url, {'export': 'csv'})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('text/csv', response.get('Content-Type', ''))

    def test_the_poll_results_PAGE_is_not_blocked(self):
        """
        The reason this guard lives in the view rather than in the name list.
        Blocking the export must not block the page it hangs off.
        """
        url = reverse('poll_results', args=[self.announcement.id])
        response = self._client(flagged=True).get(url)
        self.assertEqual(response.status_code, 200)

    def test_poll_results_export_is_allowed_from_an_ordinary_session(self):
        url = reverse('poll_results', args=[self.announcement.id])
        response = self._client(flagged=False).get(url, {'export': 'csv'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])

    def test_audit_log_export_is_blocked_from_a_flagged_session(self):
        response = self._admin_v2_client(flagged=True).get(
            reverse('admin_v2_audit_log'), {'export': 'csv'})
        self.assertEqual(response.status_code, 403)
        self.assertNotIn('text/csv', response.get('Content-Type', ''))

    def test_audit_log_export_is_allowed_from_an_ordinary_session(self):
        response = self._admin_v2_client(flagged=False).get(
            reverse('admin_v2_audit_log'), {'export': 'csv'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])

    def test_the_audit_log_PAGE_is_not_blocked(self):
        response = self._admin_v2_client(flagged=True).get(
            reverse('admin_v2_audit_log'))
        self.assertEqual(response.status_code, 200)


class KaiBulkExportIsGeoRestrictedTests(TestCase):
    """The third one, which the name list *can* reach — POST-only endpoint."""

    def test_bulk_actions_kai_reports_is_in_the_list(self):
        self.assertIn('bulk_actions_kai_reports', RESTRICTED_EXPORT_VIEWS)


class EveryCsvExportIsGeoGuardedTests(TestCase):
    """
    Discovery, not membership. Walks the codebase for anything that writes a
    CSV and fails unless it is either in RESTRICTED_EXPORT_VIEWS, guarded by
    `geo_export_blocked`, or allowlisted with a stated reason.

    This is the test that would have caught the three gaps above, and it is the
    one that will catch the fourth.
    """

    #: file path → reason it needs no geo guard
    ALLOWLIST = {
        'view/calendar.py': (
            'export_calendar_ical / export_event_ical are the requesting '
            "user's own calendar, not bulk member data. event_signup_export "
            'IS geo-restricted, by URL name.'
        ),
        'view/directory.py': (
            '_export_csv is a helper; its caller export_directory is in '
            'RESTRICTED_EXPORT_VIEWS.'
        ),
    }

    def test_every_csv_export_is_geo_guarded(self):
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent.parent
        unguarded = []

        for path in sorted((root / 'view').rglob('*.py')):
            rel = str(path.relative_to(root))
            source = path.read_text(errors='ignore')
            if 'text/csv' not in source:
                continue
            if rel in self.ALLOWLIST:
                continue
            if 'geo_export_blocked' in source:
                continue
            # Otherwise every csv-writing view in the file must be named in the
            # URL-name list.
            named = any(
                name in source for name in RESTRICTED_EXPORT_VIEWS
            )
            if not named:
                unguarded.append(rel)

        self.assertEqual(
            unguarded, [],
            'These files write a CSV but are neither in RESTRICTED_EXPORT_VIEWS '
            'nor guarded with geo_export_blocked(), and are not allowlisted:\n  '
            + '\n  '.join(unguarded)
            + '\nIf the export is a query-parameter mode of a page, guard it in '
              'the view; if it has its own route, add the URL name.',
        )

    def test_allowlist_entries_are_still_needed(self):
        """An allowlist that outlives its reason is a lie about the codebase."""
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent.parent
        for rel in self.ALLOWLIST:
            path = root / rel
            self.assertTrue(path.exists(), f'{rel} no longer exists')
            self.assertIn(
                'text/csv', path.read_text(errors='ignore'),
                f'{rel} no longer writes a CSV — drop it from the allowlist',
            )
