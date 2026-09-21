"""
Kai submitter/accused identity survives ParliamentUser.anonymize() — 09-21-26.

CONTEXT
-------
`ParliamentUser.anonymize()` (v3.34.0-in-progress, src/models/users.py) scrubs
a member's `name` to 'Deleted User' in place, without touching any row that
merely has a foreign key to them — that's the whole design, so votes,
ballots, minutes, and KaiReport.submitted_by/targeted_to all keep pointing at
the same user_id.

But `KaiReport` templates render `submitted_by.name`/`targeted_to.name`
directly to reviewers holding the matching `can_view_*_identity` permission,
and that FK read now returns 'Deleted User' forever once the member is
anonymized — silently erasing who filed a case (or who it was about) from a
record this chapter keeps specifically so future officers can see what was
decided and why (Resources/CLAUDE.md, "Kai records are RETAINED
DELIBERATELY"). Flagged 09-19-26, fixed 09-21-26 at Mason's direction.

THE FIX
-------
`KaiReport.submitted_by_name_snapshot` / `targeted_to_name_snapshot`
(migration 0047): kept in sync with the live name on every `save()` while
that party isn't yet anonymized, frozen at the last synced value once they
are. `submitter_display_name` / `accused_display_name` prefer the snapshot
only when the live account is anonymized; every reader (templates, the CSV
export, `_redact_activity_log`) was moved onto these properties.

A SECOND, PRE-EXISTING BUG FOUND WHILE BUILDING THIS
-----------------------------------------------------
`_redact_activity_log` (src/view/kai_reports.py) does a substring swap to
scrub a party's name out of legacy (pre-v3.18.1) `KaiReportActivity.details`
free text for a viewer who lacks the matching identity flag. It computed the
name to search for as `report.submitted_by.name` — which, once the submitter
is anonymized, reads 'Deleted User' and can never match the *real* name
still sitting in old `details` text. That's not just a display gap, it's an
active disclosure regression: an unauthorized reviewer would see the real
historical name where the redaction used to work. `RedactionSurvivesAnonymizationTests`
below is the regression test for that, and would fail against the
pre-fix code (searching for 'Deleted User' instead of the frozen name never
finds anything to redact).

Run with: python manage.py test src.tests.kai.test_kai_identity_survives_anonymization
"""
from django.test import Client, TestCase

from src.models import Committee, ParliamentUser
from src.models.kai import KaiMemberPermission, KaiReport, KaiReportActivity
from src.view.kai_reports import _redact_activity_log


def make_user(uid, name, member_type='Member', member_status='Active'):
    user = ParliamentUser.objects.create(
        user_id=uid, name=name, username=uid,
        member_type=member_type, member_status=member_status,
    )
    user.set_password('kai-identity-test-pass-12345!')
    user.save()
    return user


