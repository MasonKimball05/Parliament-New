"""
Comprehensive tests for pledge member permissions.

These tests verify that:
1. Pledges cannot access write operations (legislation, voting, officer functions)
2. Pledges can only see events/announcements marked visible to them
3. Calendar subscription feeds correctly filter events for pledges
4. The @exclude_pledges decorator works correctly
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from src.models import (
    ParliamentUser, Event, Announcement, Legislation, SlatingPeriod
)
from src.models_calendar_subscription import CalendarSubscription


class PledgeUserPropertiesTestCase(TestCase):
    """Test the is_pledge and can_vote properties."""

    def setUp(self):
        self.pledge = ParliamentUser.objects.create_user(
            user_id='pledge1',
            name='Test Pledge',
            username='pledge1',
            member_type='Pledge'
        )
        self.member = ParliamentUser.objects.create_user(
            user_id='member1',
            name='Test Member',
            username='member1',
            member_type='Member'
        )
        self.officer = ParliamentUser.objects.create_user(
            user_id='officer1',
            name='Test Officer',
            username='officer1',
            member_type='Officer'
        )

    def test_pledge_is_pledge_property(self):
        """Pledges should have is_pledge = True."""
        self.assertTrue(self.pledge.is_pledge)
        self.assertFalse(self.member.is_pledge)
        self.assertFalse(self.officer.is_pledge)

    def test_pledge_cannot_vote(self):
        """Pledges should have can_vote = False."""
        self.assertFalse(self.pledge.can_vote)
        self.assertTrue(self.member.can_vote)
        self.assertTrue(self.officer.can_vote)


class PledgeWriteRestrictionsTestCase(TestCase):
    """Test that pledges cannot perform write operations."""

    def setUp(self):
        self.client = Client()
        self.pledge = ParliamentUser.objects.create_user(
            user_id='pledge1',
            name='Test Pledge',
            username='pledge1',
            password='testpass123',
            member_type='Pledge'
        )
        self.officer = ParliamentUser.objects.create_user(
            user_id='officer1',
            name='Test Officer',
            username='officer1',
            password='testpass123',
            member_type='Officer'
        )
        self.officer.is_admin = True
        self.officer.save()

    def test_pledge_cannot_access_officer_home(self):
        """Pledges should be forbidden from officer pages."""
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('officer_home'))
        # Should be forbidden or redirected
        self.assertIn(response.status_code, [302, 403])

    def test_pledge_cannot_upload_legislation(self):
        """Pledges should not be able to upload legislation."""
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('upload_legislation'))
        self.assertIn(response.status_code, [302, 403])

    def test_pledge_cannot_manage_events(self):
        """Pledges should not be able to manage events."""
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('manage_events'))
        self.assertIn(response.status_code, [302, 403])

    def test_pledge_cannot_manage_announcements(self):
        """Pledges should not be able to manage announcements."""
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('manage_announcements'))
        self.assertIn(response.status_code, [302, 403])

    def test_officer_can_access_officer_home(self):
        """Officers should be able to access officer pages."""
        self.client.force_login(self.officer)
        response = self.client.get(reverse('officer_home'))
        self.assertEqual(response.status_code, 200)


class EventVisibilityTestCase(TestCase):
    """Test that events respect visibility settings for pledges."""

    def setUp(self):
        self.client = Client()
        self.pledge = ParliamentUser.objects.create_user(
            user_id='pledge1',
            name='Test Pledge',
            username='pledge1',
            password='testpass123',
            member_type='Pledge'
        )
        self.member = ParliamentUser.objects.create_user(
            user_id='member1',
            name='Test Member',
            username='member1',
            password='testpass123',
            member_type='Member'
        )
        self.officer = ParliamentUser.objects.create_user(
            user_id='officer1',
            name='Test Officer',
            username='officer1',
            password='testpass123',
            member_type='Officer'
        )

        # Event visible to all (no restriction)
        self.event_all = Event.objects.create(
            title='Event for All',
            description='Visible to everyone',
            date_time=timezone.now() + timedelta(days=1),
            location='Test Location',
            visible_to=None,  # None means visible to all
            is_active=True,
            created_by=self.officer
        )

        # Event visible only to members (not pledges)
        self.event_members_only = Event.objects.create(
            title='Members Only Event',
            description='Not for pledges',
            date_time=timezone.now() + timedelta(days=2),
            location='Test Location',
            visible_to=['Member'],  # Only members
            is_active=True,
            created_by=self.officer
        )

        # Event visible to pledges explicitly
        self.event_with_pledges = Event.objects.create(
            title='Pledge Inclusive Event',
            description='Pledges can see this',
            date_time=timezone.now() + timedelta(days=3),
            location='Test Location',
            visible_to=['Member', 'Pledge'],
            is_active=True,
            created_by=self.officer
        )

        # Event visible only to pledges
        self.event_pledges_only = Event.objects.create(
            title='Pledge Only Event',
            description='Only for pledges',
            date_time=timezone.now() + timedelta(days=4),
            location='Test Location',
            visible_to=['Pledge'],
            is_active=True,
            created_by=self.officer
        )

    def test_event_visible_to_all_includes_pledge(self):
        """Events with no visibility restriction should be visible to pledges."""
        self.assertTrue(self.event_all.is_visible_to_user(self.pledge))
        self.assertTrue(self.event_all.is_visible_to_user(self.member))
        self.assertTrue(self.event_all.is_visible_to_user(self.officer))

    def test_members_only_event_excludes_pledge(self):
        """Events restricted to members should exclude pledges."""
        self.assertFalse(self.event_members_only.is_visible_to_user(self.pledge))
        self.assertTrue(self.event_members_only.is_visible_to_user(self.member))
        # Officers should see member events too
        self.assertTrue(self.event_members_only.is_visible_to_user(self.officer))

    def test_event_with_pledge_included(self):
        """Events explicitly including pledges should be visible to them."""
        self.assertTrue(self.event_with_pledges.is_visible_to_user(self.pledge))
        self.assertTrue(self.event_with_pledges.is_visible_to_user(self.member))

    def test_pledge_only_event(self):
        """Events for pledges only should be visible only to pledges."""
        self.assertTrue(self.event_pledges_only.is_visible_to_user(self.pledge))
        self.assertFalse(self.event_pledges_only.is_visible_to_user(self.member))

    def test_inactive_event_not_visible(self):
        """Inactive events should not be visible to anyone."""
        self.event_all.is_active = False
        self.event_all.save()
        self.assertFalse(self.event_all.is_visible_to_user(self.pledge))
        self.assertFalse(self.event_all.is_visible_to_user(self.member))


class AnnouncementVisibilityTestCase(TestCase):
    """Test that announcements respect visibility settings for pledges."""

    def setUp(self):
        self.client = Client()
        self.pledge = ParliamentUser.objects.create_user(
            user_id='pledge1',
            name='Test Pledge',
            username='pledge1',
            password='testpass123',
            member_type='Pledge'
        )
        self.member = ParliamentUser.objects.create_user(
            user_id='member1',
            name='Test Member',
            username='member1',
            password='testpass123',
            member_type='Member'
        )
        self.officer = ParliamentUser.objects.create_user(
            user_id='officer1',
            name='Test Officer',
            username='officer1',
            password='testpass123',
            member_type='Officer'
        )

        # Announcement visible to all
        self.announce_all = Announcement.objects.create(
            title='Announcement for All',
            content='Everyone can see this',
            posted_by=self.officer,
            publish_at=timezone.now() - timedelta(hours=1),  # Published
            visible_to=None,
            is_active=True
        )

        # Announcement visible only to members
        self.announce_members_only = Announcement.objects.create(
            title='Members Only Announcement',
            content='Not for pledges',
            posted_by=self.officer,
            publish_at=timezone.now() - timedelta(hours=1),
            visible_to=['Member'],
            is_active=True
        )

        # Announcement visible to pledges explicitly
        self.announce_with_pledges = Announcement.objects.create(
            title='Pledge Inclusive Announcement',
            content='Pledges can see this',
            posted_by=self.officer,
            publish_at=timezone.now() - timedelta(hours=1),
            visible_to=['Member', 'Pledge'],
            is_active=True
        )

    def test_announcement_visible_to_all_includes_pledge(self):
        """Announcements with no visibility restriction should be visible to pledges."""
        self.assertTrue(self.announce_all.is_visible_to_user(self.pledge))
        self.assertTrue(self.announce_all.is_visible_to_user(self.member))

    def test_members_only_announcement_excludes_pledge(self):
        """Announcements restricted to members should exclude pledges."""
        self.assertFalse(self.announce_members_only.is_visible_to_user(self.pledge))
        self.assertTrue(self.announce_members_only.is_visible_to_user(self.member))

    def test_announcement_with_pledge_included(self):
        """Announcements explicitly including pledges should be visible to them."""
        self.assertTrue(self.announce_with_pledges.is_visible_to_user(self.pledge))
        self.assertTrue(self.announce_with_pledges.is_visible_to_user(self.member))

    def test_unpublished_announcement_not_visible(self):
        """Unpublished announcements should not be visible."""
        future_announce = Announcement.objects.create(
            title='Future Announcement',
            content='Not published yet',
            posted_by=self.officer,
            publish_at=timezone.now() + timedelta(days=1),  # Future
            visible_to=None,
            is_active=True
        )
        self.assertFalse(future_announce.is_visible_to_user(self.pledge))
        self.assertFalse(future_announce.is_visible_to_user(self.member))


class CalendarSubscriptionFeedTestCase(TestCase):
    """Test that calendar subscription feeds correctly filter events for pledges."""

    def setUp(self):
        self.client = Client()
        self.pledge = ParliamentUser.objects.create_user(
            user_id='pledge1',
            name='Test Pledge',
            username='pledge1',
            password='testpass123',
            member_type='Pledge'
        )
        self.member = ParliamentUser.objects.create_user(
            user_id='member1',
            name='Test Member',
            username='member1',
            password='testpass123',
            member_type='Member'
        )
        self.officer = ParliamentUser.objects.create_user(
            user_id='officer1',
            name='Test Officer',
            username='officer1',
            password='testpass123',
            member_type='Officer'
        )

        # Create calendar subscription for pledge
        self.pledge_subscription = CalendarSubscription.get_or_create_for_user(
            self.pledge
        )

        # Create calendar subscription for member
        self.member_subscription = CalendarSubscription.get_or_create_for_user(
            self.member
        )

        # Event visible to all
        self.event_all = Event.objects.create(
            title='Event for All',
            description='Visible to everyone',
            date_time=timezone.now() + timedelta(days=1),
            location='Test Location',
            visible_to=None,
            is_active=True,
            created_by=self.officer
        )

        # Event for members only
        self.event_members_only = Event.objects.create(
            title='Secret Member Event',
            description='Not for pledges',
            date_time=timezone.now() + timedelta(days=2),
            location='Secret Location',
            visible_to=['Member'],
            is_active=True,
            created_by=self.officer
        )

    def test_pledge_calendar_feed_excludes_member_only_events(self):
        """Pledge's calendar feed should not include member-only events."""
        response = self.client.get(
            reverse('calendar_subscription_feed', args=[self.pledge_subscription.token])
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Should include the all-members event
        self.assertIn('Event for All', content)
        # Should NOT include the members-only event
        self.assertNotIn('Secret Member Event', content)

    def test_member_calendar_feed_includes_member_events(self):
        """Member's calendar feed should include member-only events."""
        response = self.client.get(
            reverse('calendar_subscription_feed', args=[self.member_subscription.token])
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')

        # Should include both events
        self.assertIn('Event for All', content)
        self.assertIn('Secret Member Event', content)

    def test_invalid_token_returns_error(self):
        """Invalid calendar token should return an error."""
        response = self.client.get(
            reverse('calendar_subscription_feed', args=['invalid-token-12345'])
        )
        self.assertEqual(response.status_code, 404)


class ExcludePledgesDecoratorTestCase(TestCase):
    """Test the @exclude_pledges decorator functionality."""

    def setUp(self):
        self.client = Client()
        self.pledge = ParliamentUser.objects.create_user(
            user_id='pledge1',
            name='Test Pledge',
            username='pledge1',
            password='testpass123',
            member_type='Pledge'
        )
        self.member = ParliamentUser.objects.create_user(
            user_id='member1',
            name='Test Member',
            username='member1',
            password='testpass123',
            member_type='Member'
        )
        # Create a slating period for testing
        self.period = SlatingPeriod.objects.create(
            name='Test Period',
            status='nominations_open'
        )

    def test_pledge_excluded_from_slating_apply(self):
        """Pledges should be excluded from slating apply view."""
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('slating_apply', args=[self.period.id]))
        # Should be forbidden
        self.assertEqual(response.status_code, 403)

    def test_member_can_access_slating_apply(self):
        """Members should be able to access slating apply view."""
        self.client.force_login(self.member)
        response = self.client.get(reverse('slating_apply', args=[self.period.id]))
        # Should be allowed (200 or could be redirect if already applied)
        self.assertIn(response.status_code, [200, 302])


class PledgeVotingRestrictionsTestCase(TestCase):
    """Test that pledges cannot vote on legislation."""

    def setUp(self):
        self.client = Client()
        self.pledge = ParliamentUser.objects.create_user(
            user_id='pledge1',
            name='Test Pledge',
            username='pledge1',
            password='testpass123',
            member_type='Pledge'
        )
        self.officer = ParliamentUser.objects.create_user(
            user_id='officer1',
            name='Test Officer',
            username='officer1',
            password='testpass123',
            member_type='Officer'
        )

        # Create active legislation
        self.legislation = Legislation.objects.create(
            title='Test Bill',
            description='A test bill',
            document='test.pdf',
            posted_by=self.officer,
            available_at=timezone.now() - timedelta(hours=1),
            is_active=True
        )

    def test_pledge_can_vote_property_false(self):
        """Pledges should have can_vote = False."""
        self.assertFalse(self.pledge.can_vote)
        self.assertTrue(self.officer.can_vote)

    def test_pledge_cannot_vote(self):
        """Pledges should not be able to cast votes via the vote view."""
        self.client.force_login(self.pledge)
        # The vote view at /vote/ requires password confirmation and legislation_id
        response = self.client.post(
            reverse('vote'),
            {
                'vote_choice': 'yes',
                'legislation_id': self.legislation.id,
                'password': 'testpass123'
            }
        )
        # Should be rejected with redirect (the view checks can_vote)
        self.assertIn(response.status_code, [200, 302])

        # Most importantly: verify no vote was recorded
        from src.models import Vote
        votes = Vote.objects.filter(
            legislation=self.legislation,
            user=self.pledge
        )
        self.assertEqual(votes.count(), 0)


class HomePageVisibilityTestCase(TestCase):
    """Test that the home page correctly filters content for pledges."""

    def setUp(self):
        self.client = Client()
        self.pledge = ParliamentUser.objects.create_user(
            user_id='pledge1',
            name='Test Pledge',
            username='pledge1',
            password='testpass123',
            member_type='Pledge'
        )
        self.officer = ParliamentUser.objects.create_user(
            user_id='officer1',
            name='Test Officer',
            username='officer1',
            password='testpass123',
            member_type='Officer'
        )

        # Create events with different visibility
        self.event_all = Event.objects.create(
            title='Public Event',
            description='Everyone can see',
            date_time=timezone.now() + timedelta(days=1),
            location='Public Place',
            visible_to=None,
            is_active=True,
            created_by=self.officer
        )

        self.event_secret = Event.objects.create(
            title='Secret Event',
            description='Members only',
            date_time=timezone.now() + timedelta(days=2),
            location='Secret Place',
            visible_to=['Member'],
            is_active=True,
            created_by=self.officer
        )

    def test_pledge_home_shows_filtered_events(self):
        """Pledge home page should only show events they can see."""
        # home.py filters events with visible_to__contains (JSONField), which
        # sqlite doesn't support — skip on non-postgres backends; CI (postgres)
        # still runs this.
        from unittest import SkipTest
        from django.db import connection
        if connection.vendor != 'postgresql':
            raise SkipTest('visible_to__contains lookup requires PostgreSQL')
        self.client.force_login(self.pledge)
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)

        content = response.content.decode('utf-8')
        # Should see public event
        self.assertIn('Public Event', content)
        # Should NOT see secret event
        self.assertNotIn('Secret Event', content)
