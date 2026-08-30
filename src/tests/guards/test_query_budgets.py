"""
A query ceiling for every page that matters.

WHY THIS EXISTS
---------------
Dev mode (v3.17.0) can tell you a page fires 40 queries and where each came
from. It is an excellent instrument and it found a great deal — v3.17.1 through
v3.17.5 are mostly its findings. But it only works **when someone opens the
page with it switched on**, which means it catches regressions at the speed a
human happens to look.

The 08-02-26 review is the argument for automating it. Two of that day's six
findings were performance regressions, and **both were introduced by
correctness fixes**:

* v3.18.1 fixed a real disclosure in the Kai list's cross-case activity panel by
  calling `_case_access` per entry — and `_case_access` costs two `KaiRecusal`
  queries for any case the viewer is not a party to. Eight entries, sixteen
  queries, every load, for every reviewer.
* The same release correctly moved `stale_count` and two `assigned_counts` off
  a capped list so they would stop under-reporting — and did it with three
  separate `.count()` round trips sitting directly beneath two `GROUP BY`
  aggregates over the same queryset.

Neither was careless. Both were the *right* fix carrying an unnoticed cost, and
that is precisely the class of change code review waves through: the diff is
about confidentiality, the reviewer is thinking about confidentiality, and the
query count is not in the diff at all.

**A budget puts the query count in the diff.** It is not a performance test —
it does not care about milliseconds, machines, or how much data exists. It
answers one question: *did this page start doing more round trips than it used
to?*

HOW TO USE IT WHEN IT FAILS
---------------------------
The failure message names the view, the old ceiling, the new count, and the
repeated query shapes. Then:

* **You added a feature that legitimately needs a query.** Raise the number and
  say why in `BUDGETS`. That is a one-line, deliberate act — which is the whole
  point. The number should never move silently.
* **You added an N+1.** The duplicate-shape list in the failure will show it as
  one shape with a count of N. Fix it, usually with `select_related`,
  `prefetch_related`, an annotation, or a batched map like `_recusal_rows_for`.
* **The count went DOWN by more than `STALENESS_SLACK`.** Also a failure,
  deliberately, and enforced by the same assertion. A ceiling nobody lowers
  becomes a ceiling nobody notices, and the slack accumulates until it can hide
  a real regression. Lower it in the same commit that earned it — the failure
  message tells you the number to write.

⚠️ THERE IS A SECOND QUERY-BUDGET CLASS. READ THIS BEFORE ADDING TO EITHER.
---------------------------------------------------------------------------
`src/test_query_narrowing.py` already contains a class called
`QueryBudgetTests` (v3.17.3). It is **not** the same thing and neither
supersedes the other — but two files claiming the same job is exactly the
"second copy" pattern this codebase keeps paying for, so the division of labour
is written down here rather than left to be rediscovered:

* **`test_query_narrowing.QueryBudgetTests` — a SCALING test.** It builds 4
  bills, then 30, and asserts the two legislation pages do not cost
  proportionally more. It has no absolute ceiling: it does not care whether a
  page costs 12 queries or 40, only that the number is flat in the size of the
  data. That is the right shape for pagination-correctness, which is what
  v3.17.3 was fixing.
* **This module — a RATCHET.** Absolute per-view ceilings, measured and pinned,
  which catch a regression that is flat in data size and simply *more work per
  request*. Both 08-02 findings were that shape: sixteen queries for eight
  fixed activity rows, three counts where one aggregate would do. A scaling
  test cannot see either, because neither grows with the row count.

Each also carries a "does not scale" test for its own pages, and that overlap
is deliberate — the property is cheap to assert and the two suites cover
different views.

**Adding a page: ceiling here, scaling test wherever its data lives.**

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not assert an absolute *right* number of queries. Nobody knows what
that is, and a budget that tries to be an ideal instead of a ratchet gets
argued with and then ignored. Every number below was **measured, not chosen**;
its only claim is "this is what the page did on the day it was recorded, and it
should not get worse by accident".

It also does not run against production-sized data. Query *count* is what
regresses structurally; query *cost* depends on data you cannot fixture. An N+1
shows up as a count at any scale, which is why count is the thing worth
guarding.
"""

from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from src.dev_mode import normalize_sql
from src.models import Committee, KaiReport, ParliamentUser
from src.models.kai import KaiMemberPermission, KaiReportActivity

#: How much slack a budget may carry before `assert_within_budget` calls it
#: stale. Come in more than this far under a ceiling and the test fails, asking
#: you to lower the number.
#:
#: Four is chosen so that a genuinely variable page (one extra query for a
#: session touch, one for a flag lookup) does not nag, while a page that got
#: 10 queries faster cannot sit at its old number for a month. The measurements
#: below turned out to be exactly reproducible run to run once the cache is
#: cleared, so in practice the tolerance is unused — it is there so that a
#: future page with a genuinely variable cost can be budgeted without flaking.
STALENESS_SLACK = 4


def make_user(uid, name=None, member_type='Officer', is_admin=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=name or f'User {uid}', username=uid,
        member_type=member_type, member_status='Active', is_admin=is_admin,
    )
    user.set_password('budget-test-pass-12345!')
    user.save()
    return user


def duplicate_shapes(captured):
    """
    `[(count, sample_sql), …]` for query shapes that fired more than once.

    Uses `dev_mode.normalize_sql` deliberately rather than a second
    normalisation of its own — the dev-mode panel and this test should agree on
    what "the same query twice" means, or the tool and the guard will disagree
    at the worst possible moment.
    """
    groups = {}
    for query in captured:
        key = normalize_sql(query['sql'])
        groups.setdefault(key, []).append(query['sql'])
    return sorted(
        ((len(v), v[0]) for v in groups.values() if len(v) > 1),
        reverse=True,
    )


def warm_singleton_rows():
    """
    Create the get-or-create singletons before measuring, so that the first
    measurement in a test is not charged for their creation.

    ⚠️ v3.19.10 — WITHOUT THIS, EVERY BUDGET IN THIS MODULE WAS EXACTLY 3
    QUERIES TOO HIGH, AND THE SUITE COULD NOT SEE IT.

    `SystemLockdown.get_instance()` is `get_or_create(pk=1)` and
    `EmergencyLockdownMiddleware` calls it on every request. In production the
    row has existed since the table was created, so it costs one cached read.
    Under `TestCase` every method runs in a transaction that is rolled back, so
    the row is absent again at the start of each test and the FIRST request a
    test makes pays `SAVEPOINT` + `INSERT INTO src_systemlockdown` + `RELEASE
    SAVEPOINT` — three queries that production will never spend.

    Measured 08-17-26, four consecutive cold-cache requests per page:

        view_kai_reports          45, 42, 42, 42      (BUDGET was 45)
        activity_logs             41, 38, 38, 38      (BUDGET was 41)
        home                      44, 41, 41, 41      (BUDGET was 44)
        admin_v2_security_alerts  35, 32, 32, 32      (BUDGET was 35)
        admin_v2_two_factor       32, 29, 29, 29      (never measured)
        service_dashboard         43, 40, 40, 40      (never measured)

    Every declared budget was the first number. **The ratchet was pinned to a
    fixture artefact rather than to the page**, which means each of these four
    pages could have grown by 3 queries without anything failing.

    ⚠️ AND IT SAT JUST UNDER THE SUITE'S OWN DETECTOR. `assert_within_budget`
    fails when a page comes in more than `STALENESS_SLACK` (= 4) under its
    ceiling, precisely so that accumulated slack gets noticed. The slack here
    was 3. A one-query-wider artefact would have been caught on the day it
    landed; this one was invisible for fifteen days.

    **The general form, and it is the one worth keeping: a ceiling measured
    from inside a fixture measures the fixture too.** The remedy is not a
    bigger tolerance — it is to make the measured request look like the
    thousandth request rather than the first, which is what production's
    always does.

    (`cache.clear()` in `measure()` deliberately does NOT cover this: the
    lockdown singleton is cached, but the row it caches has to exist first, and
    that is a database fact rather than a cache one.)

    ⚠️ v3.19.10 ALSO FIXED THE OTHER HALF — `get_instance()` no longer creates
    the row at all, because a write on a read path is a bug independent of what
    it does to a test's arithmetic. **This function is still required and its
    job has changed**: it no longer works around a side effect, it makes the
    fixture resemble production, where the row *does* exist. Without it the
    pages measure the same number by a different route (one lookup that misses
    instead of one that hits), which is the wrong path to pin a ceiling to.

    So it now creates the row explicitly rather than leaning on someone else's
    side effect — which is also why it survives that fix instead of silently
    becoming a no-op.
    """
    from src.models.security import SystemLockdown

    SystemLockdown.objects.get_or_create(pk=1)


