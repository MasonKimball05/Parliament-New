"""
Recusal — a Kai member cannot act on a case they are a party to.

WHAT WENT WRONG (found 07-31-26, fixed v3.18.0)
------------------------------------------------
The chapter bylaws (§ vi, seeded in `src/management/data/cnb_data.py`):

    "Should members of the Kai Committee be recused from their duties, the head
     of Kai shall appoint suitable replacement(s) for the position. However,
     should the offenses be separate from each other, then their trials remain
     separated and only the accused must temporarily recuse their seat for
     their trial."

The app implemented none of it. `_get_kai_access(user, committee)` takes a user
and a committee and **never sees the report**, so a Kai member who was the
accused in an open case could read the allegation against themselves, see who
reported them, and — holding `can_close_cases` — close it.

THE PART WORTH REMEMBERING
--------------------------
**Recusal is computed from the case, not from a `KaiRecusal` row.** Enforcement
that depended on a record could be defeated by failing to create one. The row
exists to record who filled the vacated seat, which is the other half of § vi;
`KaiReport.is_party()` is the rule.

And it is EIGHT surfaces, not the five the old comment claimed — see the
enumeration in `templates/kai/view_reports.html`. Every one is tested here,
because a control applied to seven of eight surfaces is not a control.
"""

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Committee, KaiMemberPermission, KaiRecusal, KaiReport, ParliamentUser,
)

ALLEGATION = 'CONFIDENTIAL-ALLEGATION-BODY-XYZZY'
REPORTER_NAME = 'Reporter Rowan'


def make_user(uid, name=None, member_type='Member', status='Active', is_admin=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=name or f'User {uid}', username=uid,
        member_type=member_type, member_status=status, is_admin=is_admin,
    )
    user.set_password('recusal-test-pass-12345!')
    user.save()
    return user


class RecusalTestCase(TestCase):
    """A Kai committee where the accused is himself a full-access reviewer."""

    def setUp(self):
        self.committee = Committee.objects.create(
            name='Kai', code='KAI', is_kai_committee=True,
        )
        self.reporter = make_user('rec-reporter', REPORTER_NAME)
        # The accused holds EVERY Kai permission — the point of the test is that
        # permissions are not what stops him.
        self.accused = make_user('rec-accused', 'Accused Avery')
        self.committee.members.add(self.accused, self.reporter)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.accused,
            can_view_report_list=True, can_view_report_details=True,
            can_view_submitter_identity=True, can_view_accused_identity=True,
            can_edit_open_cases=True, can_add_activity=True, can_close_cases=True,
        )
        self.report = KaiReport.objects.create(
            title='Case about Avery', description=ALLEGATION,
            submitted_by=self.reporter, targeted_to=self.accused,
        )
        # An unrelated case, to prove recusal is per-case and not per-user.
        self.other = KaiReport.objects.create(
            title='Unrelated case', description='OTHER-BODY',
            submitted_by=self.reporter,
        )

    def _client(self, user):
        client = Client()
        client.force_login(user)
        return client


