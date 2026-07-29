"""
Developer mode — gate, isolation and record-safety tests.

The gate is two-factor (allowlist AND preference opt-in), so there are three
"off" states to prove and one "on" state. The most important test in this file
is `DevModeDoesNotWidenAccessTests`: dev mode must never reveal a record the
user could not otherwise see. If that ever fails, the developer allowlist has
quietly become a master key to Kai reports, ballots and slating notes.
"""

from unittest.mock import patch

from django.template import Context, Template
from django.test import TestCase, SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from . import dev_mode
from .dev_mode import (
    DEV_USER_IDS,
    find_duplicate_queries,
    normalize_sql,
    set_dev_mode,
    user_may_use_dev_mode,
)
from .models import (
    Committee, KaiMemberPermission, KaiReport, ParliamentUser, UserPreferences,
)


class AllowlistParityTests(SimpleTestCase):
    """
    dev_mode duplicates admin_v2's env parsing to avoid an import cycle. That
    duplication is only safe if it cannot drift.
    """

    def test_dev_allowlist_matches_admin_v2_allowlist(self):
        from src.view.admin_v2 import ALLOWED_USER_IDS
        self.assertEqual(DEV_USER_IDS, ALLOWED_USER_IDS)


class SqlNormalisationTests(SimpleTestCase):
    def test_literals_are_stripped_so_shapes_group(self):
        a = normalize_sql('SELECT * FROM t WHERE id = 1')
        b = normalize_sql('SELECT * FROM t WHERE id = 22')
        self.assertEqual(a, b)

    def test_string_literals_are_stripped(self):
        a = normalize_sql("SELECT * FROM t WHERE name = 'alice'")
        b = normalize_sql("SELECT * FROM t WHERE name = 'bob'")
        self.assertEqual(a, b)

    def test_different_shapes_do_not_group(self):
        a = normalize_sql('SELECT * FROM t WHERE id = 1')
        b = normalize_sql('SELECT * FROM other WHERE id = 1')
        self.assertNotEqual(a, b)

    def test_three_repeats_is_not_flagged_but_four_is(self):
        three = [{'sql': 'SELECT a FROM t WHERE id = %d' % i, 'ms': 1.0} for i in range(3)]
        self.assertEqual(find_duplicate_queries(three), [])

        four = [{'sql': 'SELECT a FROM t WHERE id = %d' % i, 'ms': 1.0} for i in range(4)]
        flagged = find_duplicate_queries(four)
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0][1], 4)

    def test_duplicates_are_sorted_worst_first(self):
        queries = (
            [{'sql': 'SELECT a FROM t WHERE id = %d' % i, 'ms': 1.0} for i in range(4)]
            + [{'sql': 'SELECT b FROM u WHERE id = %d' % i, 'ms': 1.0} for i in range(9)]
        )
        flagged = find_duplicate_queries(queries)
        self.assertEqual([row[1] for row in flagged], [9, 4])


class CacheIsolatedTestCase(TestCase):
    """
    Clear the cache between tests.

    `dev_mode_enabled_for` reads UserPreferences through the same
    `user_prefs_<pk>` cache key the context processor uses (deliberately — it
    costs no extra query). But the cache is NOT rolled back with the test
    transaction, while SQLite reuses primary keys from test to test. Without
    this, one test's dev-enabled preferences object is served to a different
    user who happens to get the same pk. Caught exactly that way.
    """

    def setUp(self):
        from django.core.cache import cache
        cache.clear()
        super().setUp()


class DevModeGateTests(CacheIsolatedTestCase):
    """Three off-states, one on-state."""

    def setUp(self):
        super().setUp()
        self.dev = ParliamentUser.objects.create_user(
            user_id='555', name='Dev User', username='devuser', member_type='Member')
        self.normal = ParliamentUser.objects.create_user(
            user_id='999', name='Normal User', username='normaluser', member_type='Member')

    def test_off_for_a_user_not_on_the_allowlist(self):
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            self.assertFalse(user_may_use_dev_mode(self.normal))
            self.assertFalse(dev_mode.dev_mode_enabled_for(self.normal))

    def test_off_for_an_allowlisted_user_who_has_not_opted_in(self):
        """Being on the list is necessary, not sufficient."""
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            self.assertTrue(user_may_use_dev_mode(self.dev))
            self.assertFalse(dev_mode.dev_mode_enabled_for(self.dev))

    def test_off_for_anonymous(self):
        from django.contrib.auth.models import AnonymousUser
        self.assertFalse(user_may_use_dev_mode(AnonymousUser()))

    def test_on_only_with_both_factors(self):
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            set_dev_mode(self.dev, True)
            self.assertTrue(dev_mode.dev_mode_enabled_for(self.dev))

    def test_opting_out_turns_it_off_again(self):
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            set_dev_mode(self.dev, True)
            set_dev_mode(self.dev, False)
            self.assertFalse(dev_mode.dev_mode_enabled_for(self.dev))

    def test_preference_survives_an_allowlist_removal_but_gate_still_closes(self):
        """Dropping someone from the env list is enough; no DB cleanup needed."""
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            set_dev_mode(self.dev, True)
        with patch.object(dev_mode, 'DEV_USER_IDS', set()):
            self.assertFalse(dev_mode.dev_mode_enabled_for(self.dev))


class DevModeToggleEndpointTests(CacheIsolatedTestCase):
    def setUp(self):
        super().setUp()
        self.dev = ParliamentUser.objects.create_user(
            user_id='555', name='Dev User', username='devuser2', member_type='Member')
        self.normal = ParliamentUser.objects.create_user(
            user_id='999', name='Normal User', username='normaluser2', member_type='Member')

    def test_non_allowlisted_user_is_refused(self):
        self.client.force_login(self.normal)
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            resp = self.client.post(reverse('toggle_dev_mode'), {'enabled': '1'})
        self.assertEqual(resp.status_code, 403)

    def test_refusal_writes_nothing_to_their_preferences(self):
        self.client.force_login(self.normal)
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            self.client.post(reverse('toggle_dev_mode'), {'enabled': '1'})
        prefs, _ = UserPreferences.objects.get_or_create(user=self.normal)
        self.assertNotIn('dev', prefs.prefs or {})

    def test_allowlisted_user_can_enable_and_disable(self):
        self.client.force_login(self.dev)
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            self.client.post(reverse('toggle_dev_mode'), {'enabled': '1'})
            self.assertTrue(dev_mode.dev_mode_enabled_for(self.dev))
            self.client.post(reverse('toggle_dev_mode'), {'enabled': '0'})
            self.assertFalse(dev_mode.dev_mode_enabled_for(self.dev))

    def test_get_is_rejected(self):
        self.client.force_login(self.dev)
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            resp = self.client.get(reverse('toggle_dev_mode'))
        self.assertEqual(resp.status_code, 405)

    def test_card_is_absent_from_preferences_for_normal_users(self):
        self.client.force_login(self.normal)
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            resp = self.client.get(reverse('preferences'))
        self.assertNotContains(resp, 'Developer Mode')

    def test_card_is_present_for_allowlisted_users(self):
        self.client.force_login(self.dev)
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            resp = self.client.get(reverse('preferences'))
        self.assertContains(resp, 'Developer Mode')