class QueryBudgetMixin:
    """Measure the queries one authenticated GET costs."""

    def measure(self, user, url_name, *args, **params):
        # ⚠️ COLD CACHE, DELIBERATELY, AND THIS IS LOAD-BEARING.
        #
        # Django's test runner uses a process-wide LocMemCache that persists
        # across tests in a run. Several context processors cache per user —
        # `two_factor_status` caches for 5 minutes precisely because it "used
        # to fire 2 uncached DB queries on every single page load". So without
        # this line a page measures ~2 queries cheaper when some earlier test
        # happened to warm the cache first, and **the budget would depend on
        # test ordering** — the kind of flake that gets a suite disabled rather
        # than debugged.
        #
        # Clearing means every number here is the COLD path: the pessimistic
        # bound, what the first request after a deploy or a cache flush costs.
        # Production will usually be a query or two under. That is the right
        # direction for a ceiling to be wrong in.
        from django.core.cache import cache
        cache.clear()
        warm_singleton_rows()

        client = Client()
        client.force_login(user)
        url = reverse(url_name, args=args)
        with CaptureQueriesContext(connection) as captured:
            response = client.get(url, params)
        # A redirect or a 500 costs almost no queries and would "pass" any
        # ceiling. Assert the page actually rendered, or the budget is
        # measuring nothing — this is the failure mode that makes a green
        # performance suite worthless.
        self.assertEqual(
            response.status_code, 200,
            f'{url_name} returned {response.status_code}, so its query count '
            f'is meaningless. Fix the fixture before trusting the budget.',
        )
        return list(captured.captured_queries)

    def assert_within_budget(self, user, url_name, ceiling, *args, **params):
        """
        A ratchet in **both** directions.

        Over budget is the obvious failure. Being far *under* budget is also a
        failure, and that is deliberate: a ceiling nobody lowers stops
        constraining anything, and the accumulated slack is exactly where the
        next regression hides unnoticed. When a fix makes a page cheaper, the
        budget moves down in the same commit that earned it.
        """
        captured = self.measure(user, url_name, *args, **params)
        count = len(captured)

        if count > ceiling:
            dupes = duplicate_shapes(captured)
            detail = '\n'.join(
                f'    ×{n}  {sql[:160]}' for n, sql in dupes[:6]
            ) or '    (no repeated shapes — this is new work, not an N+1)'
            self.fail(
                f'{url_name} now costs {count} queries, budget is {ceiling}.\n'
                f'  Repeated query shapes:\n{detail}\n'
                f'  If the new queries are justified, raise the budget and say '
                f'why. If they are an N+1, the shapes above are where to look.'
            )

        if count < ceiling - STALENESS_SLACK:
            self.fail(
                f'{url_name} costs {count} queries but its budget is {ceiling} '
                f'— {ceiling - count} queries of slack.\n'
                f'  This is a PASS in the sense that nothing regressed, and a '
                f'failure in the sense that the ceiling has stopped doing its '
                f'job: this page could nearly double before anyone heard about '
                f'it. Lower the budget to {count}.\n'
                f'  If the count is legitimately variable by more than '
                f'{STALENESS_SLACK}, the fixture is nondeterministic — fix that '
                f'first, because a flaky budget gets disabled, not debugged.'
            )
        return count


def _budget_classes(directory=None, package='src.tests'):
    """
    Every class in `directory` (walked recursively) that uses this module's
    budget machinery.

    ⚠️ THE KEY IS THE MIXIN, NOT THE NAME OR THE FILE, and both of those were
    tried first. See `test_the_unmeasured_list_is_accurate_in_both_directions`
    for what each of them missed.

    Takes a directory so the control below can point it at a temporary one. A
    walk that can only ever be run over the tree it lives in cannot be shown to
    work — which is the same objection v3.21.7 raised about an enumeration that
    asserted a property of Django.

    Recursive since test modules live under `src/tests/<domain>/test_x.py`,
    not flat in one directory — `package` plus the path from `directory` down
    to each file builds the dotted module name (e.g. `src.tests.kai.test_x`).
    """
    import importlib
    import inspect
    import os

    directory = directory or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = {}
    for root, _dirs, files in os.walk(directory):
        rel = os.path.relpath(root, directory)
        rel_parts = [] if rel == '.' else rel.split(os.sep)
        for filename in sorted(files):
            if not filename.startswith('test_') or not filename.endswith('.py'):
                continue
            stem = filename[:-3]
            module_parts = ([package] if package else []) + rel_parts + [stem]
            module_name = '.'.join(module_parts)
            module = importlib.import_module(module_name)
            for name, obj in inspect.getmembers(module, inspect.isclass):
                # Defined here, not imported into here — otherwise a module that
                # imports a budget class reports it a second time under its own
                # name and the two disagree about nothing.
                if obj.__module__ != module.__name__:
                    continue
                if issubclass(obj, QueryBudgetMixin) and obj is not QueryBudgetMixin:
                    found[f'{module.__name__}.{name}'] = obj
    return found


class KaiListQueryBudgetTests(QueryBudgetMixin, TestCase):
    """
    The Kai reviewer list — the page both 08-02 perf findings landed on, and
    the densest page in the app: eight stat aggregates, a cross-case activity
    panel, an aging banner and a capped list.
    """

    #: Measured 08-02-26 against v3.18.2 on sqlite, cold cache, twice, same
    #: number both times. See the module docstring — this is a ratchet, not an
    #: ideal, and not an opinion about what the number ought to be.
    #:
    #: v3.19.10: 45 → 42. Not a code change — see `warm_singleton_rows`. The
    #: page never cost 45; the first request in each test was paying to create
    #: the `SystemLockdown` singleton.
    BUDGET = 42

    def setUp(self):
        self.committee = Committee.objects.create(
            name='Kai', code='KAI', is_kai_committee=True,
        )
        self.chair = make_user('qb-chair', 'Chair Chris')
        self.committee.chairs.add(self.chair)

        # Enough cases and activity that an N+1 would actually show. The panel
        # reads the newest 8 activity rows across all cases, so 12 cases each
        # with activity is comfortably past it.
        submitters = [make_user(f'qb-sub{i}', f'Submitter {i}') for i in range(4)]
        for i in range(12):
            report = KaiReport.objects.create(
                title=f'Case {i}',
                description='body',
                submitted_by=submitters[i % len(submitters)],
                targeted_to=submitters[(i + 1) % len(submitters)],
            )
            KaiReportActivity.objects.create(
                report=report, user=report.submitted_by,
                action='created', details='Report created',
            )

    def test_the_kai_list_stays_within_budget(self):
        self.assert_within_budget(self.chair, 'view_kai_reports', self.BUDGET)

    def test_the_kai_list_does_not_scale_with_case_count(self):
        """
        The property a ceiling alone cannot express: **adding rows must not add
        queries.** This is the direct regression test for the v3.18.1 activity
        panel, which cost two queries per entry — a fixed ceiling would have
        absorbed that at 12 cases and failed at 40.
        """
        before = len(self.measure(self.chair, 'view_kai_reports'))

        extra = make_user('qb-extra', 'Extra Person')
        for i in range(20):
            report = KaiReport.objects.create(
                title=f'Extra {i}', description='body',
                submitted_by=extra, targeted_to=self.chair,
            )
            KaiReportActivity.objects.create(
                report=report, user=extra, action='created', details='x',
            )

        after = len(self.measure(self.chair, 'view_kai_reports'))
        self.assertLessEqual(
            after, before,
            f'The Kai list cost {before} queries with 12 cases and {after} with '
            f'32. Query count must be flat in row count — something in the page '
            f'is querying per row.',
        )

    def test_a_list_only_reviewer_is_also_flat(self):
        """
        Permission level changes which branches run, and the cheap-looking
        branch is the one nobody measures. A list-only reviewer skips the
        identity work and must still not pay per row.
        """
        reviewer = make_user('qb-listonly', 'List Only')
        self.committee.members.add(reviewer)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=reviewer, can_view_report_list=True,
        )
        before = len(self.measure(reviewer, 'view_kai_reports'))

        for i in range(15):
            report = KaiReport.objects.create(
                title=f'More {i}', description='body', submitted_by=self.chair,
            )
            KaiReportActivity.objects.create(
                report=report, user=self.chair, action='created', details='x',
            )

        after = len(self.measure(reviewer, 'view_kai_reports'))
        self.assertLessEqual(after, before)


class ActivityLogQueryBudgetTests(QueryBudgetMixin, TestCase):
    """
    The officer activity log — added to the budget because v3.18.2 put a
    per-page redaction pass on it (`redact_kai_logs`), and a redaction pass over
    50 rows is exactly the shape that becomes an N+1 if someone later resolves
    the case per row instead of in one batch.
    """

    #: Measured 08-02-26, cold cache. Includes v3.18.2's `redact_kai_logs`
    #: pass, which resolves every referenced case in one query.
    #: v3.19.10: 41 → 38, see `warm_singleton_rows`.
    BUDGET = 38

    def setUp(self):
        from src.models import ActivityLog

        Committee.objects.create(name='Kai', code='KAI', is_kai_committee=True)
        self.officer = make_user('qb-officer', 'Officer Olive')
        submitter = make_user('qb-logsub', 'Log Submitter')

        # 30 Kai rows across 10 distinct cases: enough that per-row case
        # resolution would show up loudly against a batched one.
        for i in range(10):
            report = KaiReport.objects.create(
                title=f'Logged case {i}', description='body',
                submitted_by=submitter, targeted_to=self.officer,
            )
            for _ in range(3):
                ActivityLog.log_activity(
                    action_type='kai_action', user=submitter,
                    description=f'A member submitted Kai case {report.display_number}',
                    object_type='KaiReport', object_id=report.id,
                    object_repr=report.display_number,
                )

    def test_the_activity_log_stays_within_budget(self):
        self.assert_within_budget(
            self.officer, 'activity_logs', self.BUDGET, date_range='all',
        )

    def test_the_redaction_pass_does_not_query_per_row(self):
        """
        `redact_kai_logs` resolves every referenced case in ONE query
        (`_party_index`). If that ever becomes a per-row lookup, this fails —
        and it is the likeliest future regression in that module, because the
        per-row version is the obvious way to write it.
        """
        from src.models import ActivityLog

        before = len(self.measure(self.officer, 'activity_logs', date_range='all'))

        submitter = ParliamentUser.objects.get(pk='qb-logsub')
        for i in range(10):
            report = KaiReport.objects.create(
                title=f'Extra logged {i}', description='body',
                submitted_by=submitter, targeted_to=self.officer,
            )
            ActivityLog.log_activity(
                action_type='kai_action', user=submitter,
                description=f'A member submitted Kai case {report.display_number}',
                object_type='KaiReport', object_id=report.id,
                object_repr=report.display_number,
            )

        after = len(self.measure(self.officer, 'activity_logs', date_range='all'))
        self.assertLessEqual(
            after, before,
            f'The activity log cost {before} queries and now costs {after} with '
            f'10 more Kai cases on the page. `_party_index` should resolve them '
            f'all in one query — check that redact_kai_logs is still batching.',
        )


