"""
Every Kai CSV export redacts against the same permissions.

WHAT WENT WRONG (found 07-31-26, fixed v3.17.7)
-----------------------------------------------
There were two exports writing the same thirteen columns of Kai report data:

  * `export_kai_reports_csv`      — `src/view/kai_reports.py`, ~line 590
  * `bulk_actions_kai_reports`    — same file, ~line 1,700, the
                                    `action == 'export_csv'` branch

v3.16.2 established the admin-confidentiality boundary and, as part of it, added
per-permission redaction to the FIRST one. Its comment is still in the file and
says exactly why:

    # v3.16.2: the allegation body is governed by can_view_report_details,
    # but this export only gates on can_view_report_list — a list-only
    # reviewer could dump every description via CSV.

**Nobody looked 1,100 lines down.** The bulk branch wrote `report.submitted_by`,
`report.targeted_to` and `report.description` raw, gated on nothing narrower
than `can_view_report_list`. And it was not an obscure endpoint —
`templates/kai/view_reports.html` renders the bulk-action `<select>` with no
permission guard at all, so "Export CSV" was a menu item for anyone who could
load the report list. A list-only reviewer, whom the in-app detail view refuses
the allegation body, could select all and download it.

THE PART WORTH REMEMBERING
--------------------------
**When a control is applied to one view, grep for the other views that write the
same columns.** This codebase has now paid for that four times: v3.16.2's
admin/CSV pair, v3.16.3's list-filter/export pair, v3.17.5's four separate sites
of the vote-COUNT pattern, and this. Two copies of a redaction rule is one copy
too many.

So the fix is not "add the conditionals to the second one" — that just makes two
copies that currently agree. There is now one `KAI_CSV_HEADERS` and one
`_kai_csv_row()`, and `test_no_export_builds_its_own_row` below fails if a third
`csv.writer` appears in the module without going through them.
"""

import inspect
import re

from django.test import Client, TestCase
from django.urls import reverse

from src.models import Committee, ParliamentUser
from src.models.kai import KaiMemberPermission, KaiReport
from src.view import kai_reports as kai_module
from src.view.kai_reports import KAI_CSV_HEADERS, _kai_csv_row

SUBMITTER_NAME = 'Submitter Sam'
ACCUSED_NAME = 'Accused Alex'
ALLEGATION_BODY = 'CONFIDENTIAL-ALLEGATION-BODY-DO-NOT-LEAK'


def make_user(uid, name=None, member_type='Member', is_admin=False):
    user = ParliamentUser.objects.create(
        user_id=uid, name=name or f'User {uid}', username=uid,
        member_type=member_type, member_status='Active', is_admin=is_admin,
    )
    user.set_password('kai-csv-test-pass-12345!')
    user.save()
    return user


class KaiCsvRedactionTestCase(TestCase):
    """Shared fixture: one report, one Kai committee, graded reviewers."""

    def setUp(self):
        self.committee = Committee.objects.create(
            name='Kai', code='KAI', is_kai_committee=True,
        )
        self.submitter = make_user('kai-sub', SUBMITTER_NAME)
        self.accused = make_user('kai-acc', ACCUSED_NAME)
        self.report = KaiReport.objects.create(
            title='Test allegation',
            description=ALLEGATION_BODY,
            submitted_by=self.submitter,
            targeted_to=self.accused,
        )

    def _reviewer(self, uid, **perms):
        """A Kai member holding exactly the permissions named."""
        user = make_user(uid)
        self.committee.members.add(user)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=user,
            can_view_report_list=True, **perms,
        )
        return user

    def _bulk_export(self, user):
        client = Client()
        client.force_login(user)
        return client.post(reverse('bulk_actions_kai_reports'), {
            'report_ids': [str(self.report.id)],
            'bulk_action': 'export_csv',
        })

    def _plain_export(self, user):
        client = Client()
        client.force_login(user)
        return client.get(reverse('export_kai_reports_csv'))

    @staticmethod
    def _body(response):
        return b''.join(response.streaming_content).decode() \
            if response.streaming else response.content.decode()


