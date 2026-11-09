"""
11-09-26 (secret ballot, after the election): a SlatingVote can't be matched
to the SlatingBallot written in the same request. The vote had the ballot's
timestamp to the microsecond (``voted_at``) and a sequential id that put votes
in the order ballots were cast. Migration 0078 drops the timestamp and gives
votes random UUID keys, including the votes already recorded.
"""
import uuid

from django.db import connection, models
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from src.models import SlatingAttendance, SlatingBallot, SlatingVote
from src.slating_ballots import cast_slate
from src.tests.slating.test_slating_review_fixes import _Base, member
from src.view.slating.results import rejection_breakdown
from src.view.slating.tallies import slate_tally

BEFORE = '0077_groupme_message_delete'
AFTER = '0078_slating_vote_unlinkable'


class VoteShapeTests(_Base):

    def test_vote_has_no_time_and_no_sequence(self):
        fields = SlatingVote._meta.get_fields()
        self.assertFalse([f.name for f in fields
                          if isinstance(f, (models.DateTimeField, models.DateField, models.AutoField))])
        self.assertIsInstance(SlatingVote._meta.pk, models.UUIDField)
        self.assertEqual(SlatingVote._meta.ordering, ['vote_hash'])

    def test_cast_vote_gets_a_random_key(self):
        voters = [self.voter, member('sr-v2'), member('sr-v3')]
        for v in voters:
            SlatingAttendance.objects.get_or_create(period=self.period, member=v)
        cast_slate(voters[0], self.period, self.slate, [self.c_pres, self.c_vp], 'approve', [])
        cast_slate(voters[1], self.period, self.slate, [self.c_pres, self.c_vp], 'approve', [])
        cast_slate(voters[2], self.period, self.slate, [self.c_pres, self.c_vp], 'reject', [self.vp.id])
        votes = list(SlatingVote.objects.all())
        self.assertEqual(len(votes), 3)
        self.assertTrue(all(isinstance(v.pk, uuid.UUID) for v in votes))
        self.assertEqual(len({v.pk for v in votes}), 3)
        self.assertEqual(SlatingBallot.objects.count(), 3)

        # Counting and the committee's objections are unchanged.
        tally = slate_tally(self.period, self.slate)
        self.assertEqual((tally['approve'], tally['reject'], tally['ballots_online']), (2, 1, 3))
        rows = rejection_breakdown(self.period, self.slate)
        self.assertEqual([(r['position_id'], r['objection_count']) for r in rows], [(self.vp.id, 1)])


class Migration0078Tests(TransactionTestCase):
    """The real 0077 -> 0078 migration over votes already recorded."""

    def _migrate(self, target):
        executor = MigrationExecutor(connection)
        executor.migrate([('src', target)])
        return executor.loader.project_state(('src', target)).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def _columns(self):
        with connection.cursor() as c:
            return {col.name: col for col in
                    connection.introspection.get_table_description(c, 'src_slatingvote')}

    def test_existing_votes_keep_their_choices_and_lose_time_and_sequence(self):
        apps = self._migrate(BEFORE)
        Period = apps.get_model('src', 'SlatingPeriod')
        Slate = apps.get_model('src', 'Slate')
        Vote = apps.get_model('src', 'SlatingVote')
        period = Period.objects.create(name='Fall', academic_term='Fall 2026', status='voting_closed')
        slate = Slate.objects.create(period=period, is_approved=True)
        for i, choice in enumerate(['approve', 'approve', 'reject', 'abstain']):
            Vote.objects.create(period=period, slate=slate, voting_attempt=1, vote_choice=choice,
                                rejected_positions=[7] if choice == 'reject' else [], vote_hash=f'h{i}')
        self.assertIn('voted_at', self._columns())

        apps = self._migrate(AFTER)
        Vote = apps.get_model('src', 'SlatingVote')
        cols = self._columns()
        self.assertNotIn('voted_at', cols)
        self.assertNotIn('new_id', cols)
        rows = list(Vote.objects.filter(period_id=period.pk).values('id', 'vote_choice', 'rejected_positions'))
        self.assertEqual(sorted(r['vote_choice'] for r in rows), ['abstain', 'approve', 'approve', 'reject'])
        self.assertEqual([r['rejected_positions'] for r in rows if r['vote_choice'] == 'reject'], [[7]])
        self.assertTrue(all(isinstance(r['id'], uuid.UUID) for r in rows))
        self.assertEqual(len({r['id'] for r in rows}), 4)
        # New rows still get a key after the swap.
        Vote.objects.create(period_id=period.pk, slate_id=slate.pk, voting_attempt=2,
                            vote_choice='approve', vote_hash='h-new')
        self.assertEqual(Vote.objects.filter(period_id=period.pk).count(), 5)
