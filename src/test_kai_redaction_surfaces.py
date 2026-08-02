"""
Every Kai surface that can emit a redacted field, tested against the flag that
governs it.

WHAT WENT WRONG (found 08-01-26 by the nightly review, fixed v3.18.1)
---------------------------------------------------------------------
Two disclosures, both of party identity, both in surfaces no enumeration in
this codebase had ever counted.

**1. The reviewer list's search box was an oracle (🔴).**

v3.18.0 changed the list from *excluding* a case the viewer is the accused on
to *showing it as a redacted row*. The reasoning was right — hiding the row
protects nothing, the accused is notified and it is on their own dashboard, and
excluding it made the stat cards disagree with the rows.

What did not move with the decision was the SEARCH PREDICATE. `_kai_search_q`
builds its `Q` from the viewer's *committee-level* flags, and those say the
viewer may read `description` and `submitted_by__name` — true in general, false
for this one row. So the row was matchable on precisely the three fields the
card refuses to print:

    search "PINEAPPLEGATE"  (a word only in the hidden allegation) -> row returns
    search "Quillfeather"   (the redacted reporter's surname)      -> row returns

The row is trivially identifiable: it carries its KAI-YYYY-NNN number and a
"Recused" badge. That is a clean oracle over the allegation body and over the
identity of whoever filed the case — the one thing CLAUDE.md names as the
actual Kai promise: *the accused never learns who reported them.*

**2. The activity feed printed both parties' names, three times over (🟠).**

`submit_kai_report` writes the `created` entry with `user=request.user` — the
submitter — and all three activity templates printed `{{ entry.user.name }}`.
Three of them, because v3.18.0 added a second copy (the Case Timeline partial)
and the print view was already a third. Separately, three call sites
interpolated the accused's name into `details`.

So a reviewer holding `can_view_report_details` and NEITHER identity flag — a
real configuration, since `KaiMemberPermission` models these as four
independent booleans — opened a case detail page whose header correctly
redacted both names and whose activity feed underneath printed both.

THE PART WORTH REMEMBERING
--------------------------
**When a surface stops EXCLUDING a row and starts REDACTING it, every predicate
that touches that row becomes a disclosure.** Exclusion protects the filters for
free; redaction does not. That is the general form of finding 1, and it is a new
entry in the same family as v3.16.3's "a filter predicate is a join key".

And the count keeps being wrong: v3.16.3 said five surfaces, v3.17.7 found a
sixth, v3.18.0 found a seventh and an eighth, and this module adds the activity
feed as a ninth — in three copies. `test_no_template_renders_raw_activity_fields`
is the version of that enumeration a grep maintains instead of a person.
"""

from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse

from src.models import Committee, ParliamentUser
from src.models.kai import KaiMemberPermission, KaiReport, KaiReportActivity

SUBMITTER_NAME = 'Zebediah Quillfeather'
ACCUSED_NAME = 'Bartholomew Nettlewicke'
BODY_WORD = 'PINEAPPLEGATE'


def make_user(uid, name, member_type='Member', member_status='Active', is_admin=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=name, username=uid,
        member_type=member_type, member_status=member_status, is_admin=is_admin,
    )
    user.set_password('redaction-test-pass-12345!')
    user.save()
    return user