class TheAccusedIsRecusedEverywhereTests(RecusalTestCase):
    """All eight surfaces. Each assertion fails against the pre-v3.18.0 code."""

    def test_1_the_reviewer_list_SHOWS_the_case_but_withholds_its_content(self):
        """
        ⚠️ CORRECTED 07-31-26. This asserted the row was OMITTED. That was the
        wrong contract and it broke the page — see the section at the bottom of
        this module.

        Hiding the row protects nothing: the accused is notified of the case and
        sees it on their own dashboard. What must not appear is the allegation
        body and the submitter's identity, and they must not be able to act.
        """
        response = self._client(self.accused).get(reverse('view_kai_reports'))
        self.assertEqual(response.status_code, 200)
        ids = [r.id for r in response.context['reports']]
        self.assertIn(self.report.id, ids, 'the row must be visible')
        self.assertIn(self.other.id, ids)

        body = response.content.decode()
        start = body.index(self.report.title)
        row = body[start:start + 2500]
        self.assertNotIn(ALLEGATION, row, 'the allegation body must be withheld')
        self.assertNotIn(REPORTER_NAME, row, "the submitter's identity must be withheld")

    def test_1b_the_counts_agree_with_the_rows(self):
        """
        The property whose breakage surfaced the bug. A "Total 3" card above a
        one-row list is both wrong and a disclosure; so is hiding both.
        """
        response = self._client(self.accused).get(reverse('view_kai_reports'))
        self.assertEqual(
            response.context['counts']['all'],
            len(response.context['reports']),
        )
        self.assertEqual(response.context['counts']['all'], 2)

    def test_2_the_case_detail_refuses_him(self):
        response = self._client(self.accused).get(
            reverse('manage_kai_report', args=[self.report.id]))
        self.assertEqual(response.status_code, 302)

    def test_3_the_print_view_refuses_him(self):
        """The print view is a separate route and was a separate way in."""
        response = self._client(self.accused).get(
            reverse('print_kai_report', args=[self.report.id]))
        self.assertEqual(response.status_code, 302)

    def test_4_the_csv_export_omits_the_case(self):
        response = self._client(self.accused).get(reverse('export_kai_reports_csv'))
        body = response.content.decode()
        self.assertNotIn(ALLEGATION, body)
        self.assertNotIn(self.report.title, body)
        # The unrelated case legitimately carries the reporter's name, so
        # asserting on REPORTER_NAME here would be testing the wrong thing —
        # the first draft of this test did exactly that and failed.
        self.assertIn(self.other.title, body)

    def test_5_global_search_omits_the_case(self):
        response = self._client(self.accused).get(reverse('global_search'), {'q': 'Avery'})
        found = [r.id for r in response.context.get('results', {}).get('kai_reports', [])]
        self.assertNotIn(self.report.id, found)

    def test_6_the_bulk_export_omits_the_case(self):
        response = self._client(self.accused).post(
            reverse('bulk_actions_kai_reports'),
            {'report_ids': [str(self.report.id)], 'bulk_action': 'export_csv'})
        body = response.content.decode() if response.status_code == 200 else ''
        self.assertNotIn(ALLEGATION, body)

    def test_6b_a_bulk_write_action_cannot_touch_his_own_case(self):
        self._client(self.accused).post(
            reverse('bulk_actions_kai_reports'),
            {'report_ids': [str(self.report.id)], 'bulk_action': 'archive'})
        self.report.refresh_from_db()
        self.assertNotEqual(
            self.report.status, 'archived',
            'the accused archived the case against himself',
        )

    def test_7_the_committee_home_preview_omits_the_case(self):
        self.committee.chairs.add(self.accused)  # chairs get full access
        response = self._client(self.accused).get(
            reverse('committee_home', args=[self.committee.code]))
        previews = [r.id for r in response.context.get('kai_reports', [])]
        self.assertNotIn(self.report.id, previews)

    def test_8_the_archived_committee_detail_view_is_still_unrouted(self):
        """
        `src/view/committee/committee_detail.py` renders the same ungated Kai
        preview as `committee_home`, and it was hardened alongside it — but the
        view is **archived**: `committee_detail` resolves to a RedirectView onto
        `committee_home` (urls.py:597, comment at :45). So it is seven LIVE
        surfaces, not eight.

        Asserted rather than assumed, because "it's dead code" is exactly the
        kind of claim that quietly stops being true. If someone re-routes it,
        this fails and they are pointed at the gating requirement.
        """
        response = self._client(self.accused).get(
            reverse('committee_detail', args=[self.committee.code]))
        self.assertEqual(
            response.status_code, 301,
            'committee_detail is no longer a redirect — if the archived view has '
            'been revived, its Kai preview must gate on kai_access and exclude '
            'recused cases (it already does; add a real test here).',
        )


class TheSubmitterKeepsSightButCannotDecideTests(RecusalTestCase):
    """
    ⚠️ CORRECTED 07-31-26, same day it was written.

    The first cut treated the submitter exactly like the accused: every
    permission withdrawn, case hidden from the list, counts and exports. Two
    things were wrong with that.

    **The bylaws say the opposite.** § vi: "…*only the accused* must temporarily
    recuse their seat for their trial." Submitter recusal was an inference.

    **It broke immediately in real use.** Mason filed three test reports as
    himself and the Kai list rendered an empty queue with a count of 0. In a
    chapter this size the head of Kai is often the person who files.

    What survives is the narrow part: nobody adjudicates a complaint they filed.
    The submitter keeps every read permission and loses `can_edit_open_cases`
    and `can_close_cases` — on that case only.
    """

    def setUp(self):
        super().setUp()
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.reporter,
            can_view_report_list=True, can_view_report_details=True,
            can_edit_open_cases=True, can_close_cases=True,
        )

    def test_the_submitter_can_open_a_case_he_filed(self):
        response = self._client(self.reporter).get(
            reverse('manage_kai_report', args=[self.report.id]))
        self.assertEqual(response.status_code, 200)

    def test_the_submitter_sees_his_own_cases_in_the_list(self):
        """The reported bug. Both fixture cases were filed by him."""
        response = self._client(self.reporter).get(reverse('view_kai_reports'))
        ids = [r.id for r in response.context['reports']]
        self.assertIn(self.report.id, ids)
        self.assertIn(self.other.id, ids)

    def test_the_counts_agree_with_the_list(self):
        response = self._client(self.reporter).get(reverse('view_kai_reports'))
        self.assertEqual(
            response.context['counts']['all'],
            len(response.context['reports']),
        )

    def test_the_submitter_cannot_edit_the_case_he_filed(self):
        response = self._client(self.reporter).get(
            reverse('manage_kai_report', args=[self.report.id]))
        self.assertFalse(response.context['kai_access']['can_edit_open_cases'])
        self.assertFalse(response.context['kai_access']['can_close_cases'])

    def test_the_submitter_keeps_full_powers_on_a_case_he_did_not_file(self):
        """The narrowing is per-case, not per-user."""
        outsider = make_user('rec-outsider-sub')
        elsewhere = KaiReport.objects.create(
            title='Not his', description='x', submitted_by=outsider)
        response = self._client(self.reporter).get(
            reverse('manage_kai_report', args=[elsewhere.id]))
        self.assertTrue(response.context['kai_access']['can_edit_open_cases'])

    def test_the_submitter_cannot_archive_the_case_he_filed(self):
        self._client(self.reporter).post(
            reverse('bulk_actions_kai_reports'),
            {'report_ids': [str(self.report.id)], 'bulk_action': 'archive'})
        self.report.refresh_from_db()
        self.assertNotEqual(self.report.status, 'archived')


