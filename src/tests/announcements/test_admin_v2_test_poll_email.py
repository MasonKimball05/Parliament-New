"""
Mason: "Can you update the send test email in the admin-v2 page to allow
me to also include a test poll to make sure that embedded correctly too?"

`send_test_announcement_email` / `preview_test_email` both render the real
`emails/announcement_notification.html` against a `MockAnnouncement` (id=0,
not a real row) — so once the template gained poll rendering, the natural
way to test it is a matching mock poll rather than a real `AnnouncementPoll`
tied to a nonexistent announcement.
"""
from unittest import mock

from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import ParliamentUser
from src.view import admin_v2


def make_admin(uid='900'):
    return ParliamentUser.objects.create_user(
        user_id=uid, name='Admin Aardvark', username=f'admin_{uid}',
        member_type='Officer', password='testpass123', email='admin@example.com',
    )


class AdminV2TestPollEmailTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = make_admin()

        # require_admin_v2_auth: allowed id + session flags (same pattern as
        # src/tests/activity/test_page_visits_filter.py).
        self._allowed = mock.patch.object(admin_v2, 'ALLOWED_USER_IDS', {'900'})
        self._allowed.start()
        self.addCleanup(self._allowed.stop)
        self.client.force_login(self.admin)
        session = self.client.session
        session['admin_v2_authenticated'] = True
        session['admin_v2_auth_time'] = timezone.now().isoformat()
        session.save()

    # -- send_test_announcement_email ---------------------------------------

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_send_without_checkbox_has_no_poll_content(self):
        mail.outbox = []
        self.client.post(reverse('send_test_announcement_email'))
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertNotIn('class="poll-info"', html)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_send_with_checkbox_includes_test_poll(self):
        mail.outbox = []
        self.client.post(reverse('send_test_announcement_email'), {
            'include_test_poll': 'on',
        })
        self.assertEqual(len(mail.outbox), 1)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn('class="poll-info"', html)
        self.assertIn('Test Poll', html)
        self.assertIn('Monday', html)
        self.assertIn('with test poll', mail.outbox[0].subject)

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_send_with_test_poll_text_question_has_no_option_list(self):
        """The mock poll's second question is text-type — must not render an
        options list under it, same guard as the real-poll email test."""
        mail.outbox = []
        self.client.post(reverse('send_test_announcement_email'), {
            'include_test_poll': 'on',
        })
        html = mail.outbox[0].alternatives[0][0]
        idx = html.index('Any scheduling conflicts')
        tail = html[idx:idx + 200]
        self.assertNotIn('<ul class="poll-options">', tail)

    # -- preview_test_email ---------------------------------------------------

    def test_preview_without_param_has_no_poll_content(self):
        response = self.client.get(reverse('preview_test_email'))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b'class="poll-info"', response.content)

    def test_preview_with_param_includes_test_poll(self):
        response = self.client.get(reverse('preview_test_email'), {'include_test_poll': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="poll-info"', response.content)
        self.assertIn(b'Test Poll', response.content)
