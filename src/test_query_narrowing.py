"""
v3.17.3 — tests for the join-narrowing sweep and the 07-29 auto-run fixes.

Three groups, in rough order of how much they would hurt if they broke:

1. `MemberDeferIntegrityTests` — every relation path handed to `member_defer()`
   anywhere in `src/` must resolve to a real relation ending on ParliamentUser.
   Django raises `FieldError` on an unknown `defer()` name at query-compile
   time, so a typo here is a 500 on whatever page owns that queryset — and for
   `DeferredProfileModelBackend`, that page is *every* authenticated page.

2. `QueryBudgetTests` — the legislation pages must not do more work as the
   chapter's archive grows. These assert **growth**, not a fixed query count:
   a fixed number is a test that fails every time someone adds a feature, which
   teaches people to update the number without reading why it moved.

3. The rest — narrower regression tests for the individual fixes.
"""

import ast
import pathlib
from datetime import timedelta

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import Client, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from src.auth_backends import DeferredProfileModelBackend
from src.models import Attendance, Legislation, ParliamentUser, Vote
from src.models.users import (
    MEMBER_DISPLAY_FIELDS,
    MEMBER_PROFILE_FIELDS,
    member_defer,
    member_prefetch,
)
from src.models_feature_flags import FeatureFlag, PageToggle

SRC = pathlib.Path(__file__).resolve().parent


def make_user(uid, name=None, username=None, member_type='Active', **extra):
    return ParliamentUser.objects.create_user(
        user_id=uid,
        name=name or f'Member {uid}',
        username=username or f'user{uid}',
        member_type=member_type,
        password='testpass123!',
        **extra,
    )


# ---------------------------------------------------------------------------
# 1. member_defer integrity
# ---------------------------------------------------------------------------

class MemberDeferIntegrityTests(TestCase):
    """A deferred field name that isn't real is a 500, not a slow page."""

    def _member_relation_names(self):
        names = set()
        for model in apps.get_models():
            for field in model._meta.get_fields():
                if getattr(field, 'related_model', None) is ParliamentUser:
                    names.add(field.name)
        return names

    def test_every_member_defer_argument_is_a_member_relation(self):
        """
        Scan every `member_defer('x', 'y__z')` call in src/ and check that the
        final hop of each path is a relation that points at ParliamentUser.

        This is the check that makes the ~120-site v3.17.3 sweep safe to repeat:
        the transformation is mechanical, so the guard has to be mechanical too.
        """
        member_names = self._member_relation_names()
        self.assertIn('user', member_names, 'sanity: `user` should be a member FK')

        checked, bad = 0, []
        for path in sorted(SRC.rglob('*.py')):
            if path.name == 'test_query_narrowing.py':
                continue
            source = path.read_text(encoding='utf-8')
            if 'member_defer(' not in source:
                continue
            for node in ast.walk(ast.parse(source)):
                if not (isinstance(node, ast.Call)
                        and getattr(node.func, 'id', '') == 'member_defer'):
                    continue
                for arg in node.args:
                    if not isinstance(arg, ast.Constant):
                        continue
                    checked += 1
                    if arg.value.split('__')[-1] not in member_names:
                        bad.append(f'{path.relative_to(SRC)}:{node.lineno} {arg.value!r}')

        self.assertGreater(checked, 50, 'expected the sweep to have left many call sites')
        self.assertEqual(bad, [], 'member_defer() paths that do not end on ParliamentUser')

    def test_member_relation_names_are_unambiguous(self):
        """
        The sweep keys off relation *names*. If some model ever grows a
        `reviewer` FK pointing at, say, Committee, deferring `reviewer__about_me`
        on that model becomes a FieldError. Fail here rather than there.
        """
        member_names = self._member_relation_names()
        conflicts = []
        for model in apps.get_models():
            for field in model._meta.get_fields():
                related = getattr(field, 'related_model', None)
                if related is None or field.name not in member_names:
                    continue
                if related is not ParliamentUser and field.name != 'parliamentuser':
                    conflicts.append(f'{model.__name__}.{field.name} -> {related.__name__}')
        self.assertEqual(conflicts, [], 'relation name reused for a non-member target')

    def test_member_defer_builds_usable_paths(self):
        """The helper's output must actually work on a real queryset."""
        author = make_user('md1')
        Legislation.objects.create(
            title='L', description='d', posted_by=author,
            available_at=timezone.now(), vote_mode='percentage',
            required_percentage='50',
        )
        qs = (Legislation.objects
              .select_related('posted_by')
              .defer(*member_defer('posted_by')))
        leg = qs.get()                      # compiles the SQL — FieldError if wrong
        self.assertEqual(leg.posted_by.name, author.name)
        from src.models.users import MEMBER_ACCOUNT_FIELDS

        self.assertEqual(
            len(member_defer('posted_by', 'co_authors')),
            2 * (len(MEMBER_PROFILE_FIELDS) + len(MEMBER_ACCOUNT_FIELDS)),
        )

    def test_member_prefetch_narrows_and_still_returns_members(self):
        author = make_user('md2')
        coauthor = make_user('md3', name='Co Author')
        leg = Legislation.objects.create(
            title='L', description='d', posted_by=author,
            available_at=timezone.now(), vote_mode='percentage',
            required_percentage='50',
        )
        leg.co_authors.add(coauthor)
        fetched = (Legislation.objects
                   .prefetch_related(member_prefetch('co_authors'))
                   .get(pk=leg.pk))
        got = list(fetched.co_authors.all())
        self.assertEqual([u.name for u in got], ['Co Author'])
        # The display columns must be present without a further query...
        with self.assertNumQueries(0):
            _ = got[0].name, got[0].member_type
        # ...and the profile columns must not be (deferred, so touching costs one).
        with self.assertNumQueries(1):
            _ = got[0].about_me