class PreferencesFormPreservationTests(CacheIsolatedTestCase):
    """
    UserPreferencesForm.save() rebuilds `prefs` wholesale. The dev section is
    written by a different endpoint, so it has to be carried across or a user
    saving any other preference silently switches dev mode off.
    """

    def setUp(self):
        super().setUp()
        self.dev = ParliamentUser.objects.create_user(
            user_id='555', name='Dev User', username='devuser3', member_type='Member')

    def test_saving_normal_preferences_does_not_clobber_dev_mode(self):
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            set_dev_mode(self.dev, True)

            self.client.force_login(self.dev)
            self.client.post(reverse('preferences'), {
                'theme': 'dark',
                'home_layout': 'modern',
                'landing_page': 'home',
                'show_vote_menu': 'on',
            })
            self.assertTrue(dev_mode.dev_mode_enabled_for(self.dev))


class DevModeDoesNotWidenAccessTests(CacheIsolatedTestCase):
    """
    THE LOAD-BEARING TEST.

    Dev mode shows metadata, never gated record content. A developer who is not
    otherwise entitled to a Kai allegation body must not obtain it by switching
    on a debug panel.
    """

    MARKER = 'pomegranate'

    def setUp(self):
        super().setUp()
        self.submitter = ParliamentUser.objects.create_user(
            user_id='dsub', name='Devtest Submitter', username='dsub', member_type='Member')
        # On the dev allowlist, but only list-level Kai access.
        self.dev_reviewer = ParliamentUser.objects.create_user(
            user_id='555', name='Dev Reviewer', username='devrev', member_type='Member')

        self.committee = Committee.objects.create(
            name='Kai Committee (devmode)', code='KAIDEV', is_kai_committee=True)
        self.committee.members.add(self.dev_reviewer)
        KaiMemberPermission.objects.create(
            committee=self.committee, user=self.dev_reviewer, can_view_report_list=True)

        self.report = KaiReport.objects.create(
            title='Devmode visible title',
            category='behavioral',
            description='Alleged conduct with marker word: %s.' % self.MARKER,
            submitted_by=self.submitter,
        )

    def test_dev_mode_on_still_hides_the_allegation_body(self):
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            set_dev_mode(self.dev_reviewer, True)
            self.client.force_login(self.dev_reviewer)
            resp = self.client.get(reverse('view_kai_reports'))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Devmode visible title')
        self.assertNotContains(resp, self.MARKER)

    def test_dev_mode_on_still_hides_submitter_identity(self):
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            set_dev_mode(self.dev_reviewer, True)
            self.client.force_login(self.dev_reviewer)
            resp = self.client.get(reverse('view_kai_reports'))

        self.assertNotContains(resp, 'Devtest Submitter')

    def test_dev_mode_on_does_not_make_gated_fields_searchable(self):
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            set_dev_mode(self.dev_reviewer, True)
            self.client.force_login(self.dev_reviewer)
            resp = self.client.get(reverse('view_kai_reports'), {'search': self.MARKER})

        self.assertEqual(list(resp.context['reports']), [])

    def test_panel_is_injected_when_dev_mode_is_on(self):
        """Sanity: the above assertions would pass trivially if dev mode were off."""
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            set_dev_mode(self.dev_reviewer, True)
            self.client.force_login(self.dev_reviewer)
            resp = self.client.get(reverse('view_kai_reports'))

        self.assertEqual(resp['X-Parliament-Dev-Mode'], '1')
        self.assertContains(resp, 'pdev-root')

    def test_panel_is_absent_when_dev_mode_is_off(self):
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            self.client.force_login(self.dev_reviewer)
            resp = self.client.get(reverse('view_kai_reports'))

        self.assertNotContains(resp, 'pdev-root')
        self.assertFalse(resp.has_header('X-Parliament-Dev-Mode'))

    def test_dev_responses_are_not_cacheable(self):
        """Cloudflare has served cached pages across users before (07-18)."""
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            set_dev_mode(self.dev_reviewer, True)
            self.client.force_login(self.dev_reviewer)
            resp = self.client.get(reverse('view_kai_reports'))

        self.assertIn('no-store', resp['Cache-Control'])


class DevValueTagTests(CacheIsolatedTestCase):
    """The {% dev_value %} tag: transparent when off, annotated when on, gated always."""

    def setUp(self):
        super().setUp()
        self.user = ParliamentUser.objects.create_user(
            user_id='tag1', name='Tag User', username='taguser', member_type='Member')

    def _render(self, template_string, **ctx):
        return Template('{% load dev_tags %}' + template_string).render(Context(ctx))

    def test_renders_plain_value_when_dev_mode_is_off(self):
        dev_mode.stop_recording()
        out = self._render("{% dev_value obj 'name' %}", obj=self.user)
        self.assertEqual(out.strip(), 'Tag User')
        self.assertNotIn('pdev-val', out)

    def test_annotates_when_dev_mode_is_on(self):
        dev_mode.start_recording()
        try:
            out = self._render("{% dev_value obj 'name' %}", obj=self.user)
        finally:
            dev_mode.stop_recording()
        self.assertIn('pdev-val', out)
        self.assertIn('Tag User', out)
        self.assertIn('ParliamentUser', out)

    def test_gated_value_is_withheld_even_in_dev_mode(self):
        recorder = dev_mode.start_recording()
        try:
            out = self._render(
                "{% dev_value obj 'name' gated_by=allowed gate_name='can_view_x' %}",
                obj=self.user, allowed=False,
            )
        finally:
            dev_mode.stop_recording()

        self.assertNotIn('Tag User', out)
        self.assertIn('[gated]', out)
        self.assertIn('can_view_x', out)
        self.assertTrue(recorder.objects[0]['gated'])

    def test_gated_value_renders_nothing_when_dev_mode_is_off(self):
        dev_mode.stop_recording()
        out = self._render(
            "{% dev_value obj 'name' gated_by=allowed %}", obj=self.user, allowed=False)
        self.assertEqual(out.strip(), '')

    def test_dev_note_records_but_renders_nothing(self):
        recorder = dev_mode.start_recording()
        try:
            out = self._render('{% dev_note "src" "computed in view" %}')
        finally:
            dev_mode.stop_recording()
        self.assertEqual(out.strip(), '')
        self.assertEqual(recorder.notes[0]['label'], 'src')