class RecusalDoesNotDependOnARecordTests(RecusalTestCase):
    """
    The property that makes this safe. If enforcement read `KaiRecusal`, then
    forgetting to create a row — or deleting one — would restore access.
    """

    def test_no_recusal_row_exists_and_access_is_still_refused(self):
        self.assertFalse(KaiRecusal.objects.filter(report=self.report).exists())
        response = self._client(self.accused).get(
            reverse('manage_kai_report', args=[self.report.id]))
        self.assertEqual(response.status_code, 302)

    def test_deleting_the_row_does_not_restore_access(self):
        KaiRecusal.objects.create(
            report=self.report, user=self.accused, reason='accused')
        KaiRecusal.objects.all().delete()
        response = self._client(self.accused).get(
            reverse('manage_kai_report', args=[self.report.id]))
        self.assertEqual(response.status_code, 302)


class StandInAppointmentTests(RecusalTestCase):
    """Bylaws §§ vi–ix — filling the vacated seat."""

    def setUp(self):
        super().setUp()
        self.chair = make_user('rec-chair', 'Chair Casey', member_type='Chair')
        self.committee.chairs.add(self.chair)
        self.standin = make_user('rec-standin', 'Standin Sam')
        self.advisor = make_user('rec-advisor', 'Advisor Ash', member_type='Advisor')
        self.pledge = make_user('rec-pledge', 'Pledge Pat', member_type='Pledge')
        self.alumnus = make_user('rec-alum', 'Alum Al', status='Alumni')
        self.recusal = KaiRecusal.objects.create(
            report=self.report, user=self.accused, reason='accused')

    # -- eligibility ---------------------------------------------------------

    def test_active_members_and_advisors_are_eligible(self):
        eligible = set(KaiRecusal.eligible_standins(self.report).values_list('pk', flat=True))
        self.assertIn(self.standin.pk, eligible)
        self.assertIn(self.advisor.pk, eligible)

    def test_pledges_are_not_eligible(self):
        eligible = KaiRecusal.eligible_standins(self.report).values_list('pk', flat=True)
        self.assertNotIn(self.pledge.pk, eligible)

    def test_non_active_members_are_not_eligible(self):
        eligible = KaiRecusal.eligible_standins(self.report).values_list('pk', flat=True)
        self.assertNotIn(self.alumnus.pk, eligible)

    def test_the_parties_are_not_eligible(self):
        """
        Appointing the accused would be immediately undone by `_case_access`,
        leaving a seat that looks filled and is empty. Refuse it up front.
        """
        eligible = KaiRecusal.eligible_standins(self.report).values_list('pk', flat=True)
        self.assertNotIn(self.accused.pk, eligible)
        self.assertNotIn(self.reporter.pk, eligible)

    # -- appointment ---------------------------------------------------------

    def _appoint(self, actor, replacement):
        return self._client(actor).post(
            reverse('appoint_kai_standin', args=[self.report.id]),
            {'recusal_id': self.recusal.id, 'replacement': replacement.pk})

    def test_the_chair_may_appoint(self):
        self._appoint(self.chair, self.standin)
        self.recusal.refresh_from_db()
        self.assertEqual(self.recusal.replacement_id, self.standin.pk)

    def test_an_ordinary_member_may_not_appoint(self):
        outsider = make_user('rec-nobody')
        self._appoint(outsider, self.standin)
        self.recusal.refresh_from_db()
        self.assertIsNone(self.recusal.replacement_id)

    def test_a_recused_member_may_not_appoint_his_own_replacement(self):
        self._appoint(self.accused, self.standin)
        self.recusal.refresh_from_db()
        self.assertIsNone(self.recusal.replacement_id)

    def test_an_ineligible_replacement_is_refused_server_side(self):
        """The <select> is built from `eligible_standins`, but a POST is not a form."""
        self._appoint(self.chair, self.pledge)
        self.recusal.refresh_from_db()
        self.assertIsNone(self.recusal.replacement_id)

    # -- the grant -----------------------------------------------------------

    def test_the_standin_can_open_the_case(self):
        self._appoint(self.chair, self.standin)
        response = self._client(self.standin).get(
            reverse('manage_kai_report', args=[self.report.id]))
        self.assertEqual(response.status_code, 200)

    def test_the_standin_sees_only_that_case(self):
        self._appoint(self.chair, self.standin)
        response = self._client(self.standin).get(reverse('view_kai_reports'))
        ids = [r.id for r in response.context['reports']]
        self.assertEqual(ids, [self.report.id])

    def test_the_grant_is_a_snapshot_and_does_not_drift(self):
        """
        If the recused member's own permissions are later narrowed, the
        stand-in's authority on a case that may already be decided must not
        move with it.
        """
        self._appoint(self.chair, self.standin)
        KaiMemberPermission.objects.filter(user=self.accused).update(
            can_view_report_details=False)
        self.recusal.refresh_from_db()
        self.assertTrue(self.recusal.granted_permissions['can_view_report_details'])

    def test_withdrawing_the_standin_removes_access(self):
        self._appoint(self.chair, self.standin)
        self._client(self.chair).post(
            reverse('remove_kai_standin', args=[self.report.id]),
            {'recusal_id': self.recusal.id})
        response = self._client(self.standin).get(
            reverse('manage_kai_report', args=[self.report.id]))
        self.assertEqual(response.status_code, 302)

    def test_a_standin_who_is_also_a_party_is_still_recused(self):
        """Recusal is checked before the stand-in grant, and wins."""
        KaiRecusal.objects.create(
            report=self.report, user=self.reporter, reason='submitter',
            replacement=self.accused,
            granted_permissions={'can_view_report_details': True},
        )
        response = self._client(self.accused).get(
            reverse('manage_kai_report', args=[self.report.id]))
        self.assertEqual(response.status_code, 302)