class DeferredProfileFieldTests(TestCase):
    """
    `DeferredProfileModelBackend.get_user` runs on every authenticated request,
    so a bad name here is a site-wide outage rather than a slow page.
    """

    def test_all_deferred_names_are_real_fields(self):
        names = set()
        for field in ParliamentUser._meta.get_fields():
            names.add(field.name)
            if hasattr(field, 'attname'):
                names.add(field.attname)
        unknown = [f for f in DeferredProfileModelBackend.DEFERRED_FIELDS if f not in names]
        self.assertEqual(unknown, [], 'DEFERRED_FIELDS names that are not model fields')

    def test_no_duplicates(self):
        fields = DeferredProfileModelBackend.DEFERRED_FIELDS
        self.assertEqual(len(fields), len(set(fields)))

    def test_hot_path_fields_are_not_deferred(self):
        """Documented exclusions — deferring these adds a query to a common page."""
        for field in ('onboarding_data', 'profile_picture', 'name', 'preferred_name',
                      'member_type', 'member_status', 'is_admin', 'username', 'email'):
            self.assertNotIn(field, DeferredProfileModelBackend.DEFERRED_FIELDS, field)

    def test_get_user_still_returns_a_usable_user(self):
        user = make_user('df1')
        loaded = DeferredProfileModelBackend().get_user(user.pk)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.name, user.name)
        self.assertIsNone(DeferredProfileModelBackend().get_user('no-such-user'))

    def test_display_and_profile_field_lists_stay_disjoint(self):
        self.assertFalse(set(MEMBER_DISPLAY_FIELDS) & set(MEMBER_PROFILE_FIELDS))


# ---------------------------------------------------------------------------
# 2. Query budgets
# ---------------------------------------------------------------------------

