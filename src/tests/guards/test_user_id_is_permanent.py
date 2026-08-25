"""
`user_id` never changes after creation, and initiation proves it (v3.23.0).

⚠️ THE PROPERTY THIS MODULE EXISTS TO DEFEND.

`ParliamentUser.user_id` is the primary key, so 150 foreign-key columns in this
schema hold a *copy of the string*. `src_vote.user_id` literally contains
`'P-C7JKZY'`. Changing it is therefore not a row update — it invalidates 150
tables' worth of pointers at once.

That is what initiation used to do, and the cost was ~180 lines of raw SQL:
rename the unique columns behind a `_migrating_` prefix, copy the row by
introspecting `information_schema`, walk `_meta` repointing every relation,
consult a hand-maintained list of non-ORM tables, consult a *second*
hand-maintained list of CASCADE tables, delete the original.

**None of it was necessary.** `role_number` has had its own column the whole
time, described in its own help text as *"assigned at initiation (unique
identifier visible to members)"*, rendered by 32 templates. Initiation was
changing the primary key to a value it was separately storing correctly.

> **A primary key must be something that can never need to change. The moment a
> value is both an identifier and information, you have bet that the information
> is permanent** — and this one was not, because initiation changed it on
> purpose.

⚠️ WHY THE RELATION TEST WALKS `_meta` RATHER THAN CHECKING A FEW TABLES.

The old code's two hand-maintained lists are the reason this release exists: a
relation nobody remembered to add was a relation silently orphaned. So
`test_every_relation_survives_initiation` derives the population from the schema
and would go red if a future model's rows stopped following their owner. It
asserts about **this schema**, not about Django — the distinction v3.21.7 had to
make about an enumeration that could not fail.
"""
import re

from django.apps import apps
from django.test import TestCase
from django.urls import reverse

from src.models import (Attendance, Event, ParliamentUser, PledgeTask,
                        PledgeTaskCompletion)
from src.models.users import (MEMBER_UID_ALPHABET, MEMBER_UID_LENGTH,
                              MEMBER_UID_PREFIX, generate_member_uid)


class TheGeneratedIdTests(TestCase):

    def test_it_has_the_shape_we_asked_for(self):
        uid = generate_member_uid()
        self.assertTrue(uid.startswith(MEMBER_UID_PREFIX))
        self.assertEqual(len(uid), len(MEMBER_UID_PREFIX) + MEMBER_UID_LENGTH)
        self.assertRegex(uid, rf'^{re.escape(MEMBER_UID_PREFIX)}[{MEMBER_UID_ALPHABET}]+$')

    def test_the_alphabet_excludes_characters_that_are_read_wrongly(self):
        """
        ⚠️ Not style. These ids are read aloud across a room and typed off a
        phone by somebody who has had the account for four minutes. `O` vs `0`
        costs a support conversation; the entropy it costs is irrelevant at
        ~1e9 combinations.
        """
        confusable_pairs = (('0', 'O'), ('1', 'I'), ('1', 'L'),
                            ('5', 'S'), ('2', 'Z'), ('8', 'B'))
        for digit, letter in confusable_pairs:
            with self.subTest(pair=f'{digit}/{letter}'):
                self.assertFalse(
                    digit in MEMBER_UID_ALPHABET and letter in MEMBER_UID_ALPHABET,
                    f'Both {digit!r} and {letter!r} are in the alphabet, so an '
                    f'id can be transcribed into a different valid id.',
                )

    def test_it_does_not_collide_with_an_existing_member(self):
        existing = generate_member_uid()
        ParliamentUser.objects.create(
            user_id=existing, name='Taken', username='taken',
            member_type='Pledge', member_status='Active',
        )
        self.assertNotEqual(generate_member_uid(), existing)

    def test_it_is_unique_across_a_realistic_pledge_class(self):
        """CONTROL against a generator that returns a constant."""
        self.assertEqual(len({generate_member_uid() for _ in range(200)}), 200)

    def test_it_raises_rather_than_looping_forever_when_the_space_collapses(self):
        """
        A one-character alphabet makes every candidate identical, which is the
        shape of a real defect (a truncated constant, a mocked `secrets`).
        Failing loudly is the only useful response.
        """
        ParliamentUser.objects.create(
            user_id='X-AAAAAA', name='Blocker', username='blocker',
            member_type='Pledge', member_status='Active',
        )
        with self.assertRaises(RuntimeError):
            _exhaust_single_value_space()