class HomePageQueryBudgetTests(QueryBudgetMixin, TestCase):
    """
    The home page — every authenticated request lands here, so it is the one
    page where a single extra query is paid by everyone. v3.17.7 found a wrong
    committee member count here caused by `filter()` before `annotate()`; that
    fix and this budget guard opposite failure modes on the same queryset.
    """

    #: Measured 08-02-26, cold cache. Every authenticated request lands here,
    #: so this is the budget where one query costs the most in aggregate.
    #: v3.19.10: 44 → 41, see `warm_singleton_rows`.
    BUDGET = 41

    def setUp(self):
        self.member = make_user('qb-home', 'Home Member', member_type='Member')
        for i in range(6):
            committee = Committee.objects.create(name=f'C{i}', code=f'C{i}')
            committee.members.add(self.member)

    def test_the_home_page_stays_within_budget(self):
        self.assert_within_budget(self.member, 'home', self.BUDGET)

    def test_the_home_page_does_not_scale_with_committee_count(self):
        before = len(self.measure(self.member, 'home'))
        for i in range(10):
            committee = Committee.objects.create(name=f'X{i}', code=f'X{i}')
            committee.members.add(self.member)
        after = len(self.measure(self.member, 'home'))
        self.assertLessEqual(
            after, before,
            f'Home cost {before} queries across 6 committees and {after} across '
            f'16 — something iterates committees and queries per one.',
        )


class SecurityAlertsQueryBudgetTests(QueryBudgetMixin, TestCase):
    """
    The security-alerts page — added because it broke in production on
    08-02-26, hours after this module was written, with **51 identical
    `src_parliamentuser` queries** on one page load.

    `security_alerts.html` reads `{% if alert.reviewed_by %}` and its display
    name; the queryset joined only `user`. What makes it worth a permanent
    budget rather than just a fix is *how it looked*: the queryset already
    carried `select_related` and `member_defer`, so it read as narrowed. **A
    half-narrowed join is indistinguishable from a finished one at a glance** —
    which is exactly the sort of thing a measurement catches and a reading does
    not.
    """

    #: Measured 08-02-26, cold cache, after adding `reviewed_by` to the join.
    #: The page is stat-card heavy (five separate `LoginAlert` counts plus the
    #: admin-v2 chrome), so the absolute number is not small — but it is now
    #: FLAT, which is what `test_it_does_not_scale_with_alert_count` pins and
    #: what the 51-query incident violated.
    #: v3.19.10: 35 → 32, see `warm_singleton_rows`.
    BUDGET = 32

    #: admin-v2 has its own session gate on top of login (`require_admin_v2_auth`
    #: — an allowlisted user id plus two session keys), so `force_login` alone
    #: gets a 302. The mixin's status-code assertion caught that immediately,
    #: which is the assertion earning its keep: without it this budget would
    #: have "passed" while measuring a redirect. Same setup as
    #: `test_page_visits_filter`.
    def _admin_v2_client(self, user):
        from unittest import mock
        from django.utils import timezone as tz
        from src.view import admin_v2

        patcher = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {user.pk})
        patcher.start()
        self.addCleanup(patcher.stop)

        client = Client()
        client.force_login(user)
        session = client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = tz.now().isoformat()
        session.save()
        return client

    def measure(self, user, url_name, *args, **params):
        """Override: admin-v2 needs its own session, so build that client."""
        from django.core.cache import cache
        from django.test.utils import CaptureQueriesContext

        cache.clear()
        warm_singleton_rows()
        client = self._admin_v2_client(user)
        with CaptureQueriesContext(connection) as captured:
            response = client.get(reverse(url_name, args=args), params)
        self.assertEqual(
            response.status_code, 200,
            f'{url_name} returned {response.status_code}, so its query count '
            f'is meaningless. Fix the fixture before trusting the budget.',
        )
        return list(captured.captured_queries)

    def setUp(self):
        from src.models import LoginAlert

        self.admin = make_user('qb-sec', 'Sec Admin', is_admin=True)
        reviewer = make_user('qb-sec-rev', 'Reviewing Rita', is_admin=True)
        subjects = [make_user(f'qb-sec-u{i}', f'Subject {i}') for i in range(5)]
        for i in range(20):
            LoginAlert.objects.create(
                user=subjects[i % len(subjects)],
                # Half reviewed, so the `{% if alert.reviewed_by %}` branch is
                # actually taken — an unreviewed-only fixture would render the
                # cheap side of the branch and measure nothing.
                reviewed_by=reviewer if i % 2 == 0 else None,
                alert_type='new_location', severity='high', status='new',
                title=f'Alert {i}', description='d',
            )

    def test_the_security_alerts_page_stays_within_budget(self):
        self.assert_within_budget(
            self.admin, 'admin_v2_security_alerts', self.BUDGET)

    def test_it_does_not_scale_with_alert_count(self):
        """The direct regression test for the 51-query incident."""
        from src.models import LoginAlert

        before = len(self.measure(self.admin, 'admin_v2_security_alerts'))
        reviewer = ParliamentUser.objects.get(pk='qb-sec-rev')
        subject = ParliamentUser.objects.get(pk='qb-sec-u0')
        for i in range(30):
            LoginAlert.objects.create(
                user=subject, reviewed_by=reviewer, alert_type='new_location',
                severity='high', status='new', title=f'Extra {i}', description='d',
            )
        after = len(self.measure(self.admin, 'admin_v2_security_alerts'))
        self.assertLessEqual(
            after, before,
            f'Security alerts cost {before} queries with 20 alerts and {after} '
            f'with 50 — a foreign key the template reads is not in the join.',
        )