class QueryBudgetTests(TestCase):
    """
    Both legislation pages show 20 items. Neither should care how many bills
    exist behind those 20.
    """

    def _build(self, n_bills, members=6, noise_per_week=6):
        author = make_user('qb0', member_type='Active', is_admin=True)
        member_objs = [make_user(f'qb{i + 1}') for i in range(members)]
        now = timezone.now()
        for i in range(n_bills):
            end = now - timedelta(weeks=i + 1)
            leg = Legislation.objects.create(
                title=f'Bill {i}', description='d', posted_by=author,
                available_at=end - timedelta(hours=6),
                voting_starts_at=end - timedelta(hours=6),
                voting_ended_at=end, voting_closed=True, status='passed',
                passed=True, vote_mode='percentage', required_percentage='50',
            )
            Vote.objects.create(user=author, legislation=leg, vote_choice='yes')
            for m in member_objs:
                row = Attendance.objects.create(
                    attendance_type='event', user=m, status='present')
                Attendance.objects.filter(pk=row.pk).update(
                    created_at=end - timedelta(hours=1))
            # committee attendance during the same week but outside every
            # 6-hour voting window — ordinary chapter activity, and the rows
            # the pre-v3.17.3 bounding box dragged in for nothing.
            for j in range(noise_per_week):
                row = Attendance.objects.create(
                    attendance_type='committee', user=member_objs[j % members],
                    status='present')
                Attendance.objects.filter(pk=row.pk).update(
                    created_at=end - timedelta(days=3, hours=j % 12))
        return author

    def _queries_for(self, url, author):
        client = Client()
        client.force_login(author)
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(url)
        self.assertEqual(response.status_code, 200)
        return len(ctx.captured_queries), response

    def test_passed_legislation_does_not_grow_with_the_archive(self):
        small = self._queries_for('/passed_legislation/?status=all', self._build(4))[0]
        Attendance.objects.all().delete()
        Vote.objects.all().delete()
        Legislation.objects.all().delete()
        ParliamentUser.objects.all().delete()
        large, response = self._queries_for('/passed_legislation/?status=all', self._build(30))

        self.assertLessEqual(len(response.context['passed_legislation']), 20)
        # A little slack for genuinely conditional work (plurality tally, the
        # personal-tab probe), but nowhere near proportional to 30/4.
        self.assertLessEqual(
            large, small + 3,
            f'query count grew with archive size: {small} -> {large}',
        )

    def test_passed_legislation_pages_are_full(self):
        """
        The property that paginating *after* the per-row filter would break:
        every page but the last holds a full 20 rows, and the rows across all
        pages add up to the count shown in the header.
        """
        author = self._build(45)
        client = Client()
        client.force_login(author)
        sizes, page, total = [], 1, None
        while True:
            response = client.get(f'/passed_legislation/?status=all&page={page}')
            sizes.append(len(response.context['passed_legislation']))
            total = response.context['total_count']
            if not response.context['page_obj'].has_next():
                break
            page += 1
        self.assertTrue(all(size == 20 for size in sizes[:-1]), sizes)
        self.assertEqual(sum(sizes), total)

    def test_legislation_history_is_paginated_and_flat(self):
        author = self._build(4)
        small = self._queries_for('/legislation/history/', author)[0]
        Attendance.objects.all().delete()
        Vote.objects.all().delete()
        Legislation.objects.all().delete()
        ParliamentUser.objects.all().delete()
        author = self._build(30)
        large, response = self._queries_for('/legislation/history/', author)
        self.assertLessEqual(len(response.context['legislation_history']), 20)
        self.assertLessEqual(
            large, small + 3,
            f'query count grew with archive size: {small} -> {large}',
        )

    def test_attendance_is_one_query_for_the_whole_page(self):
        author = self._build(30)
        client = Client()
        client.force_login(author)
        with CaptureQueriesContext(connection) as ctx:
            client.get('/passed_legislation/?status=all')
        attendance = [
            q for q in ctx.captured_queries
            if 'src_attendance' in q['sql'] and q['sql'].lstrip().upper().startswith('SELECT')
        ]
        self.assertEqual(len(attendance), 1, 'attendance should be one batched fetch')
        # And it must be narrow: the profile columns have no business here.
        sql = attendance[0]['sql']
        for column in ('about_me', 'custom_socials', 'initiation_chapters'):
            self.assertNotIn(column, sql, f'{column} selected on the legislation list')


# ---------------------------------------------------------------------------
# 3. The individual 07-29 fixes
# ---------------------------------------------------------------------------