# ═══════════════════════════════════════════════════════════════════════════
#  The reported bug — "the Kai list shows 0 of 3" (07-31-26)
# ═══════════════════════════════════════════════════════════════════════════
#
# Mason filed three test cases and the queue rendered empty, then one of three.
# Twice, the fix for the first version made a second version.
#
#   v1: excluded every case the viewer was a PARTY to → 0 of 3.
#       Wrong because the bylaws recuse "only the accused" (§ vi); submitter
#       recusal was an inference.
#   v2: excluded only cases NAMING the viewer → 1 of 3.
#       Still wrong, and this was the real error: **excluding rows was the wrong
#       tool entirely.** Hiding the row protects nothing — the accused already
#       knows the case exists, is notified of it, and sees it on their own
#       dashboard under "Reports Where I'm Named".
#   v3: show every row; withhold the CONTENT of the ones you are a party to,
#       refuse the detail page, and exclude from exports and bulk writes.
#
# The counts then agree with the rows, which is the property that broke and the
# thing that made it visible. **A control that hides a row it does not need to
# hide is a bug waiting to be reported as one.**

BODY = 'SECRET-ALLEGATION-TEXT'


class MasonsThreeReports(TestCase):
    """
    The exact reported situation: three cases, mixed party roles, one operator.

    ⚠️ v3.18.2 — THIS SETUP CHANGED, AND THE CHANGE IS THE OPERATIONAL WARNING
    FOR THE WHOLE RELEASE.

    It used to give `self.me` nothing but `is_admin=True`, and that was enough
    to reach Kai, because `_get_kai_access` short-circuited on it. v3.18.2
    removes that shortcut (an admin is an operational role, not a judicial one
    — the standing v3.16.2 rule reaching the app layer), so this fixture began
    failing with a permission redirect rather than a list.

    **That failure is the real deploy risk, not a test artefact.** Anyone who
    was reaching Kai through `is_admin` loses access the moment this ships,
    silently. `manage.py check` now emits `src.W001` when the Kai committee has
    no chairs and no permission rows, precisely so that lockout announces
    itself; `manage.py kai_break_glass` is the way back in if it happens.

    The fixture now makes `me` an actual Kai chair, which is what the reported
    situation really was.
    """

    def setUp(self):
        kai = Committee.objects.create(name='Kai', code='KAI', is_kai_committee=True)
        self.me = ParliamentUser.objects.create(
            user_id='f-me', name='Mason', username='f-me',
            member_type='Officer', member_status='Active', is_admin=True)
        self.me.set_password('x-pass-12345!'); self.me.save()
        kai.chairs.add(self.me)
        self.other = ParliamentUser.objects.create(
            user_id='f-other', name='Other Person', username='f-other',
            member_type='Member', member_status='Active')
        self.other.set_password('x-pass-12345!'); self.other.save()

        self.a = KaiReport.objects.create(title='A: I filed it', description=BODY,
                                          submitted_by=self.me)
        self.b = KaiReport.objects.create(title='B: names me', description=BODY,
                                          submitted_by=self.other, targeted_to=self.me)
        self.c = KaiReport.objects.create(title='C: unrelated', description=BODY,
                                          submitted_by=self.other)

    def test_all_three_show_and_the_count_agrees(self):
        cl = Client(); cl.force_login(self.me)
        r = cl.get(reverse('view_kai_reports'))
        n, total = len(r.context['reports']), r.context['counts']['all']
        print(f'\n  list={n}  total-card={total}   (both must be 3)')
        self.assertEqual(n, 3)
        self.assertEqual(total, 3)

    def test_the_case_naming_me_is_redacted_on_the_row(self):
        """
        Assert on the ROW, not the page. `Other Person` legitimately appears as
        the submitter of case C, so a whole-page assertNotIn is testing the
        wrong thing — the codebase already records this trap for /kai/reports/
        (v3.17.0: "whole-page assertNotContains is unreliable here"). I walked
        into it anyway; slicing to the row is the fix.
        """
        cl = Client(); cl.force_login(self.me)
        body = cl.get(reverse('view_kai_reports')).content.decode()
        self.assertIn('B: names me', body)                  # row visible
        self.assertIn('You are named in this case', body)   # and marked

        start = body.index('B: names me')
        row = body[start:start + 2500]                      # just this card
        self.assertNotIn('Other Person', row)               # submitter withheld
        self.assertNotIn(BODY, row)                         # allegation withheld
        print('  row visible, submitter + body withheld  OK')

    def test_i_still_cannot_open_the_case_naming_me(self):
        cl = Client(); cl.force_login(self.me)
        self.assertEqual(
            cl.get(reverse('manage_kai_report', args=[self.b.id])).status_code, 302)
        print('  detail page still refuses  OK')

    def test_i_can_open_the_two_that_do_not_name_me(self):
        cl = Client(); cl.force_login(self.me)
        for rep in (self.a, self.c):
            self.assertEqual(
                cl.get(reverse('manage_kai_report', args=[rep.id])).status_code, 200)
        print('  other two open fine  OK')

    def test_i_cannot_archive_the_case_naming_me(self):
        cl = Client(); cl.force_login(self.me)
        cl.post(reverse('bulk_actions_kai_reports'),
                {'report_ids': [str(self.b.id)], 'bulk_action': 'archive'})
        self.b.refresh_from_db()
        self.assertNotEqual(self.b.status, 'archived')
        print('  cannot archive the case naming me  OK')


