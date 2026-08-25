"""
v3.25.2 — a failing `subTest` aborted the whole `--parallel` run and reported
nothing, with `tblib` installed and its guard green.

⚠️ THIS IS v3.19.9'S FINDING IN THE HALF v3.19.9 DECLARED UNASSERTABLE.

`src/test_parallel_reporting.py` pins the one condition Django branches on —
`django.test.runner.tblib is not None` — and says, correctly for tracebacks,
that the behaviour itself "cannot be asserted from inside the suite without
deliberately failing a test". That guard has been green the whole time. On
08-24-26 a run of the full suite still ended like this:

    multiprocessing.pool.MaybeEncodingError: Error sending result:
      '(69, [('startTest', 0), ('addSubTest', 0, <unittest.case._SubTest ...
      Reason: 'AttributeError("Can't pickle local object
               'convert_exception_to_response.<locals>.inner'")'

**No `Ran N tests` line, no `FAIL:` line, and no results for the 1,600-odd
tests that passed.** `tblib` makes tracebacks picklable; it does not make a
live `TestCase` picklable, and nothing ever claimed it did.

> **A GUARD THAT PINS THE CONDITION A FIX TURNED ON IS NOT A GUARD ON THE
> FAILURE MODE.** v3.21.7 asked *what would have to be true for this check to
> go red*. The companion question, and the one this needed: **what would have
> to be true for the original symptom to come back, and would the green guard
> notice?** Here it came back through the same pipe by a different route, and
> the guard could not see it, because it watches a *dependency* rather than an
> *outcome*. `TheRunnerReportsAFailingSubTestTests` below watches the outcome.

THE MECHANISM
-------------
`RemoteTestResult.addSubTest` appends the live `_SubTest`, whose `test_case` is
the running `TestCase`. Django knows this — `SimpleTestCase.__getstate__`
exists and says it is there *"to make SimpleTestCase picklable for parallel
tests using subtests"* — but its filtering is guarded:

    state = super().__dict__
    if state["_outcome"]:
        ... return only values that pass is_pickable() ...
    return state                          # ← unfiltered

`_outcome` is cleared when the test finishes. The check runs *during* the test
(filtered, passes, prints nothing); the pool pickles `result.events` *after the
subsuite finishes* (unfiltered, and `self.client.handler._middleware_chain` is
`convert_exception_to_response.<locals>.inner`, a local closure).
`TheDjangoAsymmetryThatCausesItTests` pins exactly that, so this workaround
goes red — usefully — on the day Django fixes it.

⚠️ WHY EVERY EARLIER MINIMAL REPRODUCTION "DID NOT REPRODUCE"
`DiscoverRunner.build_suite` computes `processes = min(self.parallel,
len(subsuites))` and only builds a parallel suite `if processes > 1`, and
subsuites are partitioned **per TestCase class**. **`--parallel=8` over a
single test class runs serially, silently.** Every probe written to reproduce
this — and, on the evidence of its write-up, v3.19.8's too — was a single
class, so none of them was ever running in parallel. It is not batch
dependence and it is not flakiness. The end-to-end test below uses **two**
classes for that reason, and would be worthless with one.
"""
import os
import pickle
import subprocess
import sys
import tempfile
import textwrap

from django.test import Client, SimpleTestCase
from django.test.runner import RemoteTestResult

from src.cache_isolated_runner import (CacheIsolatedParallelSuite,
                                       PicklableSubTestResult,
                                       PicklableSubTestRunner,
                                       _SubTestDescription)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Fake:
    """Stands in for `_SubTest` without needing a live test to build one."""

    def __init__(self, text="(field='x')", short='a docstring line'):
        self._text, self._short = text, short
        self.unpicklable = lambda: None      # a local closure, like the real one

    def __str__(self):
        return self._text

    def shortDescription(self):
        return self._short


class TheParallelSuiteShipsADescriptionTests(SimpleTestCase):

    def test_the_suite_is_wired_to_the_patched_result_class(self):
        self.assertIs(CacheIsolatedParallelSuite.runner_class,
                      PicklableSubTestRunner)
        self.assertIs(PicklableSubTestRunner.resultclass, PicklableSubTestResult)

    def test_django_default_is_what_we_are_replacing(self):
        """The control: without the override the suite would ship Django's."""
        self.assertTrue(issubclass(PicklableSubTestResult, RemoteTestResult))


class TheStandInCarriesTheTextAndNothingElseTests(SimpleTestCase):

    def test_it_reproduces_str_and_short_description(self):
        original = _Fake()
        stand_in = _SubTestDescription(original)
        self.assertEqual(str(stand_in), str(original))
        self.assertEqual(stand_in.shortDescription(), original.shortDescription())

    def test_the_original_cannot_be_pickled_and_the_stand_in_can(self):
        """
        ⚠️ BOTH HALVES. Without the first assertion this test cannot tell a
        working substitution from a fixture that was picklable all along.
        """
        original = _Fake()
        with self.assertRaises(Exception):
            pickle.dumps(original)
        pickle.loads(pickle.dumps(_SubTestDescription(original)))  # nosec B301  # round-trips a value built on the previous line

    def test_a_stand_in_survives_a_round_trip_intact(self):
        restored = pickle.loads(pickle.dumps(_SubTestDescription(_Fake())))  # nosec B301  # round-trips a value built on the same line
        self.assertEqual(str(restored), "(field='x')")
        self.assertEqual(restored.shortDescription(), 'a docstring line')


