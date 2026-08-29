"""
Event reminders — optional email alongside the push (per-slot).

Event already had two independent push-reminder slots
(reminder_1_enabled/reminder_2_enabled, each with its own hours-before and
sent_at). This adds reminder_1_email_enabled/reminder_2_email_enabled: when
on, send_event_reminder_pushes also emails that slot's eligible recipients,
independently of whether they have a push subscription. Each slot's email
option is independent of the other's.

No test file existed for send_event_reminder_pushes before this — these
tests cover both the new email path and (incidentally) the pre-existing push
behavior, since the two are now interleaved in one function.
"""
from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from src.models import (
    Event, EventReminderLog, EventReminderRecipient, ParliamentUser,
    PushSubscription,
)
from src.tasks.notifications import send_event_reminder_pushes


def make_member(uid, email='', push=True, member_type='Member'):
    user = ParliamentUser.objects.create_user(
        user_id=uid, name=f'Member {uid}', username=uid,
        member_type=member_type, member_status='Active', email=email,
    )
    if push:
        PushSubscription.objects.create(
            user=user, endpoint=f'https://push.example.com/{uid}',
            p256dh='key', auth='secret',
        )
    return user


def make_officer(uid='reminder-officer'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Officer', username=uid, member_type='Officer',
        member_status='Active',
    )


