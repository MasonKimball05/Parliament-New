"""
v3.29.35 — `manage.py dump_db` used to print every field of every row,
unfiltered, straight from the ORM: Kai case content, Slating interview
notes, API tokens, WebAuthn credentials, password hashes. Raised by Mason.
See `src/dump_redaction.py` for the reasoning; this covers the redaction
function directly (no DB writes needed — `redacted_field_value` only reads
attributes off an object, so these build unsaved model instances in memory)
plus one end-to-end smoke test that the command itself uses it.
"""
from django.test import TestCase

from src.dump_redaction import redacted_field_value, _is_confidential_model
from src.models import (
    KaiReport, KaiReportActivity, SlatingInterview, APIToken,
    PushSubscription, WebAuthnCredential, ParliamentUser, Committee,
)


def _field(model, name):
    return model._meta.get_field(name)


class ConfidentialModelDetectionTests(TestCase):
    def test_kai_report_is_confidential(self):
        self.assertTrue(_is_confidential_model(KaiReport))

    def test_kai_report_activity_is_confidential(self):
        self.assertTrue(_is_confidential_model(KaiReportActivity))

    def test_slating_interview_is_confidential(self):
        self.assertTrue(_is_confidential_model(SlatingInterview))

    def test_ordinary_model_is_not_confidential(self):
        self.assertFalse(_is_confidential_model(ParliamentUser))
        self.assertFalse(_is_confidential_model(Committee))


class KaiFieldRedactionTests(TestCase):
    def setUp(self):
        self.reporter = ParliamentUser.objects.create(
            user_id='DR-001', name='Reporter', username='reporter',
        )
        # Unsaved — redacted_field_value only reads attributes, no DB round trip needed.
        self.report = KaiReport(
            title='A specific, identifying case title',
            description='Confidential allegation content naming people.',
            chair_notes='Chair-only deliberation notes.',
            submitted_by=self.reporter,
        )

    def test_title_is_redacted(self):
        result = redacted_field_value(self.report, _field(KaiReport, 'title'))
        self.assertTrue(result.startswith('[REDACTED sha256:'))
        self.assertNotIn('identifying case title', result)

    def test_description_is_redacted(self):
        result = redacted_field_value(self.report, _field(KaiReport, 'description'))
        self.assertTrue(result.startswith('[REDACTED sha256:'))
        self.assertNotIn('Confidential allegation', result)

    def test_chair_notes_is_redacted(self):
        result = redacted_field_value(self.report, _field(KaiReport, 'chair_notes'))
        self.assertTrue(result.startswith('[REDACTED sha256:'))

    def test_pk_style_fields_pass_through(self):
        # id/created_at etc. are on the safe list — a redacted dump should
        # still show row identity and timing, just not content.
        self.report.pk = 7
        result = redacted_field_value(self.report, _field(KaiReport, 'id'))
        self.assertEqual(result, 7)

    def test_blank_value_is_not_redacted(self):
        self.report.chair_notes = ''
        result = redacted_field_value(self.report, _field(KaiReport, 'chair_notes'))
        self.assertEqual(result, '')

    def test_digest_is_stable_for_the_same_value(self):
        """The whole point of hashing rather than dropping: two dumps of an
        unchanged row should produce the same redacted marker."""
        first = redacted_field_value(self.report, _field(KaiReport, 'title'))
        second = redacted_field_value(self.report, _field(KaiReport, 'title'))
        self.assertEqual(first, second)

    def test_digest_changes_if_the_value_changes(self):
        before = redacted_field_value(self.report, _field(KaiReport, 'title'))
        self.report.title = 'A different title entirely'
        after = redacted_field_value(self.report, _field(KaiReport, 'title'))
        self.assertNotEqual(before, after)


class SlatingFieldRedactionTests(TestCase):
    def test_interview_notes_are_redacted(self):
        interview = SlatingInterview(
            notes='Specific concerns about this candidate.',
            strengths='Named strengths.',
            concerns='Named concerns.',
        )
        for name in ('notes', 'strengths', 'concerns'):
            result = redacted_field_value(interview, _field(SlatingInterview, name))
            self.assertTrue(result.startswith('[REDACTED sha256:'))


