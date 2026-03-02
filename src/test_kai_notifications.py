"""
Tests for Kai User Dashboard and Notification Admin Features

Tests cover:
- Kai report submission and viewing
- Closure request functionality (submitter and accused)
- Drop case functionality
- Notification schedule CRUD operations
- Notification log viewing
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
import json

from .models import (
    ParliamentUser, Committee, KaiReport, KaiClosureRequest,
    KaiReportActivity, NotificationSchedule, NotificationLog
)


class KaiUserDashboardTestCase(TestCase):
    """Tests for Kai user dashboard functionality"""

    def setUp(self):
        """Set up test users and a Kai committee"""
        self.client = Client()

        # Create users
        self.submitter = ParliamentUser.objects.create_user(
            user_id='submitter1',
            name='Test Submitter',
            username='submitter',
            member_type='Member'
        )
        self.submitter.set_password('testpass')
        self.submitter.save()

        self.accused = ParliamentUser.objects.create_user(
            user_id='accused1',
            name='Test Accused',
            username='accused',
            member_type='Member'
        )
        self.accused.set_password('testpass')
        self.accused.save()

        self.kai_chair = ParliamentUser.objects.create_user(
            user_id='kaichair1',
            name='Kai Chair',
            username='kaichair',
            member_type='Chair'
        )
        self.kai_chair.set_password('testpass')
        self.kai_chair.save()

        # Create Kai committee
        self.kai_committee = Committee.objects.create(
            name='Kai Committee',
            code='KAI',
            is_active=True
        )
        self.kai_committee.chairs.add(self.kai_chair)

    def create_report(self, deliberation_outcome='pending', status='pending'):
        """Helper to create a Kai report"""
        return KaiReport.objects.create(
            title='Test Report',
            category='behavioral',
            description='Test description for the report',
            submitted_by=self.submitter,
            targeted_to=self.accused,
            status=status,
            deliberation_outcome=deliberation_outcome
        )

    def test_submitter_can_view_dashboard(self):
        """Test that submitter can view their Kai dashboard"""
        self.client.force_login(self.submitter)
        response = self.client.get(reverse('user_kai_dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Kai')

    def test_submitter_can_view_own_report(self):
        """Test that submitter can view their submitted report"""
        report = self.create_report()
        self.client.force_login(self.submitter)

        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Report')

    def test_accused_cannot_view_pending_report(self):
        """Test that accused user cannot view report while deliberation is pending"""
        report = self.create_report(deliberation_outcome='pending')
        self.client.force_login(self.accused)

        # Dashboard should not show pending reports to accused
        response = self.client.get(reverse('user_kai_dashboard'))
        self.assertEqual(response.status_code, 200)
        # Report should NOT appear in accused_reports
        self.assertNotIn(report, response.context['accused_reports'])

    def test_accused_can_view_addressed_report(self):
        """Test that accused user can view report after case is addressed"""
        report = self.create_report(deliberation_outcome='heard')
        self.client.force_login(self.accused)

        # Should be able to view the report
        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Report')

    def test_closure_request_unavailable_when_pending(self):
        """Test that closure request is not available when deliberation is pending"""
        report = self.create_report(deliberation_outcome='pending')
        self.client.force_login(self.submitter)

        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['can_request_closure'])

    def test_closure_request_available_after_heard(self):
        """Test that closure request is available after case is heard"""
        report = self.create_report(deliberation_outcome='heard')
        self.client.force_login(self.submitter)

        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['can_request_closure'])

    def test_closure_request_available_after_warning(self):
        """Test closure request available after warning issued"""
        report = self.create_report(deliberation_outcome='warning_issued')
        self.client.force_login(self.submitter)

        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertTrue(response.context['can_request_closure'])

    def test_closure_request_available_after_mediation(self):
        """Test closure request available after mediation"""
        report = self.create_report(deliberation_outcome='mediation')
        self.client.force_login(self.submitter)

        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertTrue(response.context['can_request_closure'])

    def test_closure_request_available_after_sanctions(self):
        """Test closure request available after sanctions applied"""
        report = self.create_report(deliberation_outcome='sanctions_applied')
        self.client.force_login(self.submitter)

        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertTrue(response.context['can_request_closure'])

    def test_closure_request_available_after_dismissed(self):
        """Test closure request available after case dismissed"""
        report = self.create_report(deliberation_outcome='dismissed')
        self.client.force_login(self.submitter)

        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertTrue(response.context['can_request_closure'])

    def test_closure_request_available_after_thrown_out(self):
        """Test closure request available after case thrown out"""
        report = self.create_report(deliberation_outcome='thrown_out')
        self.client.force_login(self.submitter)

        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertTrue(response.context['can_request_closure'])

    def test_submitter_can_submit_closure_request(self):
        """Test that submitter can submit a closure request"""
        report = self.create_report(deliberation_outcome='heard')
        self.client.force_login(self.submitter)

        response = self.client.post(
            reverse('kai_request_closure', args=[report.id]),
            {'reason': 'I believe this matter has been resolved.'}
        )

        self.assertEqual(response.status_code, 302)  # Redirect on success
        self.assertEqual(
            KaiClosureRequest.objects.filter(report=report, status='pending').count(),
            1
        )

    def test_accused_can_submit_closure_request(self):
        """Test that accused can submit a closure request after case addressed"""
        report = self.create_report(deliberation_outcome='heard')
        self.client.force_login(self.accused)

        response = self.client.post(
            reverse('kai_request_closure', args=[report.id]),
            {'reason': 'I accept the outcome and request closure.'}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            KaiClosureRequest.objects.filter(report=report, status='pending').count(),
            1
        )

    def test_cannot_submit_closure_when_pending_exists(self):
        """Test that cannot submit another closure request while one is pending"""
        report = self.create_report(deliberation_outcome='heard')

        # Create pending closure request
        KaiClosureRequest.objects.create(
            report=report,
            requested_by=self.submitter,
            request_type='closure',
            reason='Previous request',
            status='pending'
        )

        self.client.force_login(self.submitter)
        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertFalse(response.context['can_request_closure'])

    def test_drop_case_available_to_submitter(self):
        """Test that drop case option is available to submitter"""
        report = self.create_report(deliberation_outcome='pending')
        self.client.force_login(self.submitter)

        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertTrue(response.context['can_drop_case'])

    def test_drop_case_not_available_to_accused(self):
        """Test that accused cannot drop a case"""
        report = self.create_report(deliberation_outcome='heard')
        self.client.force_login(self.accused)

        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertFalse(response.context['can_drop_case'])

    def test_drop_case_blocked_after_sanctions(self):
        """Test that case cannot be dropped after sanctions applied"""
        report = self.create_report(deliberation_outcome='sanctions_applied')
        self.client.force_login(self.submitter)

        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertFalse(response.context['can_drop_case'])

    def test_submitter_can_submit_drop_request(self):
        """Test that submitter can submit a drop case request"""
        report = self.create_report(deliberation_outcome='pending')
        self.client.force_login(self.submitter)

        response = self.client.post(
            reverse('kai_request_drop', args=[report.id]),
            {'reason': 'I no longer wish to pursue this matter.'}
        )

        self.assertEqual(response.status_code, 302)
        closure_req = KaiClosureRequest.objects.get(report=report)
        self.assertEqual(closure_req.request_type, 'drop')

    def test_archived_report_cannot_request_closure(self):
        """Test that archived reports cannot have closure requested"""
        report = self.create_report(deliberation_outcome='heard', status='archived')
        self.client.force_login(self.submitter)

        response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
        self.assertFalse(response.context['can_request_closure'])

    def test_activity_logged_on_closure_request(self):
        """Test that activity is logged when closure is requested"""
        report = self.create_report(deliberation_outcome='heard')
        initial_count = KaiReportActivity.objects.filter(report=report).count()

        self.client.force_login(self.submitter)
        self.client.post(
            reverse('kai_request_closure', args=[report.id]),
            {'reason': 'Test reason'}
        )

        self.assertEqual(
            KaiReportActivity.objects.filter(report=report).count(),
            initial_count + 1
        )
        last_activity = KaiReportActivity.objects.filter(report=report).first()
        self.assertEqual(last_activity.action, 'closure_requested')


class NotificationAdminTestCase(TestCase):
    """Tests for notification admin dashboard functionality"""

    def setUp(self):
        """Set up admin user and session"""
        self.client = Client()

        # Create admin user
        self.admin = ParliamentUser.objects.create_user(
            user_id='admin1',
            name='Test Admin',
            username='admin',
            member_type='Chair'
        )
        self.admin.is_admin = True
        self.admin.set_password('adminpass')
        self.admin.save()

        # Create a test committee
        self.committee = Committee.objects.create(
            name='Test Committee',
            code='TEST',
            is_active=True
        )

    def login_and_auth_admin_v2(self):
        """Helper to login and set admin v2 session"""
        self.client.force_login(self.admin)
        session = self.client.session
        session['admin_v2_authenticated'] = True
        session.save()

    def test_notification_dashboard_requires_auth(self):
        """Test that notification dashboard requires admin v2 auth"""
        self.client.force_login(self.admin)
        # Without admin_v2_authenticated in session
        response = self.client.get(reverse('admin_v2_notifications'))
        self.assertEqual(response.status_code, 302)  # Redirect to login

    def test_notification_dashboard_accessible_when_authed(self):
        """Test that dashboard is accessible with proper auth"""
        self.login_and_auth_admin_v2()
        response = self.client.get(reverse('admin_v2_notifications'))
        self.assertEqual(response.status_code, 200)

    def test_can_create_notification_schedule(self):
        """Test creating a notification schedule"""
        self.login_and_auth_admin_v2()

        schedule_data = {
            'name': 'Test Event Reminder',
            'notification_type': 'event_reminder',
            'description': 'Reminder for upcoming events',
            'hours_before': 24,
            'send_email': True,
            'send_in_app': True,
            'target_audience': 'all_active',
            'message_template': 'Reminder: {event_name} is happening soon!'
        }

        response = self.client.post(
            reverse('admin_v2_create_notification_schedule'),
            json.dumps(schedule_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertTrue(NotificationSchedule.objects.filter(name='Test Event Reminder').exists())

    def test_create_schedule_requires_name(self):
        """Test that schedule creation fails without required fields"""
        self.login_and_auth_admin_v2()

        schedule_data = {
            'notification_type': 'event_reminder',
            'message_template': 'Test message'
            # Missing 'name'
        }

        response = self.client.post(
            reverse('admin_v2_create_notification_schedule'),
            json.dumps(schedule_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])

    def test_can_update_notification_schedule(self):
        """Test updating a notification schedule"""
        self.login_and_auth_admin_v2()

        # Create a schedule first
        schedule = NotificationSchedule.objects.create(
            name='Original Name',
            notification_type='event_reminder',
            message_template='Original message',
            created_by=self.admin
        )

        update_data = {
            'name': 'Updated Name',
            'hours_before': 48
        }

        response = self.client.post(
            reverse('admin_v2_update_notification_schedule', args=[schedule.id]),
            json.dumps(update_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        schedule.refresh_from_db()
        self.assertEqual(schedule.name, 'Updated Name')
        self.assertEqual(schedule.hours_before, 48)

    def test_can_toggle_schedule_status(self):
        """Test toggling a schedule's active status"""
        self.login_and_auth_admin_v2()

        schedule = NotificationSchedule.objects.create(
            name='Toggle Test',
            notification_type='event_reminder',
            message_template='Test',
            is_active=True,
            created_by=self.admin
        )

        response = self.client.post(
            reverse('admin_v2_toggle_notification_schedule', args=[schedule.id])
        )

        self.assertEqual(response.status_code, 200)
        schedule.refresh_from_db()
        self.assertFalse(schedule.is_active)

        # Toggle again
        response = self.client.post(
            reverse('admin_v2_toggle_notification_schedule', args=[schedule.id])
        )
        schedule.refresh_from_db()
        self.assertTrue(schedule.is_active)

    def test_can_delete_schedule(self):
        """Test deleting a notification schedule"""
        self.login_and_auth_admin_v2()

        schedule = NotificationSchedule.objects.create(
            name='Delete Test',
            notification_type='event_reminder',
            message_template='Test',
            created_by=self.admin
        )
        schedule_id = schedule.id

        response = self.client.post(
            reverse('admin_v2_delete_notification_schedule', args=[schedule_id])
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(NotificationSchedule.objects.filter(id=schedule_id).exists())

    def test_notification_logs_page_accessible(self):
        """Test that notification logs page is accessible"""
        self.login_and_auth_admin_v2()
        response = self.client.get(reverse('admin_v2_notification_logs'))
        self.assertEqual(response.status_code, 200)

    def test_notification_logs_filtering(self):
        """Test that notification logs can be filtered"""
        self.login_and_auth_admin_v2()

        # Create some test logs
        NotificationLog.objects.create(
            notification_type='event_reminder',
            title='Test Event',
            message='Event reminder message',
            status='sent'
        )
        NotificationLog.objects.create(
            notification_type='vote_reminder',
            title='Test Vote',
            message='Vote reminder message',
            status='failed'
        )

        # Filter by status
        response = self.client.get(reverse('admin_v2_notification_logs') + '?status=sent')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['logs']), 1)

        # Filter by type
        response = self.client.get(reverse('admin_v2_notification_logs') + '?type=vote_reminder')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context['logs']), 1)

    def test_dashboard_shows_stats(self):
        """Test that dashboard shows correct statistics"""
        self.login_and_auth_admin_v2()

        # Create schedules
        NotificationSchedule.objects.create(
            name='Active Schedule 1',
            notification_type='event_reminder',
            message_template='Test',
            is_active=True,
            created_by=self.admin
        )
        NotificationSchedule.objects.create(
            name='Active Schedule 2',
            notification_type='vote_reminder',
            message_template='Test',
            is_active=True,
            created_by=self.admin
        )
        NotificationSchedule.objects.create(
            name='Inactive Schedule',
            notification_type='custom',
            message_template='Test',
            is_active=False,
            created_by=self.admin
        )

        response = self.client.get(reverse('admin_v2_notifications'))
        self.assertEqual(response.status_code, 200)

        stats = response.context['stats']
        self.assertEqual(stats['total_schedules'], 3)
        self.assertEqual(stats['active_schedules'], 2)


