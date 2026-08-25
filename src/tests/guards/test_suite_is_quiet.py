"""
v3.21.4 — the test suite does not print.

⚠️ WHY. `scripts/pre-push.sh` runs the suite on every push and tees the output,
so anything a test writes to stdout lands in the push log. There were **43**
such writes — vote tallies, Kai fixture chatter, feature-flag dumps — plus a
management command whose stdout was not captured, which printed a fixture
version (`v9.9.9`) and a paste-ready ledger line that read exactly like a real
finding about a real release.

Two costs, and the second is the one that matters:

1. Hundreds of lines of noise per push.
2. **A gate whose output nobody reads is a gate nobody reads.** v3.19.9 already
   had to make the hook re-print the verdict lines at the end, because a real
   `FAIL:` was scrolling past behind exactly this chatter and the first failure
   it ever reported was misdiagnosed as a vote bug. That fix treated the
   symptom; this removes the cause.

A test that needs to explain itself should do it in an assertion message, where
it appears only when it fails, or in a docstring, where it appears in `-v 2`.
Neither costs anything on a green run.
"""

import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


def _test_modules():
    root = Path(settings.BASE_DIR) / 'src'
    return sorted(
        p for p in root.rglob('*.py')
        if p.name.startswith('test_')
        and 'migrations' not in p.parts
    )


def _print_calls(path):
    """`(lineno, …)` for every bare `print(...)` statement in a module."""
    try:
        tree = ast.parse(path.read_text(encoding='utf-8'))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    found = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == 'print'):
            found.append(node.lineno)
    return found


class TheSuiteDoesNotPrintTests(SimpleTestCase):

    def test_the_scan_sees_the_test_modules(self):
        """
        The control. A scan that matches no files passes the real assertion
        vacuously — and "the suite is quiet" is exactly the claim that would go
        unnoticed if it were being made about nothing.
        """
        modules = _test_modules()
        self.assertGreater(len(modules), 40)
        names = {p.name for p in modules}
        self.assertIn('test_hardcoded_urls.py', names)

    def test_no_test_module_prints_to_stdout(self):
        offenders = []
        for path in _test_modules():
            relative = path.relative_to(settings.BASE_DIR)
            for lineno in _print_calls(path):
                offenders.append(f'{relative}:{lineno}')

        self.assertEqual(
            offenders, [],
            f'{len(offenders)} print() call(s) in the test suite. Every one goes '
            f'into the push log, where it buries the failures the hook exists to '
            f'show:\n  ' + '\n  '.join(offenders)
            + '\n\nPut the explanation in the assertion message (it appears when '
              'it fails) or the docstring (it appears under -v 2).',
        )


class ManagementCommandOutputIsCapturedTests(SimpleTestCase):
    """
    ⚠️ The subtler half. A management command instantiated directly in a test
    writes to the real `sys.stdout` unless something says otherwise — which is
    how `v9.9.9` and a paste-ready ledger row ended up in a push log looking
    like a genuine warning about a genuine release.

    Narrow on purpose: this asserts the pattern in the module where it bit,
    rather than trying to detect every possible command invocation. A broader
    rule here would be guesswork.
    """

    def test_ledger_check_captures_preflight_stdout(self):
        body = (
            Path(settings.BASE_DIR) / 'src' / 'tests' / 'infra' / 'test_ledger_check.py'
        ).read_text(encoding='utf-8')

        # Every direct `Command()` in this module must have its stdout replaced
        # before any check runs.
        constructions = body.count('command = Command()')
        captures = body.count('command.stdout =')
        self.assertEqual(
            constructions, captures,
            f'{constructions} direct Command() instantiations but {captures} '
            f'stdout captures. An uncaptured one prints its verdict into every '
            f'push log.',
        )
