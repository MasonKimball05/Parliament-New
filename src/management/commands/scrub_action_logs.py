"""
v3.25.2 — remove Kai identities and case content from `django_actions.log*`.

⚠️ WHY SCRUBBING AND NOT JUST ROTATING.

Rotating renames. `RotatingFileHandler` here keeps `backupCount = 3`, so one
rotation moves the current file to `.log.1` and the lines are still on disk —
and `/officers/system-logs/` only ever reads the live file, so a rotation
*would* clear the page while leaving the content exactly where it was. Both
halves are worth doing, and this command does the half rotation cannot: it
rewrites every line through the same `redact_kai_log_message` the log viewer
uses, so the files and the page agree.

⚠️ IT TRUNCATES IN PLACE RATHER THAN REPLACING THE FILE.
On the server Daphne holds an open descriptor on `django_actions.log`. Writing a
new file and renaming it over the old one leaves that descriptor pointing at the
unlinked original, so the app goes on appending to a file nobody can see and the
scrubbed copy stops growing. Opening with `r+`, truncating and rewriting keeps
the inode. There is a moment mid-write when a concurrent append can interleave;
that is a garbled line, not a lost one, and it is the price of not breaking the
running process.

⚠️ DRY RUN BY DEFAULT. `--apply` writes.

    python manage.py scrub_action_logs                  # report only
    python manage.py scrub_action_logs --apply          # rewrite in place
    python manage.py scrub_action_logs --apply --backup # keep a .prescrub copy

⚠️ `--backup` KEEPS THE UNREDACTED CONTENT. It exists for the first run on a
server, where being able to undo matters more than the disclosure does for an
hour — the copy is written `0600` next to the log. **Delete it once you are
satisfied**, because a `.prescrub` file is the thing this command was run to get
rid of, sitting in the directory it was removed from.
"""
import glob
import os
import stat

from django.conf import settings
from django.core.management.base import BaseCommand


def _first_difference(before, after):
    """Index of the first character that differs, or 0 if they are identical."""
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right:
            return index
    return min(len(before), len(after)) if before != after else 0


class Command(BaseCommand):
    help = 'Redact Kai identities and case content from the action logs.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Actually rewrite the files. Without this, report only.')
        parser.add_argument(
            '--backup', action='store_true',
            help='Write a 0600 .prescrub copy before rewriting. Delete it after.')
        parser.add_argument(
            '--log-dir', default=None,
            help='Directory to scan. Defaults to the configured LOG_DIR.')
        parser.add_argument(
            '--pattern', default='django_actions.log*',
            help='Glob within the log directory.')

    def handle(self, *args, **options):
        from src.kai_audit import (_username_pattern, kai_log_view_names,
                                   member_usernames, redact_kai_log_message)

        log_dir = options['log_dir'] or os.path.join(
            settings.BASE_DIR, os.getenv('LOG_DIR', 'logs'))
        paths = sorted(glob.glob(os.path.join(log_dir, options['pattern'])))
        if not paths:
            self.stdout.write(self.style.WARNING(
                f'No files matched {options["pattern"]} in {log_dir}'))
            return

        view_names = kai_log_view_names()
        usernames = member_usernames()
        if usernames is None:
            # Refuse rather than withhold: `WITHHELD` is the right answer for a
            # page being rendered now, and the wrong one to write permanently
            # into a file in place of content that may not need redacting.
            self.stderr.write(self.style.ERROR(
                'The member list could not be read, so usernames cannot be '
                'scrubbed. Refusing to rewrite anything.'))
            raise SystemExit(1)
        pattern = _username_pattern(usernames)

        total_lines = total_changed = 0
        for path in paths:
            changed, lines, preview = self._scrub(
                path, view_names, pattern, redact_kai_log_message,
                apply=options['apply'], backup=options['backup'])
            total_lines += lines
            total_changed += changed
            flag = '' if changed else '  (clean)'
            self.stdout.write(
                f'{os.path.basename(path):<32} {lines:>7} lines, '
                f'{changed:>5} redacted{flag}')
            for before, after in preview:
                # ⚠️ Window on the FIRST DIFFERENCE, not on the head of the
                # line. These lines start with a timestamp and a logger name,
                # so a head-anchored preview shows two identical prefixes and
                # tells the reader nothing about what the command did.
                start = max(0, _first_difference(before, after) - 30)
                lead = '…' if start else ''
                self.stdout.write(f'    - {lead}{before[start:start + 110]}')
                self.stdout.write(f'    + {lead}{after[start:start + 110]}')

        verb = 'redacted' if options['apply'] else 'would redact'
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{len(paths)} file(s), {total_lines} lines, {verb} {total_changed}.'))
        if total_changed and not options['apply']:
            self.stdout.write('Re-run with --apply to write the changes.')

    def _scrub(self, path, view_names, pattern, redact, *, apply, backup):
        with open(path, encoding='utf-8', errors='replace') as handle:
            original = handle.readlines()

        changed, preview, out = 0, [], []
        for line in original:
            stripped = line.rstrip('\n')
            scrubbed = redact(stripped, view_names, pattern)
            if scrubbed != stripped:
                changed += 1
                if len(preview) < 2:
                    preview.append((stripped, scrubbed))
            out.append(scrubbed + '\n' if line.endswith('\n') else scrubbed)

        if apply and changed:
            if backup:
                copy = f'{path}.prescrub'
                with open(copy, 'w', encoding='utf-8') as handle:
                    handle.writelines(original)
                os.chmod(copy, stat.S_IRUSR | stat.S_IWUSR)
            # ⚠️ `r+` then truncate: keeps the inode, so a running Daphne's open
            # descriptor still points at this file. See the module docstring.
            with open(path, 'r+', encoding='utf-8') as handle:
                handle.seek(0)
                handle.writelines(out)
                handle.truncate()

        return changed, len(original), preview