class KaiRedactionTestCase(TestCase):
    """One case, one Kai committee, graded reviewers."""

    def setUp(self):
        self.committee = Committee.objects.create(
            name='Kai', code='KAI', is_kai_committee=True,
        )
        self.submitter = make_user('red-sub', SUBMITTER_NAME)
        self.accused = make_user('red-acc', ACCUSED_NAME)
        self.report = KaiReport.objects.create(
            title='Distinctive Case Title Alpha',
            description=f'He did the {BODY_WORD} thing at the house.',
            submitted_by=self.submitter,
            targeted_to=self.accused,
        )
        # A decoy the viewer is not party to, so "no rows" means something.
        other = make_user('red-other', 'Other Oliver')
        self.decoy = KaiReport.objects.create(
            title='Unrelated Case Beta', description='nothing to see',
            submitted_by=other, targeted_to=other,
        )

    def _reviewer(self, uid, **perms):
        """A Kai member holding can_view_report_list plus whatever is named."""
        user = make_user(uid, f'Reviewer {uid}')
        self.committee.members.add(user)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=user,
            can_view_report_list=True, **perms,
        )
        return user

    def _grant_full_but_accused(self, uid='red-party'):
        """
        The population that matters for the oracle: a Kai reviewer holding
        every read permission who is ALSO the accused on `self.report`.
        """
        self.committee.members.add(self.accused)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.accused,
            can_view_report_list=True, can_view_report_details=True,
            can_view_submitter_identity=True, can_view_accused_identity=True,
        )
        return self.accused

    def _list(self, user, **params):
        client = Client()
        client.force_login(user)
        response = client.get(reverse('view_kai_reports'), params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def _detail(self, user, report=None):
        client = Client()
        client.force_login(user)
        response = client.get(
            reverse('manage_kai_report', args=[(report or self.report).id]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


# ---------------------------------------------------------------------------
# 1. The search oracle
# ---------------------------------------------------------------------------


class TheSearchBoxIsNotAnOracleTests(KaiRedactionTestCase):
    """
    The 🔴. Every assertion here FAILS against the pre-v3.18.1 helper —
    verified by restoring the old `_kai_search_q` signature and re-running,
    which is the only way to know a regression test tests anything.
    """

    def test_control_the_row_is_shown_and_redacted(self):
        """Not a leak — the whole point of the v3.18.0 design. Pinned so a
        future 'fix' that goes back to excluding the row fails loudly."""
        html = self._list(self._grant_full_but_accused(), search='Distinctive')
        self.assertIn('Distinctive Case Title Alpha', html)
        self.assertNotIn(SUBMITTER_NAME, html)
        self.assertNotIn(BODY_WORD, html)

    def test_control_a_nonsense_term_returns_nothing(self):
        html = self._list(self._grant_full_but_accused(), search='zzzz-no-such-term')
        self.assertNotIn('Distinctive Case Title Alpha', html)

    def test_the_accused_cannot_search_the_hidden_allegation_body(self):
        html = self._list(self._grant_full_but_accused(), search=BODY_WORD)
        self.assertNotIn(
            'Distinctive Case Title Alpha', html,
            'ORACLE: a word appearing only in the redacted allegation body '
            'returned the accused viewer\'s own case row.',
        )

    def test_the_accused_cannot_search_the_hidden_submitter_name(self):
        html = self._list(self._grant_full_but_accused(), search='Quillfeather')
        self.assertNotIn(
            'Distinctive Case Title Alpha', html,
            'ORACLE: searching the redacted reporter\'s name returned the '
            'accused viewer\'s own case row, identifying who reported them.',
        )

    def test_the_accused_can_still_search_other_peoples_cases_normally(self):
        """The narrowing is per-row, not a blanket downgrade of the search."""
        html = self._list(self._grant_full_but_accused(), search='nothing to see')
        self.assertIn('Unrelated Case Beta', html)

    def test_the_accused_can_still_find_their_own_case_by_title(self):
        """Title and tags stay searchable — they are what the card renders."""
        html = self._list(
            self._grant_full_but_accused(), search='Distinctive Case Title Alpha')
        self.assertIn('Distinctive Case Title Alpha', html)

    def test_an_uninvolved_reviewer_searches_everything_as_before(self):
        """No regression for the ordinary case: full permissions, not a party."""
        reviewer = self._reviewer(
            'red-full', can_view_report_details=True,
            can_view_submitter_identity=True, can_view_accused_identity=True)
        self.assertIn('Distinctive Case Title Alpha',
                      self._list(reviewer, search=BODY_WORD))
        self.assertIn('Distinctive Case Title Alpha',
                      self._list(reviewer, search='Quillfeather'))

    def test_a_list_only_reviewer_still_cannot_search_gated_fields(self):
        """The v3.16.3 guarantee, re-pinned — the new argument did not undo it."""
        reviewer = self._reviewer('red-list-only')
        self.assertNotIn('Distinctive Case Title Alpha',
                         self._list(reviewer, search=BODY_WORD))
        self.assertNotIn('Distinctive Case Title Alpha',
                         self._list(reviewer, search='Quillfeather'))


# ---------------------------------------------------------------------------
# 2. The activity feed
# ---------------------------------------------------------------------------


class TheActivityFeedRedactsIdentitiesTests(KaiRedactionTestCase):
    """
    The 🟠. `can_view_report_details` is NOT a superset of the identity flags,
    and the feed treated it as one.
    """

    def setUp(self):
        super().setUp()
        KaiReportActivity.objects.filter(report=self.report).delete()
        # Exactly what submit_kai_report writes — author IS the submitter.
        KaiReportActivity.objects.create(
            report=self.report, user=self.submitter, action='created',
            details='Report created with category: Other',
        )
        # A legacy row of the shape the three fixed call sites used to write.
        KaiReportActivity.objects.create(
            report=self.report, user=None, action='status_changed',
            details=f'Accused ({ACCUSED_NAME}) notified of the case',
        )

    def test_details_only_reviewer_does_not_see_the_submitter(self):
        html = self._detail(self._reviewer(
            'red-d1', can_view_report_details=True, can_view_accused_identity=True))
        self.assertNotIn(SUBMITTER_NAME, html)
        self.assertIn('Anonymous', html)

    def test_details_only_reviewer_does_not_see_the_accused(self):
        html = self._detail(self._reviewer(
            'red-d2', can_view_report_details=True, can_view_submitter_identity=True))
        self.assertNotIn(ACCUSED_NAME, html)

    def test_legacy_details_strings_are_scrubbed_not_just_new_ones(self):
        """The three write sites no longer emit names, but rows written before
        v3.18.1 still contain them. Redaction happens at render for that
        reason — a fix that only changed the writers would have left every
        existing case leaking."""
        html = self._detail(self._reviewer(
            'red-d3', can_view_report_details=True))
        self.assertNotIn(ACCUSED_NAME, html)
        self.assertIn('Redacted', html)

    def test_a_fully_permissioned_reviewer_still_sees_both_names(self):
        """Redaction, not removal — the feed must stay useful to the committee."""
        html = self._detail(self._reviewer(
            'red-d4', can_view_report_details=True,
            can_view_submitter_identity=True, can_view_accused_identity=True))
        self.assertIn(SUBMITTER_NAME, html)
        self.assertIn(ACCUSED_NAME, html)

    def test_the_print_view_redacts_too(self):
        """The third copy, and the worst one: it renders the ENTIRE log rather
        than the last 20, and its output is a document that leaves the app."""
        reviewer = self._reviewer('red-d5', can_view_report_details=True)
        client = Client()
        client.force_login(reviewer)
        response = client.get(reverse('print_kai_report', args=[self.report.id]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn(SUBMITTER_NAME, html)
        self.assertNotIn(ACCUSED_NAME, html)

    def test_no_template_renders_raw_activity_fields(self):
        """
        The enumeration, maintained by grep rather than by memory.

        Three templates render the activity feed and all three were wrong. Any
        fourth copy must go through `display_actor` / `display_details` too, and
        this fails the moment one does not.
        """
        # Scoped to templates/kai/ deliberately. Other modules keep their own
        # activity logs (service hours, for one) and those carry no
        # confidentiality promise — this is a Kai rule, not a global one.
        offenders = []
        for path in (Path(settings.BASE_DIR) / 'templates' / 'kai').rglob('*.html'):
            text = path.read_text(encoding='utf-8', errors='ignore')
            for raw in ('activity.user.name', 'activity.details',
                        'entry.user.name', 'entry.details'):
                if '{{ ' + raw + ' }}' in text or '{{ ' + raw + '|' in text:
                    offenders.append(f'{path.relative_to(settings.BASE_DIR)}: {raw}')
        self.assertEqual(
            offenders, [],
            'These Kai templates render un-redacted activity fields. Use '
            'display_actor / display_details — see _redact_activity_log.',
        )

    def test_the_print_view_gates_both_party_names_in_its_header_too(self):
        """
        Found 08-01-26 while fixing the feed: the print view's "Submitted By"
        and "Directed To" fields were ungated outright — it checked
        `can_view_report_details` at the door and printed both names, in a
        document designed to be exported to PDF. The template had never been
        passed `kai_access` at all.
        """
        reviewer = self._reviewer('red-d6', can_view_report_details=True)
        client = Client()
        client.force_login(reviewer)
        html = client.get(
            reverse('print_kai_report', args=[self.report.id])).content.decode()
        self.assertIn('Anonymous', html)
        self.assertIn('Redacted', html)
        self.assertNotIn(SUBMITTER_NAME, html)
        self.assertNotIn(ACCUSED_NAME, html)


# ---------------------------------------------------------------------------
# 3. The exec-board bypass
# ---------------------------------------------------------------------------


class ExecBoardDoesNotGrantKaiAccessTests(KaiRedactionTestCase):
    """
    `Committee.is_chair()` returns True for any member of an `is_exec_board`
    committee. v3.18.0 rewrote both committee-page Kai previews specifically to
    escape that — but rewrote them to call `_get_kai_access`, which had the same
    bypass one level down. The previews were routed THROUGH the hole rather than
    around it. Found 08-01-26.
    """

    def test_a_plain_member_of_an_exec_board_kai_gets_nothing(self):
        from src.view.kai_reports import _get_kai_access

        member = make_user('red-exec', 'Exec Member')
        self.committee.members.add(member)
        self.committee.is_exec_board = True
        self.committee.save(update_fields=['is_exec_board'])

        access = _get_kai_access(member, self.committee)
        self.assertFalse(
            any(access[f] for f in (
                'can_view_report_list', 'can_view_report_details',
                'can_view_submitter_identity', 'can_view_accused_identity',
                'can_edit_open_cases', 'can_add_activity', 'can_close_cases')),
            'Flagging Kai as exec board granted full judicial access to a '
            'member holding no KaiMemberPermission.',
        )

    def test_an_exec_board_member_cannot_appoint_standins(self):
        from src.view.kai_reports import _can_appoint_standins

        member = make_user('red-exec2', 'Exec Member Two')
        self.committee.members.add(member)
        self.committee.is_exec_board = True
        self.committee.save(update_fields=['is_exec_board'])
        self.assertFalse(_can_appoint_standins(member, self.committee))

    def test_a_real_chair_still_gets_full_access(self):
        from src.view.kai_reports import _can_appoint_standins, _get_kai_access

        chair = make_user('red-chair', 'Real Chair')
        self.committee.chairs.add(chair)
        self.assertTrue(_get_kai_access(chair, self.committee)['is_full_access'])
        self.assertTrue(_can_appoint_standins(chair, self.committee))

    def test_a_site_admin_still_gets_full_access(self):
        from src.view.kai_reports import _get_kai_access

        admin = make_user('red-admin', 'Site Admin', is_admin=True)
        self.assertTrue(_get_kai_access(admin, self.committee)['is_full_access'])


# ---------------------------------------------------------------------------
# 4. Case numbers
# ---------------------------------------------------------------------------


class CaseNumbersAreUniqueTests(KaiRedactionTestCase):

    def test_two_reports_get_different_numbers(self):
        second = KaiReport.objects.create(
            title='Second', description='x',
            submitted_by=self.submitter, targeted_to=self.accused,
        )
        self.assertNotEqual(self.report.case_number, second.case_number)
        self.assertTrue(second.case_number.startswith('KAI-'))

    def test_a_duplicate_number_is_refused_by_the_database(self):
        from django.db import IntegrityError, transaction

        clash = KaiReport(
            title='Clash', description='x',
            submitted_by=self.submitter, targeted_to=self.accused,
            case_number=self.report.case_number,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                # Bypass save()'s retry — we are testing the constraint itself.
                super(KaiReport, clash).save()

    def test_blank_numbers_do_not_collide(self):
        """The constraint is partial on purpose: bulk_created fixtures leave
        many blanks and that must stay legal."""
        KaiReport.objects.bulk_create([
            KaiReport(title='A', description='x', submitted_by=self.submitter),
            KaiReport(title='B', description='x', submitted_by=self.submitter),
        ])
        self.assertEqual(KaiReport.objects.filter(case_number='').count(), 2)

    def test_update_fields_save_does_not_discard_a_fresh_number(self):
        """`save(update_fields=[...])` used to compute a number, set it on the
        instance and then not write it — a silent loss plus a wasted query."""
        (bare,) = KaiReport.objects.bulk_create([
            KaiReport(title='Bare', description='x', submitted_by=self.submitter),
        ])
        bare.assigned_to = self.submitter
        bare.save(update_fields=['assigned_to'])
        bare.refresh_from_db()
        self.assertTrue(bare.case_number, 'case_number was not persisted')
