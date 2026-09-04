"""
Feedback & Support — v3.29.5.

Requested by Mason: a page built like the existing bug-report system, but
two-in-one — a feature-ideas board *and* a direct "contact me" support
ticket that emails the admin. The two share one model (`FeedbackRequest`)
split by `request_type`.

The property worth testing hardest is the privacy split, since it's the one
place this genuinely isn't just BugReport with an extra field: a feature
idea is public (board, detail page, attachment — any logged-in member), a
support ticket is private (submitter + admin only, on all three surfaces),
and only a support ticket emails on submission.
"""
from django.core import mail
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from src.models import FeedbackRequest, ParliamentUser


def _member(uid, name, member_type='Member', member_status='Active'):
    return ParliamentUser.objects.create_user(
        user_id=uid, password='feedback-test-pass-12345!',
        name=name, username=uid.lower().replace('-', '_'),
        member_type=member_type, member_status=member_status,
    )


def _admin(uid='73'):
    """The hardcoded feedback/bug admin id — see CLAUDE.md, intentional."""
    return _member(uid, 'Feedback Admin', member_type='Officer')


class FeedbackRequestModelTests(TestCase):
    def test_str_includes_type_and_title(self):
        user = _member('FB-M1', 'Alice')
        fb = FeedbackRequest.objects.create(
            request_type='feature_idea', title='Dark mode for calendar',
            description='...', submitted_by=user,
        )
        self.assertIn('Feature Idea', str(fb))
        self.assertIn('Dark mode for calendar', str(fb))

    def test_is_public_property(self):
        user = _member('FB-M2', 'Bob')
        idea = FeedbackRequest.objects.create(
            request_type='feature_idea', title='X', description='...', submitted_by=user,
        )
        ticket = FeedbackRequest.objects.create(
            request_type='support_ticket', title='Y', description='...', submitted_by=user,
        )
        self.assertTrue(idea.is_public)
        self.assertFalse(ticket.is_public)

    def test_mark_resolved(self):
        user = _member('FB-M3', 'Carol')
        admin = _admin()
        fb = FeedbackRequest.objects.create(
            request_type='support_ticket', title='Help', description='...', submitted_by=user,
        )
        fb.mark_resolved(admin)
        fb.refresh_from_db()
        self.assertEqual(fb.status, 'resolved')
        self.assertEqual(fb.resolved_by_id, admin.pk)
        self.assertIsNotNone(fb.resolved_at)


@override_settings(
    REAL_EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST_USER='test@example.com',  # makes send_feedback_notification's
    # has_smtp guard pass, same guard shape as send_bug_report_notification's
)
class SubmitFeedbackTests(TestCase):
    def setUp(self):
        self.user = _member('FB-S1', 'Dana')
        self.client.login(username=self.user.username, password='feedback-test-pass-12345!')

    def test_requires_title_and_description(self):
        response = self.client.post(reverse('feedback_request'), {
            'request_type': 'feature_idea', 'title': '', 'description': '',
        })
        self.assertEqual(FeedbackRequest.objects.count(), 0)
        self.assertRedirects(response, reverse('feedback_request'))

    def test_creates_feature_idea(self):
        response = self.client.post(reverse('feedback_request'), {
            'request_type': 'feature_idea', 'title': 'Bulk excuse export',
            'description': 'Would be nice to export excuses to CSV.',
        })
        fb = FeedbackRequest.objects.get()
        self.assertEqual(fb.request_type, 'feature_idea')
        self.assertEqual(fb.submitted_by_id, self.user.pk)
        self.assertRedirects(response, reverse('feedback_request_success', args=[fb.id]))

    def test_creates_support_ticket(self):
        self.client.post(reverse('feedback_request'), {
            'request_type': 'support_ticket', 'title': 'Locked out',
            'description': "Can't log in, need help.",
        })
        fb = FeedbackRequest.objects.get()
        self.assertEqual(fb.request_type, 'support_ticket')

    def test_invalid_request_type_falls_back_to_support_ticket(self):
        self.client.post(reverse('feedback_request'), {
            'request_type': 'not_a_real_type', 'title': 'X', 'description': 'Y',
        })
        fb = FeedbackRequest.objects.get()
        self.assertEqual(fb.request_type, 'support_ticket')

    def test_support_ticket_sends_email_feature_idea_does_not(self):
        """
        The one behavior distinction that matters most: only the "contact me
        directly" path (support_ticket) should page the admin's inbox. A
        feature idea is meant to collect on the public board — emailing on
        every one of those would be noise for something that isn't urgent
        by nature (this is a deliberate design choice, not an oversight).
        """
        mail.outbox = []
        self.client.post(reverse('feedback_request'), {
            'request_type': 'feature_idea', 'title': 'Idea', 'description': 'Details',
        })
        self.assertEqual(len(mail.outbox), 0)

        mail.outbox = []
        self.client.post(reverse('feedback_request'), {
            'request_type': 'support_ticket', 'title': 'Ticket', 'description': 'Details',
        })
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Support Ticket', mail.outbox[0].subject)

    def test_login_required(self):
        self.client.logout()
        response = self.client.get(reverse('feedback_request'))
        self.assertNotEqual(response.status_code, 200)


