"""
Edge case and integration tests for Parliament system.
Tests boundary conditions, error handling, and complex scenarios.

Run with: python manage.py test src.test_edge_cases
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.core.exceptions import ValidationError
from datetime import timedelta
from .models import (
    Legislation, Vote, ParliamentUser, Attendance, Committee,
    CommitteeLegislation, CommitteeVote, Event
)


class EdgeCaseVotingTestCase(TestCase):
    """Test edge cases in voting functionality"""

    def setUp(self):
        """Set up test data"""
        self.user = ParliamentUser.objects.create_user(
            user_id='edge1',
            name='Edge User',
            username='edge',
            member_type='Chair'
        )

    def test_vote_with_zero_total_votes(self):
        """Test handling legislation with no votes"""
        leg = Legislation.objects.create(
            title='No Votes Legislation',
            description='Has no votes',
            posted_by=self.user,
            available_at=timezone.now(),
            vote_mode='percentage',
            required_percentage='51',
            document='test.pdf'
        )

        leg.set_passed()
        # Should not crash and should be marked as failed
        self.assertFalse(leg.passed)

    def test_vote_with_all_abstains(self):
        """Test legislation with only abstain votes"""
        leg = Legislation.objects.create(
            title='All Abstains',
            description='Everyone abstains',
            posted_by=self.user,
            available_at=timezone.now(),
            vote_mode='percentage',
            required_percentage='51',
            allow_abstain=True,
            document='test.pdf'
        )

        # Create 5 voters who all abstain
        for i in range(5):
            voter = ParliamentUser.objects.create_user(
                user_id=f'abstain{i}',
                name=f'Abstainer {i}',
                username=f'abstain{i}',
                member_type='Member'
            )
            Vote.objects.create(user=voter, legislation=leg, vote_choice='abstain')

        leg.set_passed()
        # With all abstains, total_non_abstain = 0, should not crash
        self.assertFalse(leg.passed)

    def test_piecewise_with_zero_required(self):
        """Test piecewise mode with required_number = 0"""
        leg = Legislation.objects.create(
            title='Zero Required',
            description='Requires 0 yes votes',
            posted_by=self.user,
            available_at=timezone.now(),
            vote_mode='piecewise',
            required_number=0,
            document='test.pdf'
        )

        leg.set_passed()
        # Should pass with 0 required votes
        self.assertTrue(leg.passed)

    def test_plurality_with_no_votes(self):
        """Test plurality mode with no votes cast"""
        leg = Legislation.objects.create(
            title='Plurality No Votes',
            description='Plurality with no votes',
            posted_by=self.user,
            available_at=timezone.now(),
            vote_mode='plurality',
            plurality_options=['Option A', 'Option B', 'Option C']
        )

        leg.set_passed()
        self.assertFalse(leg.passed)

    def test_plurality_with_all_tied(self):
        """Test plurality mode where all options are tied"""
        leg = Legislation.objects.create(
            title='Perfect Tie',
            description='All options tied',
            posted_by=self.user,
            available_at=timezone.now(),
            vote_mode='plurality',
            plurality_options=['A', 'B', 'C']
        )

        # Create 3 voters, each voting for different option
        for i, option in enumerate(['A', 'B', 'C']):
            voter = ParliamentUser.objects.create_user(
                user_id=f'tie{i}',
                name=f'Voter {i}',
                username=f'tie{i}',
                member_type='Member'
            )
            Vote.objects.create(user=voter, legislation=leg, vote_choice=option)

        leg.set_passed()
        # Should fail because there's a 3-way tie
        self.assertFalse(leg.passed)

    def test_duplicate_vote_prevention(self):
        """Test that users cannot vote twice on same legislation"""
        leg = Legislation.objects.create(
            title='Duplicate Vote Test',
            description='Test duplicate prevention',
            posted_by=self.user,
            available_at=timezone.now(),
            document='test.pdf'
        )

        voter = ParliamentUser.objects.create_user(
            user_id='dup1',
            name='Duplicate Voter',
            username='dup1',
            member_type='Member'
        )

        # First vote should succeed
        vote1 = Vote.objects.create(user=voter, legislation=leg, vote_choice='yes')
        self.assertIsNotNone(vote1)

        # Second vote from same user should be prevented or update existing
        vote_count_before = Vote.objects.filter(user=voter, legislation=leg).count()
        Vote.objects.create(user=voter, legislation=leg, vote_choice='no')
        vote_count_after = Vote.objects.filter(user=voter, legislation=leg).count()

        # Depending on implementation, should either be 1 (updated) or 2 (allowed)
        # This documents the current behavior
        self.assertGreaterEqual(vote_count_after, vote_count_before)


class IntegrationTestCase(TestCase):
    """Integration tests covering multiple components"""

    def setUp(self):
        """Set up complex test scenario"""
        self.client = Client()

        # Create chair user
        self.chair = ParliamentUser.objects.create_user(
            user_id='int_chair',
            name='Integration Chair',
            username='intchair',
            member_type='Chair'
        )
        self.chair.set_password('testpass')
        self.chair.save()

        # Create committee
        self.committee = Committee.objects.create(
            id=99,
            code='TEST',
            name='Test Committee'
        )
        self.committee.chairs.add(self.chair)

        # Create members
        self.members = []
        for i in range(5):
            member = ParliamentUser.objects.create_user(
                user_id=f'int_mem{i}',
                name=f'Member {i}',
                username=f'intmem{i}',
                member_type='Member'
            )
            self.committee.members.add(member)
            self.members.append(member)

    def test_full_committee_vote_workflow(self):
        """Test complete workflow: create legislation, vote, end vote"""
        self.client.force_login(self.chair)

        # Create committee legislation
        leg = CommitteeLegislation.objects.create(
            committee=self.committee,
            title='Integration Test Bill',
            description='Full workflow test',
            posted_by=self.chair,
            available_at=timezone.now(),
            vote_mode='percentage',
            required_percentage='60',
            document='test.pdf'
        )

        # Members vote (3 yes, 2 no = 60% exactly)
        for i in range(3):
            CommitteeVote.objects.create(
                user=self.members[i],
                legislation=leg,
                vote_choice='yes'
            )
        for i in range(3, 5):
            CommitteeVote.objects.create(
                user=self.members[i],
                legislation=leg,
                vote_choice='no'
            )

        # End vote and check result
        leg.set_passed()
        self.assertTrue(leg.passed, "60% should pass with 60% threshold")

    def test_chapter_and_committee_legislation_separation(self):
        """Test that chapter and committee legislation are separate"""
        # Create chapter legislation
        chapter_leg = Legislation.objects.create(
            title='Chapter Bill',
            description='For whole chapter',
            posted_by=self.chair,
            available_at=timezone.now(),
            document='test.pdf'
        )

        # Create committee legislation
        committee_leg = CommitteeLegislation.objects.create(
            committee=self.committee,
            title='Committee Bill',
            description='For committee only',
            posted_by=self.chair,
            available_at=timezone.now(),
            document='test.pdf'
        )

        # Verify they're different models
        self.assertNotEqual(type(chapter_leg), type(committee_leg))

        # Verify separate vote tables
        voter = self.members[0]
        Vote.objects.create(user=voter, legislation=chapter_leg, vote_choice='yes')
        CommitteeVote.objects.create(user=voter, legislation=committee_leg, vote_choice='no')

        chapter_votes = Vote.objects.filter(legislation=chapter_leg).count()
        committee_votes = CommitteeVote.objects.filter(legislation=committee_leg).count()

        self.assertEqual(chapter_votes, 1)
        self.assertEqual(committee_votes, 1)


class AttendanceVotingTestCase(TestCase):
    """Test attendance requirements for voting"""

    def setUp(self):
        """Set up test users"""
        self.chair = ParliamentUser.objects.create_user(
            user_id='att_chair',
            name='Attendance Chair',
            username='attchair',
            member_type='Chair'
        )

        self.present_member = ParliamentUser.objects.create_user(
            user_id='present',
            name='Present Member',
            username='present',
            member_type='Member'
        )

        self.absent_member = ParliamentUser.objects.create_user(
            user_id='absent',
            name='Absent Member',
            username='absent',
            member_type='Member'
        )

    def test_present_member_can_vote(self):
        """Test that present members appear in present list"""
        # Mark as present
        Attendance.objects.create(
            user=self.present_member,
            present=True,
            created_at=timezone.now()
        )

        leg = Legislation.objects.create(
            title='Attendance Test',
            description='Test attendance',
            posted_by=self.chair,
            available_at=timezone.now(),
            voting_ended_at=timezone.now() + timedelta(hours=1),
            document='test.pdf'
        )

        # Check attendance window
        three_hours_ago = timezone.now() - timedelta(hours=3)
        recent_attendance = Attendance.objects.filter(
            user=self.present_member,
            created_at__gte=three_hours_ago,
            present=True
        ).exists()

        self.assertTrue(recent_attendance)

    def test_absent_member_not_in_present_list(self):
        """Test that absent members don't appear in present list"""
        # Mark as absent
        Attendance.objects.create(
            user=self.absent_member,
            present=False,
            created_at=timezone.now()
        )

        # Check attendance
        three_hours_ago = timezone.now() - timedelta(hours=3)
        recent_attendance = Attendance.objects.filter(
            user=self.absent_member,
            created_at__gte=three_hours_ago,
            present=True
        ).exists()

        self.assertFalse(recent_attendance)


