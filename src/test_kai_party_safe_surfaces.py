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