class FeedbackTrackerVisibilityTests(TestCase):
    """
    `feedback_tracker` is the public ideas board. It must NEVER list a
    support ticket — filtered at the queryset level (src/view/feedback.py's
    `feedback_tracker`), not just hidden by the template, since a ticket can
    carry the kind of personal detail nobody else in the chapter should see.
    """
    def setUp(self):
        self.owner = _member('FB-T1', 'Erin')
        self.viewer = _member('FB-T2', 'Frank')
        self.idea = FeedbackRequest.objects.create(
            request_type='feature_idea', title='Public Idea', description='...',
            submitted_by=self.owner,
        )
        self.ticket = FeedbackRequest.objects.create(
            request_type='support_ticket', title='Private Ticket', description='...',
            submitted_by=self.owner,
        )
        self.client.login(username=self.viewer.username, password='feedback-test-pass-12345!')

    def test_tracker_lists_ideas_only(self):
        response = self.client.get(reverse('feedback_tracker'))
        ids = {fb.id for fb in response.context['feedback_requests']}
        self.assertIn(self.idea.id, ids)
        self.assertNotIn(self.ticket.id, ids)

    def test_tracker_never_renders_ticket_title(self):
        response = self.client.get(reverse('feedback_tracker'))
        self.assertNotContains(response, 'Private Ticket')
        self.assertContains(response, 'Public Idea')


class FeedbackRequestDetailAccessTests(TestCase):
    """
    The detail view is the one place the public/private split has real
    teeth against a direct/guessed URL, not just an absence from a list.
    """
    def setUp(self):
        self.owner = _member('FB-D1', 'Grace')
        self.other = _member('FB-D2', 'Heidi')
        self.admin = _admin()
        self.idea = FeedbackRequest.objects.create(
            request_type='feature_idea', title='Idea', description='...',
            submitted_by=self.owner,
        )
        self.ticket = FeedbackRequest.objects.create(
            request_type='support_ticket', title='Ticket', description='...',
            submitted_by=self.owner,
        )

    def _get(self, user, feedback_id):
        self.client.login(username=user.username, password='feedback-test-pass-12345!')
        response = self.client.get(reverse('feedback_request_detail', args=[feedback_id]))
        self.client.logout()
        return response

    def test_idea_is_visible_to_any_member(self):
        response = self._get(self.other, self.idea.id)
        self.assertEqual(response.status_code, 200)

    def test_ticket_is_visible_to_owner(self):
        response = self._get(self.owner, self.ticket.id)
        self.assertEqual(response.status_code, 200)

    def test_ticket_is_visible_to_admin(self):
        response = self._get(self.admin, self.ticket.id)
        self.assertEqual(response.status_code, 200)

    def test_ticket_404s_for_unrelated_member(self):
        response = self._get(self.other, self.ticket.id)
        self.assertEqual(response.status_code, 404)


