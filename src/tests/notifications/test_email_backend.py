"""
Tests for the FeatureFlag-gated email backend (src/email_backend.py).

⚠️ Django's test runner forces `settings.EMAIL_BACKEND` to locmem in
`setup_test_environment()`, unconditionally, before any test runs — see
`django.test.utils.setup_test_environment`. That means `django.core.mail.
send_mail()` never goes through `FeatureFlagGatedEmailBackend` during the
suite, regardless of what settings.py says. These tests instantiate the
backend directly instead of going through `send_mail`, which is the only way
to exercise it under `manage.py test` at all.
"""
from unittest.mock import patch

from django.core import mail
from django.core.mail import EmailMessage
from django.test import TestCase, override_settings

from src.email_backend import FeatureFlagGatedEmailBackend, _bypasses_flag
from src.models_feature_flags import FeatureFlag


def _msg(subject='Test', to=('member@example.com',)):
    return EmailMessage(subject=subject, body='body', from_email='noreply@example.com', to=list(to))


@override_settings(REAL_EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class FeatureFlagGatedEmailBackendTests(TestCase):
    def setUp(self):
        mail.outbox = []

    def _set_flag(self, enabled):
        FeatureFlag.objects.update_or_create(
            name='email_notifications',
            defaults={'display_name': 'Email Notifications', 'is_enabled': enabled},
        )

    def test_sends_when_flag_enabled(self):
        self._set_flag(True)
        backend = FeatureFlagGatedEmailBackend()
        sent = backend.send_messages([_msg()])
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_drops_non_security_mail_when_flag_disabled(self):
        self._set_flag(False)
        backend = FeatureFlagGatedEmailBackend()
        sent = backend.send_messages([_msg(subject='New Announcement: Chapter Retreat')])
        # The caller still gets a truthy count back — nothing should raise or
        # look like a delivery failure — but nothing actually left the app.
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_missing_flag_row_fails_open(self):
        # No row at all — FeatureFlag.is_feature_enabled's own fail-open
        # default applies (missing name, not in DISABLED_BY_DEFAULT -> True).
        FeatureFlag.objects.filter(name='email_notifications').delete()
        backend = FeatureFlagGatedEmailBackend()
        sent = backend.send_messages([_msg()])
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_flag_lookup_exception_fails_open(self):
        self._set_flag(False)
        backend = FeatureFlagGatedEmailBackend()
        with patch(
            'src.models_feature_flags.FeatureFlag.is_feature_enabled',
            side_effect=RuntimeError('cache down'),
        ):
            sent = backend.send_messages([_msg()])
        # Flag says off, but the LOOKUP itself blew up — send anyway.
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_empty_message_list_is_a_noop(self):
        self._set_flag(True)
        backend = FeatureFlagGatedEmailBackend()
        self.assertEqual(backend.send_messages([]), 0)
        self.assertEqual(backend.send_messages(None), 0)

    def test_security_alert_bypasses_disabled_flag(self):
        self._set_flag(False)
        backend = FeatureFlagGatedEmailBackend()
        sent = backend.send_messages([_msg(subject='[SECURITY ALERT] New device login')])
        self.assertEqual(sent, 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_2fa_recovery_bypasses_disabled_flag(self):
        self._set_flag(False)
        backend = FeatureFlagGatedEmailBackend()
        sent = backend.send_messages([_msg(subject='Parliament — 2FA Re-enrollment Link')])
        self.assertEqual(len(mail.outbox), 1)

    def test_mixed_batch_sends_only_the_bypassing_message(self):
        self._set_flag(False)
        backend = FeatureFlagGatedEmailBackend()
        sent = backend.send_messages([
            _msg(subject='New Announcement: Chapter Retreat'),
            _msg(subject='[WATCH FLAG] Password changed'),
        ])
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('WATCH FLAG', mail.outbox[0].subject)
        # The dropped message still counts in the returned total — see the
        # backend's own comment on why.
        self.assertEqual(sent, 2)

    def test_bypasses_flag_helper_is_exact_not_prefix_only(self):
        self.assertTrue(_bypasses_flag(_msg(subject='[SECURITY ALERT] x')))
        self.assertTrue(_bypasses_flag(_msg(subject='Confirm your new email address')))
        self.assertFalse(_bypasses_flag(_msg(subject='New Announcement: x')))
        self.assertFalse(_bypasses_flag(_msg(subject='Kai Report Update: x')))


class EmailBackendSettingsWiringTests(TestCase):
    """
    The wrapper is only as good as the two consumers who used to read
    settings.EMAIL_BACKEND expecting the real transport name.
    """

    def test_check_env_reads_real_backend_not_the_wrapper_class(self):
        import io
        from django.core.management.base import OutputWrapper
        from src.management.commands.check_env import Command

        with override_settings(REAL_EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend'):
            cmd = Command()
            buf = io.StringIO()
            cmd.stdout = OutputWrapper(buf)
            cmd.check_email()
            printed = buf.getvalue().lower()
            self.assertIn('console', printed)
            self.assertNotIn('featureflaggated', printed)

    def test_settings_wires_the_wrapper_as_email_backend(self):
        # ⚠️ NOT `django.conf.settings.EMAIL_BACKEND` — Django's test runner
        # force-overrides that to locmem in setup_test_environment(), for
        # every test, unconditionally. Reading the live settings object here
        # would assert the test runner's override, not this project's
        # settings.py. Read the source instead, the same technique
        # `test_production_security_settings.py` uses for the same reason.
        import Parliament.settings as settings_module
        import inspect
        source = inspect.getsource(settings_module)
        self.assertIn(
            "EMAIL_BACKEND = 'src.email_backend.FeatureFlagGatedEmailBackend'",
            source,
        )
        self.assertIn('REAL_EMAIL_BACKEND = os.getenv', source)