class NamedSecretFieldRedactionTests(TestCase):
    def test_api_token_key_is_redacted(self):
        user = ParliamentUser.objects.create(user_id='SEC-001', name='U', username='u_sec001')
        token = APIToken(user=user, key='super-secret-token-value', name='Test Token')
        result = redacted_field_value(token, _field(APIToken, 'key'))
        self.assertTrue(result.startswith('[REDACTED sha256:'))
        self.assertNotIn('super-secret-token-value', result)

    def test_api_token_name_is_not_redacted(self):
        """Control: only `key` is a secret — the token's own display name isn't."""
        user = ParliamentUser.objects.create(user_id='SEC-002', name='U', username='u_sec002')
        token = APIToken(user=user, key='x', name='My Laptop Token')
        result = redacted_field_value(token, _field(APIToken, 'name'))
        self.assertEqual(result, 'My Laptop Token')

    def test_push_subscription_secrets_are_redacted(self):
        user = ParliamentUser.objects.create(user_id='SEC-003', name='U', username='u_sec003')
        sub = PushSubscription(user=user, endpoint='https://push.example/x', p256dh='pubkeyvalue', auth='authsecret')
        self.assertTrue(redacted_field_value(sub, _field(PushSubscription, 'p256dh')).startswith('[REDACTED'))
        self.assertTrue(redacted_field_value(sub, _field(PushSubscription, 'auth')).startswith('[REDACTED'))
        # The endpoint URL is not on the secret list — left as-is.
        self.assertEqual(
            redacted_field_value(sub, _field(PushSubscription, 'endpoint')),
            'https://push.example/x',
        )

    def test_webauthn_credential_is_redacted(self):
        user = ParliamentUser.objects.create(user_id='SEC-004', name='U', username='u_sec004')
        cred = WebAuthnCredential(user=user, credential_id=b'\x01\x02\x03', public_key=b'\x04\x05\x06')
        self.assertTrue(redacted_field_value(cred, _field(WebAuthnCredential, 'credential_id')).startswith('[REDACTED'))
        self.assertTrue(redacted_field_value(cred, _field(WebAuthnCredential, 'public_key')).startswith('[REDACTED'))

    def test_password_field_is_redacted_on_any_model(self):
        user = ParliamentUser(user_id='SEC-005', name='U', username='u_sec005')
        user.set_password('some-real-password-12345!')
        result = redacted_field_value(user, _field(ParliamentUser, 'password'))
        self.assertTrue(result.startswith('[REDACTED sha256:'))
        self.assertNotIn('pbkdf2', result)


class OrdinaryFieldPassThroughTests(TestCase):
    def test_non_confidential_model_field_is_unredacted(self):
        """Control: this whole mechanism must not redact everything —
        only the specific categories it targets."""
        committee = Committee(name='Social', code='SOC')
        result = redacted_field_value(committee, _field(Committee, 'name'))
        self.assertEqual(result, 'Social')


class DumpDbCommandIntegrationTests(TestCase):
    def test_dump_db_does_not_print_kai_content(self):
        from io import StringIO
        from django.core.management import call_command

        reporter = ParliamentUser.objects.create(user_id='DUMP-001', name='Reporter', username='dumprep')
        KaiReport.objects.create(
            title='UNIQUE-MARKER-TITLE-ABC123',
            description='UNIQUE-MARKER-DESCRIPTION-XYZ789',
            submitted_by=reporter,
        )

        out = StringIO()
        call_command('dump_db', stdout=out)
        output = out.getvalue()

        self.assertNotIn('UNIQUE-MARKER-TITLE-ABC123', output)
        self.assertNotIn('UNIQUE-MARKER-DESCRIPTION-XYZ789', output)
        self.assertIn('KaiReport', output)  # the model section itself still prints
        self.assertIn('[REDACTED sha256:', output)