class BulkExportRedactsLikeItsSiblingTests(KaiCsvRedactionTestCase):
    """
    The 🔴 itself. Each assertion below FAILS against the pre-v3.17.7 code —
    verified by restoring the old branch and re-running, which is the only way
    to know a regression test tests anything.
    """

    def test_list_only_reviewer_gets_no_allegation_body(self):
        user = self._reviewer('kai-list-only')
        body = self._body(self._bulk_export(user))
        self.assertNotIn(ALLEGATION_BODY, body)
        self.assertIn('[Redacted]', body)

    def test_list_only_reviewer_gets_no_submitter_identity(self):
        user = self._reviewer('kai-list-only-2')
        self.assertNotIn(SUBMITTER_NAME, self._body(self._bulk_export(user)))

    def test_list_only_reviewer_gets_no_accused_identity(self):
        user = self._reviewer('kai-list-only-3')
        self.assertNotIn(ACCUSED_NAME, self._body(self._bulk_export(user)))

    def test_details_permission_reveals_only_the_body(self):
        """Each flag is independent — granting one must not grant the others."""
        user = self._reviewer('kai-details', can_view_report_details=True)
        body = self._body(self._bulk_export(user))
        self.assertIn(ALLEGATION_BODY, body)
        self.assertNotIn(SUBMITTER_NAME, body)
        self.assertNotIn(ACCUSED_NAME, body)

    def test_full_access_chair_sees_everything(self):
        """The redaction must not break the people it does not apply to."""
        admin = make_user('kai-admin', is_admin=True)
        body = self._body(self._bulk_export(admin))
        self.assertIn(ALLEGATION_BODY, body)
        self.assertIn(SUBMITTER_NAME, body)
        self.assertIn(ACCUSED_NAME, body)

    def test_no_kai_permission_row_is_refused_outright(self):
        outsider = make_user('kai-outsider')
        response = self._bulk_export(outsider)
        self.assertNotIn(ALLEGATION_BODY, self._body(response))

    def test_the_two_exports_redact_identically(self):
        """
        The property the shared row builder exists to guarantee. Compare the
        two exports for the same user rather than asserting each separately —
        drift between them is the bug, and only a comparison can see it.
        """
        user = self._reviewer('kai-both')
        bulk = self._body(self._bulk_export(user))
        plain = self._body(self._plain_export(user))
        for sensitive in (ALLEGATION_BODY, SUBMITTER_NAME, ACCUSED_NAME):
            self.assertEqual(
                sensitive in bulk, sensitive in plain,
                f'{sensitive!r} appears in one Kai export but not the other — '
                f'the redaction rules have drifted apart again',
            )


class KaiCsvRowBuilderTests(KaiCsvRedactionTestCase):
    """Unit-level checks on the shared builder, independent of any view."""

    def test_row_length_matches_the_header(self):
        access = {f: True for f in (
            'can_view_report_details', 'can_view_submitter_identity',
            'can_view_accused_identity',
        )}
        row = _kai_csv_row(self.report, access)
        self.assertEqual(
            len(row), len(KAI_CSV_HEADERS),
            'a column was added to one of the header/row pair and not the other',
        )

    def test_every_sensitive_field_has_a_redacted_form(self):
        denied = {f: False for f in (
            'can_view_report_details', 'can_view_submitter_identity',
            'can_view_accused_identity',
        )}
        row = _kai_csv_row(self.report, denied)
        self.assertEqual(row.count('[Redacted]'), 3)


class NoExportBuildsItsOwnRowTests(TestCase):
    """
    The structural guard, and the one that would actually have caught this.

    The behavioural tests above only cover the two exports that exist today. A
    third `csv.writer` added to this module a year from now would be invisible
    to them — which is precisely how the second one got written.
    """

    def test_no_export_builds_its_own_row(self):
        source = inspect.getsource(kai_module)

        # A writerow() whose argument is an inline list literal is a hand-built
        # row. The two legitimate calls pass KAI_CSV_HEADERS or _kai_csv_row(...).
        inline = re.findall(r'writerow\(\s*\[', source)
        self.assertEqual(
            inline, [],
            'kai_reports.py builds a CSV row from an inline list. Use '
            'KAI_CSV_HEADERS and _kai_csv_row() so the redaction rule has one '
            'definition — see the module docstring in this file for why.',
        )

    def test_the_sensitive_fields_are_only_read_in_the_row_builder(self):
        """
        `report.description` outside `_kai_csv_row` is not automatically wrong —
        the detail view reads it legitimately — but inside a csv-writing
        function it always is. Assert the CSV paths go through the builder.
        """
        source = inspect.getsource(kai_module)
        for func_name in ('export_kai_reports_csv', 'bulk_actions_kai_reports'):
            func_src = inspect.getsource(getattr(kai_module, func_name))
            if 'csv.writer' not in func_src:
                continue
            self.assertIn(
                '_kai_csv_row', func_src,
                f'{func_name} writes a CSV without using the shared, '
                f'permission-redacting row builder',
            )
        self.assertIn('def _kai_csv_row', source)
