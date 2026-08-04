"""
The site-wide audit log (`ActivityLog`) as a Kai confidentiality surface.

WHAT WENT WRONG (found 08-02-26 by the nightly review, fixed v3.18.2)
---------------------------------------------------------------------
`ActivityLog` was the **eleventh** surface that emits Kai party identities, and
the first one no enumeration in this codebase could have caught.

Four releases audited Kai confidentiality — v3.16.2, v3.16.3, v3.17.7,
v3.18.1 — and all four enumerated *templates and views*. This model is neither:
it is not a Kai model, does not live in `src/models/kai.py`, is not rendered by
any `templates/kai/` file, and has no `submitted_by` or `targeted_to` field to
redact. It simply stores both identities in a `TextField` called `description`,
plus a third copy in the row's own `user` FK.

Three writers made it a disclosure:

  * `submit_kai_report` wrote `"<Name> submitted Kai case #12"` with
    `user=request.user` — and on a submission that user IS the reporter.
  * `file_appeal` wrote `"<Name> filed an appeal on Kai case KAI-2026-007"`,
    and it fetches with `targeted_to=user`, so **only the accused can ever
    write that row**. Its existence is an assertion of who the accused is.
  * `request_drop` wrote `"<Name> requested to drop Kai case #12"`, and only
    the person who filed a case can withdraw it.

Readable at `/activity-logs/` by every officer and chair — `@officer_required`
admits officers, all chairs and admins and consults no `KaiMemberPermission`
anywhere — behind a one-click *Kai Committee* category chip, with a CSV export
carrying the same Description column, an `/admin/` search box over
`description`, and a per-member drill-down in admin-v2 that turns the whole
thing into "which cases did this person report?".

THE PART WORTH REMEMBERING
--------------------------
**When you enumerate the surfaces that render a confidential field, enumerate
the MODELS that can store it first.** Prose is storage. An audit description, a
notification body, an email subject and a log line are all places a value comes
to rest under a different name, and none of them appear in a grep for the field.

Second time the miss was a *place* rather than a *rule*: `CalendarSubscription`
escaped v3.16.0's admin coverage pass because it lived outside `src/models/`.

The fix splits along the v3.18.1 line deliberately rather than discovering it
again: **redaction** where the row should stay visible (the officer log and its
CSV, the admin-v2 dashboard), **exclusion** where a hit is itself the
disclosure (the per-member drill, whose predicate IS the author; and
`/admin/`, per the standing v3.16.2 boundary).
"""

from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from src.kai_audit import audit_search_q, exclude_kai_logs, redact_kai_logs
from src.models import (
    ActivityLog, Committee, KaiBreakGlassGrant, ParliamentUser,
)
from src.models.kai import KaiMemberPermission, KaiRecusal, KaiReport
from src.view.kai_reports import _get_kai_access

SUBMITTER_NAME = 'Zebediah Quillfeather'
ACCUSED_NAME = 'Bartholomew Nettlewicke'


def make_user(uid, name, member_type='Member', is_admin=False, is_officer_role=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=name, username=uid,
        member_type='Officer' if is_officer_role else member_type,
        member_status='Active', is_admin=is_admin,
    )
    user.set_password('audit-test-pass-12345!')
    user.save()
    return user


