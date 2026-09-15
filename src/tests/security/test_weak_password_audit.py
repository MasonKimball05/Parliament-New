"""
v3.30.2 — the weekly weak-password audit (src/weak_password_audit.py).

Covers: a user with a known-weak current password gets flagged; a user with
a strong password does not; the default-reset-format guess only applies to
has_default_password accounts; an already-flagged user is skipped (and
re-checked once resolved); inactive (is_active=False) accounts are not
checked; --dry-run creates no alerts; the management command runs end to end.
"""
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from src.models import LoginAlert, ParliamentUser
from src.weak_password_audit import audit_active_users_for_weak_passwords


def make_user(uid, name='Test User', password='Xk9$mQz2#vLp7&wR', **kwargs):
    defaults = dict(
        name=name, username=uid,
        member_type='Member', member_status='Active',
    )
    defaults.update(kwargs)
    user = ParliamentUser.objects.create(user_id=uid, **defaults)
    user.set_password(password)
    user.save()
    return user


class AuditFlagsKnownWeakPasswordsTests(TestCase):
    def test_a_common_weak_password_is_flagged(self):
        user = make_user('pwaudit-weak', password='Password123!')
        summary = audit_active_users_for_weak_passwords()
        self.assertEqual(summary['flagged'], 1)
        self.assertTrue(
            LoginAlert.objects.filter(user=user, alert_type='weak_password').exists()
        )

    def test_a_strong_password_is_not_flagged(self):
        make_user('pwaudit-strong', password='Xk9$mQz2#vLp7&wR')
        summary = audit_active_users_for_weak_passwords()
        self.assertEqual(summary['flagged'], 0)
        self.assertFalse(LoginAlert.objects.filter(alert_type='weak_password').exists())

    def test_the_alert_does_not_contain_the_literal_password(self):
        make_user('pwaudit-noleak', name='Noleak Person', password='Welcome123!')
        audit_active_users_for_weak_passwords()
        alert = LoginAlert.objects.get(alert_type='weak_password')
        self.assertNotIn('Welcome123!', alert.description)
        self.assertNotIn('Welcome123!', alert.title)

    def test_inactive_accounts_are_not_checked(self):
        make_user('pwaudit-inactive', password='Password123!', is_active=False)
        summary = audit_active_users_for_weak_passwords()
        self.assertEqual(summary['checked'], 0)
        self.assertEqual(summary['flagged'], 0)


class AuditChecksTheDefaultResetFormatOnlyWhenFlagged(TestCase):
    def test_a_has_default_password_account_on_the_predictable_reset_format_is_flagged(self):
        # Mirrors reset_all_passwords.py / reset_user_password.py's exact
        # format: first_initial + lastname + user_id, lowercase.
        # Guess format is first_initial + lastname + user_id, with user_id's
        # OWN casing preserved (neither reset_all_passwords.py nor
        # reset_user_password.py lowercase it) — hence the lowercase id here,
        # so the expected password is unambiguous: "m" + "kimball" + "reset77".
        user = make_user(
            'reset77', name='Mason Kimball', password='mkimballreset77',
            has_default_password=True,
        )
        summary = audit_active_users_for_weak_passwords()
        self.assertEqual(summary['flagged'], 1)
        self.assertTrue(LoginAlert.objects.filter(user=user).exists())

    def test_the_same_password_is_not_tried_against_an_account_without_the_flag(self):
        # Same literal password, but has_default_password=False — the guess
        # is user_id-specific, so this is really just confirming the global
        # candidate list alone doesn't happen to contain it (it doesn't) and
        # that the per-user reset-format guess is gated correctly.
        make_user(
            'reset78', name='Mason Kimball', password='mkimballreset78',
            has_default_password=False,
        )
        summary = audit_active_users_for_weak_passwords()
        self.assertEqual(summary['flagged'], 0)


class AuditSkipsAlreadyFlaggedUsersTests(TestCase):
    def test_a_user_with_an_open_alert_is_skipped_on_the_next_run(self):
        user = make_user('pwaudit-repeat', password='Password123!')
        first = audit_active_users_for_weak_passwords()
        self.assertEqual(first['flagged'], 1)

        second = audit_active_users_for_weak_passwords()
        self.assertEqual(second['flagged'], 0)
        self.assertEqual(second['already_flagged_skipped'], 1)
        self.assertEqual(
            LoginAlert.objects.filter(user=user, alert_type='weak_password').count(), 1,
            'A second run created a duplicate alert instead of skipping the already-flagged user.',
        )

    def test_resolving_the_alert_makes_the_user_eligible_again(self):
        user = make_user('pwaudit-resolved', password='Password123!')
        audit_active_users_for_weak_passwords()
        LoginAlert.objects.filter(user=user).update(status='resolved')

        summary = audit_active_users_for_weak_passwords()
        self.assertEqual(summary['flagged'], 1)
        self.assertEqual(
            LoginAlert.objects.filter(user=user, alert_type='weak_password').count(), 2,
            'Resolving the old alert should allow a fresh one on the next run.',
        )


class AuditDryRunTests(TestCase):
    def test_dry_run_reports_but_creates_no_alerts(self):
        make_user('pwaudit-dryrun', password='Password123!')
        summary = audit_active_users_for_weak_passwords(dry_run=True)
        self.assertEqual(summary['flagged'], 1)
        self.assertFalse(LoginAlert.objects.exists())


class CheckWeakPasswordsCommandTests(TestCase):
    def test_the_command_runs_end_to_end_and_flags_a_weak_password(self):
        make_user('pwaudit-cmd', password='Password123!')
        out = StringIO()
        call_command('check_weak_passwords', stdout=out)
        self.assertIn('Flagged: 1', out.getvalue())
        self.assertTrue(LoginAlert.objects.filter(alert_type='weak_password').exists())

    def test_the_command_dry_run_flag_creates_no_alerts(self):
        make_user('pwaudit-cmd-dry', password='Password123!')
        out = StringIO()
        call_command('check_weak_passwords', '--dry-run', stdout=out)
        self.assertIn('Would flag: 1', out.getvalue())
        self.assertFalse(LoginAlert.objects.exists())

    def test_the_command_reports_a_clean_run(self):
        make_user('pwaudit-cmd-clean', password='Xk9$mQz2#vLp7&wR')
        out = StringIO()
        call_command('check_weak_passwords', stdout=out)
        self.assertIn('No known-weak passwords found', out.getvalue())
