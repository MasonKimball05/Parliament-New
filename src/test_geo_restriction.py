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

        source = (pathlib.Path(__file__).resolve().parent / 'view' / 'calendar.py')
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