class KaiAuditTestCase(TestCase):
    """One Kai case, its real audit rows, and viewers at each access level."""

    def setUp(self):
        self.committee = Committee.objects.create(
            name='Kai', code='KAI', is_kai_committee=True,
        )
        self.submitter = make_user('aud-sub', SUBMITTER_NAME)
        self.accused = make_user('aud-acc', ACCUSED_NAME)
        self.report = KaiReport.objects.create(
            title='Audit Case Alpha',
            description='Something happened.',
            submitted_by=self.submitter,
            targeted_to=self.accused,
        )

        # The row `submit_kai_report` writes — the shape that matters, with the
        # author FK pointing at the reporter.
        self.submit_log = ActivityLog.log_activity(
            action_type='kai_action',
            user=self.submitter,
            description=f'A member submitted Kai case {self.report.display_number}',
            object_type='KaiReport',
            object_id=self.report.id,
            object_repr=self.report.display_number,
            metadata={'action': 'submitted'},
        )
        # The row `file_appeal` writes — only the accused can produce one.
        self.appeal_log = ActivityLog.log_activity(
            action_type='kai_action',
            user=self.accused,
            description=f'An appeal was filed on Kai case {self.report.display_number}',
            object_type='KaiAppeal',
            metadata={'report_id': self.report.id, 'level': 'chapter'},
        )
        # A LEGACY row, written before v3.18.2, with both names in the prose.
        # These are the rows actually in the database today and they are the
        # reason the fix is a render-time scrub and not just a writer change.
        self.legacy_log = ActivityLog.log_activity(
            action_type='kai_action',
            user=self.submitter,
            description=(
                f'{SUBMITTER_NAME} submitted Kai case #{self.report.id} '
                f'naming {ACCUSED_NAME}'
            ),
            object_type='KaiReport',
            object_id=self.report.id,
            object_repr=f'Case #{self.report.id}',
        )
        # A non-Kai row, so "redacted everything" would be a visible failure.
        self.other_log = ActivityLog.log_activity(
            action_type='login',
            user=self.submitter,
            description=f'{SUBMITTER_NAME} logged in successfully',
        )

    # -- viewers ---------------------------------------------------------

    def _officer(self, uid='aud-off'):
        """An officer with NO KaiMemberPermission. The population at issue."""
        return make_user(uid, f'Officer {uid}', is_officer_role=True)

    def _full_reviewer(self, uid='aud-rev'):
        user = make_user(uid, f'Reviewer {uid}', is_officer_role=True)
        self.committee.members.add(user)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=user,
            can_view_report_list=True, can_view_report_details=True,
            can_view_submitter_identity=True, can_view_accused_identity=True,
        )
        return user

    def _all_logs(self):
        return list(ActivityLog.objects.all().select_related('user'))


# ---------------------------------------------------------------------------
# 1. The redaction itself
# ---------------------------------------------------------------------------


class TheAuditLogRedactsPartyIdentitiesTests(KaiAuditTestCase):
    """
    The 🔴. Every assertion here fails against the pre-v3.18.2 code, because
    before it there was no `display_actor` at all and the templates read
    `log.user` directly.
    """

    def test_an_officer_cannot_see_who_submitted_a_case(self):
        logs = redact_kai_logs(self._all_logs(), self._officer())
        submit = next(l for l in logs if l.pk == self.submit_log.pk)
        self.assertEqual(submit.display_actor, 'Anonymous')
        self.assertEqual(submit.display_actor_id, '')
        self.assertNotIn(SUBMITTER_NAME, submit.display_description)

    def test_an_officer_cannot_see_who_filed_an_appeal(self):
        """Only the accused can file, so the author of this row IS the accused."""
        logs = redact_kai_logs(self._all_logs(), self._officer())
        appeal = next(l for l in logs if l.pk == self.appeal_log.pk)
        self.assertEqual(appeal.display_actor, 'Redacted')
        self.assertNotIn(ACCUSED_NAME, appeal.display_description)

    def test_legacy_rows_are_scrubbed_not_just_new_ones(self):
        """
        The writers stopped interpolating names in v3.18.2. That fixes nothing
        on its own — every row already in the database still contains them, and
        those are the rows an officer would read today.
        """
        logs = redact_kai_logs(self._all_logs(), self._officer())
        legacy = next(l for l in logs if l.pk == self.legacy_log.pk)
        self.assertNotIn(SUBMITTER_NAME, legacy.display_description)
        self.assertNotIn(ACCUSED_NAME, legacy.display_description)
        self.assertIn('Anonymous', legacy.display_description)
        self.assertIn('Redacted', legacy.display_description)

    def test_non_kai_rows_are_untouched(self):
        """Over-redacting everything would pass the tests above and be wrong."""
        logs = redact_kai_logs(self._all_logs(), self._officer())
        other = next(l for l in logs if l.pk == self.other_log.pk)
        self.assertEqual(other.display_actor, self.submitter.get_display_name())
        self.assertIn(SUBMITTER_NAME, other.display_description)

    def test_a_full_reviewer_still_sees_both_names(self):
        """The redaction is permission-driven, not blanket."""
        logs = redact_kai_logs(self._all_logs(), self._full_reviewer())
        submit = next(l for l in logs if l.pk == self.submit_log.pk)
        legacy = next(l for l in logs if l.pk == self.legacy_log.pk)
        self.assertEqual(submit.display_actor, self.submitter.get_display_name())
        self.assertIn(SUBMITTER_NAME, legacy.display_description)
        self.assertIn(ACCUSED_NAME, legacy.display_description)

    def test_an_unresolvable_kai_row_fails_closed(self):
        """
        A Kai row whose case cannot be resolved might be a submission, and a
        submission's author is the reporter. Redact rather than guess.
        """
        orphan = ActivityLog.log_activity(
            action_type='kai_action', user=self.submitter,
            description='Something Kai-ish happened', object_type='KaiReport',
            object_id='999999',
        )
        logs = redact_kai_logs(self._all_logs(), self._officer())
        row = next(l for l in logs if l.pk == orphan.pk)
        self.assertEqual(row.display_actor, 'Anonymous')


