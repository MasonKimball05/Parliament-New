"""
Smoke test: GET every zero-argument URL as an admin and fail on any 5xx.

WHY THIS EXISTS
---------------
v3.16.3 added `TemplateUrlNameTests`, which scans every literal `{% url 'x' %}`
in `templates/` and asserts `x` is a registered route name. Its own docstring
records the gap it knowingly left:

    knowingly does not check *arguments*

That gap is not theoretical. `templates/guide/officers/recruitment.html` called
`{% url 'recruitment_dashboard' %}` with no arguments against a route defined as
`committee/<code>/recruitment/`. The NAME is real, so the scan passed — and the
page was a hard 500 the whole time, on the very guide page v3.16.3 had just
"fixed". This test found it on its first run (07-29-26).

Actually requesting the page is what closes the gap: it catches wrong arity,
wrong argument types, missing context, and — the reason it was written during
the v3.17.3 join sweep — a bad `defer()`/`only()` field name, which Django
raises as `FieldError` at query-compile time rather than at import.

WHAT IT DOES NOT COVER
----------------------
Only routes that reverse with no arguments (282 of them; ~930 take arguments and
are skipped). Detail pages therefore need their own tests. It also only exercises
the admin's view of each page — a permission-dependent branch that only members
see is not reached.
"""

from django.test import Client, TestCase
from django.urls import NoReverseMatch, get_resolver, reverse

from src.models import ParliamentUser

#: Routes that cannot pass under the test settings, with the reason. Keep this
#: list short and each entry justified — it is an allowlist for real failures,
#: and `test_known_failures_are_still_failing` makes leaving a stale entry here
#: a test failure rather than a permanent exemption nobody revisits.
#:
#: It had exactly one entry when this file was written: `home`, because
#: `src/view/home.py` filtered `Q(visible_to__contains=[...])` on a JSONField
#: and JSON containment is unsupported on SQLite, so the documented local-dev
#: setup could not open the home page at all. That was fixed in the same
#: release (`src/utils/visibility.py`) and the guard test is what told us to
#: remove it from here. Empty is the target state.
KNOWN_FAILURES = {}