def _exhaust_single_value_space():
    from unittest import mock
    with mock.patch('secrets.choice', return_value='A'):
        return generate_member_uid(prefix='X-', length=6)


class InitiationKeepsThePrimaryKeyTests(TestCase):
    """The reproduction, end to end through the real endpoint."""

    def setUp(self):
        self.officer = ParliamentUser.objects.create_user(
            user_id='900', password='initiate-test-pass-12345!',
            name='Officer Oak', username='officer_oak',
            member_type='Officer', is_admin=True,
        )
        self.pledge = ParliamentUser.objects.create_user(
            user_id=generate_member_uid(), password='pledge-test-pass-12345!',
            name='Pledge Pine', username='pledge_pine', member_type='Pledge',
        )
        self.client.force_login(self.officer)

    def _initiate(self, role_number='173'):
        import json
        return self.client.post(
            reverse('initiate_pledges'),
            data=json.dumps({'pledges': [
                {'user_id': self.pledge.user_id, 'role_number': role_number},
            ]}),
            content_type='application/json',
        )

    def test_the_primary_key_is_untouched(self):
        original = self.pledge.user_id

        response = self._initiate()

        self.assertEqual(response.status_code, 200, response.content[:400])
        self.pledge.refresh_from_db()
        self.assertEqual(
            self.pledge.user_id, original,
            'Initiation moved the primary key. That is the operation this '
            'release exists to delete — 150 FK columns hold a copy of it.',
        )

    def test_the_roll_number_lands_in_role_number(self):
        self._initiate(role_number='173')

        self.pledge.refresh_from_db()
        self.assertEqual(self.pledge.role_number, '173')
        self.assertEqual(self.pledge.member_type, 'Member')

    def test_there_is_exactly_one_row_for_the_person(self):
        """
        The old path INSERTed a copy and then DELETEd the original, so a failure
        between the two left the chapter with two user records for one member.
        """
        self._initiate()

        self.assertEqual(
            ParliamentUser.objects.filter(name='Pledge Pine').count(), 1,
        )

    def test_every_relation_survives_initiation(self):
        """
        ⚠️ The point of the whole change. Under the old path each of these had
        to be found and repointed by hand; now they are simply never disturbed,
        because the row they point at did not move.
        """
        from django.utils import timezone
        event = Event.objects.create(
            title='Chapter', description='x', date_time=timezone.now(),
            created_by=self.officer,
        )
        Attendance.objects.create(user=self.pledge, event=event, present=True)
        task = PledgeTask.objects.create(
            title='Task', task_type='reading', activation_mode='immediate',
        )
        PledgeTaskCompletion.objects.create(
            task=task, pledge=self.pledge, status='completed',
        )

        self._initiate()
        self.pledge.refresh_from_db()

        self.assertEqual(self.pledge.attendance_records.count(), 1)
        self.assertEqual(self.pledge.task_completions.count(), 1)

    def test_a_duplicate_roll_number_initiates_nobody(self):
        """
        ⚠️ ONE transaction for the batch. The old code committed per pledge and
        returned a 500 on the first failure, so a class of ten failing on the
        seventh left six initiated and four not, with no record of which.
        """
        ParliamentUser.objects.create(
            user_id='other', name='Other', username='other',
            member_type='Member', member_status='Active', role_number='173',
        )

        response = self._initiate(role_number='173')

        self.assertEqual(response.status_code, 400)
        self.pledge.refresh_from_db()
        self.assertEqual(self.pledge.member_type, 'Pledge')


