"""
Comprehensive test suite for Parliament system.
Tests legislation views, vote modes, user profiles, committees, and events.

Run with: python manage.py test src.test_comprehensive
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model
from datetime import timedelta
from .models import (
    Legislation, Vote, ParliamentUser, Attendance, Committee,
    CommitteeLegislation, CommitteeVote, Role, Event
)


class VoteModeTestCase(TestCase):
    """Test all three vote modes: percentage, piecewise, and plurality"""

    def setUp(self):
        """Create test user and client"""
        self.client = Client()
        self.uploader = ParliamentUser.objects.create_user(
            user_id='chair1',
            name='Test Chair',
            username='chair',
            member_type='Chair'
        )
        self.uploader.set_password('testpass')
        self.uploader.save()
        self.client.force_login(self.uploader)

        # Create voters
        self.voters = []
        for i in range(10):
            voter = ParliamentUser.objects.create_user(
                user_id=f'voter{i}',
                name=f'Voter {i}',
                username=f'voter{i}',
                member_type='Member'
            )
            Attendance.objects.create(user=voter, present=True)
            self.voters.append(voter)

    def test_percentage_mode_passes(self):
        """Test that percentage mode correctly passes with 51%"""
        leg = Legislation.objects.create(
            title='Percentage Test - Pass',
            description='Should pass with 6/10 yes votes',
            posted_by=self.uploader,
            available_at=timezone.now(),
            vote_mode='percentage',
            required_percentage='51',
            document='test.pdf'
        )

        # 6 yes, 4 no = 60% yes
        for i in range(6):
            Vote.objects.create(user=self.voters[i], legislation=leg, vote_choice='yes')
        for i in range(6, 10):
            Vote.objects.create(user=self.voters[i], legislation=leg, vote_choice='no')

        leg.set_passed()
        self.assertTrue(leg.passed, "60% should pass with 51% threshold")

    def test_percentage_mode_fails(self):
        """Test that percentage mode correctly fails with < 51%"""
        leg = Legislation.objects.create(
            title='Percentage Test - Fail',
            description='Should fail with 4/10 yes votes',
            posted_by=self.uploader,
            available_at=timezone.now(),
            vote_mode='percentage',
            required_percentage='51',
            document='test.pdf'
        )

        # 4 yes, 6 no = 40% yes
        for i in range(4):
            Vote.objects.create(user=self.voters[i], legislation=leg, vote_choice='yes')
        for i in range(4, 10):
            Vote.objects.create(user=self.voters[i], legislation=leg, vote_choice='no')

        leg.set_passed()
        self.assertFalse(leg.passed, "40% should fail with 51% threshold")

    def test_piecewise_mode_passes(self):
        """Test that piecewise mode passes with exact required votes"""
        leg = Legislation.objects.create(
            title='Piecewise Test - Pass',
            description='Should pass with 5 yes votes',
            posted_by=self.uploader,
            available_at=timezone.now(),
            vote_mode='piecewise',
            required_number=5,
            document='test.pdf'
        )

        # Exactly 5 yes votes
        for i in range(5):
            Vote.objects.create(user=self.voters[i], legislation=leg, vote_choice='yes')
        for i in range(5, 10):
            Vote.objects.create(user=self.voters[i], legislation=leg, vote_choice='no')

        leg.set_passed()
        self.assertTrue(leg.passed, "5 yes votes should pass with required_number=5")

    def test_piecewise_mode_fails(self):
        """Test that piecewise mode fails with insufficient votes"""
        leg = Legislation.objects.create(
            title='Piecewise Test - Fail',
            description='Should fail with 4 yes votes',
            posted_by=self.uploader,
            available_at=timezone.now(),
            vote_mode='piecewise',
            required_number=5,
            document='test.pdf'
        )

        # Only 4 yes votes
        for i in range(4):
            Vote.objects.create(user=self.voters[i], legislation=leg, vote_choice='yes')
        for i in range(4, 10):
            Vote.objects.create(user=self.voters[i], legislation=leg, vote_choice='no')

        leg.set_passed()
        self.assertFalse(leg.passed, "4 yes votes should fail with required_number=5")

    def test_plurality_mode_clear_winner(self):
        """Test that plurality mode passes with clear winner"""
        leg = Legislation.objects.create(
            title='Plurality Test - Clear Winner',
            description='Should pass with Pizza winning',
            posted_by=self.uploader,
            available_at=timezone.now(),
            vote_mode='plurality',
            plurality_options=['Pizza', 'Burgers', 'Tacos']
        )

        # Pizza: 5, Burgers: 3, Tacos: 2
        for i in range(5):
            Vote.objects.create(user=self.voters[i], legislation=leg, vote_choice='Pizza')
        for i in range(5, 8):
            Vote.objects.create(user=self.voters[i], legislation=leg, vote_choice='Burgers')
        for i in range(8, 10):
            Vote.objects.create(user=self.voters[i], legislation=leg, vote_choice='Tacos')

        leg.set_passed()
        self.assertTrue(leg.passed, "Plurality should pass with clear winner")

    def test_plurality_mode_tie(self):
        """Test that plurality mode fails with tie"""
        leg = Legislation.objects.create(
            title='Plurality Test - Tie',
            description='Should fail with tie between Pizza and Burgers',
            posted_by=self.uploader,
            available_at=timezone.now(),
            vote_mode='plurality',
            plurality_options=['Pizza', 'Burgers', 'Tacos']
        )

        # Pizza: 5, Burgers: 5 (tie)
        for i in range(5):
            Vote.objects.create(user=self.voters[i], legislation=leg, vote_choice='Pizza')
        for i in range(5, 10):
            Vote.objects.create(user=self.voters[i], legislation=leg, vote_choice='Burgers')

        leg.set_passed()
        self.assertFalse(leg.passed, "Plurality should fail with tie")


class LegislationViewTestCase(TestCase):
    """Test passed legislation and legislation history views"""

    def setUp(self):
        """Set up test data"""
        self.client = Client()
        self.user = ParliamentUser.objects.create_user(
            user_id='user1',
            name='Test User',
            username='testuser',
            member_type='Officer'
        )
        self.user.set_password('testpass')
        self.user.save()
        self.client.force_login(self.user)

        # Create some test legislation
        self.passed_leg = Legislation.objects.create(
            title='Passed Bill',
            description='This should show as passed',
            posted_by=self.user,
            available_at=timezone.now() - timedelta(days=1),
            voting_closed=True,
            passed=True,
            vote_mode='percentage',
            required_percentage='51',
            document='test.pdf'
        )

        self.failed_leg = Legislation.objects.create(
            title='Failed Bill',
            description='This should show as failed',
            posted_by=self.user,
            available_at=timezone.now() - timedelta(days=2),
            voting_closed=True,
            passed=False,
            vote_mode='percentage',
            required_percentage='51',
            document='test.pdf'
        )

    def test_passed_legislation_view_accessible(self):
        """Test that passed legislation page loads"""
        response = self.client.get(reverse('passed_legislation'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'passed_legislation.html')

    def test_legislation_history_view_accessible(self):
        """Test that legislation history page loads"""
        response = self.client.get(reverse('view_legislation_history'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'legislation_history.html')

    def test_legislation_history_shows_user_legislation(self):
        """Test that history shows only user's legislation"""
        # Create another user's legislation
        other_user = ParliamentUser.objects.create_user(
            user_id='other1',
            name='Other User',
            username='other',
            member_type='Member'
        )
        other_leg = Legislation.objects.create(
            title='Other User Bill',
            description='Should not appear in history',
            posted_by=other_user,
            available_at=timezone.now(),
            document='test.pdf'
        )

        response = self.client.get(reverse('view_legislation_history'))
        self.assertEqual(response.status_code, 200)

        # Check that user's legislation is in context
        leg_history = response.context['legislation_history']
        user_titles = [item['title'] for item in leg_history]

        self.assertIn('Passed Bill', user_titles)
        self.assertIn('Failed Bill', user_titles)
        self.assertNotIn('Other User Bill', user_titles)