class UserManagerTests(TestCase):
    """
    `create_user` accepted a `username` and then overwrote it with `name`.
    See the docstring on ParliamentUserManager.create_user.
    """

    def test_username_argument_is_honoured(self):
        user = make_user('um1', name='John Smith', username='jsmith')
        self.assertEqual(user.username, 'jsmith')
        self.assertEqual(user.name, 'John Smith')

    def test_two_members_with_the_same_name_can_both_be_created(self):
        """
        `username` is unique. While it was being overwritten with `name`, the
        second John Smith raised IntegrityError — and the uniqueness check the
        callers run (`ensure_unique_username`) was being applied to a value
        that never reached the database.
        """
        first = make_user('um2', name='John Smith', username='jsmith')
        second = make_user('um3', name='John Smith', username='jsmith2')
        self.assertNotEqual(first.username, second.username)
        self.assertEqual(first.name, second.name)

    def test_login_works_with_the_username_that_was_set(self):
        make_user('um4', name='Jane Doe', username='jdoe')
        self.assertTrue(Client().login(username='jdoe', password='testpass123!'))

    def test_extra_fields_are_applied(self):
        user = make_user('um5', is_admin=True, member_status='Alumni')
        self.assertTrue(user.is_admin)
        self.assertEqual(user.member_status, 'Alumni')

    def test_create_superuser_is_an_admin(self):
        user = get_user_model().objects.create_superuser(
            user_id='um6', name='Root', username='root',
            member_type='Active', password='testpass123!',
        )
        self.assertTrue(user.is_admin)
        self.assertEqual(user.username, 'root')

    def test_blank_username_still_rejected(self):
        with self.assertRaises(ValueError):
            ParliamentUser.objects.create_user(
                user_id='um7', name='N', username='', member_type='Active')


class FlagCacheInvalidationTests(TestCase):
    """
    Invalidation used to hang off `save()`/`delete()`, which `queryset.delete()`
    — the Django admin's "Delete selected" action — never calls.
    """

    def setUp(self):
        cache.clear()

    def test_toggle_takes_effect_immediately(self):
        flag = FeatureFlag.objects.create(
            name='budget_test_flag', display_name='T', description='d',
            is_enabled=True)
        self.assertTrue(FeatureFlag.is_feature_enabled('budget_test_flag'))
        flag.is_enabled = False
        flag.save()
        self.assertFalse(FeatureFlag.is_feature_enabled('budget_test_flag'))

    def test_bulk_delete_invalidates(self):
        FeatureFlag.objects.create(
            name='bulk_delete_flag', display_name='T', description='d',
            is_enabled=False)
        self.assertFalse(FeatureFlag.is_feature_enabled('bulk_delete_flag'))  # caches False
        FeatureFlag.objects.filter(name='bulk_delete_flag').delete()          # no Model.delete()
        # No row now, and the name is not DISABLED_BY_DEFAULT, so the documented
        # fail-open default applies. Before v3.17.3 this returned the stale False.
        self.assertTrue(FeatureFlag.is_feature_enabled('bulk_delete_flag'))

    def test_page_toggle_bulk_delete_invalidates(self):
        PageToggle.objects.create(
            url_name='bulk_delete_page', display_name='T', is_enabled=False)
        self.assertFalse(PageToggle.is_page_enabled('bulk_delete_page'))
        PageToggle.objects.filter(url_name='bulk_delete_page').delete()
        self.assertTrue(PageToggle.is_page_enabled('bulk_delete_page'))

    def test_context_dict_is_dropped_too(self):
        """Python and templates must not disagree after a write."""
        cache.set('context_feature_flags', {'feature_flags': {'x': True}}, 300)
        FeatureFlag.objects.create(
            name='ctx_flag', display_name='T', description='d', is_enabled=True)
        self.assertIsNone(cache.get('context_feature_flags'))


class AttendanceLoggingTests(TestCase):
    """Member attendance rosters should not be written to the app log on a GET."""

    def test_present_members_are_not_logged_by_name(self):
        source = (SRC / 'view' / 'passed_legislation.py').read_text(encoding='utf-8')
        self.assertNotRegex(
            source,
            r'logger\.info\(f?["\'][^"\']*present members',
            'attendance rosters must not be logged at INFO with member names',
        )
        self.assertIn('logger.debug', source)


# ---------------------------------------------------------------------------
# 4. Dev-mode template attribution (v3.17.3, second pass)
# ---------------------------------------------------------------------------

