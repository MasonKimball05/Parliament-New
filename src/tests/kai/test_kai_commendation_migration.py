"""
Regression coverage for migration `0030_rename_kai_accommodation_to_commendation`.

Why this exists: `0029_kai_accommodations` (v3.28.8) had already applied
against a real database — under the feature's original, wrong name —
before the same-day correction to "commendation" happened. The first
draft of that correction rewrote migration `0029` itself under the same
number with different table/field definitions, which is unsafe the
moment a migration has ever actually run anywhere: Django tracks
"applied" by (app, name), not by content, so a database that already
ran the real `0029_kai_accommodations` collides with a same-numbered
migration that tries to build something else (confirmed live —
`DuplicateColumn: column "form_type" ... already exists`, from Mason
running `manage.py migrate` against a database where the original had
already applied). The correct fix, used here, is a normal forward
migration (`0030`) built entirely from `RenameModel`/`RenameField`/
`RunPython`, which is safe to apply on top of `0029` regardless of
whether `0029` ran under the old code or the restored one — and never
touches migration `0029` again.

These tests exercise the ACTUAL migration file end to end — via
`MigrationExecutor`, seeding rows into the old (`kaiaccommodationrequest`
etc.) tables while state is frozen at `0029`, then migrating forward to
`0030` and asserting the data survived under the new names — rather than
just testing the resulting model shape, which the ordinary
`KaiCommendationModelTests` suite already covers and which would not
have caught the numbering mistake in the first place.
"""
from django.db.migrations.executor import MigrationExecutor
from django.db import connection
from django.test import TransactionTestCase