class TheResultDoesNotStoreALiveSubTestTests(SimpleTestCase):

    def _err(self):
        try:
            raise AssertionError('a subtest failure that has to cross a boundary')
        except AssertionError:
            return sys.exc_info()

    def test_a_failed_subtest_is_replaced_before_it_reaches_the_events_list(self):
        result = PicklableSubTestResult()
        original = _Fake()
        result.addSubTest(self, original, self._err())

        recorded = [e for e in result.events if e[0] == 'addSubTest']
        self.assertEqual(len(recorded), 1)
        shipped = recorded[0][2]
        self.assertIsInstance(shipped, _SubTestDescription)
        self.assertIsNot(shipped, original)
        self.assertEqual(str(shipped), str(original))

    def test_the_whole_events_list_pickles(self):
        """The assertion that would have gone red on 08-24-26."""
        result = PicklableSubTestResult()
        result.addSubTest(self, _Fake(), self._err())
        pickle.dumps(result.events)

    def test_a_passing_subtest_records_nothing(self):
        """
        THE CONTROL — Django deliberately records no event for a successful
        subtest, and substituting on the success path would be a behaviour
        change dressed as a fix.
        """
        result = PicklableSubTestResult()
        result.addSubTest(self, _Fake(), None)
        self.assertEqual([e for e in result.events if e[0] == 'addSubTest'], [])


class TheDjangoAsymmetryThatCausesItTests(SimpleTestCase):
    """
    Pins the upstream behaviour the workaround exists for, so that the day
    Django starts filtering unconditionally this goes red and somebody can
    delete `PicklableSubTestResult` instead of inheriting it forever.
    """

    def test_get_state_filters_only_while_outcome_is_set(self):
        import inspect

        from django.test.testcases import SimpleTestCase as DjangoSimpleTestCase

        source = inspect.getsource(DjangoSimpleTestCase.__getstate__)
        self.assertIn('_outcome', source,
                      'Django no longer guards the filtering on _outcome — '
                      're-measure before trusting the workaround.')

    def test_the_same_subtest_pickles_during_the_test_and_not_after(self):
        """
        THE MEASUREMENT, not the source read. Builds a `_SubTest` over a test
        case that has used its client, and asks the same question twice.
        """
        from unittest.case import _SubTest

        # ⚠️ A module-level class, deliberately. A `SimpleTestCase` subclass
        # defined inside this method is a *local* class, and pickle addresses
        # classes by name — so the round trip below would fail for a reason
        # that has nothing to do with the finding, which is exactly the
        # "assertion that cannot distinguish the bug from the fixture" trap.
        victim = TheParallelSuiteShipsADescriptionTests(
            'test_the_suite_is_wired_to_the_patched_result_class')
        victim.client = Client()
        # ⚠️ `load_middleware()` rather than `client.get('/')`. Making a real
        # request from a `SimpleTestCase` reaches `LockdownMiddleware`, which
        # queries a singleton — so the first draft of this test passed only
        # when some earlier test in the same process had warmed that cache, and
        # failed the moment it ran beside a sibling class. Building the chain
        # is the only part that matters here and it touches no database.
        victim.client.handler.load_middleware()
        subtest = _SubTest(victim, 'm', {'f': 'x'})

        # During a test, `_outcome` is set and Django's filter drops `client`.
        victim._outcome = object()
        pickle.dumps(subtest)

        # Once the test finishes, `_outcome` is cleared and it does not.
        victim._outcome = None
        with self.assertRaises(Exception):
            pickle.dumps(subtest)


class TheRunnerReportsAFailingSubTestTests(SimpleTestCase):
    """
    END TO END, and the only test here that would have caught the original.

    Runs a real `manage.py test --parallel=2` over a scratch package in a child
    interpreter and requires it to *report*. Against the pre-fix tree the child
    dies with `MaybeEncodingError` and prints no `Ran N tests` line at all —
    verified 08-24-26 by commenting out one line of
    `CacheIsolatedParallelSuite`.

    ⚠️ TWO `TestCase` CLASSES, NOT ONE. Django partitions subsuites per test
    case class and falls back to a serial run when there is only one, so the
    single-class version of this test passes on a broken tree.

    ⚠️ The child is *expected* to fail. `FAILED (failures=2)` is the assertion;
    a green child would mean the scratch tests did not run.
    """

    _SCRATCH = textwrap.dedent('''
        from django.test import Client, TestCase

        class SubTestFailureTests(TestCase):
            def test_fails_a_subtest_after_using_the_client(self):
                self.client = Client()
                self.client.get('/')
                for i in (1, 2):
                    with self.subTest(i=i):
                        self.assertEqual(i, 99)

        class SecondClassSoThePoolIsActuallyUsedTests(TestCase):
            def test_passes(self):
                self.assertTrue(True)
    ''')

    def test_a_failing_subtest_is_reported_rather_than_aborting_the_run(self):
        with tempfile.TemporaryDirectory() as scratch:
            module = 'zz_subtest_parallel_probe'
            with open(os.path.join(scratch, f'{module}.py'), 'w') as fh:
                fh.write(self._SCRATCH)

            completed = subprocess.run(
                [sys.executable, 'manage.py', 'test', module, '--parallel=2', '-v0'],
                cwd=_REPO_ROOT,
                env={**os.environ,
                     'DJANGO_SETTINGS_MODULE': 'Parliament.settings',
                     'DB_BACKEND': 'sqlite',
                     'REDIS_URL': '',
                     'PYTHONPATH': os.pathsep.join([scratch, _REPO_ROOT])},
                capture_output=True, text=True, timeout=300,
            )

        output = completed.stdout + completed.stderr
        self.assertNotIn('MaybeEncodingError', output, output[-3000:])
        self.assertIn('Ran 2 tests', output, output[-3000:])
        self.assertIn('FAILED (failures=2)', output, output[-3000:])
        # The descriptions have to survive the crossing, not just the count.
        self.assertIn('i=1', output, output[-3000:])
        self.assertIn('i=2', output, output[-3000:])
