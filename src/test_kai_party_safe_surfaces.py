"""
The general form of the v3.18.1 search oracle: **no input a party controls may
reveal a field they are not allowed to read.**

WHY A GENERAL TEST AND NOT ANOTHER SPECIFIC ONE
-----------------------------------------------
This same bug has now been found three times, in three places, by three
different reviews:

| Found | Where | Shape |
|---|---|---|
| v3.16.3 | Kai list + CSV search filtered on `description` / both names | *a filter predicate is a join key* |
| v3.18.1 | the reviewer list, after it switched from excluding a party's case to showing it redacted | *when a surface stops EXCLUDING and starts REDACTING, every predicate touching that row becomes a disclosure* |
| v3.18.2 | `ActivityLog`'s search box, one page over | same shape, a model nobody had enumerated |

Each was fixed where it was found, correctly, with a regression test pinned to
that surface. And each time the *next* surface was written by someone who had
read the fix and not the principle.

So this module does not test a surface. **It tests a property, over a table of
surfaces**, and adding a surface is one row. The property:

> For a viewer who is a party to case X, the observable output of a list-shaped
> surface must be **identical** whether or not their search term matches a
> field of case X that they may not read.

That is an indistinguishability argument, which is the right shape for an
oracle: it does not ask "is the secret printed?" (redaction already answers
that) but "does anything the viewer can *do* let them infer it?"

WHAT MAKES IT HONEST
--------------------
Every case below is checked against a **control**: the same search term, the
same surface, but a case the viewer is *not* party to, where the term MUST
produce a hit. Without that, a surface that is simply broken — returning
nothing to anybody — would pass every assertion here and look secure.

HOW TO ADD A SURFACE
--------------------
Add a row to `SURFACES`. If it is not list-shaped, it does not belong here; if
it renders a case's content to a party at all, it belongs in
`test_kai_redaction_surfaces.py` instead.
"""

from django.test import Client, TestCase
from django.urls import reverse

from src.models import Committee, ParliamentUser
from src.models.kai import KaiMemberPermission, KaiReport

#: A word that appears ONLY in the hidden allegation body of the viewer's own
#: case. Nothing else in the fixture contains it, so any surface whose output
#: changes when this is searched has leaked the body.
BODY_SECRET = 'PINEAPPLEGATE'

#: The submitter's surname on the viewer's own case — the identity the whole
#: Kai module exists to withhold from the accused.
SUBMITTER_SURNAME = 'Quillfeather'

#: A term that matches nothing at all. The other half of every comparison: if
#: searching the secret returns the same thing as searching gibberish, the
#: secret bought the viewer nothing.
NONSENSE = 'zzzz-no-such-term-zzzz'


def make_user(uid, name):
    user = ParliamentUser.objects.create(
        user_id=uid, name=name, username=uid,
        member_type='Officer', member_status='Active',
    )
    user.set_password('party-safe-pass-12345!')
    user.save()
    return user