class ProfileTestCase(TestCase):
    """Test user profile functionality including preferred name"""

    def setUp(self):
        """Set up test user"""
        self.client = Client()
        self.user = ParliamentUser.objects.create_user(
            user_id='profile1',
            name='Michael David Johnson',
            username='mjohnson',
            member_type='Member'
        )
        self.user.set_password('testpass')
        self.user.save()
        self.client.force_login(self.user)

    def test_profile_page_loads(self):
        """Test that profile page is accessible"""
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'profile.html')

    def test_preferred_name_update(self):
        """Test updating preferred name"""
        response = self.client.post(reverse('profile'), {
            'profile_submit': '1',
            'username': 'mjohnson',
            'preferred_name': 'Mike'
        })

        self.user.refresh_from_db()
        self.assertEqual(self.user.preferred_name, 'Mike')
        self.assertEqual(self.user.get_display_name(), 'Mike Johnson')

    def test_preferred_name_clear(self):
        """Test clearing preferred name"""
        self.user.preferred_name = 'Mike'
        self.user.save()

        response = self.client.post(reverse('profile'), {
            'profile_submit': '1',
            'username': 'mjohnson',
            'preferred_name': ''
        })

        self.user.refresh_from_db()
        self.assertIsNone(self.user.preferred_name)
        self.assertEqual(self.user.get_display_name(), 'Michael David Johnson')

    def test_username_update(self):
        """Test updating username"""
        response = self.client.post(reverse('profile'), {
            'profile_submit': '1',
            'username': 'newyusername',
            'preferred_name': ''
        })

        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'newyusername')

    def test_get_display_name_without_preferred(self):
        """Test display name without preferred name set"""
        self.user.preferred_name = None
        self.user.save()
        self.assertEqual(self.user.get_display_name(), 'Michael David Johnson')

    def test_get_display_name_with_preferred(self):
        """Test display name with preferred name set"""
        self.user.preferred_name = 'Mike'
        self.user.save()
        self.assertEqual(self.user.get_display_name(), 'Mike Johnson')