class RecorderIsolationTests(SimpleTestCase):
    """
    The recorder is a ContextVar, not a thread-local, because Daphne interleaves
    requests on one thread — a thread-local would leak one user's SQL into
    another user's panel.
    """

    def test_recorder_is_none_outside_a_recorded_request(self):
        dev_mode.stop_recording()
        self.assertIsNone(dev_mode.get_recorder())

    def test_record_helpers_are_no_ops_when_inactive(self):
        dev_mode.stop_recording()
        dev_mode.record_flag('x', True)
        dev_mode.record_permission('y', 'z')
        dev_mode.record_note('a', 'b')  # must not raise

    def test_recorder_uses_a_contextvar(self):
        import contextvars
        self.assertIsInstance(dev_mode._recorder, contextvars.ContextVar)


class PermissionInstrumentationTests(CacheIsolatedTestCase):
    """
    Regression for 07-28-26: the Perms panel read "no permission gate ran" on
    officer pages, which are gated by @officer_or_advisor_required. Only
    _get_kai_access was instrumented; none of the decorators in src/decorators.py
    were. Any gate that isn't recorded is invisible to dev mode.
    """

    def setUp(self):
        super().setUp()
        self.officer = ParliamentUser.objects.create_user(
            user_id='555', name='Olive Officer', username='olive', member_type='Officer')

    def _record_via(self, decorator, user=None, **view_kwargs):
        """Run a decorated no-op view under a recorder and return recorded gates."""
        from django.test import RequestFactory
        recorder = dev_mode.start_recording()
        try:
            request = RequestFactory().get('/')
            request.user = user or self.officer
            decorator(lambda req, *a, **k: 'ok')(request, **view_kwargs)
        except Exception:
            pass
        finally:
            dev_mode.stop_recording()
        return recorder.permissions

    def test_officer_or_advisor_required_is_recorded(self):
        from src.decorators import officer_or_advisor_required
        recorded = self._record_via(officer_or_advisor_required)
        self.assertTrue(
            any(p['label'] == 'officer_or_advisor_required' for p in recorded),
            'officer gate did not reach the Perms panel: %r' % recorded,
        )

    def test_officer_required_is_recorded(self):
        from src.decorators import officer_required
        recorded = self._record_via(officer_required)
        self.assertTrue(any(p['label'] == 'officer_required' for p in recorded))

    def test_admin_required_records_a_denial(self):
        from src.decorators import admin_required
        recorded = self._record_via(admin_required)
        entry = next(p for p in recorded if p['label'] == 'admin_required')
        self.assertEqual(entry['result'], 'DENIED')

    def test_exclude_pledges_is_recorded(self):
        from src.decorators import exclude_pledges
        recorded = self._record_via(exclude_pledges)
        self.assertTrue(any(p['label'] == 'exclude_pledges' for p in recorded))

    def test_non_pledge_passthrough_is_still_recorded(self):
        """A gate that trivially passes is still a gate that ran."""
        from src.decorators import pledge_page_allowed
        recorded = self._record_via(pledge_page_allowed('directory'))
        entry = next(p for p in recorded if 'pledge_page_allowed' in p['label'])
        self.assertEqual(entry['result'], 'allowed')
        self.assertIn('not a pledge', entry['detail'])

    def test_every_authz_decorator_routes_through_the_gate_helper(self):
        """
        Structural guard: adding a new decorator to src/decorators.py without
        calling _gate() makes it invisible in dev mode. Catch that here rather
        than by noticing an empty panel months later.
        """
        import inspect
        import src.decorators as decorators

        exempt = {'log_function_call', '_gate'}
        source = inspect.getsource(decorators)
        missing = []
        for name, obj in vars(decorators).items():
            if name.startswith('__') or name in exempt or not inspect.isfunction(obj):
                continue
            if obj.__module__ != 'src.decorators':
                continue
            body = inspect.getsource(obj)
            if '_gate(' not in body:
                missing.append(name)
        self.assertEqual(
            missing, [],
            'These authorization decorators never record a decision, so dev '
            'mode will report "no permission gate ran" on pages using them: %s'
            % ', '.join(missing),
        )


