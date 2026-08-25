"""
v3.25.2 — tests for `manage.py scrub_action_logs`.

⚠️ WRITTEN AT THE SAME TIME AS THE COMMAND, WHICH IS THE POINT.

v3.25.1's whole finding was that `scripts/stamp_ledger.py` — the tool that
exists to satisfy a check — had no tests, and went four months half-working
because every release had its row added by hand by someone who did not notice
they were doing the tool's job. This command is in the same position: it is the
remedy the v3.25.2 changelog points at for the log files already on the server,
and if it quietly does half its job nobody will find out until the next audit.

⚠️ It rewrites files in place, so every test here works on a temporary
directory and none of them touches `logs/`.
"""
import os
import tempfile
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from src.models import ParliamentUser

PASSWORD = 'scrub-action-logs-pass-13579!'

TIMESTAMP = '2026-08-24 10:00:0'

#: One line of each shape the redactor knows about, plus two that must survive.
LINES = [
    f"{TIMESTAMP}0,000 [INFO] function_calls: User reporter_guy called "
    "submit_kai_report with arguments: (), {}, Action: No specific action",

    f"{TIMESTAMP}1,000 [INFO] function_calls: reporter_guy requested closure "
    "for Kai report 'Conduct at the Feb 14 formal' (ID: 12)",

    f'{TIMESTAMP}2,000 [INFO] function_calls: [SUCCESS] | User: System '
    '(unknown) | Action: CREATE | Resource: KaiReport | ID: 1 | Details: '
    '{"model": "KaiReport", "instance_id": "1", "title": "A case title"}',

    f"{TIMESTAMP}3,000 [INFO] function_calls: User nosy_officer called "
    "event_attendance_list with arguments: (), {}, Action: No specific action",

    f'{TIMESTAMP}4,000 [INFO] function_calls: [SUCCESS] | User: nosy_officer '
    '(X) | Action: CREATE | Resource: Announcement | ID: 4 | Details: '
    '{"model": "Announcement", "title": "Chapter meeting moved"}',
]