class CommitteeTestCase(TestCase):
    """Test committee functionality"""

    def setUp(self):
        """Set up test committee and users"""
        self.client = Client()

        # Create role
        self.role = Role.objects.create(
            id=1,
            code='VPB',
            name='Vice President of Brotherhood'
        )

        # Create committee
        self.committee = Committee.objects.create(
            id=1,
            code='BROTHER',
            name='Brotherhood Committee'
        )

        # Create users
        self.chair = ParliamentUser.objects.create_user(
            user_id='chair1',
            name='Chair User',
            username='chair',
            member_type='Chair'
        )
        self.chair.set_password('testpass')
        self.chair.save()

        self.member = ParliamentUser.objects.create_user(
            user_id='member1',
            name='Member User',
            username='member',
            member_type='Member'
        )

        self.committee.chairs.add(self.chair)
        self.committee.members.add(self.member)

    def test_committee_is_chair(self):
        """Test is_chair method"""
        self.assertTrue(self.committee.is_chair(self.chair))
        self.assertFalse(self.committee.is_chair(self.member))

    def test_committee_is_member(self):
        """Test is_member method"""
        self.assertTrue(self.committee.is_member(self.member))
        self.assertTrue(self.committee.is_member(self.chair))

    def test_committee_detail_view(self):
        """Test committee detail page loads"""
        self.client.force_login(self.chair)
        response = self.client.get(reverse('committee_detail', args=[self.committee.code]))
        self.assertEqual(response.status_code, 200)

    def test_committee_legislation_creation(self):
        """Test creating committee legislation"""
        self.client.force_login(self.chair)

        leg = CommitteeLegislation.objects.create(
            committee=self.committee,
            title='Committee Bill',
            description='Test committee legislation',
            posted_by=self.chair,
            available_at=timezone.now(),
            vote_mode='percentage',
            required_percentage='51',
            document='test.pdf'
        )

        self.assertEqual(leg.committee, self.committee)
        self.assertEqual(leg.posted_by, self.chair)


class EventArchivingTestCase(TestCase):
    """Test event archiving functionality"""

    def setUp(self):
        """Set up test events and admin user"""
        self.client = Client()

        self.admin = ParliamentUser.objects.create_user(
            user_id='admin1',
            name='Admin User',
            username='admin',
            member_type='Officer'
        )
        self.admin.is_admin = True
        self.admin.set_password('testpass')
        self.admin.save()

        # Create old event (should be archived)
        self.old_event = Event.objects.create(
            title='Old Event',
            description='Event from over a year ago',
            date_time=timezone.now() - timedelta(days=400),
            created_by=self.admin,
            is_active=True
        )

        # Create recent event (should not be archived)
        self.recent_event = Event.objects.create(
            title='Recent Event',
            description='Recent event',
            date_time=timezone.now() - timedelta(days=30),
            created_by=self.admin,
            is_active=True
        )

    def test_manual_archive_event(self):
        """Test manually archiving an event"""
        self.client.force_login(self.admin)

        response = self.client.get(reverse('archive_event', args=[self.recent_event.id]))

        self.recent_event.refresh_from_db()
        self.assertTrue(self.recent_event.archived)
        self.assertFalse(self.recent_event.is_active)

    def test_manual_unarchive_event(self):
        """Test manually unarchiving an event"""
        self.old_event.archived = True
        self.old_event.is_active = False
        self.old_event.save()

        self.client.force_login(self.admin)
        response = self.client.get(reverse('unarchive_event', args=[self.old_event.id]))

        self.old_event.refresh_from_db()
        self.assertFalse(self.old_event.archived)
        self.assertTrue(self.old_event.is_active)

    def test_archived_events_view(self):
        """Test archived events page loads for admin"""
        self.old_event.archived = True
        self.old_event.save()

        self.client.force_login(self.admin)
        response = self.client.get(reverse('view_archived_events'))

        self.assertEqual(response.status_code, 200)
        self.assertIn('archived_events', response.context)


