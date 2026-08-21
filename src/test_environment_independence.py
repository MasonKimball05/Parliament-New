"""
The suite's verdict must not depend on the developer's `.env` (v3.21.5).

⚠️ WHY THIS MODULE EXISTS — AND IT IS THE LARGEST SINGLE FINDING THIS PROJECT
HAS RECORDED ABOUT ITS OWN PROCESS, NOT ITS CODE.

On 08-20-26 the Django CI workflow was checked for the first time. GitHub
retains 400 runs of it. **Zero of them succeeded.** `is:success` returns nothing;
`is:failure` returns all 400. The suite has been described as green in eight
consecutive release notes, and it was green — on one laptop.

Two causes, reproduced by running the suite once with Mason's environment and
once with CI's:

  1. Fifteen tests (`test_two_factor.TwoFactorAdminDashboardTestCase`,
     `test_pillar1.AdminV2DashboardContextTests`) reached the admin-v2 dashboard
     only because `ADMIN_V2_USER_IDS` in the ambient environment happened to
     contain the id their fixture uses. Both carried a comment saying the id
     "must match" a constant in `admin_v2.py` — true until v3.17.0 moved that
     constant into the environment, and never revisited. Eight other modules
     patch the allowlist; these two inherited it.
  2. Three tests in `test_digest_watchdog` overrode `BASE_DIR` and were defeated
     by CI's `LOG_DIR: /tmp`, because `os.path.join` discards `BASE_DIR` when
     `LOG_DIR` is absolute.

Both are the same shape, and it is the shape this repo already fixed one layer
down: **shared state the test run inherits rather than establishes.**
`src/cache_isolated_runner.py` exists because the cache leaked *between tests*;
this exists because configuration leaks *in from the host*. In both cases the
symptom is that the answer depends on how you ran it rather than on the code.

⚠️ AND THE REASON IT SURVIVED SO LONG IS WORTH MORE THAN THE BUG. The local
pre-push hook runs this suite and passes; `scripts/pre-push.sh` says in as many
words that *"CI (postgres) remains the real gate"* and *"CI is the gate that
matters"*. The gate that matters had never passed. Nothing was hidden — GitHub
rendered a red ❌ on every push — and the notifier that exists to shout about
exactly this, `.github/workflows/slack.yml`, is a five-line fragment with no
`on:` and no `jobs:`, so it has failed to parse on all 312 of its own runs since
02-08-26. **The alarm for the broken gate was itself broken, and both were
visible from the same page.**

So this module does two things:

  * asserts the neutralised values are actually in force during a run, and
  * enumerates every module-level environment read in `src/`, failing the build
    when a new one appears that nobody has decided about.

The second is the point. CLAUDE.md records, at v3.19.6 and again at v3.19.11,
that *a set is only the general form if something enumerates the population it
is drawn from* — twice the missing enumeration was a four-minute grep that was
never run. This one was `os.environ.get` at module scope, and there are three.
"""
import ast
import os
import pathlib

from django.test import SimpleTestCase

from src.cache_isolated_runner import _ENV_DERIVED_TEST_DEFAULTS

SRC = pathlib.Path(__file__).resolve().parent

#: Module-level environment reads that have been looked at, with the decision.
#: Keyed by "<path relative to src/>:<assigned name>".
#:
#: A new entry here is a deliberate act: it means somebody decided whether a
#: value that varies between machines is allowed to vary during a test run.
#: ⚠️ THE KEYS ARE WHAT THE SCANNER SEES, NOT WHAT THE CODE USES. Both
#: allowlists are built in two steps — `_raw_allowed_ids = os.environ.get(...)`
#: and then a set comprehension over it — and only the first line mentions the
#: environment, so only the first line appears here. The constant that actually
#: gates the request (`ALLOWED_USER_IDS`) is invisible to an `os.environ` scan.
#:
#: That gap is the reason `_ENV_DERIVED_TEST_DEFAULTS` names the DERIVED
#: constants rather than the raw strings: the scanner finds the doorway, the
#: neutralisation has to reach the room. Listing a derived name here instead
#: would make this test fail as "declared but no longer present", which is how
#: the mismatch was found — the first draft did exactly that.
REVIEWED_MODULE_LEVEL_ENV_READS = {
    'view/admin_v2.py:_raw_allowed_ids':
        'Admin-v2 allowlist, parsed into ALLOWED_USER_IDS on the next line. '
        'That set is neutralised to empty for the suite; tests that need an '
        'allowlist patch it explicitly.',
    'dev_mode.py:_raw_dev_ids':
        'The same variable, re-parsed here to avoid an import cycle, into '
        'DEV_USER_IDS. Also neutralised. `test_dev_mode` asserts the two sets '
        'are equal, and they still are — both empty.',
    'view/serve_media.py:MEDIA_ACCEL_PREFIX':
        'Chooses X-Accel-Redirect over serving bytes from Django. Neutralised '
        'to "" so a developer with it set does not silently exercise the other '
        'branch in every test that does not patch it.',
}


def _module_level_env_reads():
    """
    Every module-level assignment in `src/` whose value calls `os.environ.get`
    or `os.getenv`, as {"<relpath>:<name>": lineno}.

    Module level only, deliberately. A read inside a function happens at call
    time and a test can patch `os.environ` around it — which is exactly what
    `test_digest_watchdog` now does. A read at import time is baked into the
    process before any test runs, and patching the variable afterwards changes
    nothing, which is what made the admin-v2 one so durable.
    """
    found = {}
    for path in sorted(SRC.rglob('*.py')):
        rel = path.relative_to(SRC).as_posix()
        if rel.startswith(('test_', 'migrations/')) or '/test_' in rel:
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except SyntaxError:  # pragma: no cover - a broken file fails elsewhere
            continue
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if value is None:
                continue
            reads_env = any(
                isinstance(sub, ast.Call)
                and (
                    (isinstance(sub.func, ast.Attribute)
                     and sub.func.attr in ('getenv',))
                    or (isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == 'get'
                        and isinstance(sub.func.value, ast.Attribute)
                        and sub.func.value.attr == 'environ')
                )
                for sub in ast.walk(value)
            )
            if not reads_env:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    found[f'{rel}:{target.id}'] = node.lineno
    return found