class FeedbackAttachmentAccessTests(TestCase):
    """
    `serve_feedback_attachment` mirrors the detail view's rule exactly —
    same reasoning as `serve_bug_report_screenshot`, but branched on
    request_type since half of this model is public.
    """
    def setUp(self):
        self.owner = _member('FB-A1', 'Ivan')
        self.other = _member('FB-A2', 'Judy')
        self.admin = _admin()

    def _make(self, request_type):
        fb = FeedbackRequest.objects.create(
            request_type=request_type, title='X', description='...', submitted_by=self.owner,
        )
        fb.attachment.save('test.png', ContentFile(b'fake-image-bytes'))
        return fb

    def _status(self, user, fb):
        self.client.login(username=user.username, password='feedback-test-pass-12345!')
        response = self.client.get(reverse('feedback_attachment', args=[fb.id]))
        self.client.logout()
        return response.status_code

    def test_idea_attachment_readable_by_any_member(self):
        fb = self._make('feature_idea')
        self.assertEqual(self._status(self.other, fb), 200)

    def test_ticket_attachment_readable_by_owner(self):
        fb = self._make('support_ticket')
        self.assertEqual(self._status(self.owner, fb), 200)

    def test_ticket_attachment_readable_by_admin(self):
        fb = self._make('support_ticket')
        self.assertEqual(self._status(self.admin, fb), 200)

    def test_ticket_attachment_404s_for_unrelated_member(self):
        fb = self._make('support_ticket')
        self.assertEqual(self._status(self.other, fb), 404)


class MyFeedbackRequestsTests(TestCase):
    def test_shows_own_submissions_of_both_types(self):
        owner = _member('FB-MY1', 'Karl')
        other = _member('FB-MY2', 'Liam')
        idea = FeedbackRequest.objects.create(
            request_type='feature_idea', title='Mine Idea', description='...', submitted_by=owner,
        )
        ticket = FeedbackRequest.objects.create(
            request_type='support_ticket', title='Mine Ticket', description='...', submitted_by=owner,
        )
        FeedbackRequest.objects.create(
            request_type='feature_idea', title='Not Mine', description='...', submitted_by=other,
        )
        self.client.login(username=owner.username, password='feedback-test-pass-12345!')
        response = self.client.get(reverse('my_feedback_requests'))
        ids = {fb.id for fb in response.context['feedback_requests']}
        self.assertEqual(ids, {idea.id, ticket.id})
        self.assertNotContains(response, 'Not Mine')


class FeedbackAdminAccessTests(TestCase):
    """
    `feedback_admin_required` hardcodes user_id 73 — same intentional
    single-admin design as `bug_admin_required` (CLAUDE.md). This is its own
    decorator (not a reuse of bug_admin_required) only so a permission
    failure redirects into the feedback board rather than the bug tracker.
    """
    def setUp(self):
        self.admin = _admin()
        self.other = _member('FB-AD1', 'Mona', member_type='Officer')
        self.idea = FeedbackRequest.objects.create(
            request_type='feature_idea', title='Idea', description='...', submitted_by=self.other,
        )
        self.ticket = FeedbackRequest.objects.create(
            request_type='support_ticket', title='Ticket', description='...', submitted_by=self.other,
        )

    def test_non_admin_is_denied(self):
        self.client.login(username=self.other.username, password='feedback-test-pass-12345!')
        response = self.client.get(reverse('feedback_admin'))
        self.assertRedirects(response, reverse('feedback_tracker'))

    def test_admin_sees_both_types(self):
        self.client.login(username=self.admin.username, password='feedback-test-pass-12345!')
        response = self.client.get(reverse('feedback_admin'))
        self.assertEqual(response.status_code, 200)
        ids = {fb.id for fb in response.context['feedback_requests']}
        self.assertEqual(ids, {self.idea.id, self.ticket.id})

    def test_admin_can_update_status_and_notes(self):
        self.client.login(username=self.admin.username, password='feedback-test-pass-12345!')
        self.client.post(reverse('feedback_admin_update', args=[self.ticket.id]), {
            'status': 'resolved', 'admin_notes': 'Fixed via password reset.',
            'next': reverse('feedback_admin'),
        })
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'resolved')
        self.assertEqual(self.ticket.admin_notes, 'Fixed via password reset.')
        self.assertEqual(self.ticket.resolved_by_id, self.admin.pk)
        self.assertIsNotNone(self.ticket.resolved_at)

    def test_non_admin_cannot_update(self):
        self.client.login(username=self.other.username, password='feedback-test-pass-12345!')
        self.client.post(reverse('feedback_admin_update', args=[self.ticket.id]), {
            'status': 'resolved',
        })
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'new')

    def test_un_resolving_clears_resolution_fields(self):
        self.client.login(username=self.admin.username, password='feedback-test-pass-12345!')
        self.client.post(reverse('feedback_admin_update', args=[self.ticket.id]), {
            'status': 'resolved',
        })
        self.client.post(reverse('feedback_admin_update', args=[self.ticket.id]), {
            'status': 'in_progress',
        })
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'in_progress')
        self.assertIsNone(self.ticket.resolved_at)