#: Identifiers that hold a *member* rather than a row that merely points at one.
#:
#: ⚠️ This list is the weak part of detector (a) and it is deliberately visible
#: rather than buried in a regex, so that widening it is a one-line edit made on
#: purpose. `user_id` is also the FK attname on ~150 other models, so
#: `vote.user_id = 5` is ordinary and correct and must not be flagged — which is
#: why this cannot simply match every `.user_id =`.
_MEMBER_VARIABLE_NAMES = frozenset({
    'user', 'member', 'pledge', 'brother', 'person', 'account',
    'target', 'target_user', 'old_user', 'new_user', 'existing_user',
    'parliament_user', 'member_obj', 'user_obj', 'initiate', 'candidate',
})

#: Callables that bring a `ParliamentUser` row into existence or rewrite one in
#: bulk. A `user_id=` keyword on any of these is a write to the primary key.
_USER_WRITING_CALLS = frozenset({
    'ParliamentUser', 'create_user', 'create_superuser', 'update', 'update_or_create',
})


#: ⚠️ THE CREATION ALLOWLIST — a RATCHET, and it may only shrink.
#:
#: These are the functions permitted to put a `user_id` on a `ParliamentUser`,
#: because each of them is making a person who did not exist a moment ago. An
#: INSERT cannot invalidate a foreign key; only moving an existing row's key can.
#:
#: It is keyed on (file, enclosing function) rather than on the file, and that is
#: the load-bearing detail: `src/view/officer/manage_members.py` legitimately
#: creates members in `add_member` and `bulk_import_members`, and it is also
#: where `initiate_pledges` lives — the function whose ~190 lines of key-moving
#: raw SQL v3.23.0 deleted. Exempting the file would have re-opened exactly the
#: hole this module exists to close.
#:
#: **Adding an entry is a deliberate act that shows up in a diff, and it needs a
#: reason written beside it.** Renaming a variable or moving a line to get past
#: a detector is not one.
_CREATION_SITES = {
    # The manager. Building the row is its entire job.
    ('src/models/users.py', 'create_user'),
    ('src/models/users.py', 'create_superuser'),
    ('src/models/users.py', 'generate_member_uid'),
    # Add Member: the officer-facing creation form and its view.
    ('src/forms.py', 'clean_user_id'),
    ('src/view/officer/manage_members.py', 'add_member'),
    # Bulk roster import — same operation, many rows. The INSERT itself lives in
    # the per-row helper, which is what the walk sees.
    ('src/view/officer/manage_members.py', 'bulk_import_members'),
    ('src/view/officer/manage_members.py', '_import_member_row'),
    # Django admin's raw "Add user" page (found 08-25-26: readonly_fields
    # covering the add form too meant a new row saved with pk='' and 500'd on
    # the post-save redirect — nothing upstream generated an id for it). This
    # is the same generation used by Add Member, just reached from /admin/.
    ('src/admin.py', 'save_model'),
}