class BudgetHygieneTests(TestCase):
    """
    Tests about the budgets themselves. A guard that rots is worse than none,
    because it reports green while measuring nothing.
    """

    def test_budgets_are_sane_numbers(self):
        """
        A sanity check on the constants themselves.

        The real staleness enforcement lives in `assert_within_budget`, which
        fails when a page comes in more than `STALENESS_SLACK` under its
        ceiling — that is where it belongs, because it already has the measured
        count in hand and re-measuring here would double the suite's cost for
        no new information.

        What this catches is the other failure: a budget set to whatever made
        the test pass. A ceiling of 500 constrains nothing and would sail
        through every assertion above.
        """
        from src.tests.guards.test_query_budgets import (
            ActivityLogQueryBudgetTests, EducationDashboardQueryBudgetTests,
            EducationPledgeDetailQueryBudgetTests,
            EventAttendanceListQueryBudgetTests, HomePageQueryBudgetTests,
            KaiListQueryBudgetTests, ManageEventsListQueryBudgetTests,
            MyAttendanceQueryBudgetTests,
            SecurityAlertsQueryBudgetTests, ServiceDashboardQueryBudgetTests,
            TwoFactorDashboardQueryBudgetTests,
        )

        # Declared budgets, checked for obvious nonsense rather than re-measured
        # — re-running every page here would double the suite's cost for no new
        # information, since the budget tests above already measure them.
        declared = {
            'view_kai_reports': KaiListQueryBudgetTests.BUDGET,
            'activity_logs': ActivityLogQueryBudgetTests.BUDGET,
            'home': HomePageQueryBudgetTests.BUDGET,
            'admin_v2_security_alerts': SecurityAlertsQueryBudgetTests.BUDGET,
            'admin_v2_two_factor': TwoFactorDashboardQueryBudgetTests.BUDGET,
            'service_dashboard': ServiceDashboardQueryBudgetTests.BUDGET,
            'education_home': EducationDashboardQueryBudgetTests.BUDGET,
            'education_pledge_detail': EducationPledgeDetailQueryBudgetTests.BUDGET,
            'event_attendance_list': EventAttendanceListQueryBudgetTests.BUDGET,
            'my_attendance': MyAttendanceQueryBudgetTests.BUDGET,
            'manage_events': ManageEventsListQueryBudgetTests.BUDGET,
        }
        for name, ceiling in declared.items():
            self.assertGreater(
                ceiling, 0, f'{name} has a nonsensical budget of {ceiling}',
            )
            self.assertLess(
                ceiling, 200,
                f'{name} has a budget of {ceiling}, which is high enough that '
                f'it is not constraining anything. Either the page needs work '
                f'or the budget was set to whatever made the test pass.',
            )

    #: v3.19.3 — classes that assert properties but declare no measured ceiling.
    #: Each is legitimate at the moment it is written (see each class docstring:
    #: a ceiling is a MEASURED number and there was no working Django), but the
    #: exception has now been taken three times and the whole point of a ratchet
    #: suite is that it ratchets. This list is the standing to-do, in the file
    #: rather than in a changelog nobody re-reads.
    #:
    #: ⚠️ MEASURE THESE **AFTER** DEPLOYING v3.18.7, NOT BEFORE. That release
    #: removes 2–4 queries from every page in this suite, so every existing
    #: BUDGET will also need re-measuring — one pass, not two, or the suite is
    #: red in between for a reason nobody will remember.
    #: v3.19.10 — the two dashboards came OFF this list when they were
    #: measured (08-17-26). `MiddlewareChainQueryBudgetTests` stays: that class
    #: deliberately asserts relative properties (cold vs warm, anonymous vs
    #: authenticated, steady state) rather than a single page's ceiling, so a
    #: `BUDGET` there would be a number with nothing to compare against.
    AWAITING_MEASUREMENT = (
        'MiddlewareChainQueryBudgetTests',
    )

    def test_the_unmeasured_list_is_accurate_in_both_directions(self):
        """
        Keep `AWAITING_MEASUREMENT` honest.

        A to-do list that is allowed to drift is worse than none: it stops being
        read the first time someone finds a stale entry on it. So this fails
        both ways — a class that has since gained a `BUDGET` must come OFF the
        list, and a budget class that has none must be ON it.

        This deliberately does not fail merely because the list is non-empty.
        The classes are exempt for a stated and correct reason; what must not
        happen is the exemption becoming invisible.

        ⚠️ v3.25.0 — THE ENUMERATION USED TO BE KEYED ON TWO THINGS THAT ARE
        BOTH WRONG: a name ending `QueryBudgetTests`, **in this module only**.

        v3.24.0's `/my-attendance/` budget class failed both halves — it lives
        in a sibling file and is called `MyAttendanceScalesFlatTests` — so it
        declared no `BUDGET` and appeared on no list, and the mechanism whose
        entire job is to make an exemption visible could not see it. Its
        docstring said so, which made it visible to a human reading that one
        file. **A ratchet that only enumerates the module it lives in is a
        ratchet against moving code into a new file.**

        The population is now derived from `QueryBudgetMixin`: a class that uses
        this module's measuring machinery is a class this module is responsible
        for, wherever it lives and whatever it is called. That is also why the
        key is not the name — `src/test_query_narrowing.py` has a class called
        `QueryBudgetTests` which is a *scaling* test, does not use the mixin,
        and correctly is not in scope. The module docstring already warns that
        those two names collide; keying on the mixin means the warning no longer
        has to be remembered.
        """
        budget_classes = _budget_classes()

        self.assertTrue(budget_classes, 'No budget classes found — the mixin moved.')
        self.assertGreater(
            len(budget_classes), 5,
            f'Only {len(budget_classes)} budget classes found. This walk has '
            f'gone blind before by keying on the wrong thing; if the count '
            f'collapses, the enumeration is broken rather than the suite empty.',
        )

        # Compared on the bare class name so `AWAITING_MEASUREMENT` stays
        # readable; the qualified names above are only for the failure message.
        unmeasured = {
            obj.__name__ for obj in budget_classes.values()
            if getattr(obj, 'BUDGET', None) is None
        }
        listed = set(self.AWAITING_MEASUREMENT)

        self.assertEqual(
            unmeasured - listed, set(),
            f'{sorted(unmeasured - listed)} declare no BUDGET and are not in '
            f'AWAITING_MEASUREMENT. Add them, or measure them.',
        )
        self.assertEqual(
            listed - unmeasured, set(),
            f'{sorted(listed - unmeasured)} now have a measured BUDGET — remove '
            f'them from AWAITING_MEASUREMENT so the list stays worth reading.',
        )

    def test_the_walk_reaches_a_class_in_another_module(self):
        """
        ⚠️ THE CONTROL FOR THE WIDENING, and the reason `_budget_classes` takes
        a directory at all.

        The walk was widened because a budget class in a sibling file escaped
        it. Every budget class in the tree is now back in this module, so the
        widening is unfalsifiable against the real tree — *green here would mean
        nothing.* This builds a module somewhere else, containing a class named
        nothing like the convention, and requires the walk to find it.
        """
        import os
        import sys
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'test_elsewhere.py'), 'w') as fh:
                fh.write(
                    'from src.tests.guards.test_query_budgets import QueryBudgetMixin\n'
                    'class SomePageScalesFlatTests(QueryBudgetMixin):\n'
                    '    pass\n'
                )
            # A file that is not a test module, to prove the filter still bites.
            with open(os.path.join(tmp, 'helpers.py'), 'w') as fh:
                fh.write(
                    'from src.tests.guards.test_query_budgets import QueryBudgetMixin\n'
                    'class HiddenTests(QueryBudgetMixin):\n'
                    '    pass\n'
                )
            sys.path.insert(0, tmp)
            try:
                found = _budget_classes(tmp, package='')
            finally:
                sys.path.remove(tmp)
                sys.modules.pop('test_elsewhere', None)

        self.assertIn('test_elsewhere.SomePageScalesFlatTests', found)
        self.assertNotIn('helpers.HiddenTests', found)

    def test_normalize_sql_collapses_parameter_differences(self):
        """
        The duplicate detector is only as good as its notion of "same shape".
        If `normalize_sql` ever stops collapsing literals, every N+1 looks like
        N distinct queries and this whole module goes quietly blind.
        """
        a = normalize_sql('SELECT * FROM src_kaireport WHERE id = 1')
        b = normalize_sql('SELECT * FROM src_kaireport WHERE id = 2')
        self.assertEqual(a, b)


# ---------------------------------------------------------------------------
# Two dashboards the dev-mode panel caught on 08-04-26  (v3.18.6)
# ---------------------------------------------------------------------------
#
# Both were found by prod dev mode, not by this suite, because neither page was
# in it. That is the honest limitation of an absolute-ceiling suite: it only
# constrains the pages someone remembered to add.
#
# ⚠️ v3.19.10 — BOTH CLASSES NOW HAVE A MEASURED `BUDGET`. The original note is
# kept below because the reason they went thirteen days without one is the
# useful part: a ceiling is a *measured* number, these fixes were written in an
# environment with no working Django, and an invented ceiling is worse than
# none because it reads exactly like a measured one. The 08-17-26 auto-run got
# Django running in the sandbox (`pip install -r requirements.txt` succeeds
# there now) and measured them — four consecutive cold runs each, and the same
# pass found that every OTHER budget in this module was three queries too high.
# See `warm_singleton_rows`.
#
# (Historic note, v3.18.6:) **Run these, note the counts, and add
# `BUDGET = <n>` plus a `test_..._stays_within_budget` in the same commit.**
#
# What they DO assert is the property that actually catches an N+1 and needs no
# constant: **adding members must not add queries.** Both regressions scaled
# with the roster, so both would have failed this from the day they landed.


class TwoFactorDashboardQueryBudgetTests(QueryBudgetMixin, TestCase):
    """
    `/admin-v2/two-factor/` — 47 members cost 47 TOTP lookups
    (`user_has_device`), 36 more static-device lookups for the members without
    a TOTP device, and 47 reverse-OneToOne reads of `two_factor_requirement`.

    The device half is worth remembering: `user_has_device` walks **every**
    installed django-otp device class and stops at the first hit, so the second
    table is queried only for members who have no TOTP device. That is why the
    panel showed two different counts (47 and 36) for one line of code, and why
    the batch below iterates `device_classes()` rather than naming TOTPDevice.
    """

    #: Measured 08-17-26 (v3.19.10) on sqlite, cold cache, four consecutive
    #: runs: 32, 29, 29, 29. 29 is the steady-state number; 32 was the
    #: singleton-creation artefact — see `warm_singleton_rows`.
    BUDGET = 29

    def test_the_dashboard_stays_within_budget(self):
        self.assert_within_budget(self.admin, 'admin_v2_two_factor', self.BUDGET)

    def _admin_v2_client(self, user):
        from unittest import mock
        from django.utils import timezone as tz
        from src.view import admin_v2

        patcher = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {user.pk})
        patcher.start()
        self.addCleanup(patcher.stop)

        client = Client()
        client.force_login(user)
        session = client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = tz.now().isoformat()
        session.save()
        return client

    def measure(self, user, url_name, *args, **params):
        """Override: admin-v2 needs its own session, so build that client."""
        from django.core.cache import cache
        from django.test.utils import CaptureQueriesContext

        cache.clear()
        warm_singleton_rows()
        client = self._admin_v2_client(user)
        with CaptureQueriesContext(connection) as captured:
            response = client.get(reverse(url_name, args=args), params)
        self.assertEqual(
            response.status_code, 200,
            f'{url_name} returned {response.status_code}, so its query count '
            f'is meaningless. Fix the fixture before trusting the budget.',
        )
        return list(captured.captured_queries)

    def setUp(self):
        from django_otp.plugins.otp_totp.models import TOTPDevice
        from src.models import TwoFactorRequirement

        self.admin = make_user('qb-2fa-admin', 'TwoFA Admin', is_admin=True)

        # A MIXED fixture, deliberately. If every member had a TOTP device the
        # static-device table would never be reached and half the regression
        # would be invisible; if none did, the `two_factor_requirement` hit
        # branch would never render. Both branches must be exercised or the
        # measurement is of a page that does not exist in production.
        self.members = [make_user(f'qb-2fa-{i}', f'Member {i}') for i in range(8)]
        for i, member in enumerate(self.members):
            if i % 2 == 0:
                TOTPDevice.objects.create(user=member, name='phone', confirmed=True)
            if i % 3 == 0:
                TwoFactorRequirement.objects.create(
                    user=member,
                    requirement='required' if i % 2 == 0 else 'exempt',
                    reason='fixture',
                )

    def test_the_dashboard_does_not_scale_with_member_count(self):
        before = len(self.measure(self.admin, 'admin_v2_two_factor'))

        from django_otp.plugins.otp_totp.models import TOTPDevice
        from src.models import TwoFactorRequirement

        for i in range(24):
            extra = make_user(f'qb-2fa-x{i}', f'Extra {i}')
            if i % 2 == 0:
                TOTPDevice.objects.create(user=extra, name='phone', confirmed=True)
            if i % 3 == 0:
                TwoFactorRequirement.objects.create(
                    user=extra, requirement='exempt', reason='fixture')

        after = len(self.measure(self.admin, 'admin_v2_two_factor'))
        self.assertLessEqual(
            after, before,
            f'The 2FA dashboard cost {before} queries with 9 members and '
            f'{after} with 33. Query count must be flat in member count — '
            f'`user_has_device` and `two_factor_requirement` are the two that '
            f'were per-member (v3.18.6).',
        )