class ModelMethodTestCase(TestCase):
    """Test model methods and properties"""

    def setUp(self):
        """Set up test models"""
        self.user = ParliamentUser.objects.create_user(
            user_id='model1',
            name='John Robert Smith',
            username='jsmith',
            member_type='Member'
        )

    def test_legislation_is_available_past(self):
        """Test is_available with past date"""
        leg = Legislation.objects.create(
            title='Past Legislation',
            description='Available in the past',
            posted_by=self.user,
            available_at=timezone.now() - timedelta(days=1),
            document='test.pdf'
        )
        self.assertTrue(leg.is_available())

    def test_legislation_is_available_future(self):
        """Test is_available with future date"""
        leg = Legislation.objects.create(
            title='Future Legislation',
            description='Available in the future',
            posted_by=self.user,
            available_at=timezone.now() + timedelta(days=1),
            document='test.pdf'
        )
        self.assertFalse(leg.is_available())

    def test_required_yes_votes_property_piecewise(self):
        """Test required_yes_votes property for piecewise mode"""
        leg = Legislation.objects.create(
            title='Piecewise Legislation',
            description='Piecewise mode',
            posted_by=self.user,
            available_at=timezone.now(),
            vote_mode='piecewise',
            required_number=10,
            document='test.pdf'
        )
        self.assertEqual(leg.required_yes_votes, 10)

    def test_required_yes_votes_property_percentage(self):
        """Test required_yes_votes property for percentage mode"""
        leg = Legislation.objects.create(
            title='Percentage Legislation',
            description='Percentage mode',
            posted_by=self.user,
            available_at=timezone.now(),
            vote_mode='percentage',
            required_percentage='51',
            document='test.pdf'
        )
        self.assertIsNone(leg.required_yes_votes)

    def test_user_str_method(self):
        """Test ParliamentUser __str__ method"""
        expected = f'{self.user.name} ({self.user.member_type})'
        self.assertEqual(str(self.user), expected)


class PermissionTestCase(TestCase):
    """Test permission checks and access control"""

    def setUp(self):
        """Set up users with different permissions"""
        self.client = Client()

        self.admin = ParliamentUser.objects.create_user(
            user_id='admin',
            name='Admin User',
            username='admin',
            member_type='Officer'
        )
        self.admin.is_admin = True
        self.admin.set_password('testpass')
        self.admin.save()

        self.officer = ParliamentUser.objects.create_user(
            user_id='officer',
            name='Officer User',
            username='officer',
            member_type='Officer'
        )
        self.officer.set_password('testpass')
        self.officer.save()

        self.member = ParliamentUser.objects.create_user(
            user_id='member',
            name='Regular Member',
            username='member',
            member_type='Member'
        )
        self.member.set_password('testpass')
        self.member.save()

    def test_admin_can_access_officer_home(self):
        """Test that admin can access officer home"""
        self.client.force_login(self.admin)
        response = self.client.get(reverse('officer_home'))
        self.assertEqual(response.status_code, 200)

    def test_officer_can_access_officer_home(self):
        """Test that officer can access officer home"""
        self.client.force_login(self.officer)
        response = self.client.get(reverse('officer_home'))
        self.assertEqual(response.status_code, 200)

    def test_member_cannot_access_officer_home(self):
        """Test that regular member cannot access officer home"""
        self.client.force_login(self.member)
        response = self.client.get(reverse('officer_home'))
        # Should redirect to login or show forbidden
        self.assertIn(response.status_code, [302, 403])

    def test_admin_can_archive_events(self):
        """Test that admin can archive events"""
        event = Event.objects.create(
            title='Test Event',
            description='Test',
            date_time=timezone.now(),
            created_by=self.admin
        )

        self.client.force_login(self.admin)
        response = self.client.get(reverse('archive_event', args=[event.id]))

        event.refresh_from_db()
        self.assertTrue(event.archived)