class SnapshotSyncTests(TestCase):
    """`save()` keeps both snapshots tracking the live name — until it can't."""

    def setUp(self):
        self.submitter = make_user('snap-sub', 'Original Submitter Name')
        self.accused = make_user('snap-acc', 'Original Accused Name')

    def test_snapshot_is_populated_on_creation(self):
        report = KaiReport.objects.create(
            title='T', description='d', submitted_by=self.submitter, targeted_to=self.accused,
        )
        self.assertEqual(report.submitted_by_name_snapshot, 'Original Submitter Name')
        self.assertEqual(report.targeted_to_name_snapshot, 'Original Accused Name')

    def test_snapshot_is_populated_when_targeted_to_is_set_after_creation(self):
        """
        The realistic case: a report is filed with no accused named yet, and
        `targeted_to` is set later by a chair (see `manage_kai_report`). A
        snapshot taken only at creation would stay permanently blank here.
        """
        report = KaiReport.objects.create(
            title='T', description='d', submitted_by=self.submitter, targeted_to=None,
        )
        self.assertEqual(report.targeted_to_name_snapshot, '')

        report.targeted_to = self.accused
        report.save()
        report.refresh_from_db()
        self.assertEqual(report.targeted_to_name_snapshot, 'Original Accused Name')

    def test_snapshot_tracks_a_later_name_change_while_the_account_is_intact(self):
        report = KaiReport.objects.create(
            title='T', description='d', submitted_by=self.submitter, targeted_to=self.accused,
        )
        self.submitter.name = 'Updated Submitter Name'
        self.submitter.save()
        report.status = 'reviewed'  # any unrelated change that triggers save()
        report.save()
        report.refresh_from_db()
        self.assertEqual(report.submitted_by_name_snapshot, 'Updated Submitter Name')

    def test_snapshot_freezes_once_the_party_is_anonymized(self):
        report = KaiReport.objects.create(
            title='T', description='d', submitted_by=self.submitter, targeted_to=self.accused,
        )
        self.submitter.anonymize()

        # A later, unrelated save() must NOT clobber the frozen name with
        # 'Deleted User' — this is the whole point of the `not is_anonymized`
        # guard in save().
        report.chair_notes = 'a routine note added after anonymization'
        report.save()
        report.refresh_from_db()

        self.assertEqual(report.submitted_by_name_snapshot, 'Original Submitter Name')

    def test_partial_save_with_update_fields_still_persists_the_synced_snapshot(self):
        """
        `manage_kai_report` routinely calls `save(update_fields=[...])`. The
        snapshot sync must extend that list itself (same pattern as
        case_number's own update_fields handling just above it in save()) or
        the synced value is computed but never written.
        """
        report = KaiReport.objects.create(
            title='T', description='d', submitted_by=self.submitter, targeted_to=self.accused,
        )
        self.submitter.name = 'Renamed Via Partial Save'
        self.submitter.save()

        report.status = 'reviewed'
        report.save(update_fields=['status'])
        report.refresh_from_db()

        self.assertEqual(report.submitted_by_name_snapshot, 'Renamed Via Partial Save')


class DisplayPropertyTests(TestCase):
    """`submitter_display_name` / `accused_display_name` — what callers should use."""

    def setUp(self):
        self.submitter = make_user('disp-sub', 'Display Submitter')
        self.accused = make_user('disp-acc', 'Display Accused')
        self.report = KaiReport.objects.create(
            title='T', description='d', submitted_by=self.submitter, targeted_to=self.accused,
        )

    def test_returns_the_live_name_while_not_anonymized(self):
        self.assertEqual(self.report.submitter_display_name, 'Display Submitter')
        self.assertEqual(self.report.accused_display_name, 'Display Accused')

    def test_prefers_live_name_over_a_stale_snapshot_while_not_anonymized(self):
        """
        Even if the snapshot happens to be behind (e.g. a fixture written
        directly to the DB), the live name is authoritative until
        anonymize() actually runs — a stale snapshot must never leak through
        for an intact account.
        """
        KaiReport.objects.filter(pk=self.report.pk).update(submitted_by_name_snapshot='Stale Value')
        self.report.refresh_from_db()
        self.assertEqual(self.report.submitter_display_name, 'Display Submitter')

    def test_falls_back_to_the_snapshot_once_anonymized(self):
        self.submitter.anonymize()
        self.accused.anonymize()
        self.report.refresh_from_db()

        self.assertEqual(self.report.submitter_display_name, 'Display Submitter')
        self.assertEqual(self.report.accused_display_name, 'Display Accused')
        # And the live read really would have been useless, confirming this
        # property is doing something rather than passing by coincidence.
        self.assertEqual(self.report.submitted_by.name, 'Deleted User')

    def test_a_report_with_no_snapshot_falls_back_to_the_live_name_rather_than_crashing(self):
        """
        Documents the known, unavoidable limit: a report whose snapshot was
        never populated (bulk_create fixture, or a party anonymized before
        migration 0047's backfill ran) has nothing to recover and shows
        whatever the live row says — same as before this feature existed,
        not worse.
        """
        bare = KaiReport.objects.create(
            title='Bare', description='d', submitted_by=self.submitter,
        )
        KaiReport.objects.filter(pk=bare.pk).update(submitted_by_name_snapshot='')
        self.submitter.anonymize()
        bare.refresh_from_db()
        self.assertEqual(bare.submitter_display_name, 'Deleted User')

    def test_no_targeted_to_returns_empty_string(self):
        report = KaiReport.objects.create(title='T2', description='d', submitted_by=self.submitter)
        self.assertEqual(report.accused_display_name, '')

    def test_str_uses_the_display_name(self):
        self.submitter.anonymize()
        self.report.refresh_from_db()
        self.assertIn('Display Submitter', str(self.report))
        self.assertNotIn('Deleted User', str(self.report))