def _scan_for_key_writes(root='src', allowed=(), creation_sites=frozenset()):
    """
    Every place in `root` that writes a member's primary key, by AST.

    ⚠️ v3.24.0 — THIS WAS A LINE REGEX AND THE REGEX MISSED THE ONE THAT
    MATTERED. It was `(?:user|member|pledge)\\.user_id\\s*=`, i.e. attribute
    assignment on a variable with one of three names, and the docstring above it
    claimed it "fails the build if any code outside creation assigns to this
    field". `src/admin.py` wrote `user_id=new_user_id` as a **constructor
    keyword**, so the single most dangerous write in the app — a red *Migrate
    User ID* button on every row of the member list, which moved the pk and
    silently CASCADEd away the member's 2FA requirement — was invisible to the
    guard written to forbid exactly it.

    > **A GUARD WRITTEN AGAINST ONE SYNTAX IS A GUARD AGAINST ONE SYNTAX.** Ask
    > two questions of any structural check: *can it go red at all?* (v3.21.7's
    > rule, about a walk that asserted a property of Django) and *does it cover
    > what its docstring says?* This one could go red and did real work. It just
    > described a smaller population than it claimed, and the operation the
    > release existed to abolish lived in the gap.

    Five detectors now, because there are five ways to write this field:

      (a) attribute assignment   `member.user_id = x`
      (b) constructor / manager  `ParliamentUser(user_id=x)`, `create_user(...)`
      (c) queryset bulk write    `.update(user_id=x)`
      (d) dynamic assignment     `setattr(member, 'user_id', x)`
      (e) raw SQL                `cursor.execute('UPDATE ... parliamentuser')`

    (b), (c) and (d) are exact — they key off the call being made, not off what
    a variable happens to be named. (a) still leans on `_MEMBER_VARIABLE_NAMES`
    and that limitation is stated in the failure message rather than left for a
    later reader to discover.

    ⚠️ (e) WAS ADDED IN v3.25.0 AND ITS LIMITATION IS THE POINT OF READING THIS.
    The first four detectors are Python-level, and **the operation this module
    exists to forbid was originally written in raw SQL** — so for the whole of
    v3.24.0 the guard was blind to the exact technique it commemorates. That was
    survivable only because v3.24.0 swept the tree and found no raw-SQL writer
    left; a sweep is a fact about one afternoon, and this is the enforcement.

    It matches two spellings, both of which this codebase has shipped: a write
    naming `parliamentuser`, and a read of `information_schema` (the row-copy).
    **A raw statement whose table name is interpolated from a variable is
    invisible to it** — including, ironically, the deleted initiation code,
    which built its table names from `_meta.db_table`. That is why the
    `information_schema` half exists: the introspection is the part that cannot
    be spelled without saying so.
    """
    import ast
    import os
    import re

    offenders = []

    #: (e) raw SQL. Two spellings, both of which this codebase has actually
    #: shipped: a write naming the member table, and the `information_schema`
    #: row-copy that v3.23.0 deleted from initiation and v3.24.0 deleted from
    #: the admin.
    #: No `\b` before `parliamentuser`: the real table is `src_parliamentuser`
    #: and `_` is a word character, so a leading boundary never matches. The
    #: first draft had one and the detector's own control caught it.
    _RAW_SQL_WRITE = re.compile(
        r'(?is)\b(?:update|insert\s+into|delete\s+from)\b[^;]*parliamentuser\b')
    _RAW_SQL_INTROSPECTION = re.compile(r'(?i)information_schema')

    def sql_text(node):
        """
        Every string literal reachable from an argument, joined.

        f-strings arrive as `JoinedStr`, and the interpolated parts are exactly
        the ones that carry no literal text — so this reads what the author
        wrote and is deliberately blind to what a variable holds. Stated in the
        docstring rather than left to be discovered.
        """
        parts = []
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                parts.append(child.value)
        return ' '.join(parts)

    def note(path, node, kind, text):
        offenders.append(f'{path}:{node.lineno}  [{kind}] {text}')

    def enclosing_functions(tree):
        """Yield (node, function_name) for every node, innermost name wins."""
        stack = [(tree, None)]
        while stack:
            parent, name = stack.pop()
            for child in ast.iter_child_nodes(parent):
                child_name = (
                    child.name
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    else name
                )
                yield child, child_name
                stack.append((child, child_name))

    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith('.py'):
                continue
            path = os.path.join(dirpath, name)
            if any(a in path for a in allowed):
                continue
            source = open(path, encoding='utf-8', errors='ignore').read()
            try:
                tree = ast.parse(source, filename=path)
            except SyntaxError:                     # pragma: no cover
                continue

            for node, function_name in enclosing_functions(tree):
                if (path, function_name) in creation_sites:
                    continue
                # (a) member.user_id = ...   /   self.member.user_id = ...
                if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                    targets = (node.targets if isinstance(node, ast.Assign)
                               else [node.target])
                    for target in targets:
                        if (isinstance(target, ast.Attribute)
                                and target.attr == 'user_id'):
                            base = target.value
                            base_name = (
                                base.attr if isinstance(base, ast.Attribute)
                                else base.id if isinstance(base, ast.Name)
                                else None
                            )
                            if base_name in _MEMBER_VARIABLE_NAMES:
                                note(path, target, 'attribute',
                                     f'{base_name}.user_id = ...')

                if not isinstance(node, ast.Call):
                    continue

                func = node.func
                func_name = (func.attr if isinstance(func, ast.Attribute)
                             else func.id if isinstance(func, ast.Name) else None)

                # (e) cursor.execute('UPDATE ... parliamentuser ...')
                if func_name in ('execute', 'executemany') and node.args:
                    sql = sql_text(node.args[0])
                    if _RAW_SQL_WRITE.search(sql):
                        note(path, node, 'raw-sql',
                             'execute(...) writes the member table')
                        continue
                    if _RAW_SQL_INTROSPECTION.search(sql):
                        note(path, node, 'raw-sql',
                             'execute(...) reads information_schema — the '
                             'row-copy technique')
                        continue

                # (d) setattr(member, 'user_id', ...)
                if func_name == 'setattr' and len(node.args) >= 2:
                    key = node.args[1]
                    if isinstance(key, ast.Constant) and key.value == 'user_id':
                        note(path, node, 'setattr', "setattr(..., 'user_id', ...)")
                    continue

                if func_name not in _USER_WRITING_CALLS:
                    continue

                if not any(kw.arg == 'user_id' for kw in node.keywords):
                    continue

                # `.update(user_id=…)` is only about a member when the queryset
                # is one. `Vote.objects.filter(...).update(user_id=x)` is a
                # legitimate FK rewrite and must not be flagged.
                if func_name in ('update', 'update_or_create'):
                    chain = ast.dump(func)
                    if 'ParliamentUser' not in chain and 'objects' not in chain:
                        continue
                    if 'ParliamentUser' not in ast.dump(node):
                        continue
                    note(path, node, 'bulk-update', f'{func_name}(user_id=…)')
                else:
                    note(path, node, 'construction', f'{func_name}(user_id=…)')

    return offenders