# ═══════════════════════════════════════════════════════════════════════════
#  Manual recusal, widened eligibility, and the stand-in's way in (v3.18.0)
# ═══════════════════════════════════════════════════════════════════════════
#
# Three gaps Mason found by using the thing:
#
#   1. There was **no button to recuse anyone**. `_sync_recusals` records the
#      seat the CASE vacates — the accused — and nothing else. It cannot know a
#      member is travelling, ill, or standing back for a reason the data does
#      not hold. Bylaws § vi covers both; only one is computable.
#   2. Eligibility said `member_status='Active'` AND a non-pledge type, which
#      **silently dropped advisors** — an advisor is commonly carried at Alumni
#      status. The codebase already had the right predicate in two places.
#   3. A stand-in with no committee grant had **no way to reach their case**.
#      The appointment was real and invisible; they needed to be handed a URL.


class ManualRecusalTests(RecusalTestCase):

    def setUp(self):
        super().setUp()
        self.chair = make_user('mr-chair', 'Chair Casey', member_type='Chair')
        self.committee.chairs.add(self.chair)
        self.member = make_user('mr-member', 'Ordinary Olly')
        self.committee.members.add(self.member)

    def _recuse(self, actor, member, reason='unavailable'):
        return self._client(actor).post(
            reverse('recuse_kai_member', args=[self.other.id]),
            {'member': member.pk, 'reason': reason, 'notes': 'Away this month'})

    def test_the_chair_can_recuse_an_unavailable_member(self):
        self._recuse(self.chair, self.member)
        row = KaiRecusal.objects.get(report=self.other, user=self.member)
        self.assertEqual(row.reason, 'unavailable')
        self.assertEqual(row.recorded_by_id, self.chair.pk)

    def test_an_ordinary_member_cannot_recuse_anyone(self):
        self._recuse(self.member, self.chair)
        self.assertFalse(KaiRecusal.objects.filter(report=self.other).exists())

    def test_a_derived_reason_cannot_be_asserted_by_hand(self):
        """
        `accused` and `submitter` come from the case. Letting someone post one
        would record a relationship the data contradicts, while `_case_access`
        — which reads the case, not the row — carried on ignoring it. The record
        and the enforcement would disagree, which is worse than either.
        """
        self._recuse(self.chair, self.member, reason='accused')
        row = KaiRecusal.objects.get(report=self.other, user=self.member)
        self.assertEqual(row.reason, 'unavailable')

    def test_recusing_a_party_is_refused_as_redundant(self):
        self._client(self.chair).post(
            reverse('recuse_kai_member', args=[self.report.id]),
            {'member': self.accused.pk, 'reason': 'unavailable'})
        self.assertFalse(
            KaiRecusal.objects.filter(
                report=self.report, user=self.accused, reason='unavailable').exists())

    def test_a_manual_recusal_can_be_ended(self):
        self._recuse(self.chair, self.member)
        row = KaiRecusal.objects.get(report=self.other, user=self.member)
        self._client(self.chair).post(
            reverse('end_kai_recusal', args=[self.other.id]), {'recusal_id': row.id})
        self.assertFalse(KaiRecusal.objects.filter(pk=row.pk).exists())

    def test_a_derived_recusal_cannot_be_ended(self):
        """Deleting it would change the record without changing the access."""
        row = KaiRecusal.objects.create(
            report=self.report, user=self.accused, reason='accused')
        self._client(self.chair).post(
            reverse('end_kai_recusal', args=[self.report.id]), {'recusal_id': row.id})
        self.assertTrue(KaiRecusal.objects.filter(pk=row.pk).exists())