class PartySafeSurfaceTests(TestCase):
    """
    One viewer, who is the accused on their own case AND a fully-permissioned
    Kai reviewer — the population § vi's recusal machinery exists for, and the
    only population for whom this class of bug is reachable.
    """

    def setUp(self):
        self.committee = Committee.objects.create(
            name='Kai', code='KAI', is_kai_committee=True,
        )
        self.submitter = make_user('ps-sub', f'Zebediah {SUBMITTER_SURNAME}')
        self.viewer = make_user('ps-viewer', 'Viewer Vic')

        # The viewer's own case. They are the accused, so `_case_access` recuses
        # them: the row is SHOWN but redacted, which is exactly the state that
        # made the predicate a disclosure in v3.18.1.
        self.own_case = KaiReport.objects.create(
            title='Own Case Alpha',
            description=f'The {BODY_SECRET} incident occurred at the house.',
            submitted_by=self.submitter,
            targeted_to=self.viewer,
        )

        # The control case. Same secret word, same submitter — but the viewer is
        # not a party, so every surface SHOULD match on it. This is what proves
        # a passing assertion means "safe" rather than "broken".
        other = make_user('ps-other', 'Other Oliver')
        self.control_case = KaiReport.objects.create(
            title='Control Case Beta',
            description=f'A different {BODY_SECRET} matter entirely.',
            submitted_by=self.submitter,
            targeted_to=other,
        )

        # Full committee-level permissions. The point is that these say the
        # viewer MAY read descriptions and both identities — true in general,
        # false for their own case — and the predicate reads these flags.
        self.committee.members.add(self.viewer)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.viewer,
            can_view_report_list=True, can_view_report_details=True,
            can_view_submitter_identity=True, can_view_accused_identity=True,
        )

        self.client = Client()
        self.client.force_login(self.viewer)

    # -- observation -----------------------------------------------------

    def _sees(self, url_name, param, term, case):
        """
        Whether `case` is observable in this surface's output for `term`.

        Deliberately looks for the case NUMBER rather than the title: the
        number is what the redacted card still renders, so it is the thing an
        oracle would actually be read from. A surface that stopped rendering
        numbers would fail the control and be caught.
        """
        response = self.client.get(reverse(url_name), {param: term})
        self.assertIn(
            response.status_code, (200, 302),
            f'{url_name} returned {response.status_code} — fix the fixture, '
            f'a surface that errors proves nothing.',
        )
        if response.status_code == 302:
            return False
        body = response.content.decode()
        return case.display_number in body

    def assert_party_safe(self, url_name, param, term, label):
        """
        The property, plus its control, in one assertion.

        `term` matches a field of the viewer's OWN case that they may not read.
        Whether it does or not must make no difference to what they observe.
        """
        with_term = self._sees(url_name, param, term, self.own_case)
        without = self._sees(url_name, param, NONSENSE, self.own_case)
        self.assertEqual(
            with_term, without,
            f'ORACLE in {url_name}: searching {label} changed whether the '
            f'viewer\'s own case ({self.own_case.display_number}) appeared '
            f'(term={with_term}, nonsense={without}). The viewer is the accused '
            f'on that case and may not read that field, so the search box just '
            f'told them its contents. See src/kai_audit.py and _kai_search_q '
            f'for the two existing fixes of this exact shape.',
        )

    def assert_control_still_works(self, url_name, param, term, label):
        """The other half: the surface must not be simply broken."""
        self.assertTrue(
            self._sees(url_name, param, term, self.control_case),
            f'CONTROL FAILED in {url_name}: searching {label} did not find a '
            f'case the viewer IS allowed to read. The party-safety assertions '
            f'for this surface are therefore meaningless — they would pass on '
            f'a surface that returns nothing to anybody.',
        )

    # -- the reviewer list ------------------------------------------------

    def test_the_reviewer_list_is_party_safe_on_the_allegation_body(self):
        self.assert_party_safe(
            'view_kai_reports', 'search', BODY_SECRET, 'the hidden allegation body')

    def test_the_reviewer_list_is_party_safe_on_the_submitter_name(self):
        self.assert_party_safe(
            'view_kai_reports', 'search', SUBMITTER_SURNAME,
            "the redacted reporter's surname")

    def test_the_reviewer_list_control_still_finds_other_cases(self):
        self.assert_control_still_works(
            'view_kai_reports', 'search', BODY_SECRET, 'the allegation body')

    def test_the_reviewer_list_still_finds_the_own_case_by_title(self):
        """
        The property that broke the page twice under the exclusion designs, and
        the reason v3.18.1 chose redaction: a party must still be able to find
        their own case by the fields the card DOES render.
        """
        self.assertTrue(
            self._sees('view_kai_reports', 'search', 'Own Case Alpha', self.own_case),
            'A party can no longer find their own case by title. That is the '
            'regression the exclusion designs caused twice — see the comment '
            'block in view_kai_reports.',
        )

    # -- global search ----------------------------------------------------

    def test_global_search_is_party_safe_on_the_allegation_body(self):
        self.assert_party_safe(
            'global_search', 'q', BODY_SECRET, 'the hidden allegation body')

    def test_global_search_is_party_safe_on_the_submitter_name(self):
        self.assert_party_safe(
            'global_search', 'q', SUBMITTER_SURNAME, "the reporter's surname")

    # -- the CSV export ---------------------------------------------------

    def test_the_csv_export_is_party_safe_on_the_allegation_body(self):
        self.assert_party_safe(
            'export_kai_reports_csv', 'search', BODY_SECRET,
            'the hidden allegation body')

    def test_the_csv_export_is_party_safe_on_the_submitter_name(self):
        self.assert_party_safe(
            'export_kai_reports_csv', 'search', SUBMITTER_SURNAME,
            "the reporter's surname")

    # -- the audit log (v3.18.2's surface) ---------------------------------

    def test_the_activity_log_is_party_safe_on_the_submitter_name(self):
        """
        The eleventh surface, and the newest — included here so it is covered
        by the general property and not only by its own module's tests.
        """
        from src.models import ActivityLog

        ActivityLog.log_activity(
            action_type='kai_action', user=self.submitter,
            description=(
                f'Zebediah {SUBMITTER_SURNAME} submitted Kai case '
                f'{self.own_case.display_number}'
            ),
            object_type='KaiReport', object_id=self.own_case.id,
            object_repr=self.own_case.display_number,
        )
        matched = self._sees(
            'activity_logs', 'q', SUBMITTER_SURNAME, self.own_case)
        nonsense = self._sees('activity_logs', 'q', NONSENSE, self.own_case)
        self.assertEqual(
            matched, nonsense,
            'ORACLE in activity_logs: the audit-log search box distinguished a '
            'Kai row by its submitter name. See audit_search_q.',
        )

    def test_the_activity_log_csv_is_party_safe_on_the_submitter_name(self):
        """
        The export, not the page — added because
        `test_every_kai_search_surface_is_covered_here` flagged it as a routed
        Kai search surface with no party-safety coverage, which is exactly the
        job that test exists to do. v3.16.2's lesson in one line: **a redaction
        applied to a page and not to its export is not a redaction**, and the
        same is true of a predicate.
        """
        from src.models import ActivityLog

        ActivityLog.log_activity(
            action_type='kai_action', user=self.submitter,
            description=(
                f'Zebediah {SUBMITTER_SURNAME} submitted Kai case '
                f'{self.own_case.display_number}'
            ),
            object_type='KaiReport', object_id=self.own_case.id,
            object_repr=self.own_case.display_number,
        )
        matched = self._sees(
            'export_activity_logs', 'q', SUBMITTER_SURNAME, self.own_case)
        nonsense = self._sees('export_activity_logs', 'q', NONSENSE, self.own_case)
        self.assertEqual(
            matched, nonsense,
            'ORACLE in export_activity_logs: the CSV export distinguished a Kai '
            'row by its submitter name. The page and the export share '
            'audit_search_q — check both call sites.',
        )