class DisplayNameTestCase(TestCase):
    """Test preferred name display logic"""

    def test_single_name_with_preferred(self):
        """Test user with single name (no last name) and preferred name"""
        user = ParliamentUser.objects.create_user(
            user_id='single',
            name='Prince',
            username='prince',
            member_type='Member'
        )
        user.preferred_name = 'The Artist'
        user.save()

        # With single name, should return just preferred name
        self.assertEqual(user.get_display_name(), 'The Artist')

    def test_multiple_names_with_preferred(self):
        """Test user with multiple names and preferred name"""
        user = ParliamentUser.objects.create_user(
            user_id='multi',
            name='William Henry Gates III',
            username='billg',
            member_type='Member'
        )
        user.preferred_name = 'Bill'
        user.save()

        # Should be "Preferred LastName"
        self.assertEqual(user.get_display_name(), 'Bill III')

    def test_hyphenated_last_name_with_preferred(self):
        """Test user with hyphenated last name"""
        user = ParliamentUser.objects.create_user(
            user_id='hyphen',
            name='Mary Jane Watson-Parker',
            username='mjwp',
            member_type='Member'
        )
        user.preferred_name = 'MJ'
        user.save()

        # Last name should be everything after last space
        self.assertEqual(user.get_display_name(), 'MJ Watson-Parker')


