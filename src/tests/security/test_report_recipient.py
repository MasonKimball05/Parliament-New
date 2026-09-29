"""
v3.35.3 (09-29-26) — bug reports and support tickets go to BUG_REPORT_EMAIL,
else SECURITY_ALERT_EMAIL, never to an address hard-coded in the (public) repo.

The setting was read with getattr(..., '<personal address>') but never defined
in settings.py, so every deployment used the hard-coded fallback.

Run with: python manage.py test src.tests.security.test_report_recipient
"""
import pathlib

from django.conf import settings
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from src.tests.security.test_feedback_requests import _member


@override_settings(
    REAL_EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
    EMAIL_HOST_USER='test@example.com',
    SECURITY_ALERT_EMAIL='security@example.org',
)
class SupportTicketRecipientTests(TestCase):
    def setUp(self):
        self.user = _member('FB-R1', 'Riley')
        self.client.login(username=self.user.username, password='feedback-test-pass-12345!')

    def _ticket(self):
        mail.outbox = []
        self.client.post(reverse('feedback_request'), {
            'request_type': 'support_ticket', 'title': 'Help', 'description': 'Details'})
        self.assertEqual(len(mail.outbox), 1)
        return mail.outbox[0].to

    @override_settings(BUG_REPORT_EMAIL='bugs@example.org')
    def test_goes_to_bug_report_email(self):
        self.assertEqual(self._ticket(), ['bugs@example.org'])

    @override_settings(BUG_REPORT_EMAIL='')
    def test_falls_back_to_security_alert_email(self):
        self.assertEqual(self._ticket(), ['security@example.org'])


class NoHardcodedRecipientTests(TestCase):
    def test_no_personal_address_in_the_views(self):
        for rel in ('src/view/bug_report.py', 'src/view/feedback.py'):
            text = (pathlib.Path(settings.BASE_DIR) / rel).read_text(encoding='utf-8')
            with self.subTest(file=rel):
                self.assertNotIn('icloud.com', text)
                self.assertNotIn('gmail.com', text)