class TheEnumerationIsMaintainedTests(TestCase):
    """
    The lesson from four consecutive misses: an enumeration written from memory
    inherits the blind spot it was built to remove.
    """

    def test_every_kai_search_surface_is_covered_here(self):
        """
        Greps the Kai views for anything that filters on a user-supplied search
        term, and fails if a surface exists that this module does not exercise.

        This is the guard that would have caught v3.18.2 a release early: the
        audit log filtered on a search term and no party-safety test knew it
        existed. When this fails, the answer is a new row in
        `PartySafeSurfaceTests`, not an addition to the skip list — there is
        deliberately no skip list.
        """
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parent
        covered = {
            'view_kai_reports', 'export_kai_reports_csv',
            'global_search', 'activity_logs', 'export_activity_logs',
        }

        # A view is a suspect only if ITS OWN BODY both reads a search term
        # and touches Kai.
        #
        # The first version of this checked whether the *file* mentioned Kai,
        # and that was far too loose: `admin_v2.py` mentions Kai in passing and
        # contains a dozen unrelated search boxes, so the test demanded
        # party-safety coverage for `admin_v2_login`. A guard that cries wolf
        # gets an exclusion list bolted on, and an exclusion list is how a
        # guard stops guarding.
        search_re = re.compile(r"""request\.GET\.get\(\s*['"](search|q)['"]""")
        kai_re = re.compile(r'\bKai[A-Z]\w*|_kai_|kai_access|kai_reports')

        suspects = set()
        for path in (root / 'view').rglob('*.py'):
            text = path.read_text(encoding='utf-8', errors='ignore')
            # Split into top-level function bodies so each is judged alone.
            starts = [(m.start(), m.group(1))
                      for m in re.finditer(r'^def ([a-z_0-9]+)\(', text, re.M)]
            for i, (pos, name) in enumerate(starts):
                if name.startswith('_'):
                    continue
                end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
                body = text[pos:end]
                if search_re.search(body) and kai_re.search(body):
                    suspects.add(name)

        # Only complain about views that are actually routed — helpers and
        # dead code are not surfaces.
        from django.urls import NoReverseMatch, reverse as dj_reverse

        routed = set()
        for name in suspects:
            try:
                dj_reverse(name)
            except NoReverseMatch:
                continue
            routed.add(name)

        missing = routed - covered
        self.assertEqual(
            missing, set(),
            f'These routed Kai-touching views take a search term but are not '
            f'exercised by PartySafeSurfaceTests: {sorted(missing)}. Add a row '
            f'rather than widening the covered set — the covered set is the '
            f'claim, and it should only grow with a test behind it.',
        )