class ZeroArgumentUrlSmokeTests(TestCase):
    """Every page an admin can reach without arguments must not 5xx."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = ParliamentUser.objects.create(
            user_id='smoke-admin', name='Smoke Admin', username='smokeadmin',
            member_type='Officer', member_status='Active', is_admin=True,
        )
        cls.admin.set_password('smoke-pass-12345!')
        cls.admin.save()

    def _zero_argument_url_names(self):
        names = set()

        def walk(resolver, prefix=''):
            for key in resolver.reverse_dict:
                if isinstance(key, str):
                    names.add(prefix + key)
            for namespace, (_, sub) in getattr(resolver, 'namespace_dict', {}).items():
                walk(sub, f'{prefix}{namespace}:')

        walk(get_resolver())
        return sorted(names)

    def test_no_zero_argument_page_raises_or_500s(self):
        client = Client()
        client.force_login(self.admin)

        checked, failures = 0, []
        for name in self._zero_argument_url_names():
            try:
                url = reverse(name)
            except NoReverseMatch:
                continue                       # takes arguments — out of scope
            if url.startswith('/admin/') or 'logout' in url:
                continue
            if name in KNOWN_FAILURES:
                continue
            try:
                response = client.get(url)
            except Exception as exc:           # noqa: BLE001 — that's the point
                failures.append(f'{name} ({url}) raised {type(exc).__name__}: {exc}')
                continue
            checked += 1
            if response.status_code >= 500:
                failures.append(f'{name} ({url}) returned {response.status_code}')

        self.assertGreater(checked, 200, 'expected to exercise most of the site')
        self.assertEqual(failures, [], 'pages that error for an admin')

    def test_known_failures_are_still_failing(self):
        """
        If an allowlisted route starts working, take it off the list rather than
        leaving a permanent exemption nobody revisits.
        """
        client = Client()
        client.force_login(self.admin)
        for name, reason in KNOWN_FAILURES.items():
            with self.subTest(name=name):
                try:
                    response = client.get(reverse(name))
                except Exception:              # noqa: BLE001 — still broken, as recorded
                    continue
                self.assertGreaterEqual(
                    response.status_code, 500,
                    f'{name} now works — remove it from KNOWN_FAILURES ({reason})',
                )


class NoNPlusOneOnZeroArgumentPagesTests(TestCase):
    """
    No page may repeat a query shape 4+ times.

    Same 282 pages as above, but watching the queries instead of the status
    code. Literals are stripped so `WHERE id = 1` and `WHERE id = 2` collapse
    to one shape; three repeats is a page legitimately fetching three things,
    four upward is a loop. That is dev mode's own N+1 heuristic, run in CI
    instead of only when a developer happens to open the panel.

    Added in v3.17.3 (second pass), after the first pass narrowed every
    *existing* member join and then found that the home page still fired one
    member fetch per announcement — because a queryset with no `select_related`
    has no join to narrow and so was invisible to that scan. "Every join is
    narrow" and "every dereference is joined" are different properties; this
    test is the second one.

    Deliberately fixture-driven rather than assertion-free: six rows of each
    kind, because an N+1 is invisible with one row.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import timedelta

        from django.utils import timezone

        from src.models import (ActivityLog, Announcement, Committee, Event,
                                Legislation, Vote)

        cls.admin = ParliamentUser.objects.create(
            user_id='npo-admin', name='NPO Admin', username='npoadmin',
            member_type='Officer', member_status='Active', is_admin=True,
        )
        cls.admin.set_password('npo-pass-12345!')
        cls.admin.save()

        members = []
        for i in range(6):
            member = ParliamentUser.objects.create(
                user_id=f'npo-{i}', name=f'Member {i}', username=f'npo{i}',
                member_type='Member', member_status='Active',
            )
            member.set_password('npo-pass-12345!')
            member.save()
            members.append(member)

        now = timezone.now()
        for i in range(6):
            Announcement.objects.create(
                title=f'A{i}', content='c', posted_by=members[i], is_active=True)
            Event.objects.create(
                title=f'E{i}', description='d', date_time=now + timedelta(days=i + 1),
                created_by=members[i], is_active=True)
            legislation = Legislation.objects.create(
                title=f'L{i}', description='d', posted_by=members[i],
                available_at=now - timedelta(days=i + 1),
                voting_ended_at=now - timedelta(days=i), voting_closed=True,
                status='passed', passed=True, vote_mode='percentage',
                required_percentage='50')
            Vote.objects.create(
                user=members[i], legislation=legislation, vote_choice='yes')
            ActivityLog.objects.create(
                action_type='login', user=members[i], description=f'entry {i}')
            committee = Committee.objects.create(
                name=f'C{i}', code=f'npo{i}', is_active=True)
            committee.members.add(*members[:3])
            committee.chairs.add(members[0])

    def test_no_page_repeats_a_query_shape(self):
        import re
        from collections import Counter

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        literal = re.compile(r"('[^']*'|\b\d+\b)")
        client = Client()
        client.force_login(self.admin)

        offenders = []
        for name in ZeroArgumentUrlSmokeTests._zero_argument_url_names(self):
            try:
                url = reverse(name)
            except NoReverseMatch:
                continue
            if url.startswith('/admin/') or 'logout' in url or name in KNOWN_FAILURES:
                continue
            try:
                with CaptureQueriesContext(connection) as ctx:
                    response = client.get(url)
            except Exception:
                continue                       # covered by the smoke test above
            if response.status_code >= 400:
                continue
            shapes = Counter(literal.sub('?', q['sql']) for q in ctx.captured_queries)
            worst_shape, worst_count = shapes.most_common(1)[0] if shapes else ('', 0)
            if worst_count >= 4:
                table = re.search(r'FROM "(\w+)"', worst_shape)
                offenders.append(
                    f'{name} ({url}): {worst_count}× '
                    f'{table.group(1) if table else worst_shape[:60]}'
                )

        self.assertEqual(offenders, [], 'pages with a repeated query shape (N+1)')
