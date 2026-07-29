"""
Dev mode's result-row inspector — the redaction is the feature.

`src/dev_mode_rows.py` is a deliberate, bounded exception to the rule that the
panel shows metadata and never record content. These tests exist because the
exception is only acceptable while the redaction holds, and redaction that
isn't tested is a comment.

The load-bearing property is that it **fails closed**: an unclassified or
unparseable query is withheld, not shown. Everything else follows from that.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.utils import timezone

from src import dev_mode
from src.dev_mode import set_dev_mode
from src.dev_mode_rows import (MAX_ROWS, fetch_rows, sensitive_tables)
from src.models import (Announcement, KaiReport, Legislation, ParliamentUser,
                        Vote)


def make_user(uid, **extra):
    extra.setdefault('member_type', 'Officer')
    user = ParliamentUser.objects.create(
        user_id=uid, name=f'User {uid}', username=f'u{uid}',
        member_status='Active', **extra)
    user.set_password('rows-pass-12345!')
    user.save()
    return user


class RedactionPolicyTests(TestCase):
    """What must never be shown, and why."""

    def test_ballot_tables_are_withheld_whole(self):
        """
        Not column-redacted — withheld. CLAUDE.md's v3.16.2 lesson is that a
        timestamp, a sequence or an ordering is itself a join key, so showing
        the "harmless" columns of a ballot still leaks who voted when.
        """
        for table in ('src_vote', 'src_committeevote', 'src_slatingvote',
                      'src_slatingballot'):
            columns, rows, reason = fetch_rows(f'SELECT * FROM "{table}"', ())
            with self.subTest(table=table):
                self.assertIsNone(columns, table)
                self.assertIsNone(rows, table)
                self.assertIn('confidential table', reason)

    def test_every_kai_model_is_covered_without_being_listed(self):
        """
        The Kai set is derived from the models module, so a Kai model added
        tomorrow is covered the day it is written rather than the day someone
        remembers to update a list.
        """
        from django.apps import apps

        kai_tables = {
            model._meta.db_table for model in apps.get_models()
            if model.__module__.endswith('models.kai')
        }
        self.assertGreaterEqual(len(kai_tables), 7, 'expected the seven Kai models')
        self.assertTrue(kai_tables <= sensitive_tables())

        columns, _rows, reason = fetch_rows('SELECT * FROM "src_kaireport"', ())
        self.assertIsNone(columns)
        self.assertIn('confidential', reason)

    def test_a_join_that_merely_touches_a_confidential_table_is_withheld(self):
        """Redaction follows the JOIN, not just the FROM."""
        columns, _rows, reason = fetch_rows(
            'SELECT l.id FROM "src_legislation" l JOIN "src_vote" v '
            'ON v.legislation_id = l.id', ())
        self.assertIsNone(columns)
        self.assertIn('src_vote', reason)

    def test_credential_stores_are_withheld(self):
        for table in ('src_apitoken', 'src_webauthncredential',
                      'src_pushsubscription', 'src_calendarsubscription',
                      'django_session'):
            with self.subTest(table=table):
                columns, _rows, _reason = fetch_rows(f'SELECT * FROM "{table}"', ())
                self.assertIsNone(columns, table)

    def test_writes_are_never_re_run(self):
        for statement in ('UPDATE "src_announcement" SET title = 1',
                          'DELETE FROM "src_announcement"',
                          'INSERT INTO "src_announcement" (id) VALUES (1)',
                          'DROP TABLE "src_announcement"'):
            with self.subTest(statement=statement.split()[0]):
                columns, _rows, reason = fetch_rows(statement, ())
                self.assertIsNone(columns)
                self.assertIn('not a SELECT', reason)

    def test_unparseable_query_fails_closed(self):
        columns, _rows, reason = fetch_rows('SELECT 1', ())
        self.assertIsNone(columns)
        self.assertIn('failing closed', reason)

    def test_password_and_other_credential_columns_are_dropped(self):
        make_user('rp1')
        columns, rows, note = fetch_rows('SELECT * FROM "src_parliamentuser"', ())
        self.assertIsNotNone(columns)
        lowered = [c.lower() for c in columns]
        for column in ('password', 'backup_codes_acknowledged'):
            self.assertNotIn(column, lowered, column)
        self.assertIn('redacted', note)
        self.assertTrue(rows)

    def test_encrypted_fields_are_redacted_wherever_they_live(self):
        """
        Derived from the models, not enumerated: the point of an encrypted
        field is that its plaintext is not for casual reading.
        """
        from django.apps import apps

        from src.encrypted_fields import EncryptedFieldMixin
        from src.dev_mode_rows import _encrypted_columns

        found = _encrypted_columns()
        declared = {
            (model._meta.db_table, field.column)
            for model in apps.get_models()
            for field in model._meta.concrete_fields
            if isinstance(field, EncryptedFieldMixin)
        }
        for table, column in declared:
            self.assertIn(column, found.get(table, set()), f'{table}.{column}')


class InspectorBehaviourTests(TestCase):
    """It must read, and only read."""

    def setUp(self):
        self.author = make_user('ib1')
        for i in range(3):
            Announcement.objects.create(
                title=f'A{i}', content='c', posted_by=self.author, is_active=True)

    def test_ordinary_table_returns_rows(self):
        columns, rows, _note = fetch_rows(
            'SELECT * FROM "src_announcement" ORDER BY id', ())
        self.assertTrue(columns)
        self.assertEqual(len(rows), 3)

    def test_inspector_does_not_mutate(self):
        before = Announcement.objects.count()
        fetch_rows('SELECT * FROM "src_announcement"', ())
        self.assertEqual(Announcement.objects.count(), before)

    def test_row_cap_is_enforced(self):
        for i in range(MAX_ROWS + 10):
            Announcement.objects.create(
                title=f'B{i}', content='c', posted_by=self.author, is_active=True)
        _columns, rows, note = fetch_rows('SELECT * FROM "src_announcement"', ())
        self.assertEqual(len(rows), MAX_ROWS)
        self.assertIn('capped', note)

    def test_a_broken_query_does_not_raise(self):
        columns, _rows, reason = fetch_rows('SELECT * FROM "no_such_table"', ())
        self.assertIsNone(columns)
        self.assertTrue(reason)

    def test_note_warns_that_values_are_a_second_read(self):
        _columns, _rows, note = fetch_rows('SELECT * FROM "src_announcement"', ())
        self.assertIn('re-read', note)


class PanelIntegrationTests(TestCase):
    """The rows reach the panel, and the withheld ones say so."""

    def setUp(self):
        # The DB rolls back between tests; the cache does not. `set_dev_mode`
        # writes through `user_prefs_<pk>`, so without this a later test sees
        # the previous test's toggle for the same user id and dev mode appears
        # to be on when it should be off.
        from django.core.cache import cache
        cache.clear()

        self.dev = make_user('555', is_admin=True)
        now = timezone.now()
        for i in range(3):
            Announcement.objects.create(
                title=f'A{i}', content='c', posted_by=self.dev, is_active=True)
        legislation = Legislation.objects.create(
            title='L', description='d', posted_by=self.dev, available_at=now,
            vote_mode='percentage', required_percentage='50')
        Vote.objects.create(user=self.dev, legislation=legislation,
                            vote_choice='yes')

    def _load(self, url='/announcements/'):
        client = Client()
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            set_dev_mode(self.dev, True)
            client.force_login(self.dev)
            return client.get(url)

    def test_panel_renders_rows(self):
        response = self._load()
        body = response.content.decode()
        self.assertIn('pdev-root', body)
        self.assertIn('pdev-rowsbox', body)

    def test_rows_are_collapsed_behind_a_click(self):
        """
        This is the only place the panel shows record content; it should take a
        deliberate action rather than appear while you are reading query times.
        """
        body = self._load().content.decode()
        self.assertIn('rows (', body)
        self.assertIn('click to read', body)

    def test_no_ballot_content_reaches_the_page(self):
        body = self._load('/passed_legislation/?status=all').content.decode()
        # The vote table's rows must never be rendered, however the page got
        # there. If a row inspector ever starts showing them, this fails.
        self.assertNotIn('pdev-rowsbox">\n                  <table>\n                    <tr><th>vote_choice',
                         body)
        for query_marker in ('src_vote', ):
            if query_marker in body:
                # It may legitimately appear as SQL text; it must not appear as
                # a rendered results table.
                self.assertIn('rows withheld', body)

    def test_panel_absent_when_dev_mode_off(self):
        client = Client()
        with patch.object(dev_mode, 'DEV_USER_IDS', {'555'}):
            client.force_login(self.dev)
            response = client.get('/announcements/')
        self.assertNotIn('pdev-rowsbox', response.content.decode())