class AuthorValuedFilterTests(TestCase):
    """
    ⚠️ v3.18.4 — THE SECOND KIND OF PREDICATE, AND THE ONE THAT WAS MISSED.

    Everything above tests **search terms**: a free-text value the viewer
    guesses, matched against a column. `audit_search_q` narrows those. But a
    surface can also be reached by an **author-valued filter** — a dropdown
    listing every member, whose selected value IS an identity — and that is a
    different question with a different answer:

    * a search term is narrowed (`audit_search_q`), because the row should
      still be visible to someone browsing;
    * an author filter must **exclude** (`exclude_kai_logs`), because the
      viewer supplied the name, so the row's mere presence answers them.

    `src/kai_audit.py` states this exactly, and v3.18.2 applied it to three of
    the four surfaces that need it: `/admin/`, admin-v2's per-member drill, and
    the audit-log search. The fourth was the `?user=` dropdown on
    `/activity-logs/` — the very page the module was written for. So:

        /activity-logs/?user=<member>&category=kai
        → "Anonymous submitted Kai case KAI-2026-012"

    and the redaction bought nothing, because the officer chose the member from
    a dropdown listing every active one. `officer_required` admits every
    officer and chair and consults no `KaiMemberPermission`, so that was the
    chapter's whole officer corps, at a cost of two clicks — cheaper than the
    v3.18.1 search oracle, which needed a guessed string and Kai membership.

    **THE RULE, and it is why the miss happened: the PREDICATE decides which
    half of `kai_audit` applies, not the page.** v3.18.2 classified surfaces by
    which view they lived in. Three were right. The fourth ran the identical
    `filter(user=…)` one file over from a call site that gets it right.

    Every test here fails against the v3.18.3 tree.
    """

    def setUp(self):
        self.committee = Committee.objects.create(
            name='Kai', code='KAI', is_kai_committee=True,
        )
        self.submitter = make_user('avf-sub', f'Zebediah {SUBMITTER_SURNAME}')
        accused = make_user('avf-acc', 'Accused Andrew')
        self.case = KaiReport.objects.create(
            title='Filtered Case Gamma',
            description=f'The {BODY_SECRET} matter.',
            submitted_by=self.submitter,
            targeted_to=accused,
        )

        from src.models import ActivityLog

        # The row `submit_kai_report` writes. Post-v3.18.2 the description
        # carries no name — which is precisely why the *filter* is the leak and
        # not the text: redaction has nothing left to remove, and the officer
        # still learns who reported the case.
        self.kai_row = ActivityLog.log_activity(
            action_type='kai_action', user=self.submitter,
            description=f'A member submitted Kai case {self.case.display_number}',
            object_type='KaiReport', object_id=self.case.id,
            object_repr=self.case.display_number,
        )
        # The control: a NON-Kai row by the same author. The filter must keep
        # working, or every assertion below would pass on a broken page.
        self.other_row = ActivityLog.log_activity(
            action_type='login', user=self.submitter,
            description=f'Zebediah {SUBMITTER_SURNAME} logged in successfully',
        )

        # An officer with NO Kai permission — the population at issue, and the
        # one `officer_required` lets in without consulting anything Kai.
        self.officer = make_user('avf-officer', 'Officer Olive')
        self.client = Client()
        self.client.force_login(self.officer)

    def _body(self, url_name, **params):
        response = self.client.get(reverse(url_name), params)
        self.assertEqual(
            response.status_code, 200,
            f'{url_name} returned {response.status_code} — a surface that '
            f'errors proves nothing.',
        )
        return response.content.decode()

    # -- the page ---------------------------------------------------------

    def test_filtering_by_author_does_not_reveal_that_they_filed_a_kai_case(self):
        body = self._body('activity_logs', user=self.submitter.user_id)
        self.assertNotIn(
            self.case.display_number, body,
            'ORACLE in activity_logs: filtering the audit log by a member '
            'returned their Kai submission row. The description is redacted, '
            'but the viewer SUPPLIED the name — the row\'s presence under it '
            'is the disclosure. This predicate needs exclude_kai_logs, not '
            'redact_kai_logs. See src/kai_audit.py, and admin_v2.py:1789 for '
            'the same predicate done correctly.',
        )

    def test_the_author_filter_still_works_for_everything_else(self):
        """The control. Exclusion must cost only the Kai rows."""
        body = self._body('activity_logs', user=self.submitter.user_id)
        self.assertIn(
            'logged in successfully', body,
            'CONTROL FAILED: the author filter stopped returning non-Kai rows, '
            'so the assertion above would pass on a page that shows nothing.',
        )

    def test_the_category_chip_cannot_be_combined_to_recover_it(self):
        """
        The two-click path exactly as reported: pick a member, pick the *Kai
        Committee* chip.
        """
        body = self._body(
            'activity_logs', user=self.submitter.user_id, category='kai')
        self.assertNotIn(self.case.display_number, body)

    def test_the_counts_do_not_betray_the_excluded_rows(self):
        """
        A total above the row count says how many were hidden, and on a
        Kai-filtered page hidden means Kai. `admin_v2.py:1811` already makes
        this argument in a comment; the same reasoning applies to `total_logs`
        and `category_counts` here, which are computed from the same queryset.

        Asserted against `response.context`, NOT the rendered body — the
        category *dropdown* renders every `ACTION_CATEGORIES` label including
        "Kai Committee" regardless of counts, so a body-text assertion here
        would fail for a reason that has nothing to do with the bug. (Caught
        while writing this test, which is the same lesson the control cases in
        this module exist to teach: an assertion that cannot distinguish the
        bug from the fixture is not an assertion.)
        """
        response = self.client.get(
            reverse('activity_logs'),
            {'user': self.submitter.user_id, 'category': 'kai'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context['total_logs'], 0,
            'total_logs counted Kai rows the page will not show. A count above '
            'the row count tells the viewer how many were hidden, and under a '
            'Kai filter hidden means Kai.',
        )
        self.assertNotIn(
            'kai', response.context['category_counts'],
            'The Kai category count survived the exclusion. category_counts '
            'must be computed from the same queryset the rows come from.',
        )

    # -- the export -------------------------------------------------------

    def test_the_csv_export_applies_the_same_exclusion(self):
        """
        v3.16.2's lesson, which has now had to be relearned for a predicate as
        well as for a column: **a redaction applied to a page and not to its
        export is not a redaction.** The export link in activity_logs.html
        forwards `user={{ selected_user }}`, so every combination the page
        offers is reachable here as a file.
        """
        body = self._body('export_activity_logs', user=self.submitter.user_id)
        self.assertNotIn(self.case.display_number, body)
        self.assertIn(
            'logged in successfully', body,
            'CONTROL FAILED: the export returned no non-Kai rows either.',
        )

    # -- a reviewer who IS allowed keeps seeing them ----------------------

    def test_a_permissioned_reviewer_still_sees_kai_rows_under_the_filter(self):
        """
        Exclusion is gated on the viewer, not applied blindly. Someone holding
        both identity flags has a legitimate need to answer this question, and
        `exclude_kai_logs` returns the queryset untouched for them.
        """
        reviewer = make_user('avf-rev', 'Reviewer Rita')
        self.committee.members.add(reviewer)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=reviewer,
            can_view_report_list=True, can_view_report_details=True,
            can_view_submitter_identity=True, can_view_accused_identity=True,
        )
        self.client.force_login(reviewer)
        body = self._body('activity_logs', user=self.submitter.user_id)
        self.assertIn(
            self.case.display_number, body,
            'OVER-EXCLUDED: a reviewer holding both identity flags lost Kai '
            'rows they are entitled to see.',
        )