class TemplateAttributionTests(TestCase):
    """
    A query fired during rendering must name the template expression that
    caused it.

    Before this, such a query's only project stack frame was the view's
    `return render(...)` line — so the panel showed six identical member
    fetches all attributed to `home.py:280`, and the actual cause
    (`{{ announcement.posted_by.get_display_name }}`) appeared nowhere. You
    could see *that* there was an N+1 and not *where*, which is most of the
    work.
    """

    def setUp(self):
        from src.dev_mode import install_template_node_instrumentation
        install_template_node_instrumentation()
        self.author = make_user('ta1', name='Ada Author')

    def _render_with_recording(self, template_string, context):
        from django.template import Context, Template
        from src.dev_mode import (current_template_frames, start_recording,
                                  stop_recording)

        hits = []
        start_recording()
        try:
            def wrapper(execute, sql, params, many, context_):
                frames = current_template_frames()
                if frames:
                    hits.append(frames[-1])
                return execute(sql, params, many, context_)

            with connection.execute_wrapper(wrapper):
                Template(template_string).render(Context(context))
        finally:
            stop_recording()
        return hits

    def test_lazy_query_is_attributed_to_the_template_expression(self):
        Legislation.objects.create(
            title='L', description='d', posted_by=self.author,
            available_at=timezone.now(), vote_mode='percentage',
            required_percentage='50',
        )
        # No select_related — dereferencing posted_by in the template is a query.
        legislation = list(Legislation.objects.all())
        hits = self._render_with_recording(
            '{% for l in items %}{{ l.posted_by.name }}{% endfor %}',
            {'items': legislation},
        )
        self.assertTrue(hits, 'a lazily-fired query recorded no template frame')
        self.assertIn('posted_by', hits[-1]['source'])
        self.assertIsNotNone(hits[-1]['line'])

    def test_frames_are_empty_outside_rendering(self):
        """
        A query the view issues itself must NOT be attributed to a template —
        that distinction is the whole signal.
        """
        from src.dev_mode import current_template_frames, start_recording, stop_recording

        start_recording()
        try:
            self.assertEqual(current_template_frames(), [])
        finally:
            stop_recording()

    def test_stack_is_unwound_even_when_a_node_raises(self):
        """A template error must not leave the frame stack dirty for later queries."""
        from django.template import Context, Template
        from src.dev_mode import current_template_frames, start_recording, stop_recording

        start_recording()
        try:
            with self.assertRaises(Exception):
                Template('{% for x in items %}{{ x.boom }}{% endfor %}').render(
                    Context({'items': [BoomOnAccess()]}))
            self.assertEqual(current_template_frames(), [])
        finally:
            stop_recording()

    def test_instrumentation_is_inert_when_dev_mode_is_off(self):
        from django.template import Context, Template
        from src.dev_mode import current_template_frames

        # No recorder started — the wrapper must short-circuit and record nothing.
        Template('{{ x }}').render(Context({'x': 1}))
        self.assertEqual(current_template_frames(), [])

    def test_duplicate_groups_carry_the_template_that_fired_them(self):
        from src.dev_mode import find_duplicate_queries

        query = {
            'sql': 'SELECT * FROM src_parliamentuser WHERE user_id = 1',
            'ms': 0.4, 'rows': 1, 'stack': [], 'tables': ['src_parliamentuser'],
            'template': [{'template': 'home_modern.html', 'line': 317,
                          'source': 'announcement.posted_by.get_display_name'}],
        }
        duplicates = find_duplicate_queries([dict(query) for _ in range(6)])
        self.assertEqual(len(duplicates), 1)
        _shape, count, _ms, _sample, _stacks, templates = duplicates[0]
        self.assertEqual(count, 6)
        self.assertEqual(templates[0]['where'], 'home_modern.html:317')
        self.assertIn('posted_by', templates[0]['source'])


class BoomOnAccess:
    """Raises when a template touches `.boom`, to test stack unwinding."""

    @property
    def boom(self):
        raise RuntimeError('boom')


