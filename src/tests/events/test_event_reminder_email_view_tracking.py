"""
Event reminder email — view (open) tracking.

Mirrors the announcement system's tracking-pixel pattern: the HTML reminder
email embeds a 1x1 image pointing at track_event_reminder_email_view, which
stamps EventReminderRecipient.viewed_at the first time a recipient's mail
client fetches it. Covers the pixel view itself, the URL only appearing in
the email when that slot's email option is on, and the admin-v2 detail page
correctly splitting recipients into "viewed" / "not yet viewed".

test_event_email_reminders.py covers the email-vs-push dispatch logic
itself; this file is specifically about the view-tracking half of the
feature and the error-logging behavior on a failed send.
"""
from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone as tz

from src.models import (
    Event, EventReminderLog, EventReminderRecipient, ParliamentUser,
)
from src.tasks.notifications import send_event_reminder_pushes


def make_member(uid, email='', member_type='Member'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name=f'Member {uid}', username=uid,
        member_type=member_type, member_status='Active', email=email,
    )


def make_officer(uid='reminder-officer'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Officer', username=uid, member_type='Officer',
        member_status='Active',
    )


def make_admin(uid='reminder-admin'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Admin', username=uid, member_type='Officer',
        member_status='Active', is_admin=True,
    )


def make_event(created_by, **kwargs):
    defaults = dict(
        title='Chapter Meeting', description='Weekly meeting', location='Room 1',
        date_time=tz.now() + timedelta(hours=2),
        is_active=True, created_by=created_by,
        reminder_1_enabled=True, reminder_1_hours_before=24,
        reminder_1_email_enabled=True,
        reminder_2_enabled=False,
    )
    defaults.update(kwargs)
    return Event.objects.create(**defaults)