# ---------------------------------------------------------------------------
# 2. The page, the export, and the search box
# ---------------------------------------------------------------------------


class TheActivityLogPageDoesNotLeakTests(KaiAuditTestCase):

    @staticmethod
    def _rows_only(html):
        """
        Just the table body.

        The page also renders a member picker (`active_users`) listing every
        active member by name, and that is a directory, not a disclosure —
        every member can already see it. Asserting over the whole document
        would fail on the `<select>` and tell us nothing. The rows are where
        the association between a name and a case lives, so the rows are what
        this checks.
        """
        start = html.find('<tbody')
        end = html.find('</tbody>', start)
        assert start != -1 and end != -1, 'activity_logs.html has no <tbody>'
        return html[start:end]

    def test_the_officer_activity_page_names_nobody(self):
        client = Client()
        client.force_login(self._officer())
        html = client.get(
            reverse('activity_logs'), {'date_range': 'all', 'category': 'kai'},
        ).content.decode()
        rows = self._rows_only(html)
        self.assertIn(self.report.display_number, rows, 'the Kai rows should be present')
        self.assertNotIn(SUBMITTER_NAME, rows)
        self.assertNotIn(ACCUSED_NAME, rows)

    def test_the_csv_export_names_nobody(self):
        """A CSV leaves the app; v3.16.2's lesson was that exports are surfaces."""
        client = Client()
        client.force_login(self._officer())
        body = client.get(
            reverse('export_activity_logs'), {'date_range': 'all', 'category': 'kai'},
        ).content.decode()
        self.assertIn(self.report.display_number, body, 'the Kai rows should be present')
        self.assertNotIn(SUBMITTER_NAME, body)
        self.assertNotIn(ACCUSED_NAME, body)

    def test_a_full_reviewer_sees_the_names_on_the_same_page(self):
        """The control for the two above: the page is not simply name-free."""
        client = Client()
        client.force_login(self._full_reviewer())
        rows = self._rows_only(client.get(
            reverse('activity_logs'), {'date_range': 'all', 'category': 'kai'},
        ).content.decode())
        self.assertIn(SUBMITTER_NAME, rows)

    def test_the_search_box_is_not_an_oracle(self):
        """
        **A filter predicate is a join key.** Redacting the output while still
        filtering on the input recovers exactly what the page refuses to print
        — the v3.16.3 / v3.18.1 bug, one page over.
        """
        officer = self._officer()
        matched = ActivityLog.objects.filter(
            audit_search_q(SUBMITTER_NAME.split()[-1], officer)
        )
        self.assertNotIn(self.legacy_log.pk, [l.pk for l in matched])
        self.assertNotIn(self.submit_log.pk, [l.pk for l in matched])

    def test_the_search_box_still_finds_non_kai_rows_by_name(self):
        """The narrowing is Kai-scoped, not a blanket disabling of search."""
        officer = self._officer()
        matched = ActivityLog.objects.filter(
            audit_search_q(SUBMITTER_NAME.split()[-1], officer)
        )
        self.assertIn(self.other_log.pk, [l.pk for l in matched])

    def test_a_full_reviewer_can_still_search_kai_rows(self):
        matched = ActivityLog.objects.filter(
            audit_search_q(SUBMITTER_NAME.split()[-1], self._full_reviewer())
        )
        self.assertIn(self.legacy_log.pk, [l.pk for l in matched])

    def test_case_numbers_remain_searchable_for_officers(self):
        """
        `object_repr` stays open deliberately — the confidentiality matrix
        already records officer-level visibility of a case NUMBER as fine, and
        it carries no name.
        """
        matched = ActivityLog.objects.filter(
            audit_search_q(self.report.display_number, self._officer())
        )
        self.assertIn(self.submit_log.pk, [l.pk for l in matched])