class NoCredentialColumnsOnJoinsTests(TestCase):
    """
    A page that prints someone's name must not drag their password hash out of
    the database.

    v3.17.3 (second pass). `member_defer` originally dropped only the profile
    columns, leaving ~29 on every joined member — including `password`, the
    argon2/pbkdf2 hash, selected into the result set of essentially every list
    page on the site. Never rendered, so not a disclosure; but "we don't select
    it unless we need it" is a cheaper rule for a successor to keep than "we
    select it everywhere but never print it".
    """

    #: Meaningful only for the logged-in user. See MEMBER_ACCOUNT_FIELDS.
    CREDENTIAL_COLUMNS = ('password', 'last_login', 'force_password_change',
                          'has_default_password', 'backup_codes_acknowledged')

    def setUp(self):
        from src.models import Announcement, Event

        self.admin = make_user('nc0', member_type='Officer', is_admin=True)
        members = [make_user(f'nc{i + 1}') for i in range(4)]
        now = timezone.now()
        for i in range(4):
            Announcement.objects.create(
                title=f'A{i}', content='c', posted_by=members[i], is_active=True)
            Event.objects.create(
                title=f'E{i}', description='d', date_time=now + timedelta(days=i + 1),
                created_by=members[i], is_active=True)
            leg = Legislation.objects.create(
                title=f'L{i}', description='d', posted_by=members[i],
                available_at=now - timedelta(days=i + 1),
                voting_ended_at=now - timedelta(days=i), voting_closed=True,
                status='passed', passed=True, vote_mode='percentage',
                required_percentage='50')
            Vote.objects.create(user=members[i], legislation=leg, vote_choice='yes')

    def test_member_defer_drops_the_credential_columns(self):
        deferred = member_defer('posted_by')
        for column in self.CREDENTIAL_COLUMNS:
            self.assertIn(f'posted_by__{column}', deferred, column)

    def test_pages_do_not_select_credentials_on_a_join(self):
        client = Client()
        client.force_login(self.admin)
        offenders = []
        for url in ('/home/', '/announcements/', '/officers/activity-logs/',
                    '/passed_legislation/?status=all', '/calendar/export/'):
            with CaptureQueriesContext(connection) as ctx:
                client.get(url)
            for query in ctx.captured_queries:
                sql = query['sql']
                # Only joins — the session-user load legitimately needs the
                # hash, and it is a plain `FROM src_parliamentuser` with no join.
                if 'JOIN "src_parliamentuser"' not in sql:
                    continue
                for column in self.CREDENTIAL_COLUMNS:
                    if f'"src_parliamentuser"."{column}"' in sql:
                        offenders.append(f'{url}: {column}')
        self.assertEqual(sorted(set(offenders)), [])

    def test_session_user_keeps_its_credentials(self):
        """
        The other half, and the reason MEMBER_ACCOUNT_FIELDS must never reach
        `DeferredProfileModelBackend`: `request.user.password` backs
        `get_session_auth_hash()`, checked on every authenticated request.
        """
        from src.models.users import MEMBER_ACCOUNT_FIELDS

        for field in MEMBER_ACCOUNT_FIELDS:
            self.assertNotIn(field, DeferredProfileModelBackend.DEFERRED_FIELDS, field)

        loaded = DeferredProfileModelBackend().get_user(self.admin.pk)
        with self.assertNumQueries(0):
            self.assertTrue(loaded.password)          # present, not deferred
        self.assertTrue(loaded.check_password('testpass123!'))

    def test_one_source_of_truth_for_join_deferral(self):
        """
        Hand-rolled `f'rel__{f}' for f in MEMBER_PROFILE_FIELDS` lists are how
        two views missed the credential columns when member_defer gained them.
        Everything must go through the helper.
        """
        import re

        offenders = []
        for path in sorted(SRC.rglob('*.py')):
            # users.py defines the constant; auth_backends.py derives the
            # SESSION-user deferral from it and must NOT use member_defer —
            # that helper now also drops the credential columns, which the
            # logged-in user genuinely needs.
            if (path.name.startswith('test_')
                    or path.name in ('users.py', 'auth_backends.py')):
                continue
            source = path.read_text(encoding='utf-8')
            if re.search(r"for \w+ in MEMBER_PROFILE_FIELDS", source):
                offenders.append(str(path.relative_to(SRC)))
        self.assertEqual(
            offenders, [],
            'build deferral with member_defer() rather than looping the constant',
        )