class NothingWritesTheKeyTests(TestCase):
    """
    ⚠️ THE STRUCTURAL GUARD. The property is only as durable as the next
    person's memory of it, so this fails the build rather than relying on one.

    See `_scan_for_key_writes` for what it covers and, more importantly, for the
    one thing it does not.
    """

    #: Whole files exempt from the walk, as opposed to the per-function
    #: `_CREATION_SITES` above. Kept as short as it can be: every entry here is
    #: a place where the guard is off entirely, and the previous version of this
    #: module was too generous with exactly this list.
    ALLOWED = {
        # A migration's job is to write columns, including this one, and `0021`
        # is tested directly in this module instead.
        'src/migrations',
        # Test fixtures build members by the hundred, and this module builds
        # deliberate offenders to prove the detectors fire.
        'src/tests/',
    }

    def test_no_module_writes_a_members_user_id(self):
        offenders = _scan_for_key_writes('src', self.ALLOWED, _CREATION_SITES)

        self.assertEqual(
            offenders, [],
            'Something writes a member\'s `user_id` outside a creation path:\n  '
            + '\n  '.join(offenders)
            + '\n\nThat field is the primary key and ~150 FK columns hold a copy '
              'of the string, so changing it invalidates all of them at once. If '
              'you need to change what a member is CALLED, set `role_number` — '
              'that is what it is for, it is freely editable, and it is what the '
              'templates render.\n\n'
              'If this is a legitimate creation path, add the file to ALLOWED '
              'and say why. Do not rename the variable to get past detector (a).',
        )

    def test_every_detector_can_actually_see_an_offender(self):
        """
        ⚠️ CONTROL, and it is the test this module most needed. A scan that
        matches nothing passes the assertion above no matter what the code does
        — which is precisely how the previous version stayed green while
        `src/admin.py` moved primary keys behind a button.

        Each fixture below is a real spelling that has appeared in this
        codebase, or is one keystroke from one.
        """
        import os
        import tempfile

        cases = {
            'attribute': 'def f(pledge, v):\n    pledge.user_id = v\n',
            'construction': 'def f(v):\n    return ParliamentUser(user_id=v, name="x")\n',
            'manager': 'def f(v):\n    return ParliamentUser.objects.create_user(user_id=v)\n',
            'bulk-update': 'def f(v):\n    ParliamentUser.objects.filter(pk=1).update(user_id=v)\n',
            'setattr': 'def f(m, v):\n    setattr(m, "user_id", v)\n',
            'raw-sql': (
                'def f(cursor, v):\n'
                '    cursor.execute("UPDATE src_parliamentuser SET user_id = %s", [v])\n'
            ),
            'raw-introspection': (
                'def f(cursor):\n'
                '    cursor.execute("SELECT column_name FROM information_schema.columns")\n'
            ),
        }

        with tempfile.TemporaryDirectory() as tmp:
            for label, body in cases.items():
                with open(os.path.join(tmp, f'{label}.py'), 'w') as fh:
                    fh.write(body)

            found = _scan_for_key_writes(tmp)

        for label in cases:
            with self.subTest(spelling=label):
                self.assertTrue(
                    any(f'{label}.py' in line for line in found),
                    f'The scan cannot see a {label} write, so it does not '
                    f'protect against one.\nFound: {found}',
                )

    def test_the_scan_leaves_ordinary_foreign_key_writes_alone(self):
        """
        ⚠️ THE OTHER HALF OF THE CONTROL. `user_id` is the FK attname on ~150
        models, so a scan that flags every `.user_id =` would be turned off
        within a week — and a guard people turn off protects nothing.
        """
        import os
        import tempfile

        benign = {
            'fk_attribute': 'def f(vote, v):\n    vote.user_id = v\n',
            'fk_bulk': 'def f(v):\n    Attendance.objects.filter(pk=1).update(user_id=v)\n',
            'comparison': 'def f(pledge, other):\n    return pledge.user_id == other\n',
            'lookup': 'def f(v):\n    return ParliamentUser.objects.filter(user_id=v)\n',
            # (e)'s other half: raw SQL is everywhere in this app's dev-mode and
            # reporting code, and a detector that flags every `execute()` — or
            # every FK rewrite on another table — is one that gets deleted.
            'raw_select': (
                'def f(cursor):\n'
                '    cursor.execute("SELECT name FROM src_parliamentuser")\n'
            ),
            'raw_fk_rewrite': (
                'def f(cursor, v):\n'
                '    cursor.execute("UPDATE src_vote SET user_id = %s", [v])\n'
            ),
        }

        with tempfile.TemporaryDirectory() as tmp:
            for label, body in benign.items():
                with open(os.path.join(tmp, f'{label}.py'), 'w') as fh:
                    fh.write(body)

            found = _scan_for_key_writes(tmp)

        self.assertEqual(
            found, [],
            'The scan flagged something that is not a member-key write. False '
            'positives are how a structural guard gets deleted:\n  '
            + '\n  '.join(found),
        )


