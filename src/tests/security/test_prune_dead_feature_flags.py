"""
Regression coverage for `manage.py prune_dead_feature_flags`
(`src/management/commands/prune_dead_feature_flags.py`), flagged as missing
in the 09-15-26 and 09-16-26 auto-run reports. The command was reviewed and
run by hand in both — it worked — but "I ran it once against a fresh DB"
is a functional smoke test, not a regression guard, and the property this
command exists to protect (refuse to delete a name that's been re-wired,
rather than trust its own docstring's four-month-old reasoning) is exactly
the kind of thing that should have a test watching it going forward.

Two test classes:

- `PruneDeadFeatureFlagsCommandTests` — the command end to end (dry-run vs
  `--apply`, missing rows, "nothing to prune"), using a scratch source tree
  (`override_settings(BASE_DIR=...)`) so these tests control exactly what
  `_is_read_anywhere()` finds rather than depending on the real repo's
  current content staying dead forever.
- `RealRepoCandidateNamesAreStillDeadTests` — the negative control the
  scratch-tree tests can't provide: runs `_is_read_anywhere()` against the
  ACTUAL `src/`/`templates/` tree for all four `CANDIDATE_NAMES`, so if a
  future release ever does wire one of these names to a real check, this
  fails loudly here instead of the command silently agreeing to delete a
  now-live flag the next time someone runs `--apply`.
"""
import os
import tempfile
import shutil
from pathlib import Path

from django.test import TestCase, override_settings
from django.core.management import call_command
from io import StringIO

from src.management.commands.prune_dead_feature_flags import _is_read_anywhere, CANDIDATE_NAMES
from src.models_feature_flags import FeatureFlag


def _make_scratch_tree(extra_files=None):
    """
    A throwaway directory with empty `src/` and `templates/` subdirectories
    — the two `_is_read_anywhere()` greps — plus whatever `extra_files`
    (a dict of relative-path -> content) the test wants to seed. Returns the
    root; caller is responsible for cleanup (each test below uses
    `addCleanup`).
    """
    root = Path(tempfile.mkdtemp(prefix='prune_flags_scratch_'))
    (root / 'src').mkdir()
    (root / 'templates').mkdir()
    for rel_path, content in (extra_files or {}).items():
        full = root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
    return root