class StandInEligibilityIsActiveOrAdvisorTests(RecusalTestCase):
    """Mason 07-31-26: "anyone active or advisor"."""

    def test_an_advisor_carried_as_alumni_is_eligible(self):
        """
        The bug. The first cut required `member_status='Active'`, and an advisor
        is commonly carried at Alumni status — so the people most likely to be
        asked to sit in were the ones the menu excluded.
        """
        advisor = make_user('el-advisor', member_type='Advisor', status='Alumni')
        eligible = KaiRecusal.eligible_standins(self.report).values_list('pk', flat=True)
        self.assertIn(advisor.pk, eligible)

    def test_an_active_member_is_eligible(self):
        member = make_user('el-active')
        self.assertIn(
            member.pk, KaiRecusal.eligible_standins(self.report).values_list('pk', flat=True))

    def test_a_pledge_is_never_eligible_even_when_active(self):
        pledge = make_user('el-pledge', member_type='Pledge')
        self.assertNotIn(
            pledge.pk, KaiRecusal.eligible_standins(self.report).values_list('pk', flat=True))

    def test_an_inactive_non_advisor_is_not_eligible(self):
        alum = make_user('el-alum', status='Alumni')
        self.assertNotIn(
            alum.pk, KaiRecusal.eligible_standins(self.report).values_list('pk', flat=True))


class TheStandInCanFindTheirCaseTests(RecusalTestCase):
    """
    An appointment that the appointee cannot see is not an appointment.
    """

    def setUp(self):
        super().setUp()
        self.standin = make_user('si-standin', 'Standin Sam')

    def test_the_member_dashboard_lists_cases_they_stand_in_on(self):
        KaiRecusal.objects.create(
            report=self.report, user=self.accused, reason='accused',
            replacement=self.standin,
            granted_permissions={'can_view_report_list': True},
        )
        response = self._client(self.standin).get(reverse('user_kai_dashboard'))
        self.assertEqual(response.status_code, 200)
        reports = [r.report_id for r in response.context['standin_recusals']]
        self.assertIn(self.report.id, reports)
        self.assertContains(response, "Standing In On")

    def test_the_section_is_absent_for_someone_standing_in_on_nothing(self):
        response = self._client(self.standin).get(reverse('user_kai_dashboard'))
        self.assertEqual(list(response.context['standin_recusals']), [])
        self.assertNotContains(response, "Standing In On")


class ManualRecusalIsENFORCEDTests(RecusalTestCase):
    """
    ⚠️ A `KaiRecusal` row created by hand used to change NOTHING.

    `_case_access` read only the party status computed from the case, so a
    manually recused member kept every permission while the panel said they were
    recused. The record and the enforcement disagreed — the worst of the three
    possible states, because it looks handled.

    Found 07-31-26 when Mason asked for self-recusal: it would have recorded a
    row and left him with full access.
    """

    def setUp(self):
        super().setUp()
        self.chair = make_user('en-chair', 'Chair Casey', member_type='Chair')
        self.committee.chairs.add(self.chair)
        self.member = make_user('en-member', 'Ordinary Olly')
        self.committee.members.add(self.member)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.member,
            can_view_report_list=True, can_view_report_details=True,
            can_edit_open_cases=True, can_close_cases=True,
        )

    def _recuse(self, actor, members, reason='unavailable'):
        return self._client(actor).post(
            reverse('recuse_kai_member', args=[self.other.id]),
            {'member': [str(m.pk) for m in members], 'reason': reason})

    def test_a_manually_recused_member_loses_access_to_the_case(self):
        self._recuse(self.chair, [self.member])
        response = self._client(self.member).get(
            reverse('manage_kai_report', args=[self.other.id]))
        self.assertEqual(response.status_code, 302)

    def test_they_keep_access_to_every_other_case(self):
        """Per-case, like every other part of this."""
        self._recuse(self.chair, [self.member])
        response = self._client(self.member).get(
            reverse('manage_kai_report', args=[self.report.id]))
        self.assertEqual(response.status_code, 200)

    def test_ending_the_recusal_restores_access(self):
        self._recuse(self.chair, [self.member])
        row = KaiRecusal.objects.get(report=self.other, user=self.member)
        self._client(self.chair).post(
            reverse('end_kai_recusal', args=[self.other.id]), {'recusal_id': row.id})
        response = self._client(self.member).get(
            reverse('manage_kai_report', args=[self.other.id]))
        self.assertEqual(response.status_code, 200)