class TheAdminCannotMoveAPrimaryKeyTests(TestCase):
    """
    ⚠️ v3.24.0 — `/admin/` USED TO OFFER THIS AS A BUTTON, AND THAT IS WHY THIS
    TEST EXISTS RATHER THAN A COMMENT.

    `ParliamentUserAdmin.migrate_user_id_view` copied a member's row to a new
    primary key, repointed the relations it could find, and deleted the
    original. It was reachable at
    `/admin/src/parliamentuser/migrate-user-id/<str:user_id>/` and rendered by
    `login_as_link`, which is in `list_display` — so it was a red button on
    every row of the member list.

    Reproduced end to end through the real endpoint as an admin before it was
    removed: the pk moved, the old row was deleted, and the member's
    `TwoFactorRequirement` row was **silently destroyed**, because
    `getattr(user, accessor)` on a reverse OneToOne returns the related object
    rather than a manager, so `.all().update(…)` raised `AttributeError` into a
    bare `except: pass` — and `delete()` then CASCADEd the row nobody had
    repointed. `watch_flag` and `calendar_subscription` were in the same
    position, and 21 of 45 concrete fields were silently reset because the new
    row was built from a hand-written kwarg list.

    The scan above would now catch it being written again. This catches the
    *route* coming back by any other means.
    """

    def test_the_migrate_user_id_route_is_gone(self):
        from django.urls import NoReverseMatch, reverse as django_reverse

        with self.assertRaises(NoReverseMatch):
            django_reverse('admin:migrate_user_id', args=['P-C7JKZY'])

    def test_the_member_list_offers_no_key_moving_action(self):
        from src.admin import ParliamentUserAdmin

        self.assertFalse(
            hasattr(ParliamentUserAdmin, 'migrate_user_id_view'),
            'The view is back. Before restoring it, read the class docstring '
            'here: nothing needs it, because `role_number` carries what a '
            'member is called and is editable on the change form.',
        )

    def test_the_actions_column_still_offers_login_as(self):
        """
        CONTROL. Asserting a button is absent passes trivially if the whole
        column stopped rendering — which would be a different bug, not a fix.
        """
        from src.admin import ParliamentUserAdmin

        user = ParliamentUser.objects.create(
            user_id='P-ADMINQ', name='Rendered', username='rendered',
            member_type='Member', member_status='Active',
        )
        markup = ParliamentUserAdmin.login_as_link(None, user)

        self.assertIn('Login As User', markup)
        self.assertNotIn('Migrate', markup)