class ServiceDashboardQueryBudgetTests(QueryBudgetMixin, TestCase):
    """
    `/service-hours/dashboard/` — the worst of the two: **four** per-member
    queries (approved Sum, pending Sum, adjustment Sum, and
    `get_member_expected_hours`, which does its own `.get()`), so 22 active
    members cost 88 queries before the page rendered a row.

    All four are the same aggregate the loop wanted, grouped by member instead
    of filtered to one.
    """

    #: Measured 08-17-26 (v3.19.10) on sqlite, cold cache, four consecutive
    #: runs: 43, 40, 40, 40. See `warm_singleton_rows` for the 3.
    BUDGET = 40

    def test_the_dashboard_stays_within_budget(self):
        self.assert_within_budget(self.admin, 'service_dashboard', self.BUDGET)

    def setUp(self):
        from datetime import timedelta as _td
        from decimal import Decimal
        from django.utils import timezone as tz
        from src.models import (
            ServicePeriod, ServiceMemberExpectation,
            ServiceHoursSubmission, ServiceHoursAdjustment,
        )

        self.admin = make_user('qb-svc-admin', 'Service Admin', is_admin=True)
        self.today = tz.localdate()
        self.period = ServicePeriod.objects.create(
            name='Fixture Period',
            start_date=self.today - _td(days=30),
            end_date=self.today + _td(days=30),
            default_hours_required=Decimal('10.00'),
        )

        # Mixed again, and for the same reason: a member with an expectation
        # override exercises a different branch from one falling back to the
        # period default, and a member with BOTH approved and pending rows
        # exercises both aggregates.
        self.members = [make_user(f'qb-svc-{i}', f'Member {i}') for i in range(8)]
        for i, member in enumerate(self.members):
            ServiceHoursSubmission.objects.create(
                period=self.period, submitted_by=member,
                hours=Decimal('3.00'), status='approved',
                service_date=self.today, organization='Fixture Org',
                description='fixture',
            )
            if i % 2 == 0:
                ServiceHoursSubmission.objects.create(
                    period=self.period, submitted_by=member,
                    hours=Decimal('1.50'), status='pending',
                    service_date=self.today, organization='Fixture Org',
                    description='fixture',
                )
            if i % 3 == 0:
                ServiceHoursAdjustment.objects.create(
                    period=self.period, member=member,
                    hours=Decimal('2.00'), reason='fixture',
                )
            if i % 4 == 0:
                ServiceMemberExpectation.objects.create(
                    period=self.period, member=member,
                    expected_hours=Decimal('15.00'),
                )

    def test_the_dashboard_does_not_scale_with_member_count(self):
        from decimal import Decimal
        from src.models import ServiceHoursSubmission, ServiceHoursAdjustment

        before = len(self.measure(self.admin, 'service_dashboard'))

        for i in range(24):
            extra = make_user(f'qb-svc-x{i}', f'Extra {i}')
            ServiceHoursSubmission.objects.create(
                period=self.period, submitted_by=extra,
                hours=Decimal('4.00'), status='approved',
                service_date=self.today, organization='Fixture Org',
                description='x',
            )
            if i % 3 == 0:
                ServiceHoursAdjustment.objects.create(
                    period=self.period, member=extra,
                    hours=Decimal('1.00'), reason='x',
                )

        after = len(self.measure(self.admin, 'service_dashboard'))
        self.assertLessEqual(
            after, before,
            f'The service dashboard cost {before} queries with 9 members and '
            f'{after} with 33. All four per-member lookups must be grouped '
            f'aggregates, not per-member filters (v3.18.6).',
        )

    def test_the_grouped_aggregates_match_the_per_member_ones(self):
        """
        ⚠️ THE ASSERTION THAT MATTERS MORE THAN THE QUERY COUNT.

        Collapsing a per-row filter into a GROUP BY is the kind of fix that can
        be fast and wrong, and the specific trap here is that both models carry
        a `Meta.ordering` — Django adds any ordering column to the GROUP BY,
        which would silently group by member AND timestamp and return one row
        per submission. `.order_by()` clears it. A query-count test would pass
        just as happily with the wrong numbers on the page, so this checks the
        numbers.
        """
        from decimal import Decimal

        client = Client()
        client.force_login(self.admin)
        response = client.get(reverse('service_dashboard'))
        self.assertEqual(response.status_code, 200)

        progress = {
            row['member'].pk: row for row in response.context['member_progress']
        }
        for i, member in enumerate(self.members):
            with self.subTest(member=member.user_id):
                row = progress[member.pk]
                expected_adjust = Decimal('2.00') if i % 3 == 0 else Decimal('0')
                self.assertEqual(row['submitted_hours'], Decimal('3.00'))
                self.assertEqual(row['adjusted_hours'], expected_adjust)
                self.assertEqual(
                    row['approved_hours'], Decimal('3.00') + expected_adjust)
                self.assertEqual(
                    row['pending_hours'],
                    Decimal('1.50') if i % 2 == 0 else Decimal('0'))
                self.assertEqual(
                    row['expected_hours'],
                    Decimal('15.00') if i % 4 == 0 else Decimal('10.00'))


# ---------------------------------------------------------------------------
# The middleware chain
# ---------------------------------------------------------------------------

def _tables_touched(captured, table):
    """Queries in `captured` that name `table`. Case-insensitive substring."""
    return [q['sql'] for q in captured if table.lower() in q['sql'].lower()]


class MiddlewareChainQueryBudgetTests(QueryBudgetMixin, TestCase):
    """
    A budget for the code that runs on EVERY request, measured as its own
    number instead of as a share of everyone else's.

    WHY THIS CLASS EXISTS — and it is a blind spot, not an oversight
    ----------------------------------------------------------------
    Every budget above is per-view. Middleware cost is a **constant added to
    all of them**, so it hides inside each ceiling equally and reads as the
    floor rather than as a finding. Two separate reviews hit this:

    * 08-05-26 found `Enforce2FAMiddleware` spending 3–4 uncached queries on
      every authenticated request, and noted it was invisible here for exactly
      this reason;
    * 08-06-26 then found a SECOND one — `EmergencyLockdownMiddleware` doing an
      uncached `get_or_create` on the `SystemLockdown` singleton on every
      request *including anonymous ones*, in a file no review had ever opened.

    Two findings, one blind spot. The blind spot is structural, so the response
    is structural: measure a request whose view does almost nothing, and
    everything left is chain overhead by construction.

    ⚠️ NO `BUDGET` CONSTANT, DELIBERATELY — SAME RULE AS v3.18.6.
    ------------------------------------------------------------
    This module's standard is that a ceiling is a **measured** number, and
    there is no working Django in the environment these tests were written in.
    An invented ceiling reads exactly like a measured one and is worse than
    none. So this class asserts constant-free PROPERTIES instead, each of which
    would have failed against the pre-v3.18.7 tree.

    **To finish the job:** run this class, take the count from
    `test_the_chain_reports_its_own_cost` twice, cold, and add

        BUDGET = <n>
        def test_the_chain_stays_within_budget(self): ...

    plus an entry in `BudgetHygieneTests.declared`. Measure it AFTER any other
    hot-path fix in flight, not between — v3.18.7 removes 2–4 queries from
    every page in this suite, which will push every existing ceiling more than
    `STALENESS_SLACK` out of date and (correctly) fail the ratchet.
    """

    def setUp(self):
        self.member = make_user('qb-chain', 'Chain Member', member_type='Member')

    def _measure_path(self, path, *, authenticated, warm):
        """
        Queries for one GET, with the cache either cold or warmed by a prior
        identical request.

        Unlike `QueryBudgetMixin.measure` this does not assert a 200: a
        redirect has still run every middleware's request phase, which is the
        thing being counted. It asserts the status is not a 500 instead — a
        crashing request short-circuits the chain and would measure nothing.
        """
        from django.core.cache import cache
        cache.clear()

        client = Client()
        if authenticated:
            client.force_login(self.member)

        if warm:
            primer = client.get(path)
            self.assertLess(primer.status_code, 500, f'{path} 500ed while priming')

        with CaptureQueriesContext(connection) as captured:
            response = client.get(path)
        self.assertLess(
            response.status_code, 500,
            f'{path} returned {response.status_code}; a crashing request '
            f'short-circuits the chain, so this count means nothing.',
        )
        return list(captured.captured_queries)

    # -- the two regressions this class was written for ---------------------

    def test_the_lockdown_singleton_is_not_read_on_every_request(self):
        """
        v3.18.7. `EmergencyLockdownMiddleware` exempts only /static/, /media/,
        /health/ and /favicon.ico, so before the fix this row was SELECTed on
        essentially every request in the application — the widest per-request
        DB read there was, and paid by anonymous traffic too.

        Asserted on the warm path because that is the steady state: cold, one
        read is correct and expected.
        """
        captured = self._measure_path(reverse('login'), authenticated=False, warm=True)
        hits = _tables_touched(captured, 'systemlockdown')
        self.assertEqual(
            hits, [],
            f'SystemLockdown was queried {len(hits)}× on a warm anonymous '
            f'request. `get_instance()` is cached (models/security.py) and '
            f'invalidated by a post_save receiver — if this fails, either the '
            f'cache was bypassed or the receiver is firing when it should not.'
            f'\n  {hits[:2]}',
        )

    def test_site_settings_are_not_read_on_every_authenticated_request(self):
        """
        v3.18.7. `Enforce2FAMiddleware` calls
        `SiteSetting.get_setting('2fa_policy_mode')`, which was a plain
        uncached `objects.get` — the 08-05 finding. `FeatureFlag` in the same
        module had been cached since v3.17.1; `SiteSetting` simply was not the
        one that hurt first.
        """
        captured = self._measure_path(reverse('home'), authenticated=True, warm=True)
        hits = _tables_touched(captured, 'sitesetting')
        self.assertEqual(
            hits, [],
            f'SiteSetting was queried {len(hits)}× on a warm authenticated '
            f'request; `get_setting` is cached with post_save invalidation.'
            f'\n  {hits[:2]}',
        )

    # -- the properties that outlive both fixes ------------------------------

    def test_an_anonymous_request_is_cheaper_than_an_authenticated_one(self):
        """
        An ordering that should be obvious and was **false** before v3.18.7 for
        the lockdown read, which charged both alike.

        This is the assertion most likely to catch the NEXT one of these,
        because it needs no advance knowledge of which middleware or which
        table — a new per-request read added to the chain without an
        authentication check moves the two numbers together.
        """
        anonymous = len(self._measure_path(reverse('login'), authenticated=False, warm=True))
        authenticated = len(self._measure_path(reverse('home'), authenticated=True, warm=True))
        self.assertLess(
            anonymous, authenticated,
            f'An anonymous request cost {anonymous} queries and an '
            f'authenticated one {authenticated}. Anonymous traffic should be '
            f'strictly cheaper: it needs no session lookup, no 2FA policy, no '
            f'user row. If these have converged, something in the chain is '
            f'querying without first checking `request.user.is_authenticated`.',
        )

    def test_the_chain_reaches_a_steady_state(self):
        """
        Request N and request N+1 must cost the same.

        This is the property that makes the number worth pinning at all: if
        consecutive identical requests differ, the chain has per-request work
        that is not converging and no ceiling would be stable. It is also how
        you tell a genuine cache from a TTL that happens to be long.
        """
        from django.core.cache import cache
        cache.clear()

        client = Client()
        client.force_login(self.member)
        home_url = reverse('home')
        client.get(home_url)  # prime

        counts = []
        for _ in range(2):
            with CaptureQueriesContext(connection) as captured:
                client.get(home_url)
            counts.append(len(captured.captured_queries))

        self.assertEqual(
            counts[0], counts[1],
            f'Two consecutive warm requests cost {counts[0]} and {counts[1]} '
            f'queries. Something in the chain is doing work that neither '
            f'caches nor repeats predictably, so no ceiling here can be '
            f'stable until it is found.',
        )

    def test_the_chain_reports_its_own_cost(self):
        """
        Not an assertion so much as **the measurement instrument** — this is the
        test to read the future `BUDGET` off. It fails only on an absurd number,
        so it will not flake before anyone has pinned one.

        Deliberately reported for both the cold and warm paths: the gap between
        them is the size of the caching this release added, and a future run
        where the two converge means a cache stopped working.
        """
        home_url = reverse('home')
        cold = len(self._measure_path(home_url, authenticated=True, warm=False))
        warm = len(self._measure_path(home_url, authenticated=True, warm=True))


        self.assertLess(
            warm, 200,
            f'A warm authenticated request cost {warm} queries. Whatever the '
            f'right ceiling is, it is not that.',
        )
        self.assertLessEqual(
            warm, cold,
            f'The warm path ({warm}) cost MORE than the cold one ({cold}), '
            f'which should be impossible — a warm cache cannot add queries. '
            f'Suspect a cache write that itself reads, or invalidation firing '
            f'on read.',
        )