class SelfRecusalTests(RecusalTestCase):
    """Mason 07-31-26: the chair must be able to stand themselves back."""

    def setUp(self):
        super().setUp()
        self.chair = make_user('sr-chair', 'Chair Casey', member_type='Chair')
        self.committee.chairs.add(self.chair)
        self.other_chair = make_user('sr-chair2', 'Chair Two', member_type='Chair')
        self.committee.chairs.add(self.other_chair)
        self.member = make_user('sr-member', 'Ordinary Olly')
        self.committee.members.add(self.member)

    def _recuse(self, actor, members):
        return self._client(actor).post(
            reverse('recuse_kai_member', args=[self.other.id]),
            {'member': [str(m.pk) for m in members], 'reason': 'unavailable'})

    def test_the_chair_can_recuse_themselves(self):
        self._recuse(self.chair, [self.chair])
        self.assertTrue(
            KaiRecusal.objects.filter(report=self.other, user=self.chair).exists())

    def test_a_self_recused_chair_can_no_longer_open_the_case(self):
        self._recuse(self.chair, [self.chair])
        self.assertEqual(
            self._client(self.chair).get(
                reverse('manage_kai_report', args=[self.other.id])).status_code, 302)

    def test_a_self_recused_chair_cannot_recuse_anyone_else(self):
        """Mason: "will not be able to do any other actions or recuse others"."""
        self._recuse(self.chair, [self.chair])
        self._recuse(self.chair, [self.member])
        self.assertFalse(
            KaiRecusal.objects.filter(report=self.other, user=self.member).exists())

    def test_a_self_recused_chair_cannot_appoint_a_stand_in(self):
        self._recuse(self.chair, [self.chair])
        row = KaiRecusal.objects.get(report=self.other, user=self.chair)
        standin = make_user('sr-standin')
        self._client(self.chair).post(
            reverse('appoint_kai_standin', args=[self.other.id]),
            {'recusal_id': row.id, 'replacement': standin.pk})
        row.refresh_from_db()
        self.assertIsNone(row.replacement_id)

    def test_another_chair_can_still_fill_the_seats(self):
        """The escape hatch — otherwise self-recusal strands the case."""
        self._recuse(self.chair, [self.chair])
        row = KaiRecusal.objects.get(report=self.other, user=self.chair)
        standin = make_user('sr-standin2')
        self._client(self.other_chair).post(
            reverse('appoint_kai_standin', args=[self.other.id]),
            {'recusal_id': row.id, 'replacement': standin.pk})
        row.refresh_from_db()
        self.assertEqual(row.replacement_id, standin.pk)


class RecusingSeveralAtOnceTests(RecusalTestCase):
    """Mason 07-31-26: recuse/swap several people in one go."""

    def setUp(self):
        super().setUp()
        self.chair = make_user('mm-chair', 'Chair Casey', member_type='Chair')
        self.committee.chairs.add(self.chair)
        self.a = make_user('mm-a', 'Alpha')
        self.b = make_user('mm-b', 'Bravo')
        self.c = make_user('mm-c', 'Charlie')
        self.committee.members.add(self.a, self.b, self.c)

    def test_three_members_recused_in_one_post(self):
        self._client(self.chair).post(
            reverse('recuse_kai_member', args=[self.other.id]),
            {'member': [str(self.a.pk), str(self.b.pk), str(self.c.pk)],
             'reason': ['unavailable', 'unavailable', 'conflict'],
             'replacement': ['', '', '']})
        self.assertEqual(KaiRecusal.objects.filter(report=self.other).count(), 3)

    def test_recusing_yourself_in_a_batch_does_not_drop_the_others(self):
        """
        The ordering trap. Recusing yourself withdraws every permission on the
        case — so if `request.user` is processed first, the rest of the batch is
        silently dropped. Self is sorted last for exactly this reason.
        """
        self._client(self.chair).post(
            reverse('recuse_kai_member', args=[self.other.id]),
            {'member': [str(self.chair.pk), str(self.a.pk), str(self.b.pk)],
             'reason': ['unavailable'] * 3, 'replacement': ['', '', '']})
        recused = set(
            KaiRecusal.objects.filter(report=self.other).values_list('user_id', flat=True))
        self.assertEqual(recused, {self.chair.pk, self.a.pk, self.b.pk})

    def test_an_already_recused_member_is_skipped_not_duplicated(self):
        self._client(self.chair).post(
            reverse('recuse_kai_member', args=[self.other.id]),
            {'member': [str(self.a.pk)], 'reason': ['unavailable'], 'replacement': ['']})
        self._client(self.chair).post(
            reverse('recuse_kai_member', args=[self.other.id]),
            {'member': [str(self.a.pk), str(self.b.pk)],
             'reason': ['unavailable', 'unavailable'], 'replacement': ['', '']})
        self.assertEqual(
            KaiRecusal.objects.filter(report=self.other, user=self.a).count(), 1)
        self.assertTrue(
            KaiRecusal.objects.filter(report=self.other, user=self.b).exists())


