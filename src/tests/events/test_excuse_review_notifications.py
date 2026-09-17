"""
09-16-26 — Mason: "for excuses, when one is approved (or not) can it trigger
an email to the person who submitted it to let them know?"

Hooked into AttendanceExcuse.approve()/.deny() (src/models/events.py) rather
than the review views, since both the officer review UI
(src/view/officer/event_attendance.py::review_excuses) and the admin bulk
action (src/admin.py::AttendanceExcuseAdmin.approve_excuses/deny_excuses)
call those two model methods and nothing else — confirmed by grep, no other
code path changes an excuse's status. Testing at the model level therefore
covers both call sites without duplicating the test for each.

Covers: an in-app Notification is created on approve and on deny; an email
is sent when the member has an address, skipped (not failed) when they
don't; review notes are included in both; a notification-creation failure
does not block the approve/deny transaction itself; and an email failure
flags the member's address the same way every other outbound email in this
codebase does.
"""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from src.models import AttendanceExcuse, Event, Notification, ParliamentUser


def make_officer(uid='EXC-OFFICER'):
    return ParliamentUser.objects.create(
        user_id=uid, name='Officer', username=uid.lower(), member_type='Officer', member_status='Active')


def make_member(uid, email='member@example.com'):
    return ParliamentUser.objects.create(
        user_id=uid, name='Member', username=uid.lower(), member_type='Member',
        member_status='Active', email=email)


def make_excuse(member, officer_only_for_event=None):
    event = Event.objects.create(
        title='Chapter Meeting', description='d',
        date_time=timezone.now() + timedelta(days=1),
        created_by=officer_only_for_event or member, is_active=True,
    )
    return AttendanceExcuse.objects.create(event=event, user=member, reason='Family emergency')


class ApproveNotifiesTheSubmitterTests(TestCase):
    def setUp(self):
        self.officer = make_officer()
        self.member = make_member('exc-approve-1')
        self.excuse = make_excuse(self.member)

    def test_creates_an_in_app_notification(self):
        self.excuse.approve(self.officer, 'Looks good')
        notif = Notification.objects.get(recipient=self.member, notification_type='excuse_reviewed')
        self.assertIn('approved', notif.title)
        self.assertIn('Chapter Meeting', notif.title)
        self.assertEqual(notif.link, '/excuses/')
        self.assertEqual(notif.source_type, 'AttendanceExcuse')
        self.assertEqual(notif.source_id, self.excuse.pk)

    def test_sends_an_email(self):
        self.excuse.approve(self.officer, 'Looks good')
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ['member@example.com'])
        self.assertIn('approved', sent.subject)
        self.assertIn('Chapter Meeting', sent.body)

    def test_review_notes_are_included(self):
        self.excuse.approve(self.officer, 'Confirmed with your professor')
        notif = Notification.objects.get(recipient=self.member)
        self.assertIn('Confirmed with your professor', notif.message)
        self.assertIn('Confirmed with your professor', mail.outbox[0].body)


class DenyNotifiesTheSubmitterTests(TestCase):
    def setUp(self):
        self.officer = make_officer()
        self.member = make_member('exc-deny-1')
        self.excuse = make_excuse(self.member)

    def test_creates_an_in_app_notification(self):
        self.excuse.deny(self.officer, 'No documentation provided')
        notif = Notification.objects.get(recipient=self.member, notification_type='excuse_reviewed')
        self.assertIn('denied', notif.title)

    def test_sends_an_email(self):
        self.excuse.deny(self.officer, 'No documentation provided')
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('denied', mail.outbox[0].subject)
        self.assertIn('No documentation provided', mail.outbox[0].body)


class NoEmailAddressTests(TestCase):
    def test_in_app_notification_still_created_without_an_email(self):
        officer = make_officer('exc-noemail-off')
        member = make_member('exc-noemail-1', email='')
        excuse = make_excuse(member)

        excuse.approve(officer, 'ok')

        self.assertTrue(Notification.objects.filter(recipient=member, notification_type='excuse_reviewed').exists())
        self.assertEqual(len(mail.outbox), 0)


class FailuresDoNotBlockTheReviewTests(TestCase):
    def setUp(self):
        self.officer = make_officer('exc-fail-off')
        self.member = make_member('exc-fail-1')
        self.excuse = make_excuse(self.member)

    def test_a_notification_creation_failure_does_not_block_approval(self):
        with mock.patch('src.notification_service.create_notification', side_effect=RuntimeError('db down')):
            self.excuse.approve(self.officer, 'ok')  # must not raise
        self.excuse.refresh_from_db()
        self.assertEqual(self.excuse.status, 'approved')

    def test_an_email_failure_flags_the_members_email_and_does_not_block_denial(self):
        # src/notifications.py does `from django.core.mail import send_mail`
        # at module level, so the name to patch is the one bound in THAT
        # module's namespace, not django.core.mail's own attribute.
        with mock.patch('src.notifications.send_mail', side_effect=RuntimeError('smtp down')):
            self.excuse.deny(self.officer, 'no')  # must not raise
        self.excuse.refresh_from_db()
        self.assertEqual(self.excuse.status, 'denied')
        self.member.refresh_from_db()
        self.assertTrue(self.member.email_flagged)

    def test_a_top_level_failure_in_notify_excuse_reviewed_does_not_block_approval(self):
        # The model's own _notify_submitter() has a second, wider try/except
        # around the whole call — this proves that outer net independently
        # of notify_excuse_reviewed's own internal handling.
        with mock.patch('src.notifications.notify_excuse_reviewed', side_effect=RuntimeError('unexpected')):
            self.excuse.approve(self.officer, 'ok')  # must not raise
        self.excuse.refresh_from_db()
        self.assertEqual(self.excuse.status, 'approved')
