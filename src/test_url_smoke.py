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


#: A page fails if any single query shape repeats this many times.
#: See NoNPlusOneOnZeroArgumentPagesTests for how this number was chosen.
REPEAT_THRESHOLD = 3

#: (url_name, table) pairs reviewed on 07-30-26 and deliberately not fixed in
#: v3.17.5, each with the reason. This is an allowlist for *known* repeats, not
#: a place to silence new ones — `test_accepted_repeats_are_still_repeating`
#: fails if an entry stops being needed, so a fix removes its own exemption.
ACCEPTED_REPEATS = {
    # `user_has_device()` hits both device tables, and it is called by
    # Enforce2FAMiddleware, the `two_factor_status` context processor (on a cold
    # cache only — it caches for 5 min) and the profile view itself. Fixing it
    # means threading the middleware's result onto `request`; deferred because
    # it is a hot-path auth check and worth doing carefully, not at the end of a
    # release.
    ('profile', 'otp_totp_totpdevice'),
    ('profile', 'otp_static_staticdevice'),
    # Excuse pages fetch the same table per status bucket. Same shape as the
    # view_all_activity fix in this release; not batched yet.
    ('my_excuses', 'src_attendanceexcuse'),
    ('review_excuses', 'src_attendanceexcuse'),
    # ⚠️ v3.19.7 — `('admin_api_tokens', 'src_featureflag')` WAS HERE AND IS
    # DELETED, which is this guard working exactly as designed: the repeat is
    # gone, so the exemption goes with it.
    #
    # It is worth recording WHY it survived five months, because the entry read
    # plausibly and was wrong. It said the page *"reads three different flags
    # through the cached `FeatureFlag.is_feature_enabled`; the repeats are cache
    # misses on a cold cache"* — i.e. an artefact of the sweep, harmless in
    # production. Two of the three were raw `FeatureFlag.objects.get(name=…)`
    # calls in `src/view/api.py` that never touched the cache and therefore
    # repeated on a WARM cache too, on every request. The count was right; the
    # cause was wrong; and the wrong cause is what made it sound temporary.
    #
    # **An exemption is a claim about a mechanism, not about a number. Write the
    # mechanism down and check it, or the entry becomes permanent by sounding
    # reasonable.**
}

#: Transaction control statements are not queries anyone can fix.
_TRANSACTION_NOISE = ('BEGIN', 'COMMIT', 'SAVEPOINT', 'RELEASE', 'ROLLBACK')


def _is_transaction_noise(shape):
    return shape.strip().upper().startswith(_TRANSACTION_NOISE)