class PruneDeadFeatureFlagsCommandTests(TestCase):
    def setUp(self):
        self.scratch_root = _make_scratch_tree()
        self.addCleanup(shutil.rmtree, self.scratch_root, ignore_errors=True)
        self.override = override_settings(BASE_DIR=self.scratch_root)
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.flags = {
            name: FeatureFlag.objects.create(
                name=name, display_name=name, category='core', is_enabled=True,
            )
            for name in CANDIDATE_NAMES
        }

    def _run(self, *args):
        out = StringIO()
        call_command('prune_dead_feature_flags', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_reports_but_does_not_delete(self):
        output = self._run()
        self.assertIn('WOULD DELETE', output)
        for name in CANDIDATE_NAMES:
            self.assertTrue(
                FeatureFlag.objects.filter(name=name).exists(),
                f'dry run must not delete {name!r}',
            )

    def test_apply_deletes_every_candidate_when_none_are_referenced(self):
        output = self._run('--apply')
        self.assertIn('Deleted 4 row(s)', output)
        for name in CANDIDATE_NAMES:
            self.assertFalse(
                FeatureFlag.objects.filter(name=name).exists(),
                f'--apply must delete {name!r} when nothing references it',
            )

    def test_missing_rows_are_reported_without_error(self):
        # Delete two rows up front — the command must handle "no row for
        # this name" as a normal outcome, not an exception, and still act
        # on whichever candidates do have a row.
        self.flags['calendar'].delete()
        self.flags['chat_channels'].delete()

        output = self._run('--apply')
        self.assertIn("'calendar' — no row exists, nothing to do.", output)
        self.assertIn("'chat_channels' — no row exists, nothing to do.", output)
        self.assertFalse(FeatureFlag.objects.filter(name='chapter_documents').exists())
        self.assertFalse(FeatureFlag.objects.filter(name='committee_documents').exists())

    def test_nothing_to_prune_when_no_candidate_rows_exist_at_all(self):
        FeatureFlag.objects.filter(name__in=CANDIDATE_NAMES).delete()
        output = self._run('--apply')
        self.assertIn('Nothing to prune.', output)

    def test_a_reintroduced_reference_refuses_deletion_for_that_name_only(self):
        """The property this command exists to protect: if something starts
        checking one of these names for real, `--apply` must leave that row
        alone — and must NOT let one skip stop it from still cleaning up
        the others that genuinely aren't referenced."""
        (self.scratch_root / 'src' / 'some_view.py').write_text(
            "if check_feature_enabled('calendar'):\n    pass\n"
        )

        output = self._run('--apply')
        self.assertIn("SKIP 'calendar'", output)
        self.assertTrue(
            FeatureFlag.objects.filter(name='calendar').exists(),
            "a name found referenced in source must not be deleted",
        )
        # The other three had no reference added and must still go.
        for name in ('chapter_documents', 'committee_documents', 'chat_channels'):
            self.assertFalse(FeatureFlag.objects.filter(name=name).exists())

    def test_each_of_the_four_detection_shapes_is_recognised(self):
        """`_is_read_anywhere()` checks four distinct call shapes — cover
        each one directly so a future refactor of the regex can't silently
        narrow it to three without a test noticing."""
        shapes = {
            'calendar': "is_feature_enabled('calendar')",
            'chapter_documents': 'check_feature_enabled("chapter_documents")',
            'committee_documents': "@require_feature_flag('committee_documents')",
            'chat_channels': 'feature_flags.chat_channels',
        }
        for name, snippet in shapes.items():
            with self.subTest(name=name):
                root = _make_scratch_tree({'templates/x.html': snippet})
                self.addCleanup(shutil.rmtree, root, ignore_errors=True)
                self.assertTrue(
                    _is_read_anywhere(name, root),
                    f'{snippet!r} should have been recognised as a live reference to {name!r}',
                )

    def test_a_compiled_pycache_file_is_not_treated_as_a_reference(self):
        """Found 09-16-26: this very test file's own fixture strings (in
        `test_each_of_the_four_detection_shapes_is_recognised` below) are
        real source-level references to all four call shapes, and Python
        compiles them into `__pycache__/test_prune_dead_feature_flags.
        cpython-3xx.pyc` the moment this module is imported. A `.pyc`
        embeds a source file's string literals as bytecode constants, so
        grep matches the compiled file too — under a filename
        (`test_prune_dead_feature_flags.cpython-310.pyc`) that does NOT
        contain the literal substring `prune_dead_feature_flags.py`, so the
        existing self-file exclusion below never catches it. `_is_read_
        anywhere()` now passes `--exclude-dir=__pycache__` to grep so a
        compiled cache — of ANY file, not just this one — never counts as
        a live reference. Reproduce directly: a `.pyc`-named file sitting
        in a real `__pycache__` directory, containing bytes that match one
        of the four call shapes, must not flip the result."""
        root = _make_scratch_tree()
        pycache_dir = root / 'src' / '__pycache__'
        pycache_dir.mkdir()
        (pycache_dir / 'whatever.cpython-310.pyc').write_bytes(
            b"is_feature_enabled('calendar')"
        )
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.assertFalse(
            _is_read_anywhere('calendar', root),
            'a match inside __pycache__ must not count as a live reference '
            '— it is a compiled artifact, not authored source',
        )
        # Control: the same bytes in a real, non-pycache file still count.
        (root / 'src' / 'not_cached.py').write_text("is_feature_enabled('calendar')\n")
        self.assertTrue(
            _is_read_anywhere('calendar', root),
            'the exclusion must be scoped to __pycache__ specifically, not '
            'grep matches in general',
        )

    def test_the_commands_own_file_and_seed_admin_v2_are_excluded_from_hits(self):
        """Both files legitimately mention these names in prose/history —
        without the exclusion, the command would refuse to delete anything,
        forever, because it would always find itself."""
        root = _make_scratch_tree({
            'src/management/commands/prune_dead_feature_flags.py':
                "CANDIDATE_NAMES = ['calendar']\n# is_feature_enabled('calendar')\n",
            'src/seed_admin_v2.py':
                "# historically: is_feature_enabled('calendar')\n",
        })
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.assertFalse(
            _is_read_anywhere('calendar', root),
            "matches inside prune_dead_feature_flags.py or seed_admin_v2.py "
            "must not count as a live reference",
        )


class RealRepoCandidateNamesAreStillDeadTests(TestCase):
    """
    Negative control against the ACTUAL repository, not a scratch tree —
    the thing the scratch-tree tests above cannot check. If this ever goes
    red, it means one of the four `CANDIDATE_NAMES` has been wired to a
    real check somewhere in `src/` or `templates/` since this test was
    written, and `prune_dead_feature_flags --apply` would now be deleting a
    live flag's row. That is real information for whoever is touching this
    command next, not a bug in the test.
    """

    def test_no_candidate_name_is_referenced_in_the_real_tree(self):
        from django.conf import settings

        for name in CANDIDATE_NAMES:
            with self.subTest(name=name):
                self.assertFalse(
                    _is_read_anywhere(name, settings.BASE_DIR),
                    f'{name!r} is now referenced somewhere in src/ or '
                    f'templates/ — prune_dead_feature_flags.py\'s reasoning '
                    f'for treating it as dead needs re-checking before this '
                    f'command is run with --apply again.',
                )
