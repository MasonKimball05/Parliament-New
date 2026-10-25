"""
P0 vote integrity — chapter ballots.

  * `cast_vote` accepted 'abstain' on legislation with `allow_abstain=False`.
  * `Vote` had no DB constraint and the view checked-then-created, so a race
    could record two ballots. Now (user, legislation, vote_choice) is unique
    (multi-select plurality still stores one row per choice) and the
    check-then-create runs under a row lock on the legislation.

Run with: python manage.py test src.tests.legislation.test_vote_ballot_integrity
"""
from unittest import mock

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from src.models import Attendance, Legislation, ParliamentUser, Vote


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.voter = ParliamentUser.objects.create_user(
            user_id='bi1', name='Ballot Voter', username='bi1', member_type='Member')
        self.voter.set_password('testpass')
        self.voter.save()
        Attendance.objects.create(user=self.voter, status='present')
        self.client.force_login(self.voter)

    def make_leg(self, **kwargs):
        defaults = dict(title='Leg', description='D', posted_by=self.voter,
                        available_at=timezone.now(), vote_mode='percentage',
                        required_percentage='51', document='test.pdf')
        defaults.update(kwargs)
        return Legislation.objects.create(**defaults)

    def cast(self, leg, follow=False, **data):
        payload = {'action': 'cast_vote', 'legislation_id': leg.pk, 'password': 'testpass'}
        payload.update(data)
        return self.client.post(reverse('cast_vote'), payload, follow=follow)


class AbstainTests(_Base):
    def test_abstain_refused_when_not_allowed(self):
        leg = self.make_leg(allow_abstain=False)
        self.cast(leg, vote_choice='abstain')
        self.assertFalse(Vote.objects.exists())

    def test_abstain_accepted_when_allowed(self):
        leg = self.make_leg()
        self.cast(leg, vote_choice='abstain')
        self.assertTrue(Vote.objects.filter(vote_choice='abstain').exists())


class ConstraintTests(_Base):
    def test_duplicate_row_rejected_by_db(self):
        leg = self.make_leg()
        Vote.objects.create(user=self.voter, legislation=leg, vote_choice='yes')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Vote.objects.create(user=self.voter, legislation=leg, vote_choice='yes')

    def test_multi_select_rows_still_allowed(self):
        leg = self.make_leg(vote_mode='plurality', plurality_options=['A', 'B', 'C'],
                            plurality_votes_allowed=2)
        self.cast(leg, vote_choices=['A', 'B'])
        self.assertEqual(Vote.objects.filter(user=self.voter, legislation=leg).count(), 2)

    def test_multi_select_duplicate_choice_rejected(self):
        leg = self.make_leg(vote_mode='plurality', plurality_options=['A', 'B', 'C'],
                            plurality_votes_allowed=2)
        resp = self.cast(leg, vote_choices=['A', 'A'])
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Vote.objects.exists())

    def test_integrity_error_reports_already_voted(self):
        leg = self.make_leg()
        with mock.patch.object(Vote.objects, 'create', side_effect=IntegrityError('dup')):
            resp = self.cast(leg, vote_choice='yes', follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'already voted')
        self.assertNotIn('fresh_vote_receipt', self.client.session)


class DuplicateGuardMigrationTests(TestCase):
    def test_guard_raises_listing_duplicates(self):
        import importlib
        from django.apps import apps as global_apps
        mod = importlib.import_module('src.migrations.0064_vote_unique_ballot')
        rows = [{'user_id': 7, 'legislation_id': 3, 'vote_choice': 'yes', 'n': 2}]
        with mock.patch.object(mod, '_duplicate_ballots', return_value=rows):
            with self.assertRaises(RuntimeError) as ctx:
                mod.refuse_duplicate_ballots(global_apps, None)
        self.assertIn('user_id=7', str(ctx.exception))
        self.assertIn('legislation_id=3', str(ctx.exception))
        # Never the choice: it would de-anonymize an anonymous ballot.
        self.assertNotIn('yes', str(ctx.exception))

    def test_guard_passes_when_clean(self):
        import importlib
        from django.apps import apps as global_apps
        mod = importlib.import_module('src.migrations.0064_vote_unique_ballot')
        mod.refuse_duplicate_ballots(global_apps, None)  # no rows → no error