class RecuseAndReplaceInOneStepTests(RecusalTestCase):
    """
    Mason 07-31-26: dropdown rows with "Add another", submitting the recusals
    AND their replacements together.

    The pairing matters beyond convenience: recusing yourself locks you out of
    the case, so if you cannot name the stand-ins in the same submit you have to
    find another chair to finish the job you started.
    """

    def setUp(self):
        super().setUp()
        self.chair = make_user('rr-chair', 'Chair Casey', member_type='Chair')
        self.committee.chairs.add(self.chair)
        self.a = make_user('rr-a', 'Alpha')
        self.b = make_user('rr-b', 'Bravo')
        self.committee.members.add(self.a, self.b)
        self.sub_a = make_user('rr-sub-a', 'Sub Alpha')
        self.sub_b = make_user('rr-sub-b', 'Sub Bravo', member_type='Advisor',
                               status='Alumni')

    def _post(self, data):
        return self._client(self.chair).post(
            reverse('recuse_kai_member', args=[self.other.id]), data)

    def test_a_row_recuses_and_fills_the_seat_together(self):
        self._post({'member': [str(self.a.pk)], 'reason': ['unavailable'],
                    'replacement': [str(self.sub_a.pk)]})
        row = KaiRecusal.objects.get(report=self.other, user=self.a)
        self.assertEqual(row.replacement_id, self.sub_a.pk)
        self.assertTrue(row.granted_permissions)

    def test_two_rows_with_different_replacements(self):
        self._post({'member': [str(self.a.pk), str(self.b.pk)],
                    'reason': ['unavailable', 'conflict'],
                    'replacement': [str(self.sub_a.pk), str(self.sub_b.pk)]})
        self.assertEqual(
            KaiRecusal.objects.get(report=self.other, user=self.a).replacement_id,
            self.sub_a.pk)
        self.assertEqual(
            KaiRecusal.objects.get(report=self.other, user=self.b).replacement_id,
            self.sub_b.pk)

    def test_the_reason_is_per_row(self):
        self._post({'member': [str(self.a.pk), str(self.b.pk)],
                    'reason': ['unavailable', 'conflict'],
                    'replacement': ['', '']})
        self.assertEqual(
            KaiRecusal.objects.get(report=self.other, user=self.a).reason, 'unavailable')
        self.assertEqual(
            KaiRecusal.objects.get(report=self.other, user=self.b).reason, 'conflict')

    def test_a_blank_row_is_skipped(self):
        """An untouched "Add another" clone posts an empty member."""
        self._post({'member': [str(self.a.pk), ''],
                    'reason': ['unavailable', 'unavailable'],
                    'replacement': ['', '']})
        self.assertEqual(KaiRecusal.objects.filter(report=self.other).count(), 1)

    def test_leaving_the_replacement_blank_leaves_the_seat_vacant(self):
        self._post({'member': [str(self.a.pk)], 'reason': ['unavailable'],
                    'replacement': ['']})
        self.assertIsNone(
            KaiRecusal.objects.get(report=self.other, user=self.a).replacement_id)

    def test_an_ineligible_replacement_is_refused_but_the_recusal_stands(self):
        pledge = make_user('rr-pledge', member_type='Pledge')
        self._post({'member': [str(self.a.pk)], 'reason': ['unavailable'],
                    'replacement': [str(pledge.pk)]})
        row = KaiRecusal.objects.get(report=self.other, user=self.a)
        self.assertIsNone(row.replacement_id)

    def test_self_recusal_with_replacements_lands_the_whole_batch(self):
        """
        The reason rows and replacements belong in one submit. Self is processed
        last, so the other rows AND their stand-ins are all applied before the
        lockout takes effect.
        """
        self._post({'member': [str(self.chair.pk), str(self.a.pk)],
                    'reason': ['unavailable', 'unavailable'],
                    'replacement': ['', str(self.sub_a.pk)]})
        self.assertTrue(
            KaiRecusal.objects.filter(report=self.other, user=self.chair).exists())
        self.assertEqual(
            KaiRecusal.objects.get(report=self.other, user=self.a).replacement_id,
            self.sub_a.pk)