# ---------------------------------------------------------------------------
# 3. Exclusion, where a hit is itself the disclosure
# ---------------------------------------------------------------------------


class SurfacesThatExcludeRatherThanRedactTests(KaiAuditTestCase):

    def test_the_per_member_drill_excludes_kai_rows(self):
        """
        `filter(user=member)` means the PREDICATE is the author. Redacting the
        actor column achieves nothing when the page is the member's own —
        a Kai row on Zebediah's page says Zebediah, whatever it renders as.
        """
        visible = exclude_kai_logs(
            ActivityLog.objects.filter(user=self.submitter), self._officer(),
        )
        self.assertNotIn(self.submit_log.pk, [l.pk for l in visible])
        self.assertIn(self.other_log.pk, [l.pk for l in visible])

    def test_a_full_reviewer_keeps_seeing_them(self):
        visible = exclude_kai_logs(
            ActivityLog.objects.filter(user=self.submitter), self._full_reviewer(),
        )
        self.assertIn(self.submit_log.pk, [l.pk for l in visible])

    def test_no_template_renders_raw_audit_fields(self):
        """
        The enumeration, maintained by grep rather than by memory — the same
        shape as `test_no_template_renders_raw_activity_fields`, which is what
        stopped the *other* activity feed regressing.

        Any template showing an `ActivityLog` must use `display_actor` /
        `display_description`, because the raw fields carry party identities on
        Kai rows.
        """
        offenders = []
        watched = (
            Path(settings.BASE_DIR) / 'templates' / 'activity_logs.html',
            Path(settings.BASE_DIR) / 'templates' / 'admin_v2' / 'dashboard.html',
        )
        for path in watched:
            if not path.exists():
                continue
            text = path.read_text(encoding='utf-8', errors='ignore')
            for raw in ('log.description', 'log.user.name',
                        'log.user.get_display_name', 'log.user.user_id'):
                if '{{ ' + raw + ' }}' in text or '{{ ' + raw + '|' in text:
                    offenders.append(f'{path.name}: {raw}')
        self.assertEqual(
            offenders, [],
            'These templates render un-redacted ActivityLog fields. Use '
            'display_actor / display_actor_id / display_description — see '
            'src/kai_audit.py.',
        )


# ---------------------------------------------------------------------------
# 4. is_admin no longer grants Kai access; the break-glass does
# ---------------------------------------------------------------------------


