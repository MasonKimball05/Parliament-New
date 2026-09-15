"""
v3.30.2 — KnownWeakPasswordValidator (src/validators.py). A local,
network-independent backstop for "Password123!"-style passwords that pass
CustomPasswordValidator's complexity rule and may not appear in Django's
bundled CommonPasswordValidator list — see src/validators.py for the full
rationale.

These tests exercise the validator directly AND through the real
AUTH_PASSWORD_VALIDATORS pipeline (django.contrib.auth.password_validation.
validate_password), because a validator that behaves correctly in isolation
but was never actually wired into settings.py would pass the first kind of
test and fail every real user, silently.
"""
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from src.models import ParliamentUser
from src.validators import KnownWeakPasswordValidator


class KnownWeakPasswordValidatorDirectTests(TestCase):
    def setUp(self):
        self.validator = KnownWeakPasswordValidator()

    def test_rejects_the_exact_reported_password(self):
        with self.assertRaises(ValidationError):
            self.validator.validate('Password123!')

    def test_rejects_case_variants(self):
        for variant in ('password123!', 'PASSWORD123!', 'PaSsWoRd123!'):
            with self.assertRaises(ValidationError, msg=variant):
                self.validator.validate(variant)

    def test_rejects_other_known_weak_entries(self):
        for weak in ('Welcome123!', 'Qwerty123!', 'BetaThetaPi1!', 'Parliament123!'):
            with self.assertRaises(ValidationError, msg=weak):
                self.validator.validate(weak)

    def test_accepts_a_genuinely_unpredictable_password(self):
        # Should not raise.
        self.validator.validate('Xk9$mQz2#vLp7&wR')

    def test_get_help_text_mentions_the_example(self):
        self.assertIn('Password123!', self.validator.get_help_text())


class KnownWeakPasswordValidatorIsWiredIntoSettingsTests(TestCase):
    """
    Confirms the validator actually runs as part of Django's real password
    validation pipeline — not just that the class itself works. If someone
    ever removes the AUTH_PASSWORD_VALIDATORS entry, this is what catches it.
    """

    def test_validate_password_rejects_the_reported_password(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_password('Password123!')
        self.assertTrue(
            any('password_known_weak' in getattr(e, 'code', '') or
                'commonly used' in str(e) for e in ctx.exception.error_list),
            f"Expected KnownWeakPasswordValidator's message among the errors, got: {ctx.exception.messages}",
        )

    def test_validate_password_accepts_a_strong_password(self):
        # Should not raise (PwnedPasswordValidator fails open with no
        # network access in this sandbox, so this only exercises the local
        # validators — which is exactly what should pass a real password).
        validate_password('Xk9$mQz2#vLp7&wR')


def make_user(uid='pwval-user', **kwargs):
    defaults = dict(
        name='Weak Password Test User', username=uid,
        member_type='Member', member_status='Active',
    )
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password('Old-Genuinely-Strong-Pass-9!')
    user.save()
    return user


class WeakPasswordRejectedAtEveryUserChosenSetPathTests(TestCase):
    """
    The three paths where a user chooses their own new password:
    change_password, forced_password_change, and Django's built-in
    password-reset confirm view. All three must reject "Password123!" —
    proving the validator is wired in is only half the point; proving every
    real call site actually invokes Django's validation is the other half.
    """

    def setUp(self):
        self.client = Client()

    def test_change_password_view_rejects_it(self):
        user = make_user(uid='pwval-change')
        self.client.force_login(user)
        response = self.client.post(reverse('change_password'), {
            'old_password': 'Old-Genuinely-Strong-Pass-9!',
            'new_password1': 'Password123!',
            'new_password2': 'Password123!',
        })
        self.assertEqual(response.status_code, 200)  # re-renders with form errors
        user.refresh_from_db()
        self.assertTrue(
            user.check_password('Old-Genuinely-Strong-Pass-9!'),
            'Password was changed to a known-weak value.',
        )

    def test_forced_password_change_view_rejects_it(self):
        user = make_user(uid='pwval-forced', force_password_change=True)
        self.client.force_login(user)
        response = self.client.post(reverse('forced_password_change'), {
            'old_password': 'Old-Genuinely-Strong-Pass-9!',
            'new_password1': 'Password123!',
            'new_password2': 'Password123!',
        })
        self.assertEqual(response.status_code, 200)
        user.refresh_from_db()
        self.assertTrue(user.check_password('Old-Genuinely-Strong-Pass-9!'))

    def test_password_reset_confirm_rejects_it(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.encoding import force_bytes
        from django.utils.http import urlsafe_base64_encode

        user = make_user(uid='pwval-reset')
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)

        # First GET redirects to the "set-password" internal URL and stashes
        # the token in session (Django's own PasswordResetConfirmView flow).
        session = self.client.session
        response = self.client.get(
            reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token}),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)

        response = self.client.post(response.request['PATH_INFO'], {
            'new_password1': 'Password123!',
            'new_password2': 'Password123!',
        })
        self.assertEqual(response.status_code, 200)  # form error, not redirect-to-complete
        user.refresh_from_db()
        self.assertTrue(user.check_password('Old-Genuinely-Strong-Pass-9!'))