class FeatureFlagInstrumentationTests(CacheIsolatedTestCase):
    """
    Regression for 07-28-26: some flags appeared in the panel and some didn't.

    Python asks via FeatureFlag.is_feature_enabled (instrumented in the model);
    templates just index the dict built by the feature_flags context processor,
    which never calls that method. Only the first was recorded.
    """

    def _lookup_in_template(self, template_string, flags):
        from src.context_processors import _TrackedToggleDict
        recorder = dev_mode.start_recording()
        try:
            Template(template_string).render(
                Context({'feature_flags': _TrackedToggleDict(flags)})
            )
        finally:
            dev_mode.stop_recording()
        return recorder.flags

    def test_template_lookup_of_an_enabled_flag_is_recorded(self):
        recorded = self._lookup_in_template(
            '{% if feature_flags.chats %}on{% endif %}', {'chats': True})
        entry = next(f for f in recorded if f['name'] == 'chats')
        self.assertTrue(entry['result'])
        self.assertIn('template lookup', entry['source'])

    def test_template_lookup_of_an_unseeded_flag_is_recorded_as_fail_closed(self):
        """The valuable case: invisible in the template, True in Python."""
        recorded = self._lookup_in_template(
            '{% if feature_flags.never_seeded %}on{% endif %}', {})
        entry = next(f for f in recorded if f['name'] == 'never_seeded')
        self.assertFalse(entry['result'])
        self.assertIn('fail-CLOSED', entry['source'])

    def test_missing_flag_still_renders_falsy(self):
        """Instrumentation must not change template behaviour."""
        from src.context_processors import _TrackedToggleDict
        out = Template('{% if feature_flags.never_seeded %}ON{% else %}OFF{% endif %}').render(
            Context({'feature_flags': _TrackedToggleDict({})})
        )
        self.assertEqual(out, 'OFF')

    def test_repeated_lookups_are_deduped_with_a_count(self):
        recorded = self._lookup_in_template(
            '{% if feature_flags.chats %}a{% endif %}'
            '{% if feature_flags.chats %}b{% endif %}'
            '{% if feature_flags.chats %}c{% endif %}',
            {'chats': True},
        )
        entries = [f for f in recorded if f['name'] == 'chats']
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]['count'], 3)

    def test_tracked_dict_survives_pickling(self):
        """The context processor caches this dict for 60s; tracking must survive."""
        import pickle
        from src.context_processors import _TrackedToggleDict
        restored = pickle.loads(pickle.dumps(_TrackedToggleDict({'chats': True})))
        self.assertIsInstance(restored, _TrackedToggleDict)

        recorder = dev_mode.start_recording()
        try:
            restored['chats']
        finally:
            dev_mode.stop_recording()
        self.assertTrue(any(f['name'] == 'chats' for f in recorder.flags))

    def test_python_side_lookup_is_still_recorded(self):
        """The path that already worked must keep working."""
        from src.models_feature_flags import FeatureFlag
        recorder = dev_mode.start_recording()
        try:
            FeatureFlag.is_feature_enabled('some_unseeded_name')
        finally:
            dev_mode.stop_recording()
        entry = next(f for f in recorder.flags if f['name'] == 'some_unseeded_name')
        self.assertTrue(entry['result'])
        self.assertIn('fail-open', entry['source'])


class QueryCaptureTests(CacheIsolatedTestCase):
    """
    execute_wrapper capture: stacks, tables, row counts, shape analysis.

    The stack is the point. "6× the same query" says there's an N+1; only the
    stack says which loop — and it's the only way to tell two duplicate groups
    apart when their SQL renders identically (07-28-26).
    """

    def setUp(self):
        super().setUp()
        self.user = ParliamentUser.objects.create_user(
            user_id='qc1', name='Query User', username='qcuser', member_type='Member')

    def test_capture_stack_excludes_django_and_dev_mode_frames(self):
        from src.dev_mode import capture_stack
        frames = capture_stack()
        for frame in frames:
            self.assertNotIn('site-packages', frame['where'])
            # Note: 'src/test_dev_mode.py' legitimately contains the substring
            # 'dev_mode.py', so match the exact module paths that are skipped.
            self.assertNotEqual(frame['where'].split(':')[0], 'src/dev_mode.py')
            self.assertNotEqual(frame['where'].split(':')[0], 'src/middleware/dev_mode.py')

    def test_capture_stack_reports_project_relative_paths(self):
        from src.dev_mode import capture_stack
        frames = capture_stack()
        self.assertTrue(frames, 'expected at least this test frame')
        self.assertTrue(frames[-1]['where'].startswith('src/'))

    def test_extract_tables_finds_from_and_join(self):
        from src.dev_mode import extract_tables
        tables = extract_tables(
            'SELECT * FROM "src_legislation" INNER JOIN "src_parliamentuser" ON (x = y)')
        self.assertEqual(tables, ['src_legislation', 'src_parliamentuser'])

    def test_classify_flags_a_write(self):
        from src.dev_mode import classify_query
        self.assertIn('write', classify_query('UPDATE "src_legislation" SET passed = true'))

    def test_classify_flags_a_full_scan(self):
        from src.dev_mode import classify_query
        self.assertIn('full table scan', classify_query('SELECT a FROM "src_vote"'))

    def test_classify_recognises_a_get(self):
        from src.dev_mode import classify_query
        verdict = classify_query(
            'SELECT "src_parliamentuser"."name" FROM "src_parliamentuser" '
            'WHERE "src_parliamentuser"."user_id" = %s LIMIT 21')
        self.assertIn('.get()', verdict)

    def test_classify_flags_unbounded_ordering(self):
        from src.dev_mode import classify_query
        self.assertIn('no LIMIT', classify_query(
            'SELECT a FROM "src_vote" WHERE "src_vote"."id" = 1 ORDER BY "src_vote"."id"'))

    def test_duplicate_groups_carry_their_origins(self):
        """This is what makes two identical-looking N+1 blocks distinguishable."""
        from src.dev_mode import find_duplicate_queries
        queries = [
            {'sql': 'SELECT a FROM t WHERE id = %d' % i, 'ms': 1.0,
             'stack': [{'where': 'src/view/a.py:%d' % (10 + i % 2), 'func': 'f', 'code': ''}]}
            for i in range(6)
        ]
        (shape, count, ms, sample, stacks), = find_duplicate_queries(queries)
        self.assertEqual(count, 6)
        self.assertEqual(
            sorted(s['where'] for s in stacks),
            ['src/view/a.py:10', 'src/view/a.py:11'],
        )

    def test_shapes_aggregate_counts_rows_and_tables(self):
        from src.dev_mode import analyse_shapes
        shapes = analyse_shapes([
            {'sql': 'SELECT a FROM "src_vote" WHERE id = 1', 'ms': 1.0, 'rows': 3,
             'tables': ['src_vote'], 'stack': []},
            {'sql': 'SELECT a FROM "src_vote" WHERE id = 2', 'ms': 2.0, 'rows': 4,
             'tables': ['src_vote'], 'stack': []},
        ])
        self.assertEqual(len(shapes), 1)
        self.assertEqual(shapes[0]['count'], 2)
        self.assertEqual(shapes[0]['rows'], 7)
        self.assertEqual(shapes[0]['tables'], ['src_vote'])

    def test_real_request_captures_queries_with_stacks(self):
        with patch.object(dev_mode, 'DEV_USER_IDS', {'qc1'}):
            set_dev_mode(self.user, True)
            self.client.force_login(self.user)
            resp = self.client.get(reverse('view_legislation_history'))

        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'pdev-root')
        # The Shapes and Request tabs must actually render.
        self.assertContains(resp, 'data-pane="shapes"')
        self.assertContains(resp, 'data-pane="req"')

    def test_request_tab_reports_the_resolved_view(self):
        with patch.object(dev_mode, 'DEV_USER_IDS', {'qc1'}):
            set_dev_mode(self.user, True)
            self.client.force_login(self.user)
            resp = self.client.get(reverse('view_legislation_history'))
        self.assertContains(resp, 'view_legislation_history')

    def test_templates_are_recorded(self):
        with patch.object(dev_mode, 'DEV_USER_IDS', {'qc1'}):
            set_dev_mode(self.user, True)
            self.client.force_login(self.user)
            resp = self.client.get(reverse('view_legislation_history'))
        self.assertContains(resp, 'legislation_history.html')