class AdminIsNotAJudicialRoleTests(KaiAuditTestCase):
    """
    v3.18.2, finding 6. `_get_kai_access` used to open with
    `if user.is_admin or _is_kai_chair(...)`, so one boolean on the user row
    granted every Kai permission including both identity flags.

    That contradicted the standing v3.16.2 rule (*an admin is an operational
    role, not a judicial one* — the reason all seven Kai models are
    unregistered from /admin/) and contradicted `_is_kai_chair`'s own argument,
    added ten lines above it one release earlier.
    """

    def test_a_site_admin_gets_nothing_by_default(self):
        admin = make_user('aud-admin', 'Admin Alice', is_admin=True)
        access = _get_kai_access(admin, self.committee)
        self.assertFalse(access['can_view_report_list'])
        self.assertFalse(access['can_view_submitter_identity'])
        self.assertFalse(access['can_view_accused_identity'])
        self.assertFalse(access['is_full_access'])

    def test_a_real_chair_still_gets_full_access(self):
        chair = make_user('aud-chair', 'Chair Chris')
        self.committee.chairs.add(chair)
        access = _get_kai_access(chair, self.committee)
        self.assertTrue(access['is_full_access'])
        self.assertFalse(access['is_break_glass'])

    def test_an_admin_with_a_permission_row_is_an_ordinary_reviewer(self):
        """A real grant is checked before the break-glass, so no banner."""
        admin = make_user('aud-admin2', 'Admin Bob', is_admin=True)
        self.committee.members.add(admin)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=admin, can_view_report_list=True,
        )
        access = _get_kai_access(admin, self.committee)
        self.assertTrue(access['can_view_report_list'])
        self.assertFalse(access['can_view_submitter_identity'])
        self.assertFalse(access['is_break_glass'])

    def test_an_active_break_glass_grant_restores_full_access(self):
        admin = make_user('aud-admin3', 'Admin Carol', is_admin=True)
        KaiBreakGlassGrant.objects.create(
            user=admin, reason='All Kai chairs graduated; restoring access.',
            expires_at=timezone.now() + timedelta(hours=4),
        )
        access = _get_kai_access(admin, self.committee)
        self.assertTrue(access['is_full_access'])
        self.assertTrue(access['is_break_glass'])

    def test_an_expired_grant_confers_nothing(self):
        admin = make_user('aud-admin4', 'Admin Dave', is_admin=True)
        KaiBreakGlassGrant.objects.create(
            user=admin, reason='expired',
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertFalse(_get_kai_access(admin, self.committee)['is_full_access'])

    def test_a_revoked_grant_is_inert_immediately(self):
        admin = make_user('aud-admin5', 'Admin Erin', is_admin=True)
        KaiBreakGlassGrant.objects.create(
            user=admin, reason='revoked',
            expires_at=timezone.now() + timedelta(hours=4),
            revoked_at=timezone.now(),
        )
        self.assertFalse(_get_kai_access(admin, self.committee)['is_full_access'])

    def test_a_break_glass_grant_for_a_non_admin_does_nothing(self):
        """The branch is only reached for admins — fail closed either way."""
        member = make_user('aud-nonadmin', 'Member Mo')
        KaiBreakGlassGrant.objects.create(
            user=member, reason='mistake',
            expires_at=timezone.now() + timedelta(hours=4),
        )
        self.assertFalse(_get_kai_access(member, self.committee)['is_full_access'])

    def test_the_grant_model_is_not_registered_in_the_admin(self):
        """
        An editable admin for this model would let an admin grant themselves
        the access it exists to withhold — the `KaiMemberPermissionAdmin` edge
        v3.16.2 removed. The absence is intentional.
        """
        from django.contrib import admin as dj_admin

        registered = {m.__name__ for m in dj_admin.site._registry}
        for site in getattr(dj_admin.sites, 'all_sites', []):
            registered |= {m.__name__ for m in site._registry}
        self.assertNotIn('KaiBreakGlassGrant', registered)


# ---------------------------------------------------------------------------
# 5. The batched recusal lookup must not change any answer
# ---------------------------------------------------------------------------


class BatchedCaseAccessMatchesUnbatchedTests(KaiAuditTestCase):
    """
    v3.18.2, finding 2. The Kai list page's cross-case activity panel called
    `_case_access` per entry, and `_case_access` costs two `KaiRecusal`
    queries for any case the viewer is not a party to — sixteen queries a load.

    `recusal_rows` is a **performance argument only**. If it can change an
    answer it is a security bug, so this compares the two paths directly.
    """

    def _cases(self):
        from src.view.kai_reports import _case_access, _recusal_rows_for
        return _case_access, _recusal_rows_for

    def test_the_two_paths_agree_across_every_role(self):
        _case_access, _recusal_rows_for = self._cases()

        standin = make_user('aud-standin', 'Standin Sam')
        recusal = KaiRecusal.objects.create(
            report=self.report, user=self.accused, reason='accused',
            replacement=standin,
            granted_permissions={'can_view_report_list': True,
                                 'can_view_report_details': True},
        )
        manual = make_user('aud-manual', 'Manual Mia')
        KaiRecusal.objects.create(
            report=self.report, user=manual, reason='conflict',
        )

        reviewer = self._full_reviewer()
        base = _get_kai_access(reviewer, self.committee)
        rows = _recusal_rows_for(reviewer, {self.report.pk, self.decoy_id()})

        for user in (self.submitter, self.accused, standin, manual, reviewer):
            user_base = _get_kai_access(user, self.committee)
            user_rows = _recusal_rows_for(user, {self.report.pk})
            unbatched = _case_access(user, self.report, user_base)
            batched = _case_access(
                user, self.report, user_base, recusal_rows=user_rows,
            )
            self.assertEqual(
                unbatched, batched,
                f'batched and unbatched _case_access disagree for {user}',
            )
        self.assertIsNotNone(recusal.pk)
        self.assertIsNotNone(base)

    def decoy_id(self):
        other = make_user('aud-decoy-owner', 'Decoy Owner')
        return KaiReport.objects.create(
            title='Decoy', description='x', submitted_by=other,
        ).pk

    def test_an_empty_standin_grant_still_fails_closed_when_batched(self):
        """
        `standin_grant` returns `{}` — not None — for an appointment with no
        permissions, so the merge grants nothing. The batched path reproduces
        that exactly; getting it wrong would silently promote a stand-in.
        """
        _case_access, _recusal_rows_for = self._cases()
        standin = make_user('aud-standin2', 'Standin Sue')
        KaiRecusal.objects.create(
            report=self.report, user=self.accused, reason='accused',
            replacement=standin, granted_permissions={},
        )
        base = _get_kai_access(standin, self.committee)
        rows = _recusal_rows_for(standin, {self.report.pk})
        batched = _case_access(standin, self.report, base, recusal_rows=rows)
        self.assertFalse(batched['can_view_report_details'])
        self.assertFalse(batched['can_view_submitter_identity'])


# ---------------------------------------------------------------------------
# 6. Case numbers — the bounded retry
# ---------------------------------------------------------------------------


class CaseNumberRetryIsBoundedTests(KaiAuditTestCase):
    """v3.18.2, finding 5."""

    def test_a_collision_steps_forward_instead_of_recomputing(self):
        """
        The v3.18.1 retry called `next_case_number()` again, which reads the
        same MAX and can return the same value if the winner has not committed.
        The retry now steps past the number that just failed.
        """
        year = timezone.now().year
        taken = KaiReport.objects.create(
            title='Taken', description='x', submitted_by=self.submitter,
        )
        self.assertTrue(taken.case_number.startswith(f'KAI-{year}-'))

        fresh = KaiReport(
            title='Fresh', description='x', submitted_by=self.submitter,
            case_number=taken.case_number,
        )
        # Blank it so save() takes the assignment branch, then pre-seed the
        # collision by handing it the number already in use.
        fresh.case_number = ''
        fresh.save()
        self.assertNotEqual(fresh.case_number, taken.case_number)

    def test_a_non_collision_integrity_error_is_re_raised_unchanged(self):
        """
        `except IntegrityError` used to swallow ANY integrity failure, assign a
        fresh case number and re-raise from the retry with a number burned and
        a misleading traceback.
        """
        self.assertFalse(
            KaiReport._is_case_number_collision(Exception('null value in column "title"')),
        )
        self.assertTrue(
            KaiReport._is_case_number_collision(
                Exception('UNIQUE constraint failed: index "uniq_kai_report_case_number"')
            ),
        )

    def test_update_fields_save_does_not_discard_a_fresh_number(self):
        report = KaiReport.objects.create(
            title='UF', description='x', submitted_by=self.submitter,
        )
        KaiReport.objects.filter(pk=report.pk).update(case_number='')
        report.refresh_from_db()
        report.assigned_to = self.submitter
        report.save(update_fields=['assigned_to'])
        report.refresh_from_db()
        self.assertTrue(report.case_number)


# ---------------------------------------------------------------------------
# 9. The second axis, across EVERY permission combination  (v3.18.4)
# ---------------------------------------------------------------------------


class TheSecondAxisAppliesToEachFlagSeparatelyTests(KaiAuditTestCase):
    """
    ⚠️ v3.18.4 — THE BUG THIS CLASS EXISTS FOR IS THE ONE v3.18.3 THOUGHT IT
    HAD ALREADY FIXED.

    v3.18.3 added `viewer_party_case_ids` — the "second axis" — because
    committee-level identity flags do not apply on a case the viewer is the
    accused on, where `_case_access` withdraws every permission. Correct. But
    it gated the lookup on the CONJUNCTION of the two flags:

        party_cases = ... if (show_submitter and show_accused) else set()

    while `redact_kai_logs` consumes it PER FLAG:

        row_show_submitter = show_submitter and report_id not in party_cases

    So for a viewer holding `(submitter=True, accused=False)` the conjunction
    was False, `party_cases` stayed empty, `report_id not in set()` was
    vacuously true on every row, and the reviewer read their own reporter's
    name on the case they were the accused on. **The v3.18.1 oracle again, in
    the branch beside the fix for it.**

    WHY NO EXISTING TEST SAW IT, WHICH IS THE POINT
    -----------------------------------------------
    Every Kai fixture in this repo — `_full_reviewer` here, `PartySafeSurface
    Tests.setUp` next door — grants `can_view_submitter_identity` and
    `can_view_accused_identity` **together**. A reviewer permissioned for one
    and not the other was constructible, reachable and natural (it is the
    obvious grant for someone triaging intake), and no test had ever built one.
    Three of the four combinations passed; the fixtures only ever exercised one
    of the three.

    So this class does not test the fix. **It tests all four combinations**,
    and the table is the assertion. A future flag added to
    `_KAI_PERMISSION_FIELDS` should extend it rather than reuse it.
    """

    def _reviewer_with(self, show_submitter, show_accused):
        uid = f'axis-{int(show_submitter)}{int(show_accused)}'
        user = make_user(uid, f'Reviewer {uid}', is_officer_role=True)
        self.committee.members.add(user)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=user,
            can_view_report_list=True, can_view_report_details=True,
            can_view_submitter_identity=show_submitter,
            can_view_accused_identity=show_accused,
        )
        # The viewer is the ACCUSED on the case whose rows we render. This is
        # the whole scenario: committee-level flags say "may read", the case
        # says otherwise, and the case wins.
        self.report.targeted_to = user
        self.report.save(update_fields=['targeted_to'])
        return user

    def test_no_flag_combination_leaks_the_reporter_to_the_accused(self):
        """
        The four-row table. Only `(True, True)` passed before v3.18.3;
        `(True, False)` still failed after it.
        """
        for show_submitter, show_accused in (
            (True, True), (True, False), (False, True), (False, False),
        ):
            with self.subTest(submitter=show_submitter, accused=show_accused):
                viewer = self._reviewer_with(show_submitter, show_accused)
                log = redact_kai_logs(self._all_logs(), viewer)
                row = next(
                    r for r in log
                    if r.action_category == 'kai'
                    and r.user_id == self.submitter.pk
                )
                self.assertNotIn(
                    SUBMITTER_NAME, row.display_description,
                    f'LEAK with flags (submitter={show_submitter}, '
                    f'accused={show_accused}): the reporter is named in the '
                    f'description of a case this viewer is the ACCUSED on. '
                    f'The second axis must apply to each flag separately, not '
                    f'to their conjunction — see redact_kai_logs.',
                )
                self.assertNotEqual(
                    row.display_actor, SUBMITTER_NAME,
                    f'LEAK with flags (submitter={show_submitter}, '
                    f'accused={show_accused}): the row\'s AUTHOR column names '
                    f'the reporter. On a submission row the author IS the '
                    f'reporter — that is the third copy of the identity.',
                )

    def test_a_partial_grant_still_reads_cases_the_viewer_is_not_party_to(self):
        """
        The control. Without this, a fix that simply redacted everything for
        every partially-permissioned viewer would pass the test above — and
        over-redaction is a real cost, not a safe default: it is what made the
        first cut of submitter recusal hide a reporter's own cases from them
        (corrected 07-31-26).
        """
        viewer = self._reviewer_with(True, False)

        other_submitter = make_user('axis-other-sub', 'Perpetua Marchbanks')
        other = KaiReport.objects.create(
            title='Not Their Case', description='x',
            submitted_by=other_submitter,
            targeted_to=make_user('axis-other-acc', 'Someone Else'),
        )
        row = ActivityLog.log_activity(
            action_type='kai_action', user=other_submitter,
            description=f'Perpetua Marchbanks submitted Kai case {other.display_number}',
            object_type='KaiReport', object_id=other.id,
            object_repr=other.display_number,
        )

        redacted = next(
            r for r in redact_kai_logs(self._all_logs(), viewer) if r.pk == row.pk
        )
        self.assertIn(
            'Perpetua Marchbanks', redacted.display_description,
            'OVER-REDACTED: a viewer holding can_view_submitter_identity lost '
            'the reporter on a case they are NOT a party to. The second axis '
            'is per-case, not a blanket downgrade of a partial grant.',
        )