@override_settings(
    REAL_EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST_USER='test@example.com',
)
class FeedbackActivityLogPrivacyTests(TestCase):
    """
    v3.29.10 — a support ticket's whole point is "private: submitter + admin
    only" (module docstring), enforced on the detail page, the tracker board,
    and the attachment view. `/officers/activity-logs/` is a fourth surface
    nobody enumerated: it's gated by `@officer_required`, which admits every
    officer AND every committee chair chapter-wide — a much larger audience
    than "submitter + admin" — and `submit_feedback` used to interpolate
    `feedback.title` verbatim into the `ActivityLog.description` it writes on
    every submission, for both types, unconditionally. Same category as the
    Kai "ActivityLog is prose storage" findings this codebase has fixed
    several times before (CLAUDE.md, v3.18.1/v3.18.2): a confidential field
    doesn't only live in the model access-control covers, it also lives in
    whatever free text got interpolated into an audit trail.

    `BugReport`'s own ActivityLog entry never included the report's
    title/description — only categorical fields (`issue_type`, `priority`)
    — so this brings `FeedbackRequest` in line with its sibling's existing
    convention rather than inventing a new one.
    """
    CONFIDENTIAL_TITLE = 'CONFIDENTIAL: my roommate is stealing my meds'

    def setUp(self):
        self.submitter = _member('FB-AL1', 'Sub Mitter')
        self.unrelated_officer = _member('FB-AL2', 'Officer One', member_type='Officer')

    def _submit_ticket(self):
        self.client.login(username=self.submitter.username, password='feedback-test-pass-12345!')
        self.client.post(reverse('feedback_request'), {
            'request_type': 'support_ticket',
            'title': self.CONFIDENTIAL_TITLE,
            'description': 'Please contact me privately, do not tell anyone.',
        })
        self.client.logout()
        return FeedbackRequest.objects.get(submitted_by=self.submitter)

    def test_ticket_title_never_lands_in_activity_log_description(self):
        from src.models import ActivityLog
        self._submit_ticket()
        log = ActivityLog.objects.get(action_type='feedback_submitted')
        self.assertNotIn(self.CONFIDENTIAL_TITLE, log.description)

    def test_unrelated_officer_cannot_read_ticket_title_via_activity_logs_page(self):
        """
        The reproduction that matters: the detail page correctly 404s for an
        unrelated officer (privacy holds there); the activity log page must
        not be a second, unguarded way to read the same content.
        """
        fb = self._submit_ticket()
        self.client.login(username=self.unrelated_officer.username, password='feedback-test-pass-12345!')

        detail_response = self.client.get(reverse('feedback_request_detail', args=[fb.id]))
        self.assertEqual(detail_response.status_code, 404)

        log_response = self.client.get(reverse('activity_logs'), {'date_range': 'all'})
        self.assertEqual(log_response.status_code, 200)
        self.assertNotContains(log_response, self.CONFIDENTIAL_TITLE)

    def test_unrelated_officer_cannot_read_ticket_title_via_csv_export(self):
        """Same leak, the surface that actually leaves the app as a file."""
        self._submit_ticket()
        self.client.login(username=self.unrelated_officer.username, password='feedback-test-pass-12345!')
        export_response = self.client.get(reverse('export_activity_logs'), {'date_range': 'all'})
        self.assertEqual(export_response.status_code, 200)
        self.assertNotIn(self.CONFIDENTIAL_TITLE.encode(), export_response.content)