class LegislationHistoryQueryBudgetTests(TestCase):
    """
    The N+1 dev mode surfaced on this page, pinned so it can't come back.

    user_id is ParliamentUser's PRIMARY KEY, so `leg.posted_by` on each row was a
    separate pk fetch of the same author — six rows, six identical queries. Plus
    three .count() calls per row and a full-row UPDATE from set_passed().
    """

    def setUp(self):
        from src.models import Legislation
        self.author = ParliamentUser.objects.create_user(
            user_id='auth1', name='Author One', username='author1', member_type='Member')
        self.coauthor = ParliamentUser.objects.create_user(
            user_id='auth2', name='Author Two', username='author2', member_type='Member')

        self.bills = []
        for i in range(6):
            bill = Legislation.objects.create(
                title='Bill %d' % i, posted_by=self.author,
                required_percentage=51, voting_closed=True,
                available_at=timezone.now(),
            )
            bill.co_authors.add(self.coauthor)
            self.bills.append(bill)

        self.client.force_login(self.author)

    def _queries_for_page(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse('view_legislation_history'))
        self.assertEqual(resp.status_code, 200)
        return ctx.captured_queries

    def test_author_is_not_refetched_once_per_row(self):
        """The exact regression: select_related('posted_by')."""
        author_fetches = [
            q for q in self._queries_for_page()
            if 'src_parliamentuser' in q['sql']
            and 'LIMIT 21' in q['sql']
            and 'user_id' in q['sql']
        ]
        self.assertLessEqual(
            len(author_fetches), 1,
            'posted_by is being fetched per row again — %d pk lookups' % len(author_fetches),
        )

    def test_vote_counting_does_not_scale_with_row_count(self):
        vote_counts = [
            q for q in self._queries_for_page()
            if 'src_vote' in q['sql'] and 'COUNT' in q['sql'].upper()
        ]
        self.assertLessEqual(
            len(vote_counts), 2,
            'vote tallying is per-row again — %d COUNT queries for 6 bills' % len(vote_counts),
        )

    def test_viewing_the_page_does_not_write_legislation(self):
        """
        set_passed() used to save() every closed bill on every GET.

        Scoped to src_legislation deliberately: unrelated middleware legitimately
        creates singleton rows (SystemLockdown) on a cold request, and asserting
        'no writes at all' would fail for reasons that have nothing to do with
        this page.
        """
        writes = [
            q for q in self._queries_for_page()
            if q['sql'].strip().upper().startswith(('UPDATE', 'INSERT'))
            and 'src_legislation' in q['sql']
        ]
        self.assertEqual(
            writes, [],
            'GET on the legislation history page is writing legislation rows: %s'
            % [w['sql'][:120] for w in writes],
        )

    def test_query_count_does_not_grow_with_the_number_of_bills(self):
        """
        The real property. A fixed budget would just be measuring how many
        queries the middleware and context processors happen to make on a cold
        request; what matters is that adding legislation adds no queries.
        """
        from src.models import Legislation

        baseline = len(self._queries_for_page())

        for i in range(6):
            bill = Legislation.objects.create(
                title='Extra %d' % i, posted_by=self.author,
                required_percentage=51, voting_closed=True,
                available_at=timezone.now(),
            )
            bill.co_authors.add(self.coauthor)

        doubled = len(self._queries_for_page())

        self.assertLessEqual(
            doubled - baseline, 1,
            'doubling the legislation added %d queries — the page is O(n) again '
            '(baseline %d, doubled %d)' % (doubled - baseline, baseline, doubled),
        )

    def test_results_are_still_correct(self):
        resp = self.client.get(reverse('view_legislation_history'))
        self.assertEqual(len(resp.context['legislation_history']), 6)
        self.assertEqual(resp.context['status_counts']['all'], 6)