class KaiEligibleOutcomesTestCase(TestCase):
    """Tests specifically for the eligible outcomes logic"""

    def setUp(self):
        self.client = Client()

        self.user = ParliamentUser.objects.create_user(
            user_id='user1',
            name='Test User',
            username='testuser',
            member_type='Member'
        )

        self.other_user = ParliamentUser.objects.create_user(
            user_id='user2',
            name='Other User',
            username='otheruser',
            member_type='Member'
        )

    def test_eligible_outcomes_list(self):
        """Test all eligible outcomes allow closure request"""
        from src.view.kai_user_dashboard import ELIGIBLE_OUTCOMES

        expected_outcomes = [
            'heard', 'warning_issued', 'mediation', 'sanctions_applied',
            'dismissed', 'thrown_out'
        ]

        self.assertEqual(sorted(ELIGIBLE_OUTCOMES), sorted(expected_outcomes))

    def test_ineligible_outcomes_block_closure(self):
        """Test ineligible outcomes block closure request"""
        ineligible_outcomes = [
            'pending', 'under_investigation', 'scheduled', 'referred'
        ]

        for outcome in ineligible_outcomes:
            report = KaiReport.objects.create(
                title=f'Test Report {outcome}',
                category='behavioral',
                description='Test',
                submitted_by=self.user,
                targeted_to=self.other_user,
                deliberation_outcome=outcome
            )

            self.client.force_login(self.user)
            response = self.client.get(reverse('user_view_kai_report', args=[report.id]))
            self.assertFalse(
                response.context['can_request_closure'],
                f"Expected can_request_closure=False for outcome '{outcome}'"
            )