# ---------------------------------------------------------------------------
# v3.21.5 — the education dashboard
# ---------------------------------------------------------------------------
#
# ⚠️ ADDED BECAUSE THIS MODULE'S HONEST LIMITATION IS ITS COVERAGE, NOT ITS
# METHOD. v3.18.6 found two N+1 dashboards in production dev mode rather than
# here, and recorded why: this suite constrains only the pages somebody
# remembered to add. v3.20.0–v3.21.4 added eleven education routes and ~7,000
# lines in a day, and the 08-20 review measured three of the new pages by hand,
# found them flat, and wrote the numbers into a report.
#
# **A measurement in a report is not a ratchet.** These pages are the ones that
# loop over the pledge roster, which is the population that grows every autumn,
# so they are exactly where the next N+1 will appear.

class EducationDashboardQueryBudgetTests(QueryBudgetMixin, TestCase):
    """
    `/committee/<code>/education/` — the task × pledge grid.

    Every cell is a (task, pledge) pair, so a naive render is O(tasks × pledges)
    queries. It is not: v3.20.0 built the completion map in one query. This
    pins that.
    """

    #: Measured 08-20-26 on sqlite, cold cache, on the fixture below
    #: (12 pledges × 8 tasks, 40 completions, 3 meetings): 37.
    #:
    #: Not a small number, and it is chrome rather than the grid — the only
    #: repeated shapes are the two django-otp device lookups every
    #: authenticated page in this suite pays on a cold cache. Compare
    #: `home` at 41 and `activity_logs` at 38. What matters is that it is
    #: FLAT, which `test_it_does_not_scale_with_the_pledge_roster` pins.
    BUDGET = 37

    def setUp(self):
        from django.utils import timezone as tz

        from src.models import (
            Event, EducationMeeting, PledgeTask, PledgeTaskCompletion,
        )

        self.chair = make_user('qb-edu-chair', 'Education Chair')
        self.committee = Committee.objects.create(
            name='Education', code='QBEDU', is_active=True,
            is_education_committee=True,
        )
        self.committee.chairs.add(self.chair)

        # ⚠️ Pledge ids are NOT numeric — `P-C7JKZY`, not `101`. v3.21.1 shipped
        # a 500 past 119 passing tests because a fixture used numeric ones.
        self.pledges = [
            make_user(f'P-QB{index:04d}', f'Pledge {index}', member_type='Pledge')
            for index in range(12)
        ]
        self.tasks = [
            PledgeTask.objects.create(
                title=f'Task {index}', is_active=True, max_score=10,
                display_order=index,
            )
            for index in range(8)
        ]
        for pledge in self.pledges[:10]:
            for task in self.tasks[:4]:
                PledgeTaskCompletion.objects.create(
                    task=task, pledge=pledge, status='completed', score=8,
                )
        for index in range(3):
            event = Event.objects.create(
                title=f'Meeting {index}', description='', date_time=tz.now(),
                created_by=self.chair,
            )
            EducationMeeting.objects.create(
                event=event, committee=self.committee, created_by=self.chair,
            )

    def test_the_dashboard_stays_within_budget(self):
        self.assert_within_budget(
            self.chair, 'education_home', self.BUDGET, self.committee.code,
        )

    def test_it_does_not_scale_with_the_pledge_roster(self):
        """
        The property that outlives the number.

        A ceiling catches a regression only if somebody re-measures after the
        roster grows; this catches it on a fixture. Doubling the roster must
        cost nothing, because every pledge-shaped query on this page is either
        grouped or prefetched.
        """
        before = len(self.measure(self.chair, 'education_home', self.committee.code))

        for index in range(12, 24):
            make_user(f'P-QB{index:04d}', f'Pledge {index}', member_type='Pledge')

        after = len(self.measure(self.chair, 'education_home', self.committee.code))

        self.assertEqual(
            before, after,
            f'Doubling the pledge roster took the education dashboard from '
            f'{before} queries to {after}. That is a per-pledge query, i.e. an '
            f'N+1 on the page whose entire subject is the roster.',
        )


class EducationPledgeDetailQueryBudgetTests(QueryBudgetMixin, TestCase):
    """
    `/committee/<code>/education/pledge/<pk>/` — one pledge, every task and
    every meeting he has attended.

    The inverse shape of the dashboard: one pledge, many tasks. Both loops are
    somewhere a per-row query could be reintroduced without anybody noticing on
    a fixture of three.
    """

    #: Measured 08-20-26 on sqlite, cold cache, on the fixture below
    #: (10 tasks, 5 completions, 6 meetings attended): 34.
    BUDGET = 34

    def setUp(self):
        from django.utils import timezone as tz

        from src.models import (
            Event, EducationMeeting, EducationMeetingAttendance,
            PledgeTask, PledgeTaskCompletion,
        )

        self.chair = make_user('qb-det-chair', 'Education Chair')
        self.committee = Committee.objects.create(
            name='Education', code='QBDET', is_active=True,
            is_education_committee=True,
        )
        self.committee.chairs.add(self.chair)
        self.pledge = make_user('P-QBDET1', 'Detail Pledge', member_type='Pledge')

        for index in range(10):
            task = PledgeTask.objects.create(
                title=f'Task {index}', is_active=True, max_score=10,
                display_order=index,
            )
            if index % 2 == 0:
                PledgeTaskCompletion.objects.create(
                    task=task, pledge=self.pledge, status='completed', score=9,
                )

        for index in range(6):
            event = Event.objects.create(
                title=f'Meeting {index}', description='', date_time=tz.now(),
                created_by=self.chair,
            )
            meeting = EducationMeeting.objects.create(
                event=event, committee=self.committee, created_by=self.chair,
                points=2,
            )
            EducationMeetingAttendance.objects.create(
                meeting=meeting, pledge=self.pledge, status='present',
                marked_by=self.chair,
            )

    def test_the_detail_page_stays_within_budget(self):
        self.assert_within_budget(
            self.chair, 'education_pledge_detail', self.BUDGET,
            self.committee.code, self.pledge.pk,
        )

    def test_it_does_not_scale_with_attendance_history(self):
        """
        Attendance is the unbounded one here — a pledge accumulates a row per
        meeting for as long as he is a pledge, and the page renders each
        meeting's title and date, which live on the joined `Event`.
        """
        from django.utils import timezone as tz

        from src.models import Event, EducationMeeting, EducationMeetingAttendance

        before = len(self.measure(
            self.chair, 'education_pledge_detail',
            self.committee.code, self.pledge.pk,
        ))

        for index in range(6, 18):
            event = Event.objects.create(
                title=f'Meeting {index}', description='', date_time=tz.now(),
                created_by=self.chair,
            )
            meeting = EducationMeeting.objects.create(
                event=event, committee=self.committee, created_by=self.chair,
            )
            EducationMeetingAttendance.objects.create(
                meeting=meeting, pledge=self.pledge, status='present',
                marked_by=self.chair,
            )

        after = len(self.measure(
            self.chair, 'education_pledge_detail',
            self.committee.code, self.pledge.pk,
        ))

        self.assertEqual(
            before, after,
            f'Tripling this pledge\'s attendance history took the page from '
            f'{before} queries to {after} — a per-meeting query, which is what '
            f'the `select_related("meeting", "meeting__event")` on that '
            f'queryset exists to prevent.',
        )