class TrackingPixelViewTests(TestCase):
    """Direct tests of track_event_reminder_email_view, no login required."""

    def setUp(self):
        officer = make_officer()
        event = make_event(officer)
        self.log = EventReminderLog.objects.create(event=event, reminder_slot=1)
        self.member = make_member('px1', email='px1@example.com')
        self.recipient = EventReminderRecipient.objects.create(
            reminder_log=self.log, user=self.member,
            user_name=self.member.name, user_member_type=self.member.member_type,
            status='dispatched', email_status='dispatched',
        )
        self.client = Client()

    def _pixel_url(self, log_id, user_id):
        return reverse('track_event_reminder_email_view', args=[log_id, user_id])

    def test_hit_records_viewed_at(self):
        self.assertIsNone(self.recipient.viewed_at)

        response = self.client.get(self._pixel_url(self.log.id, self.member.user_id))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/gif')
        self.recipient.refresh_from_db()
        self.assertIsNotNone(self.recipient.viewed_at)

    def test_no_login_required(self):
        """The recipient's mail client has no Parliament session at all."""
        response = self.client.get(self._pixel_url(self.log.id, self.member.user_id))
        self.assertEqual(response.status_code, 200)

    def test_second_hit_keeps_first_timestamp(self):
        """A mail client can refetch the pixel — keep the FIRST open time."""
        self.client.get(self._pixel_url(self.log.id, self.member.user_id))
        self.recipient.refresh_from_db()
        first_seen = self.recipient.viewed_at

        with mock.patch('django.utils.timezone.now', return_value=first_seen + timedelta(hours=1)):
            self.client.get(self._pixel_url(self.log.id, self.member.user_id))

        self.recipient.refresh_from_db()
        self.assertEqual(self.recipient.viewed_at, first_seen)

    def test_unknown_log_id_does_not_error(self):
        response = self.client.get(self._pixel_url(999999, self.member.user_id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'image/gif')
        self.recipient.refresh_from_db()
        self.assertIsNone(self.recipient.viewed_at)

    def test_unknown_user_id_does_not_error(self):
        response = self.client.get(self._pixel_url(self.log.id, 'no-such-user'))
        self.assertEqual(response.status_code, 200)
        self.recipient.refresh_from_db()
        self.assertIsNone(self.recipient.viewed_at)

    def test_response_body_is_a_valid_1x1_gif(self):
        response = self.client.get(self._pixel_url(self.log.id, self.member.user_id))
        # GIF87a/GIF89a magic bytes
        self.assertIn(response.content[:6], (b'GIF87a', b'GIF89a'))


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class TrackingUrlInEmailTests(TestCase):
    """The tracking pixel should only appear when that slot's email option is on."""

    def setUp(self):
        self.officer = make_officer()
        mail.outbox = []

    def test_html_email_contains_tracking_pixel(self):
        member = make_member('px2', email='px2@example.com')
        event = make_event(self.officer, reminder_1_email_enabled=True)

        send_event_reminder_pushes()

        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        # EmailMultiAlternatives: the HTML body is the attached alternative,
        # not `.body` (which is the plain-text strip_tags() fallback).
        html_bodies = [content for content, mimetype in sent.alternatives if mimetype == 'text/html']
        self.assertEqual(len(html_bodies), 1)
        html_body = html_bodies[0]

        recipient = EventReminderRecipient.objects.get(user=member)
        expected_path = reverse(
            'track_event_reminder_email_view',
            args=[recipient.reminder_log_id, member.user_id],
        )
        self.assertIn(expected_path, html_body)
        # The plain-text alternative (used for the .body / strip_tags fallback)
        # should NOT carry the raw <img> tag — strip_tags removes markup, and
        # a tracking pixel has no place in a plain-text reading of the email.
        self.assertNotIn('<img', sent.body)

    def test_no_tracking_pixel_when_slot_email_option_is_off(self):
        """No email is sent at all when the slot's email option is off — so
        there is nothing to check the body of, but confirm no stray send happens
        and no email_status is recorded as if a pixel had been embedded."""
        member = make_member('px3', email='px3@example.com')
        make_event(self.officer, reminder_1_email_enabled=False)

        send_event_reminder_pushes()

        self.assertEqual(len(mail.outbox), 0)
        recipient = EventReminderRecipient.objects.get(user=member)
        self.assertEqual(recipient.email_status, '')


class AdminV2ViewedNotViewedSplitTests(TestCase):
    """
    admin_v2.event_reminder_log_detail splits dispatched emails into
    emails_viewed / emails_not_viewed by whether the tracking pixel fired —
    the "who has and hasn't viewed" question, same as the announcement
    email stats page.
    """

    def _admin_v2_client(self, user):
        from src.view import admin_v2

        patcher = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {user.pk})
        patcher.start()
        self.addCleanup(patcher.stop)

        client = Client()
        client.force_login(user)
        session = client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = tz.now().isoformat()
        session.save()
        return client

    def setUp(self):
        self.admin = make_admin()
        officer = make_officer('log-officer')
        event = make_event(officer)
        self.log = EventReminderLog.objects.create(
            event=event, reminder_slot=1, emails_dispatched=3,
        )

        self.viewed = EventReminderRecipient.objects.create(
            reminder_log=self.log, user=make_member('v1', email='v1@example.com'),
            user_name='Viewer One', user_member_type='Member',
            status='dispatched', email_status='dispatched', viewed_at=tz.now(),
        )
        self.not_viewed = EventReminderRecipient.objects.create(
            reminder_log=self.log, user=make_member('v2', email='v2@example.com'),
            user_name='Viewer Two', user_member_type='Member',
            status='dispatched', email_status='dispatched', viewed_at=None,
        )
        # A skipped-no-email row shouldn't appear in either bucket — it was
        # never dispatched, so there was nothing to view.
        self.skipped = EventReminderRecipient.objects.create(
            reminder_log=self.log, user=make_member('v3'),
            user_name='Viewer Three', user_member_type='Member',
            status='dispatched', email_status='skipped_no_email', viewed_at=None,
        )

    def test_detail_page_splits_viewed_and_not_viewed(self):
        client = self._admin_v2_client(self.admin)
        response = client.get(reverse('admin_v2_event_reminder_log_detail', args=[self.log.id]))

        self.assertEqual(response.status_code, 200)
        emails_viewed = list(response.context['emails_viewed'])
        emails_not_viewed = list(response.context['emails_not_viewed'])

        self.assertEqual(emails_viewed, [self.viewed])
        self.assertEqual(emails_not_viewed, [self.not_viewed])
        # The skipped (never-dispatched) row belongs in neither bucket.
        self.assertNotIn(self.skipped, emails_viewed)
        self.assertNotIn(self.skipped, emails_not_viewed)

    def test_detail_page_renders_viewer_names(self):
        client = self._admin_v2_client(self.admin)
        response = client.get(reverse('admin_v2_event_reminder_log_detail', args=[self.log.id]))
        body = response.content.decode()

        self.assertIn('Viewer One', body)
        self.assertIn('Viewer Two', body)

    def test_list_page_shows_viewed_count(self):
        client = self._admin_v2_client(self.admin)
        response = client.get(reverse('admin_v2_event_reminder_logs'))

        self.assertEqual(response.status_code, 200)
        log = next(l for l in response.context['logs'] if l.pk == self.log.pk)
        self.assertEqual(log.emails_viewed, 1)

    def test_email_enabled_false_when_no_email_status_at_all(self):
        """A push-only slot (no recipient has an email_status at all) should
        not render the viewed/not-viewed section — nothing to show."""
        push_only_log = EventReminderLog.objects.create(
            event=self.log.event, reminder_slot=2,
        )
        EventReminderRecipient.objects.create(
            reminder_log=push_only_log, user=make_member('v4', email='v4@example.com'),
            user_name='Push Only', user_member_type='Member',
            status='dispatched', email_status='',
        )
        client = self._admin_v2_client(self.admin)
        response = client.get(reverse('admin_v2_event_reminder_log_detail', args=[push_only_log.id]))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['email_enabled'])