class TheAuthorFilterEnumerationIsMaintainedTests(TestCase):
    """
    The companion to `TheEnumerationIsMaintainedTests`, for the other kind of
    predicate — because that guard greps for *search terms* and would never
    have flagged the `?user=` dropdown.

    Same discipline: no skip list. When it fails, add a row to
    `AuthorValuedFilterTests`.
    """

    def test_every_activity_log_surface_filtering_on_an_author_excludes_kai(self):
        """
        Greps the view layer for querysets filtered on `ActivityLog`'s author
        and asserts each one is wrapped in `exclude_kai_logs`.

        Deliberately syntactic rather than behavioural: the point is to catch
        the NEXT surface at the moment it is written, in the diff that writes
        it, rather than when a review notices it three releases later.
        """
        import pathlib
        import re

        root = pathlib.Path(__file__).resolve().parent
        author_filter = re.compile(
            r'ActivityLog\.objects[^\n]*\.filter\(\s*user\s*=|'
            r'\.filter\(\s*user__user_id\s*='
        )

        offenders = []
        for path in list((root / 'view').rglob('*.py')) + [root / 'admin.py']:
            lines = path.read_text(encoding='utf-8', errors='ignore').splitlines()
            for i, line in enumerate(lines):
                if not author_filter.search(line):
                    continue
                # `exclude_kai_logs(` may wrap the call on this line or open on
                # one of the three above it.
                window = '\n'.join(lines[max(0, i - 3):i + 2])
                if 'exclude_kai_logs' not in window:
                    offenders.append(f'{path.name}:{i + 1}: {line.strip()}')

        self.assertEqual(
            offenders, [],
            'These filter ActivityLog on its author without exclude_kai_logs:\n'
            + '\n'.join(offenders)
            + '\n\nAn author-valued filter is answered by the row\'s presence, '
              'so redaction cannot help — the viewer supplied the identity. '
              'See src/kai_audit.py.',
        )


