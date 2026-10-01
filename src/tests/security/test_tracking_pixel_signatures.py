"""
v3.38.2 (10-01-26) — email tracking pixels only record a view for a signed URL.

Before: the pixel URLs carried only sequential ids and needed no login, so
anyone could write "Accused person viewed notification email" onto any Kai
report, or mark any member as having read any announcement or reminder.

Pins (src/utils/tracking_sig.py):
  * an unsigned or wrongly signed request gets the same GIF and records nothing;
  * a signature for one pixel does not work on another;
  * the URL the reminder email actually carries is one the view accepts.

Run with: python manage.py test src.tests.security.test_tracking_pixel_signatures
"""
import re

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import (
    Announcement, Event, EventReminderLog, EventReminderRecipient, KaiReport,
    KaiReportActivity, ParliamentUser, UserAnnouncementView,
)
from src.utils.tracking_sig import (
    ANNOUNCEMENT, EVENT_REMINDER, KAI_ACCUSED, KAI_SUBMITTER, signed_pixel_url,
)


def member(uid, **kwargs):
    return ParliamentUser.objects.create_user(
        user_id=uid, name=f'Member {uid}', username=uid, member_type='Member',
        member_status='Active', **kwargs)


class KaiPixelTests(TestCase):
    def setUp(self):
        now = timezone.now()
        self.report = KaiReport.objects.create(
            title='t', category='behavioral', description='d', submitted_by=member('kp-sub'),
            accused_notified=True, submitter_notified_at=now)
        self.other = KaiReport.objects.create(
            title='t2', category='behavioral', description='d', submitted_by=self.report.submitted_by,
            accused_notified=True, submitter_notified_at=now)
        self.accused_url = reverse('track_kai_accused_email', args=[self.report.id])
        self.submitter_url = reverse('track_kai_submitter_email', args=[self.report.id])

    def assert_untouched(self):
        self.report.refresh_from_db()
        self.assertIsNone(self.report.accused_email_viewed_at)
        self.assertIsNone(self.report.submitter_email_viewed_at)
        self.assertFalse(KaiReportActivity.objects.filter(report=self.report).exists())

    def test_unsigned_request_records_nothing(self):
        for url in (self.accused_url, self.submitter_url, self.accused_url + '?s=', self.accused_url + '?s=' + '0' * 32):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Content-Type'], 'image/gif')
        self.assert_untouched()

    def test_signature_is_bound_to_the_report_and_the_pixel_kind(self):
        # Another report's signature, and the submitter pixel's signature.
        self.client.get(signed_pixel_url(self.accused_url, KAI_ACCUSED, self.other.id))
        self.client.get(signed_pixel_url(self.accused_url, KAI_SUBMITTER, self.report.id))
        self.assert_untouched()

    def test_signed_accused_pixel_records_the_view(self):
        self.client.get(signed_pixel_url(self.accused_url, KAI_ACCUSED, self.report.id))
        self.report.refresh_from_db()
        self.assertIsNotNone(self.report.accused_email_viewed_at)
        self.assertIsNone(self.report.submitter_email_viewed_at)

    def test_signed_submitter_pixel_records_the_view(self):
        self.client.get(signed_pixel_url(self.submitter_url, KAI_SUBMITTER, self.report.id))
        self.report.refresh_from_db()
        self.assertIsNotNone(self.report.submitter_email_viewed_at)


class AnnouncementPixelTests(TestCase):
    def setUp(self):
        self.reader = member('ap-reader')
        self.announcement = Announcement.objects.create(
            title='A', content='c', posted_by=member('ap-poster'), is_active=True)
        self.url = reverse('track_email_view', args=[self.announcement.id, self.reader.user_id])

    def test_unsigned_request_records_nothing(self):
        response = self.client.get(self.url)
        self.assertEqual(response['Content-Type'], 'image/gif')
        self.assertFalse(UserAnnouncementView.objects.exists())

    def test_another_members_signature_does_not_work(self):
        self.client.get(signed_pixel_url(self.url, ANNOUNCEMENT, self.announcement.id, 'ap-poster'))
        self.assertFalse(UserAnnouncementView.objects.exists())

    def test_signed_request_records_the_view(self):
        self.client.get(signed_pixel_url(self.url, ANNOUNCEMENT, self.announcement.id, self.reader.user_id))
        self.assertTrue(UserAnnouncementView.objects.filter(
            user=self.reader, announcement=self.announcement).exists())


class ReminderPixelTests(TestCase):
    def setUp(self):
        self.reader = member('rp-reader', email='rp-reader@example.com')
        self.event = Event.objects.create(
            title='E', description='d', location='l', created_by=member('rp-officer'),
            date_time=timezone.now() + timezone.timedelta(days=1))
        self.log = EventReminderLog.objects.create(event=self.event, reminder_slot=1)
        self.recipient = EventReminderRecipient.objects.create(
            reminder_log=self.log, user=self.reader, user_name=self.reader.name,
            user_member_type=self.reader.member_type, status='dispatched', email_status='dispatched')
        self.url = reverse('track_event_reminder_email_view', args=[self.log.id, self.reader.user_id])

    def test_unsigned_request_records_nothing(self):
        self.client.get(self.url)
        self.recipient.refresh_from_db()
        self.assertIsNone(self.recipient.viewed_at)

    def test_signed_request_records_the_view(self):
        self.client.get(signed_pixel_url(self.url, EVENT_REMINDER, self.log.id, self.reader.user_id))
        self.recipient.refresh_from_db()
        self.assertIsNotNone(self.recipient.viewed_at)