class ZeroArgumentUrlSmokeTests(TestCase):
    """Every page an admin can reach without arguments must not 5xx."""

    def setUp(self):
        # See the note in NoNPlusOneOnZeroArgumentPagesTests: visiting the whole
        # site fills caches that outlive the test database.
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)

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
        from django.core.cache import cache

        from src.models import IPBlacklist

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
            # See the note in NoNPlusOneOnZeroArgumentPagesTests: the sweep
            # blacklists its own IP partway through if we let it.
            cache.clear()
            IPBlacklist.objects.all().delete()
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
    No page may repeat a query shape 3+ times.

    Same 282 pages as above, but watching the queries instead of the status
    code. Literals are stripped so `WHERE id = 1` and `WHERE id = 2` collapse
    to one shape. That is dev mode's own N+1 heuristic, run in CI instead of
    only when a developer happens to open the panel.

    v3.17.5 tightened this in two ways, both measured before choosing:

    * **Every repeated shape counts, not just the worst one.** It used to test
      `shapes.most_common(1)`, so a page with three *different* shapes repeating
      three times each passed clean while a page with one shape at four failed.
    * **Threshold 4 -> 3.** Measured across the whole sweep: at >=2 there are
      **489** hits (a count plus a fetch of the same table is normal, so 2 is
      noise); at >=3 there are **6**; at >=4, none. Three is where the signal
      is.

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
                                Legislation, Role, Vote)

        cls.admin = ParliamentUser.objects.create(
            user_id='npo-admin', name='NPO Admin', username='npoadmin',
            member_type='Officer', member_status='Active', is_admin=True,
        )
        cls.admin.set_password('npo-pass-12345!')
        cls.admin.save()

        # v3.17.3: member_type is spread across all four kinds on purpose. The
        # directory renders a separate section per type, and with six members
        # all of one type the other sections were empty — so the role-badge
        # N+1 inside them never fired and `/directory/` looked clean here while
        # doing 12 extra queries in production. Same failure mode as the missing
        # `role` below: a fixture that doesn't vary the data doesn't reach the
        # code.
        member_types = ['Officer', 'Chair', 'Member', 'Advisor']
        members = []
        for i in range(6):
            member = ParliamentUser.objects.create(
                user_id=f'npo-{i}', name=f'Member {i}', username=f'npo{i}',
                member_type=member_types[i % len(member_types)],
                member_status='Active',
            )
            member.set_password('npo-pass-12345!')
            member.save()
            members.append(member)
            # Roles drive the directory's badge block and several dashboards.
            member.roles.add(
                Role.objects.create(name=f'NPO Role {i}', code=f'npor{i}'))

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
            # v3.17.3: committees are built WITH a `role` and with every
            # membership relation populated, because the first version of this
            # fixture did neither and missed a real N+1 as a result:
            # `Committee.get_vp()` returns early when `role_id` is None, so the
            # committee index looked clean here while doing two queries per
            # committee in production. A fixture that skips the optional FK
            # tests the early-return path, not the page.
            role = Role.objects.create(name=f'NPO VP {i}', code=f'npovp{i}')
            members[i % len(members)].roles.add(role)
            committee = Committee.objects.create(
                name=f'C{i}', code=f'npo{i}', is_active=True, role=role)
            committee.members.add(*members[:3])
            committee.chairs.add(members[0])
            committee.advisors.add(members[1])
            committee.voting_members.add(members[2])

        cls._seed_v3_17_5_families(members)

    # ------------------------------------------------------------------
    @classmethod
    def _seed_v3_17_5_families(cls, members):
        """
        Model families this sweep left EMPTY until v3.17.5.

        WHY THIS MATTERS MORE THAN IT LOOKS
        -----------------------------------
        `test_no_page_repeats_a_query_shape` below is a real N+1 detector and it
        works. On 07-30-26 it was nevertheless reporting clean while the C&B,
        songbook-category, committee-document and quarantine pages each carried a
        per-row query — because **those pages were rendering with zero rows.**
        A per-row query fired zero times repeats zero times. The detector was not
        broken; it was starved.

        Measured, on the pre-fix code: `/songbook/categories/` with no categories
        showed no repeated shape at all; with five categories it showed
        **5× `src_song`**, comfortably over the threshold. Same page, same code,
        same detector — the only variable was whether the fixture had rows.

        This is the third time this exact lesson has been paid for in this file:
        the `member_type` comment above (a fixture that doesn't *vary* the data
        doesn't reach the code), the `role=role` comment (a fixture that skips an
        optional FK tests the early-return path), and now this — a fixture that
        omits a model entirely doesn't test its pages at all. **When adding a
        model family to the app, add rows here, or its pages are excluded from
        every sweep in this module without anyone deciding that.**

        Row counts are deliberately >1 and >threshold: an N+1 with one row is
        indistinguishable from correct code.
        """
        from src.models import (Article, Committee, CommitteeDocument,
                                GoverningDocument, Resolution,
                                ResolutionAmendment, Section, Song, SongCategory)
        from src.models.security import QuarantinedAccount

        # Constitution & Bylaws: documents -> articles -> sections, and
        # resolutions with amendments pointing back at those sections.
        for doc_type, title in (('constitution', 'Constitution'), ('bylaws', 'Bylaws')):
            document = GoverningDocument.objects.create(
                doc_type=doc_type, title=title)
            for a in range(4):
                article = Article.objects.create(
                    document=document, number=str(a + 1), title=f'Article {a + 1}')
                for s in range(2):
                    Section.objects.create(
                        article=article, number=f'{a + 1}.{s + 1}',
                        content='Section text.')

        sections = list(Section.objects.all())
        statuses = ['draft', 'pending', 'passed', 'failed']
        for i in range(6):
            resolution = Resolution.objects.create(
                title=f'Resolution {i}', status=statuses[i % len(statuses)],
                created_by=members[i % len(members)])
            for k in range(2):
                ResolutionAmendment.objects.create(
                    resolution=resolution,
                    section=sections[(i + k) % len(sections)],
                    original_text_snapshot='Snapshot.')

        # Songbook: categories with songs, so the per-category count fires.
        for i in range(5):
            category = SongCategory.objects.create(
                name=f'Category {i}', display_order=i)
            for j in range(3):
                Song.objects.create(
                    title=f'Song {i}-{j}', lyrics='La la la.',
                    category=category, is_active=True,
                    created_by=members[j % len(members)])

        # Committee documents: every document_type, because view_all_reports
        # partitions on it and an absent type is an untested branch.
        committees = list(Committee.objects.all())
        doc_types = ['report', 'minutes', 'agenda', 'policy', 'general']
        for i, document_type in enumerate(doc_types):
            for k in range(2):
                CommitteeDocument.objects.create(
                    committee=committees[(i + k) % len(committees)],
                    uploaded_by=members[k % len(members)],
                    title=f'{document_type} {k}',
                    document=f'committee_documents/{document_type}{k}.pdf',
                    document_type=document_type,
                    published_to_chapter=bool(k),
                )

        # Quarantines: the admin-v2 security dashboard renders these and shows
        # the count in four places (v3.17.5 §6).
        for i in range(4):
            QuarantinedAccount.objects.create(
                user=members[i], ip_address='198.51.100.4',
                reason='fixture', is_auto=True)

    def setUp(self):
        # This class GETs ~282 pages, which populates every cache the site uses
        # — feature flags, page toggles, per-user preferences, the maintenance
        # banner. `TestCase` rolls back the database between tests but NOT the
        # cache, so without this the entries outlive the rows they describe and
        # the next test class inherits them. That is not hypothetical: it made
        # `test_login_as` return 403 from an admin-only view when this module
        # happened to run first.
        from django.core.cache import cache
        cache.clear()
        self.addCleanup(cache.clear)

    def test_no_page_repeats_a_query_shape(self):
        import re
        from collections import Counter

        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from django.core.cache import cache

        from src.models import IPBlacklist

        literal = re.compile(r"('[^']*'|\b\d+\b)")
        client = Client()
        client.force_login(self.admin)

        # v3.17.3: clear the cache between pages.
        #
        # Requesting 282 URLs back to back trips the app's own rate limiting and
        # lockdown counters, which live in the cache — and once tripped, every
        # page after it returns 403 and is silently skipped by the
        # `status_code >= 400` guard below. That is how `/directory/` escaped
        # this test while carrying a 12-query N+1: it was not clean, it was
        # never reached. `/directory/` returns 200 in isolation.
        #
        # `pages_checked` is asserted at the end so a future regression in
        # reachability fails loudly instead of quietly shrinking the sweep.
        offenders = []
        pages_checked = 0
        for name in ZeroArgumentUrlSmokeTests._zero_argument_url_names(self):
            try:
                url = reverse(name)
            except NoReverseMatch:
                continue
            if url.startswith('/admin/') or 'logout' in url or name in KNOWN_FAILURES:
                continue
            cache.clear()
            # v3.17.4: the security middleware auto-blacklists an IP into the
            # DATABASE after enough suspicious requests, and a sweep of 300 URLs
            # from one client trips it. Clearing the cache is not enough — the
            # block is a row — so after ~132 pages every later page returned 403
            # and was silently skipped by the guard below. That is how the
            # `manage_announcements` N+1 (5 queries per row) hid from this test.
            # Drop any block the sweep created about itself.
            IPBlacklist.objects.all().delete()
            try:
                with CaptureQueriesContext(connection) as ctx:
                    response = client.get(url)
            except Exception:
                continue                       # covered by the smoke test above
            if response.status_code >= 400:
                continue
            pages_checked += 1
            shapes = Counter(literal.sub('?', q['sql']) for q in ctx.captured_queries)
            for shape, count in shapes.items():
                if count < REPEAT_THRESHOLD or _is_transaction_noise(shape):
                    continue
                table_match = re.search(r'FROM "(\w+)"', shape)
                table = table_match.group(1) if table_match else shape[:60]
                if (name, table) in ACCEPTED_REPEATS:
                    continue
                offenders.append(f'{name} ({url}): {count}x {table}')

        self.assertGreater(
            pages_checked, 100,
            f'only {pages_checked} pages were actually exercised — the sweep is '
            f'shrinking, which hides N+1s rather than reporting them',
        )
        self.assertEqual(offenders, [], 'pages with a repeated query shape (N+1)')

    def test_accepted_repeats_are_still_repeating(self):
        """
        An exemption that outlives the repeat it excuses makes this sweep look
        weaker than it is, and hides the next regression on that page. Fixing a
        repeat should therefore delete its own entry from ACCEPTED_REPEATS.
        """
        import re
        from collections import Counter

        from django.core.cache import cache
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        from src.models import IPBlacklist

        literal = re.compile(r"('[^']*'|\b\d+\b)")
        client = Client()
        client.force_login(self.admin)

        still_repeating = set()
        for name, _table in ACCEPTED_REPEATS:
            try:
                url = reverse(name)
            except NoReverseMatch:
                continue
            cache.clear()
            IPBlacklist.objects.all().delete()
            try:
                with CaptureQueriesContext(connection) as ctx:
                    response = client.get(url)
            except Exception:
                continue
            if response.status_code >= 400:
                continue
            shapes = Counter(literal.sub('?', q['sql']) for q in ctx.captured_queries)
            for shape, count in shapes.items():
                if count < REPEAT_THRESHOLD or _is_transaction_noise(shape):
                    continue
                match = re.search(r'FROM "(\w+)"', shape)
                if match:
                    still_repeating.add((name, match.group(1)))

        stale = sorted(
            f'{name} / {table}'
            for name, table in ACCEPTED_REPEATS
            if (name, table) not in still_repeating
        )
        self.assertEqual(
            stale, [],
            'ACCEPTED_REPEATS entries that no longer repeat — delete them',
        )