class Kai0030RenameMigrationTests(TransactionTestCase):
    """Runs the real 0029 -> 0030 migration against seeded rows."""

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([('src', target)])
        # Reload the executor's notion of state so later calls see the
        # migration we just ran.
        executor.loader.build_graph()

    def setUp(self):
        # Roll every other app's migrations back to nothing changes here;
        # only move `src` around. Start from a clean slate at 0029 so this
        # test doesn't depend on whatever state a previous test left.
        self._migrate('0029_kai_accommodations')

    def tearDown(self):
        # Leave the DB back at the latest migration for any test that
        # runs after this one in the same process.
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_forward_migration_preserves_data_under_new_names(self):
        from src.models import ParliamentUser
        ParliamentUser.objects.create_user(
            user_id='P-MIGTEST1', name='Migration Submitter', username='migtest1',
            member_type='Member')

        with connection.cursor() as c:
            c.execute("""
                INSERT INTO src_kaiformfield
                (field_name, label, field_type, placeholder, help_text, options,
                 is_required, validation_rules, allowed_file_types, max_file_size_mb,
                 display_order, section, is_active, is_builtin, created_at, updated_at,
                 form_type)
                VALUES ('custom_field', 'Custom Field', 'text', '', '', '[]', 0,
                        '{}', '[]', 5, 0, '', 1, 0, datetime('now'), datetime('now'),
                        'accommodation')
            """)
            c.execute("SELECT id FROM src_kaiformfield WHERE form_type='accommodation'")
            field_id = c.fetchone()[0]

            c.execute("""
                INSERT INTO src_kaiaccommodationrequest
                (title, description, submitted_at, status, committee_notes,
                 resolved_at, request_number, assigned_to_id, requester_id, resolved_by_id)
                VALUES ('Great job', 'Ran the whole event', datetime('now'), 'approved',
                        '', NULL, 'ACC-2026-099', NULL, 'P-MIGTEST1', NULL)
            """)
            c.execute("SELECT id FROM src_kaiaccommodationrequest")
            req_id = c.fetchone()[0]

            c.execute(f"""
                INSERT INTO src_kaiaccommodationrequestactivity
                (action, details, timestamp, request_id, user_id)
                VALUES ('created', 'Request submitted', datetime('now'), {req_id}, 'P-MIGTEST1')
            """)
            c.execute(f"""
                INSERT INTO src_kaiaccommodationfieldresponse
                (text_value, created_at, updated_at, field_id, request_id)
                VALUES ('a response', datetime('now'), datetime('now'), {field_id}, {req_id})
            """)

        self._migrate('0030_rename_kai_accommodation_to_commendation')

        from src.models import (
            KaiCommendation, KaiCommendationActivity, KaiCommendationFieldResponse, KaiFormField,
        )

        com = KaiCommendation.objects.get(pk=req_id)
        self.assertEqual(com.title, 'Great job')
        self.assertEqual(com.submitted_by_id, 'P-MIGTEST1')
        self.assertEqual(com.commendation_number, 'ACC-2026-099')
        self.assertEqual(com.status, 'acknowledged')  # was 'approved'
        self.assertIsNone(com.commended_member_id)  # no data existed to backfill
        self.assertFalse(com.is_submitter_anonymous)  # new field default

        # __str__ must not crash on a legacy row with no honoree.
        self.assertIn('no member specified', str(com))

        activity = KaiCommendationActivity.objects.get()
        self.assertEqual(activity.commendation_id, req_id)

        response = KaiCommendationFieldResponse.objects.get()
        self.assertEqual(response.commendation_id, req_id)
        self.assertEqual(response.text_value, 'a response')

        field = KaiFormField.objects.get(pk=field_id)
        self.assertEqual(field.form_type, 'commendation')  # was 'accommodation'

    def test_status_relabeling_covers_every_old_value(self):
        from src.models import ParliamentUser
        ParliamentUser.objects.create_user(
            user_id='P-MIGTEST2', name='Migration Submitter 2', username='migtest2',
            member_type='Member')

        with connection.cursor() as c:
            old_statuses = ['pending', 'in_review', 'approved', 'denied', 'closed']
            for i, status in enumerate(old_statuses):
                c.execute(f"""
                    INSERT INTO src_kaiaccommodationrequest
                    (title, description, submitted_at, status, committee_notes,
                     resolved_at, request_number, assigned_to_id, requester_id, resolved_by_id)
                    VALUES ('Row {i}', 'd', datetime('now'), '{status}', '', NULL, '', NULL,
                            'P-MIGTEST2', NULL)
                """)

        self._migrate('0030_rename_kai_accommodation_to_commendation')

        from src.models import KaiCommendation

        expected = {
            'pending': 'pending',
            'in_review': 'pending',
            'approved': 'acknowledged',
            'denied': 'archived',
            'closed': 'archived',
        }
        rows = {c.title: c.status for c in KaiCommendation.objects.filter(submitted_by_id='P-MIGTEST2')}
        for i, old_status in enumerate(old_statuses):
            self.assertEqual(rows[f'Row {i}'], expected[old_status], old_status)

    def test_backward_migration_restores_old_names_and_values(self):
        from src.models import ParliamentUser
        ParliamentUser.objects.create_user(
            user_id='P-MIGTEST3', name='Migration Submitter 3', username='migtest3',
            member_type='Member')

        with connection.cursor() as c:
            c.execute("""
                INSERT INTO src_kaiaccommodationrequest
                (title, description, submitted_at, status, committee_notes,
                 resolved_at, request_number, assigned_to_id, requester_id, resolved_by_id)
                VALUES ('Round trip', 'd', datetime('now'), 'approved', '', NULL, '', NULL,
                        'P-MIGTEST3', NULL)
            """)
            c.execute("SELECT id FROM src_kaiaccommodationrequest")
            req_id = c.fetchone()[0]

        self._migrate('0030_rename_kai_accommodation_to_commendation')
        self._migrate('0029_kai_accommodations')

        with connection.cursor() as c:
            c.execute(f"SELECT status, title FROM src_kaiaccommodationrequest WHERE id={req_id}")
            status, title = c.fetchone()
        self.assertEqual(status, 'approved')  # round-tripped, not lost
        self.assertEqual(title, 'Round trip')