# ---------------------------------------------------------------------------
# The page the 08-23-26 sweep caught  (v3.25.0)
# ---------------------------------------------------------------------------
#
# ⚠️ THIS ONE WAS NOT MISSED BECAUSE NOBODY ADDED IT TO THIS FILE. It was in
# `src/test_url_smoke.py`'s N+1 sweep the whole time, on every CI run, and the
# sweep reported it clean at 271 queries.
#
# The sweep's fixture creates six events, all at `now + timedelta(days=i + 1)`.
# Every attendance page filters `date_time__lt=now`. So the page rendered an
# empty list, and a per-row query fired zero times repeats zero times.
#
# **The honest limitation of an absolute-ceiling suite is that it constrains
# only the pages someone remembered to add. The honest limitation of a
# fixture-driven sweep is that it constrains only the pages its fixture reaches
# — and that is much harder to notice, because the page IS in the list and the
# result IS green.** The fixture is fixed in `_seed_past_attendance`; this class
# is the second lock on the same door.


class EventAttendanceListQueryBudgetTests(QueryBudgetMixin, TestCase):
    """
    `/officers/attendance/` — the officer list of events with attendance.

    The template renders `past_events` **twice**, as a desktop table and as
    mobile cards, and each row of each layout called
    `Event.get_attendance_stats()`, which is six queries. Twenty past events
    (the view's own cap) therefore cost forty calls: 240 queries of the 271 the
    page measured.
    """

    #: Measured 08-23-26 on sqlite, cold cache, on the fixture below
    #: (24 past events so the view's `[:20]` slice is actually exercised, plus
    #: 4 upcoming), twice, same number both times.
    #:
    #: Pre-fix on the same fixture: **271**.
    BUDGET = 33

    PASSWORD = 'attendance-budget-pass-12345!'

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone as tz

        from src.models import Attendance, Event, ParliamentUser

        self.officer = ParliamentUser.objects.create_user(
            user_id='EAL-OFFICER', password=self.PASSWORD, name='Officer',
            username='eal_officer', member_type='Officer', is_admin=True,
        )
        self.members = [
            ParliamentUser.objects.create_user(
                user_id=f'EAL-{i}', password=self.PASSWORD, name=f'Member {i}',
                username=f'eal_member{i}', member_type='Member',
            )
            for i in range(8)
        ]

        now = tz.now()
        statuses = ['present', 'absent', 'excused', 'pending', 'late']
        # ⚠️ 24, not 20. The view slices `[:20]`, so a fixture of exactly the
        # cap cannot tell a page that respects the cap from one that does not.
        for i in range(24):
            event = Event.objects.create(
                title=f'Past {i}', description='d',
                date_time=now - timedelta(days=i + 1), created_by=self.officer,
                is_active=True, requires_attendance=True,
            )
            for j, member in enumerate(self.members):
                Attendance.objects.create(
                    user=member, event=event, attendance_type='event',
                    status=statuses[(i + j) % len(statuses)],
                )
        for i in range(4):
            Event.objects.create(
                title=f'Upcoming {i}', description='d',
                date_time=now + timedelta(days=i + 1), created_by=self.officer,
                is_active=True, requires_attendance=True,
            )

    def test_the_attendance_list_stays_within_budget(self):
        self.assert_within_budget(self.officer, 'event_attendance_list', self.BUDGET)

    def test_it_does_not_scale_with_the_calendar(self):
        """
        ⚠️ THE ASSERTION THE OLD CODE FAILED, and the one that needs no ceiling.

        The view caps `past_events` at 20, so the absolute count above is
        bounded even when the page is at its worst — which is precisely why the
        271 was survivable and therefore invisible. This asserts the property
        instead: the number must not move when the calendar grows.
        """
        from datetime import timedelta

        from django.utils import timezone as tz

        from src.models import Attendance, Event

        before = len(self.measure(self.officer, 'event_attendance_list'))

        now = tz.now()
        for i in range(24, 60):
            event = Event.objects.create(
                title=f'Past {i}', description='d',
                date_time=now - timedelta(days=i + 1), created_by=self.officer,
                is_active=True, requires_attendance=True,
            )
            for member in self.members:
                Attendance.objects.create(
                    user=member, event=event, attendance_type='event',
                    status='present',
                )

        after = len(self.measure(self.officer, 'event_attendance_list'))

        self.assertEqual(
            before, after,
            f'Going from 24 to 60 past events took the page from {before} '
            f'queries to {after}. `Event.prime_attendance_stats()` exists to '
            f'keep this flat — check that the view still passes its LIST to '
            f'the template rather than re-evaluating the queryset, because the '
            f'primed cache lives on the instances.',
        )

    def test_the_primed_numbers_match_the_per_event_ones(self):
        """
        ⚠️ A QUERY-COUNT TEST CANNOT SEE A FAST PAGE WITH WRONG NUMBERS, and
        that is the failure mode a batched aggregate actually has. v3.18.6's
        service-hours fix needed `.order_by()` to clear `Meta.ordering` out of
        the `GROUP BY`; without it the aggregate returns one row per record and
        every figure on the page is wrong while the query count looks perfect.

        `Attendance.Meta.ordering` is `['-created_at', 'user__name']`, so this
        page has exactly that hazard.
        """
        from src.models import Event

        events = list(Event.objects.filter(requires_attendance=True))
        slow = {
            event.pk: Event.objects.get(pk=event.pk).get_attendance_stats()
            for event in events
        }
        fast = {
            event.pk: event.get_attendance_stats()
            for event in Event.prime_attendance_stats(events)
        }

        self.assertEqual(slow, fast)
        # A control: the fixture must actually contain marked attendance, or
        # the comparison above is two empty dictionaries agreeing.
        self.assertTrue(
            any(stats['present'] for stats in fast.values()),
            'The fixture has no present marks, so this test compares nothing.',
        )

    def test_late_marks_count_as_marked_but_in_no_bucket(self):
        """
        `unmarked` is `total_members - <every attendance row>`, not
        `total_members - (present + absent + excused + pending)`. `'late'` is a
        fifth status that no bucket counts. The bulk path has to reproduce that
        arithmetic exactly, and the fixture above includes `late` marks so this
        is not vacuous.
        """
        from src.models import Attendance, Event

        event = Event.objects.filter(requires_attendance=True,
                                     attendance_records__status='late').first()
        self.assertIsNotNone(event, 'fixture has no late marks')

        stats = Event.prime_attendance_stats([event])[0].get_attendance_stats()
        buckets = sum(stats[k] for k in ('present', 'absent', 'excused', 'pending'))
        rows = Attendance.objects.filter(event=event).count()

        self.assertLess(buckets, rows, 'no late marks on this event — vacuous')
        self.assertEqual(stats['unmarked'], stats['total_members'] - rows)


# ---------------------------------------------------------------------------
# Moved here from `src/test_my_attendance_budget.py`  (v3.25.0)
# ---------------------------------------------------------------------------
#
# v3.24.0 measured `/my-attendance/` at 25 queries and then shipped its budget
# class in a sibling module with **no `BUDGET` constant**, citing v3.18.6.
#
# ⚠️ v3.18.6's REASON WAS "THERE IS NO WORKING DJANGO TO MEASURE WITH", and an
# invented ceiling reads exactly like a measured one. v3.24.0 had a working
# Django and had already measured the page. **An exemption inherited without
# its reasoning is a ritual** — the same failure `DEPLOYED.md` records about
# `--diff-filter=A`, one level in.
#
# It also sat outside every mechanism that exists to keep such an exemption
# visible: `BudgetHygieneTests` enumerated only its own module, so the class
# was in neither `AWAITING_MEASUREMENT` nor the budget list. Its docstring said
# so plainly, which made it visible to a human reading that file and invisible
# to the ratchet. That enumeration is now keyed on `QueryBudgetMixin` across
# every test module — see `test_the_unmeasured_list_is_accurate_in_both_
# directions`.
#
# The page's *correctness* tests stayed behind, in `src/test_my_attendance.py`.
# They are not budget tests and they do not belong here.