class TheRedactionCoversEveryRenderedColumnTests(TestCase):
    """
    v3.18.5, and it is the THIRD leg of the predicate-safety idea.

    The two enumeration guards above are both *syntactic*: one greps for search
    terms, the other for author-valued filters. Both are good, and neither
    could see the bug this class exists for, because the bug was not a filter
    at all — it was a **column the template rendered that the redaction never
    touched**.

    `redact_kai_logs` attached `display_actor`, `display_actor_id` and
    `display_description`. `templates/activity_logs.html` rendered those three
    *and* `log.ip_address`, raw, in its own column. So a Kai submission row
    printed `Anonymous` in the User column and the reporter's actual IP one
    cell to the right — and `/activity-logs/?user=<member>`, which v3.18.4 had
    just made Kai-free, returns that member's non-Kai rows **with their IP**,
    which is exactly the lookup table needed to read the other column. An IP is
    not a name; per v3.16.2 it does not have to be. It only has to be a join
    key.

    **So this test asserts on OUTPUT, not on field names.** It renders the page
    and asks whether anything identifying the reporter survives anywhere in the
    body. That is deliberately weaker as an enumeration and stronger as a
    guarantee: it needs no advance knowledge of which columns exist, so the
    next column added to this template is covered on the day it is added
    without anyone remembering to add it here.

    Every test in this class fails against the v3.18.4 tree.
    """

    #: Distinctive enough that a substring hit in the body is a real hit and
    #: not a coincidence in a CSS class or an SVG path.
    SUBMITTER_IP = '203.0.113.47'
    OFFICER_IP = '198.51.100.9'

    def setUp(self):
        self.committee = Committee.objects.create(
            name='Kai', code='KAI', is_kai_committee=True,
        )
        self.submitter = make_user('ipc-sub', f'Zebediah {SUBMITTER_SURNAME}')
        accused = make_user('ipc-acc', 'Accused Ambrose')
        self.case = KaiReport.objects.create(
            title='Rendered Column Case Delta',
            description=f'The {BODY_SECRET} matter.',
            submitted_by=self.submitter,
            targeted_to=accused,
        )

        from src.models import ActivityLog

        # The row `submit_kai_report` writes, WITH the IP its `request=request`
        # would have supplied in production. Post-v3.18.2 the description names
        # nobody, so the IP is the only identity left on the row — which is the
        # whole point.
        self.kai_row = ActivityLog.log_activity(
            action_type='kai_action', user=self.submitter,
            description=f'A member submitted Kai case {self.case.display_number}',
            object_type='KaiReport', object_id=self.case.id,
            object_repr=self.case.display_number,
            ip_address=self.SUBMITTER_IP,
        )
        # The control, and also the attacker's lookup table: a non-Kai row by
        # the same author, from the same address.
        self.other_row = ActivityLog.log_activity(
            action_type='login', user=self.submitter,
            description=f'Zebediah {SUBMITTER_SURNAME} logged in successfully',
            ip_address=self.SUBMITTER_IP,
        )

        self.officer = make_user('ipc-officer', 'Officer Odell')
        self.client = Client()
        self.client.force_login(self.officer)

    def _body(self, url_name, **params):
        response = self.client.get(reverse(url_name), params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    # -- the rendered page ------------------------------------------------

    def test_the_kai_row_does_not_render_the_reporters_ip(self):
        body = self._body('activity_logs', category='kai', date_range='all')
        self.assertNotIn(
            self.SUBMITTER_IP, body,
            'ORACLE: the Kai row rendered the reporter\'s IP address beside a '
            'redacted actor. An IP is a join key, and /activity-logs/?user= '
            'hands the officer the other half of the join on the same page. '
            'Render display_ip, not log.ip_address — see src/kai_audit.py.',
        )

    def test_the_control_row_still_renders_its_ip(self):
        """
        Without this, blanking the column outright would pass the test above
        and destroy a real audit signal. The IP belongs on non-Kai rows.
        """
        body = self._body('activity_logs', date_range='all')
        self.assertIn(
            self.SUBMITTER_IP, body,
            'CONTROL FAILED: no IP renders anywhere, so the assertion above '
            'cannot distinguish a fix from a blank column.',
        )

    def test_a_kai_row_by_a_non_party_keeps_its_ip(self):
        """
        The redaction is `display_ip is blank ⟺ display_actor was replaced`,
        not "blank every Kai row". A chair acting on a case is not a party to
        it; their IP is ordinary audit data and over-redacting it costs real
        fidelity for no confidentiality gain.
        """
        from src.models import ActivityLog

        chair = make_user('ipc-chair', 'Chair Cordelia')
        chair_ip = '192.0.2.77'
        ActivityLog.log_activity(
            action_type='kai_action', user=chair,
            description=f'A reviewer was assigned to Kai case {self.case.display_number}',
            object_type='KaiReport', object_id=self.case.id,
            object_repr=self.case.display_number,
            ip_address=chair_ip,
        )
        body = self._body('activity_logs', category='kai', date_range='all')
        self.assertIn(
            chair_ip, body,
            'OVER-REDACTED: a Kai row authored by someone who is neither the '
            'submitter nor the accused lost its IP. Nothing about that row is '
            'redacted, so the IP should not be either.',
        )

    # -- the export -------------------------------------------------------

    def test_the_csv_export_does_not_carry_the_reporters_ip(self):
        """
        The half that leaves the app. v3.16.2's lesson, for the fourth time and
        on the fourth kind of thing: a redaction applied to a page and not to
        its export is not a redaction.
        """
        body = self._body('export_activity_logs', category='kai', date_range='all')
        self.assertNotIn(self.SUBMITTER_IP, body)

    # -- the search box ---------------------------------------------------

    def test_searching_the_ip_does_not_return_the_kai_row(self):
        """
        Output and input, both halves. Blanking the column while leaving
        `ip_address__icontains` in `open_columns` would be *output redacted,
        input not* — the same oracle this module has now closed four times, and
        the search box placeholder advertises it: "Search description, IP...".
        """
        body = self._body(
            'activity_logs', q=self.SUBMITTER_IP, date_range='all')
        self.assertNotIn(
            self.case.display_number, body,
            'ORACLE: searching an IP returned that member\'s Kai rows. The '
            'officer supplied the identity, so this is the ?user= dropdown '
            'again, reached through the search box. ip_address belongs in '
            'identity_columns.',
        )

    def test_searching_an_ip_still_finds_non_kai_rows(self):
        """
        The control for the fix above: moving `ip_address` into
        `identity_columns` must cost only the Kai rows. An IP search is a
        legitimate and useful officer tool on everything else.
        """
        body = self._body(
            'activity_logs', q=self.SUBMITTER_IP, date_range='all')
        self.assertIn(
            'logged in successfully', body,
            'CONTROL FAILED: the IP search stopped matching non-Kai rows '
            'entirely, so the assertion above would pass on a dead search box.',
        )

    def test_a_full_reviewer_can_still_search_by_ip(self):
        """Gated on the viewer, like every other half of this module."""
        reviewer = make_user('ipc-rev', 'Reviewer Rowan')
        self.committee.members.add(reviewer)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=reviewer,
            can_view_report_list=True, can_view_report_details=True,
            can_view_submitter_identity=True, can_view_accused_identity=True,
        )
        self.client.force_login(reviewer)
        body = self._body(
            'activity_logs', q=self.SUBMITTER_IP, date_range='all')
        self.assertIn(self.case.display_number, body)