class SetPassedTests(TestCase):
    """set_passed's new counts= and conditional-save behaviour."""

    def setUp(self):
        from src.models import Legislation
        self.author = ParliamentUser.objects.create_user(
            user_id='sp1', name='SP Author', username='spauthor', member_type='Member')
        self.bill = Legislation.objects.create(
            title='SP Bill', posted_by=self.author,
            required_percentage=51, voting_closed=True,
            available_at=timezone.now(),
        )

    def test_counts_argument_issues_no_queries(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        self.bill.passed = True
        with CaptureQueriesContext(connection) as ctx:
            self.bill.set_passed(counts={'yes': 10, 'no': 1})
        self.assertEqual(len(ctx.captured_queries), 0)

    def test_counts_and_query_paths_agree(self):
        from src.models import Vote
        for choice in ('yes', 'yes', 'no', 'abstain'):
            Vote.objects.create(
                legislation=self.bill, user=self.author, vote_choice=choice)

        from_db = self.bill.set_passed(commit=False)
        from_counts = self.bill.set_passed(
            commit=False, counts={'yes': 2, 'no': 1, 'abstain': 1})
        self.assertEqual(from_db, from_counts)

    def test_no_write_when_the_value_is_unchanged(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        self.bill.set_passed(counts={'yes': 10, 'no': 0})   # -> True, writes
        with CaptureQueriesContext(connection) as ctx:
            self.bill.set_passed(counts={'yes': 10, 'no': 0})  # -> True again
        writes = [q for q in ctx.captured_queries if 'UPDATE' in q['sql'].upper()]
        self.assertEqual(writes, [])

    def test_writes_when_the_value_changes(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        self.bill.passed = False
        self.bill.save(update_fields=['passed'])
        with CaptureQueriesContext(connection) as ctx:
            self.bill.set_passed(counts={'yes': 10, 'no': 0})
        writes = [q for q in ctx.captured_queries if 'UPDATE' in q['sql'].upper()]
        self.assertEqual(len(writes), 1)
        self.bill.refresh_from_db()
        self.assertTrue(self.bill.passed)

    def test_commit_false_never_writes(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        self.bill.passed = False
        with CaptureQueriesContext(connection) as ctx:
            result = self.bill.set_passed(commit=False, counts={'yes': 10, 'no': 0})
        self.assertTrue(result)
        self.assertEqual([q for q in ctx.captured_queries if 'UPDATE' in q['sql'].upper()], [])


class FeatureFlagCacheTests(CacheIsolatedTestCase):
    """
    is_feature_enabled is cached (v3.17.1).

    The admin-v2 dashboard asked for 'push_notifications_enabled' five times in
    one page load, five identical uncached `objects.get`s. Correctness comes
    from invalidation on save/delete, not from the TTL.
    """

    def _flag_queries(self, fn):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as ctx:
            fn()
        return [q for q in ctx.captured_queries if 'src_featureflag' in q['sql']]

    def test_repeat_lookups_hit_the_cache(self):
        from src.models_feature_flags import FeatureFlag
        FeatureFlag.is_feature_enabled('some_flag')  # warm
        queries = self._flag_queries(
            lambda: [FeatureFlag.is_feature_enabled('some_flag') for _ in range(5)])
        self.assertEqual(queries, [], 'repeat flag lookups still hitting the DB')

    def test_first_lookup_still_queries(self):
        from src.models_feature_flags import FeatureFlag
        queries = self._flag_queries(lambda: FeatureFlag.is_feature_enabled('cold_flag'))
        self.assertEqual(len(queries), 1)

    def test_missing_flag_still_fails_open(self):
        from src.models_feature_flags import FeatureFlag
        self.assertTrue(FeatureFlag.is_feature_enabled('definitely_not_seeded'))
        self.assertTrue(FeatureFlag.is_feature_enabled('definitely_not_seeded'))  # cached

    def test_disabled_by_default_flag_is_still_disabled_when_cached(self):
        from src.models_feature_flags import FeatureFlag
        for _ in range(2):
            self.assertFalse(FeatureFlag.is_feature_enabled('maintenance_mode'))

    def test_saving_a_flag_invalidates_immediately(self):
        """A toggle in the admin must take effect at once, not in 5 minutes."""
        from src.models_feature_flags import FeatureFlag
        flag = FeatureFlag.objects.create(
            name='toggle_me', display_name='Toggle Me', is_enabled=True)
        self.assertTrue(FeatureFlag.is_feature_enabled('toggle_me'))

        flag.is_enabled = False
        flag.save()
        self.assertFalse(FeatureFlag.is_feature_enabled('toggle_me'))

    def test_deleting_a_flag_invalidates(self):
        from src.models_feature_flags import FeatureFlag
        flag = FeatureFlag.objects.create(
            name='delete_me', display_name='Delete Me', is_enabled=False)
        self.assertFalse(FeatureFlag.is_feature_enabled('delete_me'))
        flag.delete()
        # Falls back to the fail-open default once the row is gone.
        self.assertTrue(FeatureFlag.is_feature_enabled('delete_me'))

    def test_saving_also_busts_the_template_dict(self):
        """Python and templates must not disagree about a flag."""
        from django.core.cache import cache
        from src.models_feature_flags import FeatureFlag
        cache.set('context_feature_flags', {'feature_flags': {}, 'enabled_pages': {}}, 60)
        FeatureFlag.objects.create(name='ctx_flag', display_name='Ctx', is_enabled=True)
        self.assertIsNone(cache.get('context_feature_flags'))

    def test_cached_lookups_are_still_recorded_for_dev_mode(self):
        from src.models_feature_flags import FeatureFlag
        FeatureFlag.is_feature_enabled('recorded_flag')  # warm
        recorder = dev_mode.start_recording()
        try:
            FeatureFlag.is_feature_enabled('recorded_flag')
        finally:
            dev_mode.stop_recording()
        entry = next(f for f in recorder.flags if f['name'] == 'recorded_flag')
        self.assertIn('cached', entry['source'])


class CapabilitySummaryTests(CacheIsolatedTestCase):
    """
    The Perms panel shows what the user *is*, not only which decorators fired.

    ~31 authorization checks in this codebase are inline `if
    request.user.is_admin` inside a view. Instrumenting each would be churn;
    the capability summary answers for all of them.
    """

    def setUp(self):
        super().setUp()
        # NB: create_user() does not accept is_admin — set it after. This is the
        # same signature mismatch behind the 10 pre-existing failures in
        # test_page_visits_filter.py.
        self.dev = ParliamentUser.objects.create_user(
            user_id='555', name='Cap User', username='capuser', member_type='Officer')
        self.dev.is_admin = True
        self.dev.save(update_fields=['is_admin'])

    def _panel(self):
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            set_dev_mode(self.dev, True)
            self.client.force_login(self.dev)
            return self.client.get(reverse('preferences'))

    def test_admin_status_is_shown(self):
        self.assertContains(self._panel(), 'is_admin')

    def test_django_staff_and_superuser_are_shown(self):
        resp = self._panel()
        self.assertContains(resp, 'django is_staff')
        self.assertContains(resp, 'django is_superuser')

    def test_admin_v2_allowlist_and_session_are_shown(self):
        resp = self._panel()
        self.assertContains(resp, 'admin-v2 allowlisted')
        self.assertContains(resp, 'admin-v2 session')

    def test_roles_are_shown(self):
        self.assertContains(self._panel(), 'roles')


class PassedLegislationQueryBudgetTests(TestCase):
    """
    The N+1 dev mode actually surfaced — on passed_legislation, not the history
    page. Both duplicate groups fired lazily during template rendering, which is
    why the stack pointed at the render() call.
    """

    def setUp(self):
        from src.models import Legislation
        self.author = ParliamentUser.objects.create_user(
            user_id='pl1', name='PL Author', username='plauthor', member_type='Member')
        self.coauthor = ParliamentUser.objects.create_user(
            user_id='pl2', name='PL Coauthor', username='plcoauthor', member_type='Member')

        for i in range(6):
            bill = Legislation.objects.create(
                title='Passed Bill %d' % i, posted_by=self.author,
                required_percentage=51, voting_closed=True, passed=True,
                status='passed', available_at=timezone.now(),
            )
            bill.co_authors.add(self.coauthor)

        self.client.force_login(self.author)

    def _queries(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse('passed_legislation'))
        self.assertEqual(resp.status_code, 200)
        return ctx.captured_queries

    def test_author_is_not_refetched_per_row(self):
        author_fetches = [
            q for q in self._queries()
            if 'src_parliamentuser' in q['sql'] and 'LIMIT 21' in q['sql']
        ]
        self.assertLessEqual(
            len(author_fetches), 1,
            'posted_by is being fetched per row — %d pk lookups' % len(author_fetches),
        )

    def test_attendance_is_fetched_once_not_per_legislation(self):
        attendance = [q for q in self._queries() if 'src_attendance' in q['sql']]
        self.assertLessEqual(
            len(attendance), 2,
            'attendance is being queried per legislation — %d queries' % len(attendance),
        )

    def test_query_count_does_not_grow_with_the_number_of_bills(self):
        from src.models import Legislation
        baseline = len(self._queries())
        for i in range(6):
            bill = Legislation.objects.create(
                title='More %d' % i, posted_by=self.author,
                required_percentage=51, voting_closed=True, passed=True,
                status='passed', available_at=timezone.now(),
            )
            bill.co_authors.add(self.coauthor)
        doubled = len(self._queries())
        self.assertLessEqual(
            doubled - baseline, 1,
            'doubling the legislation added %d queries — page is O(n) again' % (doubled - baseline),
        )

    def test_page_still_renders_every_bill(self):
        resp = self.client.get(reverse('passed_legislation'))
        self.assertEqual(resp.context['total_count'], 6)


class DeferredProfileAuthTests(TestCase):
    """
    request.user is loaded without the profile columns (v3.17.1).

    The per-request user load is unavoidable — that is how request.user exists —
    but its *width* is not. ParliamentUser carries the whole member profile
    (~43 columns incl. five JSON fields), none of which base.html or the nav
    reads.
    """

    def setUp(self):
        self.user = ParliamentUser.objects.create_user(
            user_id='def1', name='Defer User', username='deferuser',
            member_type='Member')
        self.user.about_me = 'a bio nobody needs on every page'
        self.user.majors = ['Computer Science']
        self.user.save(update_fields=['about_me', 'majors'])
        self.client.force_login(self.user)

    def test_profile_columns_are_not_selected_on_an_ordinary_page(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse('preferences'))

        # The session-auth load is the wide one we care about: it is the query
        # that runs on EVERY page. A later narrow `SELECT user_id, about_me`
        # is a deferred field being read by a page that genuinely wants it —
        # that is the trade working as intended, not a failure.
        session_loads = [
            q for q in ctx.captured_queries
            if 'src_parliamentuser' in q['sql'] and 'LIMIT 21' in q['sql']
            and 'member_type' in q['sql']
        ]
        self.assertTrue(session_loads, 'expected the session user to be loaded')
        for query in session_loads:
            for column in ('about_me', 'custom_socials', 'initiation_chapters',
                           'majors', 'minors', 'concentrations'):
                self.assertNotIn(
                    column, query['sql'],
                    '%s is still selected on every request' % column)

    def test_identity_and_authorization_columns_are_still_loaded(self):
        """Deferring an authz field would turn every permission check into a query."""
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse('preferences'))

        user_loads = [
            q for q in ctx.captured_queries
            if 'src_parliamentuser' in q['sql'] and 'LIMIT 21' in q['sql']
        ]
        for column in ('member_type', 'is_admin', 'member_status', 'name'):
            self.assertIn(column, user_loads[0]['sql'], '%s must not be deferred' % column)

    def test_onboarding_data_is_not_deferred(self):
        """It is read by the onboarding checklist, which is on both home layouts."""
        from src.auth_backends import DeferredProfileModelBackend
        self.assertNotIn('onboarding_data', DeferredProfileModelBackend.DEFERRED_FIELDS)

    def test_profile_picture_is_not_deferred(self):
        """The nav avatar reads it on every page."""
        from src.auth_backends import DeferredProfileModelBackend
        self.assertNotIn('profile_picture', DeferredProfileModelBackend.DEFERRED_FIELDS)

    def test_deferred_fields_are_still_readable(self):
        """Correctness is not negotiable — deferral must be transparent."""
        from django.contrib.auth import get_user
        from django.test import RequestFactory

        request = RequestFactory().get('/')
        request.session = self.client.session
        user = get_user(request)
        self.assertEqual(user.about_me, 'a bio nobody needs on every page')
        self.assertEqual(user.majors, ['Computer Science'])

    def test_every_deferred_field_actually_exists_on_the_model(self):
        """A typo here would silently defer nothing, or raise at login."""
        from src.auth_backends import DeferredProfileModelBackend
        names = {f.name for f in ParliamentUser._meta.get_fields()}
        for field in DeferredProfileModelBackend.DEFERRED_FIELDS:
            self.assertIn(field, names, '%s is not a ParliamentUser field' % field)

    def test_backend_get_user_returns_the_right_user(self):
        """
        The backend override must not change who you get back.

        (Deliberately not testing client.login() here: username is an encrypted
        field, so authenticate() by username returns None on the stock
        ModelBackend too. That is pre-existing and unrelated to deferral.)
        """
        from src.auth_backends import DeferredProfileModelBackend
        loaded = DeferredProfileModelBackend().get_user(self.user.pk)
        self.assertEqual(loaded.pk, self.user.pk)
        self.assertEqual(loaded.name, 'Defer User')

    def test_backend_returns_none_for_a_missing_user(self):
        from src.auth_backends import DeferredProfileModelBackend
        self.assertIsNone(DeferredProfileModelBackend().get_user('no-such-user'))


class MemberFieldConstantsTests(SimpleTestCase):
    """MEMBER_DISPLAY_FIELDS / MEMBER_PROFILE_FIELDS must stay coherent."""

    def test_the_two_sets_are_disjoint(self):
        from src.models import MEMBER_DISPLAY_FIELDS, MEMBER_PROFILE_FIELDS
        overlap = set(MEMBER_DISPLAY_FIELDS) & set(MEMBER_PROFILE_FIELDS)
        self.assertEqual(overlap, set(), 'a field cannot be both display and profile')

    def test_every_name_is_a_real_field(self):
        from src.models import MEMBER_DISPLAY_FIELDS, MEMBER_PROFILE_FIELDS, ParliamentUser
        names = {f.name for f in ParliamentUser._meta.get_fields()}
        for field in tuple(MEMBER_DISPLAY_FIELDS) + tuple(MEMBER_PROFILE_FIELDS):
            self.assertIn(field, names, '%s is not a ParliamentUser field' % field)


class LegislationColumnWidthTests(TestCase):
    """
    Neither legislation page may select the member profile columns.

    select_related and prefetch_related each fetch every column by default, so
    removing an N+1 does not by itself stop ~43 columns per author and per
    co-author coming across the wire.
    """

    HEAVY = ('about_me', 'custom_socials', 'initiation_chapters',
             'majors', 'minors', 'concentrations')

    def setUp(self):
        from src.models import Legislation
        self.author = ParliamentUser.objects.create_user(
            user_id='w1', name='Width Author', username='widthauthor',
            member_type='Member')
        self.coauthor = ParliamentUser.objects.create_user(
            user_id='w2', name='Width Coauthor', username='widthco',
            member_type='Member')
        for i in range(3):
            bill = Legislation.objects.create(
                title='Width Bill %d' % i, posted_by=self.author,
                required_percentage=51, voting_closed=True, passed=True,
                status='passed', available_at=timezone.now(),
            )
            bill.co_authors.add(self.coauthor)
        self.client.force_login(self.author)

    def _member_queries(self, url_name):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as ctx:
            resp = self.client.get(reverse(url_name))
        self.assertEqual(resp.status_code, 200)
        return [q for q in ctx.captured_queries if 'src_parliamentuser' in q['sql']]

    def _assert_narrow(self, url_name):
        for query in self._member_queries(url_name):
            # The auth session load is exempt: it is the deferred-profile
            # backend's own query and is already narrow, and a deferred-field
            # follow-up legitimately names one heavy column.
            if 'password' not in query['sql']:
                continue
            for column in self.HEAVY:
                self.assertNotIn(
                    column, query['sql'],
                    '%s selects %s — narrow it with defer()/only(); see '
                    'MEMBER_PROFILE_FIELDS' % (url_name, column),
                )

    def test_passed_legislation_does_not_select_profile_columns(self):
        self._assert_narrow('passed_legislation')

    def test_legislation_history_does_not_select_profile_columns(self):
        self._assert_narrow('view_legislation_history')

    def test_co_author_prefetch_is_narrowed(self):
        """prefetch_related needs an explicit Prefetch queryset to be narrowed."""
        prefetches = [
            q for q in self._member_queries('passed_legislation')
            if 'co_authors' in q['sql']
        ]
        self.assertTrue(prefetches, 'expected the co-author prefetch to run')
        for query in prefetches:
            self.assertNotIn('about_me', query['sql'])

    def test_status_tabs_are_one_aggregate_not_six_counts(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(reverse('passed_legislation'))
        counts = [
            q for q in ctx.captured_queries
            if 'src_legislation' in q['sql'] and 'COUNT' in q['sql'].upper()
            and 'src_vote' not in q['sql']
        ]
        self.assertLessEqual(
            len(counts), 2,
            'status tabs are back to one COUNT per tab — %d queries' % len(counts),
        )

    def test_counts_are_still_correct(self):
        resp = self.client.get(reverse('passed_legislation'))
        counts = resp.context['status_counts']
        self.assertEqual(counts['all'], 3)
        self.assertEqual(counts['passed'], 3)
        self.assertEqual(counts['tabled'], 0)


class PageToggleCacheTests(CacheIsolatedTestCase):
    """@require_page_enabled decorates most views; it was uncached."""

    def test_repeat_lookups_hit_the_cache(self):
        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        from src.models_feature_flags import PageToggle

        PageToggle.is_page_enabled('some_page')  # warm
        with CaptureQueriesContext(connection) as ctx:
            for _ in range(5):
                PageToggle.is_page_enabled('some_page')
        self.assertEqual(
            [q for q in ctx.captured_queries if 'src_pagetoggle' in q['sql']], [])

    def test_missing_toggle_still_defaults_to_enabled(self):
        from src.models_feature_flags import PageToggle
        self.assertTrue(PageToggle.is_page_enabled('never_created'))
        self.assertTrue(PageToggle.is_page_enabled('never_created'))

    def test_saving_invalidates_immediately(self):
        from src.models_feature_flags import PageToggle
        toggle = PageToggle.objects.create(
            url_name='togglable', display_name='Togglable', is_enabled=True)
        self.assertTrue(PageToggle.is_page_enabled('togglable'))
        toggle.is_enabled = False
        toggle.save()
        self.assertFalse(PageToggle.is_page_enabled('togglable'))


class HoverLinkingTests(CacheIsolatedTestCase):
    """
    Dwell-hover links a page element to the queries that produced it.

    The link is by database table: `dev_value` emits data-pdev-table, every SQL
    and Shapes row carries data-pdev-tables, and the script matches them. If
    either side stops emitting the attribute the feature silently does nothing,
    so both are asserted.
    """

    def setUp(self):
        super().setUp()
        self.dev = ParliamentUser.objects.create_user(
            user_id='555', name='Hover User', username='hoveruser',
            member_type='Member')

    def test_dev_value_emits_the_db_table(self):
        recorder = dev_mode.start_recording()
        try:
            out = Template("{% load dev_tags %}{% dev_value obj 'name' %}").render(
                Context({'obj': self.dev}))
        finally:
            dev_mode.stop_recording()
        self.assertIn('data-pdev-table="src_parliamentuser"', out)

    def test_table_is_shown_in_the_hover_tooltip(self):
        recorder = dev_mode.start_recording()
        try:
            out = Template("{% load dev_tags %}{% dev_value obj 'name' %}").render(
                Context({'obj': self.dev}))
        finally:
            dev_mode.stop_recording()
        self.assertIn('src_parliamentuser', out)

    def test_panel_rows_carry_their_tables(self):
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            set_dev_mode(self.dev, True)
            self.client.force_login(self.dev)
            resp = self.client.get(reverse('preferences'))
        self.assertContains(resp, 'data-pdev-tables=')

    def test_gated_values_still_link(self):
        """A withheld value should still tell you which table it came from."""
        dev_mode.start_recording()
        try:
            out = Template(
                "{% load dev_tags %}{% dev_value obj 'name' gated_by=no %}"
            ).render(Context({'obj': self.dev, 'no': False}))
        finally:
            dev_mode.stop_recording()
        self.assertIn('data-pdev-table="src_parliamentuser"', out)
        self.assertNotIn('Hover User', out)