def _run_backfill_quietly(backfill):
    """
    Wrap the migration's data function so its deploy-time `print()` does not
    land in the test output.

    ⚠️ Not cosmetic. v3.21.4 spent a release making the push gate quiet, after
    a real `FAIL:` scrolled past behind fixture chatter and the first failure it
    reported was misdiagnosed. That print is right where it is — a migration
    runs on a terminal somebody is watching — and wrong in a suite.
    """
    import contextlib
    import io

    def quiet(*args, **kwargs):
        with contextlib.redirect_stdout(io.StringIO()):
            return backfill(*args, **kwargs)

    return quiet


class TheRollNumberIsWhatMembersSeeTests(TestCase):

    def test_role_number_is_unique_and_optional(self):
        """
        Pins the two properties the design leans on: a pledge has no roll number
        yet (so it must be nullable), and two members cannot share one.
        """
        field = ParliamentUser._meta.get_field('role_number')
        self.assertTrue(field.unique)
        self.assertTrue(field.null)

    def test_the_backfill_fills_a_blank_role_number_from_the_key(self):
        """
        ⚠️ v3.24.0 — THIS TEST USED TO BE VACUOUS, and it is worth saying how.

        It queried for non-pledges with a null `role_number` and asserted the
        list was empty, with a comment conceding *"an empty test database
        legitimately has none of either"*. Migrations run before any test does,
        against an empty database, and this class creates no members — so the
        query returned `[]` no matter what migration `0021` contained. Deleting
        the migration's body left it green.

        That is v3.21.7's rule about v3.21.6's enumeration, one release later
        and one directory over: **before trusting a check, ask what would have
        to be true for it to go red.** The answer here was "nothing".

        So it now runs the backfill function itself against a dataset built to
        exercise every branch it has.
        """
        import importlib

        from django.apps import apps as django_apps

        # Imported by name because a module whose name starts with a digit
        # cannot be reached with an `import` statement.
        backfill = _run_backfill_quietly(importlib.import_module(
            'src.migrations.0021_backfill_role_numbers').backfill_role_numbers)

        # A brother whose key is his roll number and who has no `role_number`.
        ParliamentUser.objects.create(
            user_id='173', name='Needs Backfill', username='needs_backfill',
            member_type='Member', member_status='Active',
        )
        # A brother who already has one. It must not be touched.
        ParliamentUser.objects.create(
            user_id='P-KEEPME', name='Already Set', username='already_set',
            member_type='Member', member_status='Active', role_number='42',
        )
        # ⚠️ A pledge. Copying `P-XXXXXX` into `role_number` would INVENT a roll
        # number for somebody who has not been initiated.
        ParliamentUser.objects.create(
            user_id='P-PLEDGE', name='Not Yet', username='not_yet',
            member_type='Pledge', member_status='Active',
        )
        # ⚠️ The collision branch: this member's key is already somebody else's
        # roll number. The migration must leave him blank and say so rather than
        # take the value — a wrong roll number renders as another man's number.
        ParliamentUser.objects.create(
            user_id='42', name='Collides', username='collides',
            member_type='Member', member_status='Active',
        )

        backfill(django_apps, None)

        def role_number_of(user_id):
            return ParliamentUser.objects.get(user_id=user_id).role_number

        self.assertEqual(role_number_of('173'), '173', 'The blank was not filled.')
        self.assertEqual(role_number_of('P-KEEPME'), '42',
                         'An existing roll number was overwritten.')
        self.assertIn(role_number_of('P-PLEDGE'), (None, ''),
                      'A pledge was given a roll number he has not earned.')
        self.assertIn(role_number_of('42'), (None, ''),
                      'A colliding key was copied in, so two members now claim '
                      'the same roll number.')

    def test_the_backfill_is_idempotent(self):
        """
        A migration that is run twice — a re-run, a restore, a fake-initial
        cutover — must not change its mind on the second pass.
        """
        import importlib

        from django.apps import apps as django_apps

        backfill = importlib.import_module(
            'src.migrations.0021_backfill_role_numbers').backfill_role_numbers

        ParliamentUser.objects.create(
            user_id='201', name='Twice', username='twice',
            member_type='Member', member_status='Active',
        )

        backfill(django_apps, None)
        first = ParliamentUser.objects.get(user_id='201').role_number
        backfill(django_apps, None)
        second = ParliamentUser.objects.get(user_id='201').role_number

        self.assertEqual(first, '201')
        self.assertEqual(second, first)