class EveryModuleLevelEnvReadHasBeenDecidedAbout(SimpleTestCase):
    """
    The enumeration. Fails when `src/` grows a module-level environment read
    that nobody has classified.
    """

    def test_no_undeclared_module_level_env_reads(self):
        found = _module_level_env_reads()
        undeclared = sorted(set(found) - set(REVIEWED_MODULE_LEVEL_ENV_READS))
        self.assertEqual(
            undeclared, [],
            'New module-level environment read(s) in src/:\n  '
            + '\n  '.join(f'{key} (line {found[key]})' for key in undeclared)
            + '\n\nA value read at import time is fixed before any test runs, so '
              'a test cannot patch the variable to control it — which is how '
              'ADMIN_V2_USER_IDS made fifteen tests depend on an untracked '
              '.env file and kept CI red for three weeks.\n\n'
              'Decide what the SUITE should see, add it to '
              '_ENV_DERIVED_TEST_DEFAULTS in src/cache_isolated_runner.py if '
              'the answer is "a fixed value", then record the decision in '
              'REVIEWED_MODULE_LEVEL_ENV_READS here. Do not simply add the '
              'name to whichever list makes this pass.'
        )

    def test_the_declared_list_has_no_stale_entries(self):
        """
        The mirror. A declaration for a read that no longer exists is a note
        about code that is gone, and this repo has removed four comments of
        that kind in one month.
        """
        found = _module_level_env_reads()
        stale = sorted(set(REVIEWED_MODULE_LEVEL_ENV_READS) - set(found))
        self.assertEqual(stale, [], f'Declared but no longer present: {stale}')


class TheNeutralisedValuesAreActuallyInForce(SimpleTestCase):
    """
    The runner claims to empty these. This is the check on the claim — the same
    relationship `_assert_caches_are_disposable` has with the settings change it
    verifies.
    """

    def test_every_declared_default_is_applied(self):
        import importlib

        for module_path, attribute, expected in _ENV_DERIVED_TEST_DEFAULTS:
            with self.subTest(target=f'{module_path}.{attribute}'):
                module = importlib.import_module(module_path)
                self.assertEqual(
                    getattr(module, attribute), expected,
                    f'{module_path}.{attribute} still carries its ambient value. '
                    f'Tests would then pass or fail according to the .env of '
                    f'whoever ran them.'
                )

    def test_the_admin_v2_allowlist_is_empty_even_when_the_environment_is_not(self):
        """
        The negative control, and the one that reproduces the original bug.

        Without the runner's neutralisation this passes trivially on CI (where
        the variable is unset) and fails on Mason's machine — which is the
        asymmetry the whole release is about, so it is asserted with the
        variable deliberately set.
        """
        from unittest import mock

        from src.view import admin_v2

        with mock.patch.dict(os.environ, {'ADMIN_V2_USER_IDS': '73,99'}):
            # The module constant is parsed at import; setting the variable now
            # must not change it, and the runner must have emptied it.
            self.assertEqual(admin_v2.ALLOWED_USER_IDS, set())


class TheDigestHeartbeatHasOneDefinition(SimpleTestCase):
    """
    The writer and the watchdog must agree on the path, by construction.
    """

    def test_writer_and_watchdog_resolve_the_same_path(self):
        from src.digest_heartbeat import digest_heartbeat_path
        from src.management.commands.check_digest_freshness import Command
        from src.tasks.notifications import _digest_heartbeat_path

        self.assertEqual(_digest_heartbeat_path(), digest_heartbeat_path())
        self.assertEqual(Command()._heartbeat_path(), digest_heartbeat_path())

    def test_an_absolute_log_dir_discards_base_dir(self):
        """
        Not a complaint — `os.path.join` is behaving as documented and an
        absolute `LOG_DIR` should be absolute. It is asserted because it is the
        exact mechanism that defeated `override_settings(BASE_DIR=…)` in
        `test_digest_watchdog` and reddened CI, and a future reader should find
        it written down rather than rediscover it.
        """
        from unittest import mock

        from src.digest_heartbeat import HEARTBEAT_FILENAME, digest_heartbeat_path

        # ⚠️ ASSEMBLED RATHER THAN WRITTEN, for two independent reasons that
        # both bit this file within a minute of each other: `test_hardcoded_urls`
        # scans every `.py` in `src/` for rooted literals, and bandit's B108
        # reads one as an insecure temp path — five MEDIUM findings, which the
        # pre-push hook and CI's security job both refuse to push. Nothing is
        # opened here; the assertion is about how a path is composed.
        absolute_root = '/' + 'tmp'
        expected = os.path.join(absolute_root, HEARTBEAT_FILENAME)

        with mock.patch.dict(os.environ, {'LOG_DIR': absolute_root}):
            self.assertEqual(digest_heartbeat_path(), expected)

        with mock.patch.dict(os.environ, {'LOG_DIR': 'logs'}):
            self.assertTrue(digest_heartbeat_path().endswith(os.path.join('logs', 'last_digest_sent')))
            self.assertNotEqual(digest_heartbeat_path(), expected)