def make_event(created_by, **kwargs):
    defaults = dict(
        title='Chapter Meeting', description='Weekly meeting', location='Room 1',
        date_time=timezone.now() + timedelta(hours=2),
        is_active=True, created_by=created_by,
        reminder_1_enabled=True, reminder_1_hours_before=24,
        reminder_1_email_enabled=False,
        reminder_2_enabled=False,
    )
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EventReminderEmailOptionTests(TestCase):
    """The core behavior: email is additive to push, per slot, opt-in."""

    def setUp(self):
        self.officer = make_officer()
        mail.outbox = []

    def _due_event(self, **kwargs):
        # reminder_1_hours_before=24 and the event is 2 hours out => due now.
        return make_event(self.officer, **kwargs)

    def test_email_not_sent_when_slot_email_option_is_off(self):
        member = make_member('m1', email='m1@example.com')
        event = self._due_event(reminder_1_email_enabled=False)

        send_event_reminder_pushes()

        self.assertEqual(len(mail.outbox), 0)
        recipient = EventReminderRecipient.objects.get(user=member)
        self.assertEqual(recipient.email_status, '')
        self.assertEqual(recipient.status, 'dispatched')  # push still fires

    def test_email_sent_when_slot_email_option_is_on(self):
        member = make_member('m2', email='m2@example.com')
        event = self._due_event(reminder_1_email_enabled=True)

        send_event_reminder_pushes()

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(member.email, mail.outbox[0].to)
        self.assertIn(event.title, mail.outbox[0].subject)
        recipient = EventReminderRecipient.objects.get(user=member)
        self.assertEqual(recipient.email_status, 'dispatched')
        self.assertEqual(recipient.status, 'dispatched')  # push unaffected

    def test_email_independent_of_push_subscription(self):
        """A user with no push subscription still gets the email."""
        member = make_member('m3', email='m3@example.com', push=False)
        self._due_event(reminder_1_email_enabled=True)

        send_event_reminder_pushes()

        self.assertEqual(len(mail.outbox), 1)
        recipient = EventReminderRecipient.objects.get(user=member)
        self.assertEqual(recipient.status, 'skipped_no_subscription')
        self.assertEqual(recipient.email_status, 'dispatched')

    def test_skipped_no_email_address(self):
        member = make_member('m4', email='')
        self._due_event(reminder_1_email_enabled=True)

        send_event_reminder_pushes()

        self.assertEqual(len(mail.outbox), 0)
        recipient = EventReminderRecipient.objects.get(user=member)
        self.assertEqual(recipient.email_status, 'skipped_no_email')

    def test_respects_email_events_preference_opt_out(self):
        member = make_member('m5', email='m5@example.com')
        member.preferences.prefs['email'] = {'events': False}
        member.preferences.save()
        self._due_event(reminder_1_email_enabled=True)

        send_event_reminder_pushes()

        self.assertEqual(len(mail.outbox), 0)
        recipient = EventReminderRecipient.objects.get(user=member)
        self.assertEqual(recipient.email_status, 'skipped_opted_out')

    def test_push_opt_out_does_not_suppress_email(self):
        """The two channels are independently gated by their own preference."""
        member = make_member('m6', email='m6@example.com')
        member.preferences.prefs['push'] = {'events': False}
        member.preferences.save()
        self._due_event(reminder_1_email_enabled=True)

        send_event_reminder_pushes()

        self.assertEqual(len(mail.outbox), 1)
        recipient = EventReminderRecipient.objects.get(user=member)
        self.assertEqual(recipient.status, 'skipped_opted_out')
        self.assertEqual(recipient.email_status, 'dispatched')

    def test_slots_are_independent(self):
        """Slot 1 email on, slot 2 email off — both due at once."""
        member = make_member('m7', email='m7@example.com')
        self._due_event(
            reminder_1_email_enabled=True,
            reminder_2_enabled=True, reminder_2_hours_before=1,
            reminder_2_email_enabled=False,
            date_time=timezone.now() + timedelta(minutes=30),
        )

        send_event_reminder_pushes()

        self.assertEqual(len(mail.outbox), 1)
        recipients = EventReminderRecipient.objects.filter(user=member).order_by('reminder_log__reminder_slot')
        self.assertEqual(recipients[0].email_status, 'dispatched')
        self.assertEqual(recipients[1].email_status, '')

    def test_log_counters(self):
        make_member('m8', email='m8@example.com')
        make_member('m9', email='')
        member_opted_out = make_member('m10', email='m10@example.com')
        member_opted_out.preferences.prefs['email'] = {'events': False}
        member_opted_out.preferences.save()
        self._due_event(reminder_1_email_enabled=True)

        send_event_reminder_pushes()

        log = EventReminderLog.objects.get(reminder_slot=1)
        self.assertEqual(log.emails_dispatched, 1)
        self.assertEqual(log.users_email_opted_out, 1)
        self.assertEqual(log.users_with_email, 2)  # m8 and m10 have addresses, m9 doesn't

    def test_email_send_failure_is_recorded_and_does_not_block_other_recipients(self):
        make_member('m11', email='fails@example.com')
        make_member('m12', email='succeeds@example.com')
        self._due_event(reminder_1_email_enabled=True)

        from unittest.mock import patch

        real_send_mail = mail.send_mail

        def flaky_send_mail(*args, **kwargs):
            if kwargs.get('recipient_list') == ['fails@example.com']:
                raise Exception('SMTP boom')
            return real_send_mail(*args, **kwargs)

        # send_event_reminder_pushes does `from django.core.mail import
        # send_mail` INSIDE the function body (matching this module's existing
        # local-import convention), so patching the name has to happen at its
        # real home — patching src.tasks.notifications.send_mail wouldn't
        # exist as an attribute to patch, since it's never bound at module
        # scope there.
        with patch('django.core.mail.send_mail', side_effect=flaky_send_mail):
            send_event_reminder_pushes()

        failed = EventReminderRecipient.objects.get(user_name='Member m11')
        ok = EventReminderRecipient.objects.get(user_name='Member m12')
        self.assertEqual(failed.email_status, 'failed')
        self.assertEqual(ok.email_status, 'dispatched')
        log = EventReminderLog.objects.get(reminder_slot=1)
        self.assertEqual(log.emails_dispatched, 1)

    def test_missing_default_from_email_degrades_to_push_only(self):
        make_member('m13', email='m13@example.com')
        self._due_event(reminder_1_email_enabled=True)

        with override_settings(DEFAULT_FROM_EMAIL=''):
            send_event_reminder_pushes()

        self.assertEqual(len(mail.outbox), 0)
        recipient = EventReminderRecipient.objects.get(user_name='Member m13')
        self.assertEqual(recipient.status, 'dispatched')  # push still worked
        self.assertEqual(recipient.email_status, '')  # not "skipped_no_email" — that'd be misleading

    def test_email_option_off_by_default_on_a_new_event(self):
        event = self._due_event()
        self.assertFalse(event.reminder_1_email_enabled)
        self.assertFalse(event.reminder_2_email_enabled)