class ScrubActionLogsTests(TestCase):

    def setUp(self):
        for user_id, username in (('P-SCR001', 'reporter_guy'),
                                  ('SCR-OFF', 'nosy_officer')):
            ParliamentUser.objects.create_user(
                user_id=user_id, password=PASSWORD, name=username,
                username=username, member_type='Member')

        self._dir = tempfile.TemporaryDirectory()
        self.log_dir = self._dir.name
        self.path = os.path.join(self.log_dir, 'django_actions.log')
        self._write(LINES)

    def tearDown(self):
        self._dir.cleanup()

    def _write(self, lines):
        with open(self.path, 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(lines) + '\n')

    def _read(self):
        with open(self.path, encoding='utf-8') as handle:
            return handle.read()

    def _run(self, *args):
        out = StringIO()
        call_command('scrub_action_logs', '--log-dir', self.log_dir, *args,
                     stdout=out, stderr=out)
        return out.getvalue()

    # --- dry run ------------------------------------------------------------

    def test_a_dry_run_reports_and_changes_nothing(self):
        before = self._read()
        output = self._run()
        self.assertIn('would redact 3', output)
        self.assertEqual(self._read(), before)

    def test_the_dry_run_says_how_to_apply(self):
        self.assertIn('--apply', self._run())

    # --- applying -----------------------------------------------------------

    def test_it_removes_the_party_identities_and_the_case_content(self):
        self._run('--apply')
        scrubbed = self._read()
        self.assertNotIn('reporter_guy', scrubbed)
        self.assertNotIn('Feb 14 formal', scrubbed)
        self.assertNotIn('A case title', scrubbed)

    def test_it_leaves_the_non_kai_lines_exactly_alone(self):
        """
        ⚠️ THE CONTROL, and the reason this command is not `rm`. A scrub that
        emptied the log would satisfy every assertion above.
        """
        self._run('--apply')
        scrubbed = self._read()
        self.assertIn('nosy_officer', scrubbed)
        self.assertIn('event_attendance_list', scrubbed)
        self.assertIn('Chapter meeting moved', scrubbed)

    def test_it_keeps_the_operational_content_of_the_lines_it_changes(self):
        self._run('--apply')
        scrubbed = self._read()
        self.assertIn('submit_kai_report', scrubbed)
        self.assertIn('"model": "KaiReport"', scrubbed)
        self.assertIn('(ID: 12)', scrubbed)

    def test_every_line_survives(self):
        """A rewrite that dropped lines would be data loss wearing a fix."""
        self._run('--apply')
        self.assertEqual(len(self._read().splitlines()), len(LINES))

    def test_it_is_idempotent(self):
        self._run('--apply')
        once = self._read()
        output = self._run('--apply')
        self.assertEqual(self._read(), once)
        self.assertIn('redacted 0', output)

    # --- the file itself ----------------------------------------------------

    def test_it_keeps_the_inode_so_a_running_process_keeps_writing(self):
        """
        ⚠️ THE REASON IT USES `r+` AND `truncate()` RATHER THAN A RENAME.
        Daphne holds an open descriptor on this file. Replacing it would leave
        the app appending to an unlinked inode — invisible, and the scrubbed
        copy would stop growing.
        """
        before = os.stat(self.path).st_ino
        self._run('--apply')
        self.assertEqual(os.stat(self.path).st_ino, before)

    def test_it_scrubs_the_rotated_backups_too(self):
        backup = os.path.join(self.log_dir, 'django_actions.log.1')
        with open(backup, 'w', encoding='utf-8') as handle:
            handle.write(LINES[1] + '\n')
        self._run('--apply')
        with open(backup, encoding='utf-8') as handle:
            self.assertNotIn('reporter_guy', handle.read())

    def test_backup_writes_a_restricted_copy(self):
        self._run('--apply', '--backup')
        copy = f'{self.path}.prescrub'
        self.assertTrue(os.path.exists(copy))
        self.assertEqual(os.stat(copy).st_mode & 0o777, 0o600)
        with open(copy, encoding='utf-8') as handle:
            self.assertIn('reporter_guy', handle.read(),
                          'the point of the backup is that it is the original')

    def test_no_backup_is_written_by_default(self):
        self._run('--apply')
        self.assertFalse(os.path.exists(f'{self.path}.prescrub'))

    # --- edges --------------------------------------------------------------

    def test_it_says_so_when_nothing_matches(self):
        with tempfile.TemporaryDirectory() as empty:
            out = StringIO()
            call_command('scrub_action_logs', '--log-dir', empty, stdout=out,
                         stderr=out)
            self.assertIn('No files matched', out.getvalue())

    def test_it_refuses_rather_than_half_scrubbing_without_a_member_list(self):
        """
        ⚠️ `WITHHELD` is the right answer for a page being rendered now and the
        wrong one to write permanently into a file, so the command refuses
        instead. A tool that resolves one of the two things it is asked to fix
        has moved the failure — v3.25.1.
        """
        from unittest import mock

        with mock.patch('src.kai_audit.member_usernames', return_value=None):
            with self.assertRaises(SystemExit):
                self._run('--apply')
        self.assertIn('reporter_guy', self._read())

    def test_the_preview_windows_on_the_difference(self):
        """
        Every line starts with a timestamp and a logger name, so a head-anchored
        preview shows two identical prefixes and tells the reader nothing. The
        first draft did exactly that.
        """
        output = self._run()
        self.assertIn('Redacted', output)


class FirstDifferenceTests(TestCase):

    def test_it_finds_the_index_where_two_strings_diverge(self):
        from src.management.commands.scrub_action_logs import _first_difference

        self.assertEqual(_first_difference('abcdef', 'abcXef'), 3)

    def test_identical_strings_report_zero(self):
        from src.management.commands.scrub_action_logs import _first_difference

        self.assertEqual(_first_difference('abc', 'abc'), 0)

    def test_a_pure_truncation_reports_the_shorter_length(self):
        from src.management.commands.scrub_action_logs import _first_difference

        self.assertEqual(_first_difference('abcdef', 'abc'), 3)