class RedactionSurvivesAnonymizationTests(TestCase):
    """
    The security-regression test. `_redact_activity_log`'s substring swap
    must keep finding a party's real name in legacy `details` text even
    after that party has been anonymized — using the frozen snapshot, not
    the now-scrubbed live name. Every assertion in
    `test_unauthorized_viewer_still_has_the_name_redacted_after_anonymization`
    fails against the pre-fix code, which computed the search string as
    `report.submitted_by.name` (== 'Deleted User' post-anonymization, which
    cannot appear in text written before the member was ever anonymized).
    """

    REAL_NAME = 'Persimmon Oakhurst'

    def setUp(self):
        self.submitter = make_user('redact-sub', self.REAL_NAME)
        self.accused = make_user('redact-acc', 'Some Accused')
        self.report = KaiReport.objects.create(
            title='T', description='d', submitted_by=self.submitter, targeted_to=self.accused,
        )
        # Simulate a legacy (pre-v3.18.1) activity row: free text carrying
        # the submitter's real name, written back when that was still the
        # only way this feed recorded anything.
        self.entry = KaiReportActivity.objects.create(
            report=self.report, user=self.submitter, action='created',
            details=f'Report created by {self.REAL_NAME}.',
        )

    def _redact(self, **access):
        kai_access = {
            'can_view_submitter_identity': False,
            'can_view_accused_identity': False,
            **access,
        }
        entries = list(KaiReportActivity.objects.filter(pk=self.entry.pk))
        return _redact_activity_log(entries, self.report, kai_access)[0]

    def test_control_unauthorized_viewer_is_redacted_before_anonymization(self):
        entry = self._redact()
        self.assertEqual(entry.display_actor, 'Anonymous')
        self.assertNotIn(self.REAL_NAME, entry.display_details)

    def test_unauthorized_viewer_still_has_the_name_redacted_after_anonymization(self):
        self.submitter.anonymize()
        self.report.refresh_from_db()

        entry = self._redact()

        self.assertEqual(entry.display_actor, 'Anonymous')
        self.assertNotIn(self.REAL_NAME, entry.display_details,
                          'the real name leaked through the legacy details text — '
                          'the redaction search string must use the frozen snapshot')
        self.assertNotIn('Deleted User', entry.display_details,
                          'the substring swap should never have matched this text at all')

    def test_authorized_viewer_sees_the_real_historical_name_not_deleted_user(self):
        self.submitter.anonymize()
        self.report.refresh_from_db()

        entry = self._redact(can_view_submitter_identity=True)

        self.assertEqual(entry.display_actor, self.REAL_NAME)


class EndToEndCaseDetailPageTests(TestCase):
    """One client-driven check that the fix actually reaches the rendered page."""

    def setUp(self):
        self.committee = Committee.objects.create(name='Kai', code='KAI', is_kai_committee=True)
        self.submitter = make_user('e2e-sub', 'Endtoend Submitter')
        self.reviewer = make_user('e2e-rev', 'Reviewer Person')
        self.committee.members.add(self.reviewer)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.reviewer,
            can_view_report_list=True, can_view_report_details=True,
            can_view_submitter_identity=True, can_view_accused_identity=True,
        )
        self.report = KaiReport.objects.create(
            title='Distinctive End To End Title',
            description='d', submitted_by=self.submitter,
        )

    def test_authorized_reviewer_still_sees_submitter_name_after_anonymization(self):
        from django.urls import reverse

        self.submitter.anonymize()
        self.report.refresh_from_db()

        client = Client()
        client.force_login(self.reviewer)
        response = client.get(reverse('manage_kai_report', args=[self.report.id]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        self.assertIn('Endtoend Submitter', html)