# ---------------------------------------------------------------------------
# 10. One case-resolution rule, two consumers  (v3.18.4)
# ---------------------------------------------------------------------------


class TheCaseResolutionRuleIsSharedTests(KaiAuditTestCase):
    """
    `_log_report_id` (Python, per row) and `rows_for_cases_q` (ORM, per
    queryset) answer the same question — *which case does this log row refer
    to?* — and `audit_search_q` used to carry a third, narrower answer of its
    own that matched `object_id`/`object_repr` only.

    That mattered because appeal and recusal rows set `object_type` to
    `KaiAppeal` / `KaiRecusal` and carry the report **only** in
    `metadata['report_id']`. The redactor could resolve them; the search
    predicate could not. Not live — post-v3.18.2 those descriptions carry no
    names — but it is *output redacted, input not* in miniature, which is the
    exact asymmetry this module exists to prevent, and the next writer to put
    anything identifying in an appeal description would have made it live
    without touching `kai_audit.py`.
    """

    def test_a_metadata_only_row_resolves_to_its_case(self):
        """An appeal row: `object_type` names another model entirely."""
        from src.kai_audit import _log_report_id

        appeal_row = ActivityLog.log_activity(
            action_type='kai_action', user=self.accused,
            description=f'An appeal was filed on Kai case {self.report.display_number}',
            object_type='KaiAppeal',
            metadata={'report_id': self.report.id, 'level': 'chapter'},
        )
        self.assertEqual(_log_report_id(appeal_row), self.report.id)

    def test_the_orm_predicate_matches_the_same_metadata_only_row(self):
        """
        The half that was missing. If this fails while the test above passes,
        the two resolutions have drifted apart again — which is the whole
        failure mode.
        """
        from src.kai_audit import rows_for_cases_q

        appeal_row = ActivityLog.log_activity(
            action_type='kai_action', user=self.accused,
            description=f'An appeal was filed on Kai case {self.report.display_number}',
            object_type='KaiAppeal',
            metadata={'report_id': self.report.id, 'level': 'chapter'},
        )
        matched = ActivityLog.objects.filter(rows_for_cases_q([self.report.id]))
        self.assertIn(
            appeal_row, matched,
            'rows_for_cases_q missed a row that _log_report_id resolves. The '
            'ORM predicate and the Python one must read the same two places.',
        )
        self.assertIn(self.submit_log, matched, 'The object_id path regressed.')

    def test_an_unrelated_row_with_a_colliding_object_id_is_not_swept_up(self):
        """
        `object_id` is a shared CharField across every model in the schema, so
        an unconstrained `object_id__in` also matched, say, an Event whose pk
        equalled a case pk — silently dropping unrelated rows from a viewer's
        search results. Over-restriction is cheap but it is not free, and it is
        the kind of thing nobody reports as a bug.
        """
        from src.kai_audit import rows_for_cases_q

        unrelated = ActivityLog.log_activity(
            action_type='other', user=self.submitter,
            description='An unrelated thing happened',
            object_type='Event', object_id=self.report.id,
            object_repr='Some Event',
        )
        matched = ActivityLog.objects.filter(rows_for_cases_q([self.report.id]))
        self.assertNotIn(unrelated, matched)

    def test_an_empty_case_list_matches_nothing_and_composes(self):
        from src.kai_audit import rows_for_cases_q

        self.assertEqual(
            ActivityLog.objects.filter(rows_for_cases_q([])).count(), 0)
        self.assertEqual(
            ActivityLog.objects.exclude(rows_for_cases_q([])).count(),
            ActivityLog.objects.count(),
            '~rows_for_cases_q([]) must be a no-op, not an empty result — '
            'audit_search_q composes it with `&`.',
        )