class TheAdminAddUserPageGetsAKeyTests(TestCase):
    """
    Reproduction for the 08-25-26 prod outage: `/admin/src/parliamentuser/add/`
    was returning a 500 (`NoReverseMatch` on an empty pk) and, more importantly,
    was still creating the row first — `readonly_fields = ('user_id',)` applied
    unconditionally meant the field was never part of the add ModelForm at all,
    so nothing validated it and the save went through with `user_id=''`
    (CharField's implicit default), and the crash only happened afterwards, on
    the post-save redirect. No exception during the save itself is why nothing
    was logged before the 500.
    """

    def setUp(self):
        self.officer = ParliamentUser.objects.create_user(
            user_id='admin-test-1', password='admin-test-pass-12345!',
            name='Officer Admin', username='officer_admin',
            member_type='Officer', is_admin=True, is_active=True,
        )
        self.client.force_login(self.officer)

    def _post_add(self, **overrides):
        data = {
            'name': 'New Member',
            'username': 'new_member_admin_added',
            'member_type': 'Member',
            'member_status': 'Active',
            'password': 'irrelevant-admin-set-password-1',
            'anonymous_vote': '',
            'allow_abstain': 'on',
            'is_active': 'on',
            'roles': [],
            '_save': 'Save',
        }
        data.update(overrides)
        return self.client.post(reverse('admin:src_parliamentuser_add'), data)

    def test_the_new_row_gets_a_real_key_not_an_empty_one(self):
        resp = self._post_add()
        # A successful admin add redirects (302) to the changelist/change page,
        # not the pre-fix 500 on reverse().
        self.assertEqual(resp.status_code, 302, getattr(resp, 'content', b'')[:2000])

        created = ParliamentUser.objects.exclude(pk=self.officer.pk).get(
            username='new_member_admin_added')
        self.assertTrue(created.user_id, 'user_id was left blank.')
        self.assertNotEqual(created.user_id, '')
        self.assertTrue(created.user_id.startswith(MEMBER_UID_PREFIX))

    def test_a_hand_typed_id_is_honoured(self):
        resp = self._post_add(user_id='P-HANDSET', username='hand_set_user')
        self.assertEqual(resp.status_code, 302, getattr(resp, 'content', b'')[:2000])
        created = ParliamentUser.objects.get(username='hand_set_user')
        self.assertEqual(created.user_id, 'P-HANDSET')

    def test_the_key_is_readonly_once_the_row_exists(self):
        target = ParliamentUser.objects.create_user(
            user_id=generate_member_uid(), password='existing-pass-12345!',
            name='Existing Member', username='existing_member', member_type='Member',
        )
        resp = self.client.get(
            reverse('admin:src_parliamentuser_change', args=[target.pk]))
        self.assertEqual(resp.status_code, 200)
        # A readonly field renders its value as text, not as a named input —
        # if it were still editable this would be a `<input ... name="user_id"`.
        self.assertNotIn(b'name="user_id"', resp.content)