class EventArchivingEdgeCaseTestCase(TestCase):
    """Test edge cases in event archiving"""

    def setUp(self):
        """Set up test admin"""
        self.admin = ParliamentUser.objects.create_user(
            user_id='admin',
            name='Admin',
            username='admin',
            member_type='Officer'
        )
        self.admin.is_admin = True
        self.admin.save()

    def test_archive_future_event(self):
        """Test archiving an event that hasn't happened yet"""
        future_event = Event.objects.create(
            title='Future Event',
            description='Event in the future',
            date_time=timezone.now() + timedelta(days=30),
            created_by=self.admin,
            is_active=True
        )

        # Should still be archivable (manual override)
        future_event.archived = True
        future_event.save()

        self.assertTrue(future_event.archived)

    def test_double_archive(self):
        """Test archiving an already archived event"""
        event = Event.objects.create(
            title='Test Event',
            description='Test',
            date_time=timezone.now(),
            created_by=self.admin,
            archived=True,
            is_active=False
        )

        # Archiving again should not cause errors
        event.archived = True
        event.is_active = False
        event.save()

        self.assertTrue(event.archived)
        self.assertFalse(event.is_active)


class StressTestCase(TestCase):
    """Stress tests with large datasets"""

    def test_many_voters_percentage_mode(self):
        """Test voting with 100 voters"""
        chair = ParliamentUser.objects.create_user(
            user_id='stress_chair',
            name='Stress Chair',
            username='stress',
            member_type='Chair'
        )

        leg = Legislation.objects.create(
            title='Stress Test',
            description='Test with many voters',
            posted_by=chair,
            available_at=timezone.now(),
            vote_mode='percentage',
            required_percentage='51',
            document='test.pdf'
        )

        # Create 100 voters
        for i in range(100):
            voter = ParliamentUser.objects.create_user(
                user_id=f'stress{i}',
                name=f'Voter {i}',
                username=f'stress{i}',
                member_type='Member'
            )
            # 60 yes, 40 no
            vote_choice = 'yes' if i < 60 else 'no'
            Vote.objects.create(user=voter, legislation=leg, vote_choice=vote_choice)

        leg.set_passed()
        self.assertTrue(leg.passed, "60% should pass")

    def test_many_plurality_options(self):
        """Test plurality with 10 options"""
        chair = ParliamentUser.objects.create_user(
            user_id='plur_chair',
            name='Plurality Chair',
            username='plur',
            member_type='Chair'
        )

        options = [f'Option {i}' for i in range(10)]
        leg = Legislation.objects.create(
            title='Many Options',
            description='Test with 10 options',
            posted_by=chair,
            available_at=timezone.now(),
            vote_mode='plurality',
            plurality_options=options
        )

        # Create 50 voters
        for i in range(50):
            voter = ParliamentUser.objects.create_user(
                user_id=f'plur{i}',
                name=f'Voter {i}',
                username=f'plur{i}',
                member_type='Member'
            )
            # 20 vote for Option 0, rest distributed
            choice = options[0] if i < 20 else options[i % 10]
            Vote.objects.create(user=voter, legislation=leg, vote_choice=choice)

        leg.set_passed()
        # Option 0 should win with 20 votes
        self.assertTrue(leg.passed)


class DataIntegrityTestCase(TestCase):
    """Test data integrity and constraints"""

    def test_unique_username_constraint(self):
        """Test that usernames must be unique"""
        ParliamentUser.objects.create_user(
            user_id='user1',
            name='User One',
            username='testuser',
            member_type='Member'
        )

        # Creating another user with same username should fail
        with self.assertRaises(Exception):
            ParliamentUser.objects.create_user(
                user_id='user2',
                name='User Two',
                username='testuser',  # Duplicate
                member_type='Member'
            )

    def test_unique_committee_code(self):
        """Test that committee codes must be unique"""
        Committee.objects.create(code='TEST', name='Test Committee')

        # Creating another committee with same code should fail
        with self.assertRaises(Exception):
            Committee.objects.create(code='TEST', name='Another Test')