class MyAttendanceQueryBudgetTests(QueryBudgetMixin, TestCase):
    """
    `/my-attendance/` — the personal attendance dashboard.

    ⚠️ WHY THIS PAGE HAS A BUDGET AT ALL. It was linked from nowhere until
    v3.22.0 — the only way in was to type the URL — and it cost 116 queries at
    40 events and **349** at 120 with the one-click "All time" filter. v3.22.0
    put it on every member's home page, which was the right call and is also
    what turned an unreachable page's cost into the chapter's cost.

    > **A page's query count starts mattering on the day somebody can click
    > it.** Promoting an unreachable page is a performance change.
    """

    #: Measured 08-23-26 on sqlite, cold cache, through `QueryBudgetMixin`
    #: (which clears the cache and warms the singleton rows, so this is the
    #: pessimistic first-request number and is comparable with every other
    #: ceiling in this module — it is NOT the 25 v3.24.0 measured with a bare
    #: `CaptureQueriesContext`). Fixture below: 20 past events, an excuse, and
    #: a four-instance recurring series. Twice, same number both times.
    BUDGET = 34

    PASSWORD = 'my-attendance-budget-pass-12345!'

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone as tz

        from src.models import (Attendance, AttendanceExcuse, Event,
                                ParliamentUser)

        self.officer = ParliamentUser.objects.create_user(
            user_id='MAB-OFFICER', password=self.PASSWORD, name='Officer',
            username='mab_officer', member_type='Officer', is_admin=True,
        )
        # ⚠️ A NON-NUMERIC id on purpose. v3.21.1 shipped three `<int:>` routes
        # past a green suite because the fixture used numeric pledge ids, and
        # the rule from it is that a fixture easier than production tests
        # something else.
        self.member = ParliamentUser.objects.create_user(
            user_id='P-C7JKZY', password=self.PASSWORD, name='Member',
            username='mab_member', member_type='Member',
        )
        self._build_calendar(20)

        # A recurring series, because the per-series block is a SEPARATE loop
        # from the history loop and used to cost three queries per series.
        parent = Event.objects.create(
            title='Weekly Chapter', description='x',
            date_time=tz.now() - timedelta(days=40), created_by=self.officer,
            requires_attendance=True, is_active=True, is_recurring=True,
        )
        for i in range(4):
            child = Event.objects.create(
                title='Weekly Chapter', description='x',
                date_time=tz.now() - timedelta(days=35 - i * 7),
                created_by=self.officer, requires_attendance=True,
                is_active=True, parent_event=parent,
            )
            Attendance.objects.create(user=self.member, event=child,
                                      status='present', attendance_type='event')
        AttendanceExcuse.objects.create(
            user=self.member, event=parent, reason='budget fixture')

    def _build_calendar(self, n_events):
        from datetime import timedelta

        from django.utils import timezone as tz

        from src.models import Attendance, Event

        now = tz.now()
        events = []
        for i in range(n_events):
            event = Event.objects.create(
                title=f'Chapter Meeting {i}', description='x',
                date_time=now - timedelta(days=i + 1), created_by=self.officer,
                requires_attendance=True, is_active=True,
            )
            Attendance.objects.create(
                user=self.member, event=event, attendance_type='event',
                status='present' if i % 3 else 'absent',
            )
            events.append(event)
        return events

    def test_my_attendance_stays_within_budget(self):
        self.assert_within_budget(self.member, 'my_attendance', self.BUDGET)

    def test_adding_events_does_not_add_queries(self):
        """
        ⚠️ THE ASSERTION THE OLD CODE FAILED. Both lookups in the history loop
        were `.filter(...).first()` inside the loop body — two queries per event
        — and the attendance half re-read rows the view had already fetched.
        """
        before = len(self.measure(self.member, 'my_attendance'))
        self._build_calendar(40)
        after = len(self.measure(self.member, 'my_attendance'))

        self.assertEqual(
            before, after,
            f'Tripling the calendar took the page from {before} queries to '
            f'{after}. Check the history loop and the per-series loop — those '
            f'are the two that have done this.',
        )

    def test_the_all_time_filter_does_not_change_the_shape(self):
        """
        `?range=0` is one click in the page's own filter and removes the date
        bound entirely, so it is the worst case a member can reach without
        trying. It must cost what the default costs.
        """
        default_range = len(self.measure(self.member, 'my_attendance', range='90'))
        all_time = len(self.measure(self.member, 'my_attendance', range='0'))

        self.assertEqual(
            default_range, all_time,
            f'"All time" costs {all_time} queries against {default_range} for '
            f'the default range, so the page gets slower the longer the '
            f'chapter has existed.',
        )

    def test_the_fixture_actually_reaches_the_loops(self):
        """
        ⚠️ CONTROL, and it is this release's own lesson pointed at itself: an
        assertion that adding rows does not add queries passes trivially if the
        page is rendering an empty list. `src/test_url_smoke.py` reported clean
        for years for exactly that reason.
        """
        from src.models import Event

        self.client.force_login(self.member)
        context = self.client.get(reverse('my_attendance')).context

        # 20 standalone meetings + the recurring parent + its 4 instances. The
        # parent is itself a past event that requires attendance, so it appears
        # in the history list as well as in the series block.
        self.assertEqual(Event.objects.count(), 25)
        self.assertEqual(len(context['attendance_history']), 25)
        self.assertEqual(len(context['series_stats']), 1)
        self.assertTrue(
            any(row['excuse'] for row in context['attendance_history']),
            'The excuse map is never exercised, so the history loop is only '
            'half measured.',
        )


# ---------------------------------------------------------------------------
# `/officers/events/` — added 08-30-26 after dev mode caught it live: 9
# identical queries firing from `{% if event.parent_event %}` (plus
# `.id`/`.title` on the "Instance" badge) in manage_events.html, one per row
# that happened to be a recurring instance. `select_related('created_by')`
# already existed for the same reason on a different field (v3.17.4) — this
# is that exact pattern recurring on a second FK the view never joined.
# ---------------------------------------------------------------------------

class ManageEventsListQueryBudgetTests(QueryBudgetMixin, TestCase):
    """
    The officer event-management list — desktop table + mobile cards, same
    row rendered twice, same shape of bug `EventAttendanceListQueryBudgetTests`
    documents for a different page.
    """

    #: Measured 08-30-26 on sqlite, cold cache, on the fixture below (18
    #: standalone events + 1 recurring parent + 9 of its instances = 28 rows,
    #: paginated at 25/page so the cap is actually exercised), twice, same
    #: number both times.
    #:
    #: Pre-fix on the same fixture (parent_event NOT select_related'd): 38 —
    #: the 9 extra queries are exactly the 9 instance rows on the page.
    BUDGET = 29

    PASSWORD = 'manage-events-budget-pass-12345!'

    def setUp(self):
        from datetime import timedelta

        from django.utils import timezone as tz

        from src.models import Event, ParliamentUser

        self.officer = ParliamentUser.objects.create_user(
            user_id='MEL-OFFICER', password=self.PASSWORD, name='Officer',
            username='mel_officer', member_type='Officer', is_admin=True,
        )
        now = tz.now()

        for i in range(18):
            Event.objects.create(
                title=f'Standalone {i}', description='d',
                date_time=now + timedelta(days=i + 1), created_by=self.officer,
                is_active=True,
            )

        parent = Event.objects.create(
            title='Weekly Chapter', description='recurring parent',
            date_time=now + timedelta(days=1), created_by=self.officer,
            is_active=True, is_recurring=True,
        )
        # ⚠️ 9, not fewer — the report that caught this live fired the same
        # query shape 9 times. A fixture smaller than that could pass while
        # still leaving an N+1 that just hasn't been multiplied up yet.
        for i in range(9):
            Event.objects.create(
                title=f'Child {i}', description='d',
                date_time=now + timedelta(days=i + 2), created_by=self.officer,
                is_active=True, parent_event=parent,
            )

    def test_the_events_list_stays_within_budget(self):
        self.assert_within_budget(self.officer, 'manage_events', self.BUDGET)

    def test_it_does_not_scale_with_instance_count(self):
        """
        ⚠️ THE ASSERTION THE OLD CODE FAILED. `select_related('parent_event')`
        turns the per-instance fetch into one JOIN already paid for by every
        row — including the standalone ones with no parent — so the count
        must not move as more instances are added to the page.
        """
        from datetime import timedelta

        from django.utils import timezone as tz

        from src.models import Event

        before = len(self.measure(self.officer, 'manage_events'))

        parent = Event.objects.get(title='Weekly Chapter')
        now = tz.now()
        for i in range(9, 20):
            Event.objects.create(
                title=f'Child {i}', description='d',
                date_time=now + timedelta(days=i + 2), created_by=self.officer,
                is_active=True, parent_event=parent,
            )

        after = len(self.measure(self.officer, 'manage_events'))

        self.assertEqual(
            before, after,
            f'Adding 11 more recurring instances took the page from {before} '
            f'queries to {after}. Check that `manage_events` still calls '
            f'`select_related(\'parent_event\')` on the events queryset.',
        )

    def test_the_fixture_actually_renders_instance_badges(self):
        """
        ⚠️ CONTROL. A budget/scaling pair that passes on a page not exercising
        the `{% if event.parent_event %}` branch at all would be measuring
        nothing about this bug — `test_url_smoke` reported this page clean for
        the same reason before dev mode caught it live.

        19, not 9: the template renders every row twice (desktop table +
        mobile cards, same pattern `EventAttendanceListQueryBudgetTests`
        documents for `/officers/attendance/`), so each of the 9 instance
        rows prints the "Instance" badge once per layout (18), plus one
        "Recurring Instances" filter-dropdown label that's on the page
        regardless of fixture (19). Fixture event titles deliberately avoid
        the word "Instance" themselves (they're named "Child N"), so this
        count is only the badges + the static label, not a title collision
        inflating it.
        """
        self.client.force_login(self.officer)
        response = self.client.get(reverse('manage_events'))
        body = response.content.decode()

        self.assertEqual(body.count('Instance'), 19)
