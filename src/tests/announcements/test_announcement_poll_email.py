"""
Mason: "Announcement emails with polls don't show the poll just the
announcement, can you add the poll and a link to it to that?"

`emails/announcement_notification.html` never rendered anything about an
attached `AnnouncementPoll` — `send_announcement_notification` (the task
path, triggered on publish) and `warmup_announcement_email` (the
manual-send path behind the officer's confirm-email page) both passed the
template `announcement`/`site_url`/`tracking_url`/`user` and nothing else,
so a poll's existence, its questions, and the link to answer it were
invisible in the email even though the in-app announcements list has
always shown a "Take Poll" button right next to the same post.

Both real send paths are covered here, since both independently render
the same template with their own context dict — fixing one and not the
other would have left the bug alive on whichever path Mason doesn't
happen to test.
"""
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from src.models import (
    Announcement, AnnouncementPoll, AnnouncementPollQuestion,
    AnnouncementPollOption, ParliamentUser,
)


def make_officer(uid='poll-email-officer'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Poll Officer', username=uid, member_type='Officer',
        password='testpass123', email='officer@example.com', member_status='Active',
    )


def make_member(uid='poll-email-member'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Poll Member', username=uid, member_type='Member',
        password='testpass123', email='member@example.com', member_status='Active',
    )


def make_announcement_with_poll(officer, is_open=True):
    announcement = Announcement.objects.create(
        title='Chapter Retreat Planning', content='We need your input.',
        posted_by=officer,
    )
    poll = AnnouncementPoll.objects.create(
        announcement=announcement, created_by=officer,
        title='Retreat Location Preference',
        description='Pick your favorite option below.',
        is_open=is_open,
    )
    q1 = AnnouncementPollQuestion.objects.create(
        poll=poll, text='Where should we go?', question_type='single', order=0,
    )
    AnnouncementPollOption.objects.create(question=q1, text='Lake house', order=0)
    AnnouncementPollOption.objects.create(question=q1, text='Mountain cabin', order=1)
    AnnouncementPollQuestion.objects.create(
        poll=poll, text='Any other thoughts?', question_type='text', order=1,
    )
    return announcement, poll


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class SendAnnouncementNotificationPollTests(TestCase):
    """The publish-triggered send path: src.notifications.send_announcement_notification."""

    def setUp(self):
        self.officer = make_officer()
        self.member = make_member()
        mail.outbox = []

    def test_email_with_poll_shows_title_questions_options_and_link(self):
        from src.notifications import send_announcement_notification
        announcement, poll = make_announcement_with_poll(self.officer)

        send_announcement_notification(announcement, initiated_by=self.officer)

        # Both the officer and the member are Active with a valid email and
        # no announcement-visibility restriction, so both receive it.
        self.assertEqual(len(mail.outbox), 2)
        html = mail.outbox[0].alternatives[0][0]

        self.assertIn('Retreat Location Preference', html)
        self.assertIn('Pick your favorite option below.', html)
        self.assertIn('Where should we go?', html)
        self.assertIn('Lake house', html)
        self.assertIn('Mountain cabin', html)
        self.assertIn('Any other thoughts?', html)
        self.assertIn(f'/announcements/{announcement.id}/poll/', html)
        self.assertIn('Take the Poll', html)

    def test_text_question_has_no_option_list(self):
        """A text-response question has no `options` — must not render an
        empty bulleted list under it."""
        from src.notifications import send_announcement_notification
        announcement, poll = make_announcement_with_poll(self.officer)

        send_announcement_notification(announcement, initiated_by=self.officer)

        html = mail.outbox[0].alternatives[0][0]
        # The text question's own line renders, but is not followed by an
        # option bullet for content that doesn't exist.
        idx = html.index('Any other thoughts?')
        tail = html[idx:idx + 200]
        self.assertNotIn('<ul class="poll-options">', tail)

    def test_closed_poll_says_view_poll_not_take_the_poll(self):
        from src.notifications import send_announcement_notification
        announcement, poll = make_announcement_with_poll(self.officer, is_open=False)

        send_announcement_notification(announcement, initiated_by=self.officer)

        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('View Poll', html)
        self.assertNotIn('Take the Poll', html)

    def test_announcement_without_poll_renders_no_poll_section(self):
        announcement = Announcement.objects.create(
            title='Plain announcement', content='No poll here.', posted_by=self.officer,
        )
        from src.notifications import send_announcement_notification
        send_announcement_notification(announcement, initiated_by=self.officer)

        html = mail.outbox[0].alternatives[0][0]
        # The CSS block always defines `.poll-info`/`.poll-btn` (static markup
        # in <head>, matches the bare class name too) — check for the actual
        # rendered HTML attribute instead of the CSS selector. (Not asserting
        # on a bare '/poll/' substring here — that trips
        # `test_hardcoded_urls`'s path-resolves scanner, which flags any
        # literal-looking site path in source that reverse() can't resolve.)
        self.assertNotIn('class="poll-info"', html)
        self.assertNotIn('class="poll-btn"', html)


class WarmupAnnouncementEmailPollTests(TestCase):
    """
    The manual-send path: `warmup_announcement_email` pre-renders each
    recipient's email into cache; `send_announcement_emails` reads that
    cache and sends it verbatim. Testing the warmup render directly is
    what actually proves the officer-triggered "Send Emails" button (not
    just the auto-send-on-publish task) also carries the poll.
    """

    def setUp(self):
        self.officer = make_officer('poll-email-officer-2')
        self.member = make_member('poll-email-member-2')
        cache.clear()

    def test_warmup_render_includes_poll_content_and_link(self):
        announcement, poll = make_announcement_with_poll(self.officer)
        self.client.login(username=self.officer.username, password='testpass123')

        response = self.client.post(
            reverse('warmup_announcement_email', args=[announcement.id]),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

        cache_key = f'email_warmup_{announcement.id}'
        warmup_data = cache.get(cache_key)
        self.assertIsNotNone(warmup_data)

        rendered = warmup_data['rendered_emails'][self.member.user_id]['html']
        self.assertIn('Retreat Location Preference', rendered)
        self.assertIn('Lake house', rendered)
        self.assertIn(f'/announcements/{announcement.id}/poll/', rendered)
