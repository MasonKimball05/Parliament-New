"""
Tests for ParliamentUser.anonymize() and the "Anonymize selected members"
admin action (v3.34.0-in-progress, 09-18-26).

Context: Django's admin delete-confirmation page refuses to hard-delete a
member at all if cascading would touch a model registered read-only in
/admin/ — and this codebase deliberately keeps several of those locked down
for ballot-integrity/anonymity reasons (Vote, CommitteeVote, SlatingVote,
SlatingBallot, SlatingApplicationResponse — see Resources/CLAUDE.md's "Admin
confidentiality boundary"). anonymize() is the escape hatch: it scrubs a
member's identity/contact info in place and marks them removed, but keeps
`user_id` — and therefore every foreign key pointing at it — exactly where
it is, so votes/ballots/slating history/minutes/etc. are never touched.

Run with: python manage.py test src.tests.users.test_member_anonymization
"""
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import (
    AdminActionLog, Legislation, ParliamentUser, Vote,
)
from src.models.users import (
    ANONYMIZE_CHARFIELDS_BLANKED, ANONYMIZE_EMAILFIELDS_CLEARED,
    ANONYMIZE_FIELDS_KEPT, ANONYMIZE_FIELDS_SPECIAL,
    ANONYMIZE_JSONFIELDS_CLEARED,
)


def _make_user(user_id, name='Real Name', username=None, member_type='Member', **extra):
    user = ParliamentUser.objects.create_user(
        user_id=user_id, name=name, username=username or user_id.lower(),
        member_type=member_type, **extra,
    )
    user.is_active = True
    user.member_status = 'Active'
    user.set_password('test-pass-12345')
    user.save()
    return user


def _make_admin(user_id='anon-admin'):
    admin = _make_user(user_id, name='Admin Officer', member_type='Officer')
    admin.is_admin = True
    admin.save(update_fields=['is_admin'])
    return admin


class EveryConcreteFieldIsClassifiedForAnonymizationTests(TestCase):
    """
    The enumeration. Every concrete field on ParliamentUser must appear in
    exactly one of the four classification groups anonymize() and its
    module docstring describe — otherwise a newly added profile field could
    silently survive anonymization as a leftover identifying detail, the
    exact failure mode this test exists to catch.
    """

    def _all_classified_names(self):
        return (
            set(ANONYMIZE_CHARFIELDS_BLANKED)
            | set(ANONYMIZE_EMAILFIELDS_CLEARED)
            | set(ANONYMIZE_JSONFIELDS_CLEARED)
            | set(ANONYMIZE_FIELDS_SPECIAL)
            | set(ANONYMIZE_FIELDS_KEPT.keys())
        )

    def test_every_concrete_field_is_classified_exactly_once(self):
        classified = self._all_classified_names()
        groups = [
            set(ANONYMIZE_CHARFIELDS_BLANKED),
            set(ANONYMIZE_EMAILFIELDS_CLEARED),
            set(ANONYMIZE_JSONFIELDS_CLEARED),
            set(ANONYMIZE_FIELDS_SPECIAL),
            set(ANONYMIZE_FIELDS_KEPT.keys()),
        ]
        # No field should appear in more than one group.
        seen = set()
        duplicates = set()
        for group in groups:
            duplicates |= (seen & group)
            seen |= group
        self.assertEqual(duplicates, set(), f'fields classified more than once: {duplicates}')

        concrete_field_names = {
            f.name for f in ParliamentUser._meta.get_fields()
            if getattr(f, 'concrete', False)
        }
        unclassified = concrete_field_names - classified
        self.assertEqual(
            unclassified, set(),
            f'ParliamentUser field(s) not classified for anonymize(): {unclassified} — '
            f'add each to ANONYMIZE_CHARFIELDS_BLANKED, ANONYMIZE_EMAILFIELDS_CLEARED, '
            f'ANONYMIZE_JSONFIELDS_CLEARED, ANONYMIZE_FIELDS_SPECIAL, or '
            f'ANONYMIZE_FIELDS_KEPT (with a reason).',
        )

    def test_the_control_actually_flags_an_unclassified_field(self):
        """Control: prove the completeness check can fail, not just pass."""
        concrete_field_names = {
            f.name for f in ParliamentUser._meta.get_fields()
            if getattr(f, 'concrete', False)
        }
        classified_without_name = self._all_classified_names() - {'name'}
        self.assertIn('name', concrete_field_names - classified_without_name)

    def test_every_kept_field_has_a_non_empty_reason(self):
        for field, reason in ANONYMIZE_FIELDS_KEPT.items():
            self.assertTrue(reason and reason.strip(), f'{field} has no reason recorded')


class AnonymizeScrubsIdentityFieldsTests(TestCase):
    def setUp(self):
        self.member = _make_user(
            'anon-1', name='Real Name Here', username='realusername',
            member_type='Member',
        )
        self.member.email = 'real@example.com'
        self.member.other_email = 'secondary@example.com'
        self.member.phone_number = '555-1234'
        self.member.about_me = 'A real bio about a real person'
        self.member.instagram = 'realig'
        self.member.twitter = 'realtw'
        self.member.linkedin = 'reallinkedin'
        self.member.snapchat = 'realsnap'
        self.member.facebook = 'realfb'
        self.member.house = 'Smith'
        self.member.role_number = '42'
        self.member.preferred_name = 'Realname'
        self.member.majors = ['Computer Science']
        self.member.minors = ['Mathematics']
        self.member.concentrations = ['Cyber Security']
        self.member.custom_socials = [{'platform': 'discord', 'handle': 'real#1234'}]
        self.member.initiation_chapters = [{'school': 'Real U', 'chapter': 'Alpha'}]
        self.member.email_flagged_reason = 'bounced: real@example.com'
        self.member.save()

    def test_name_becomes_placeholder(self):
        self.member.anonymize()
        self.assertEqual(self.member.name, 'Deleted User')

    def test_username_becomes_a_deterministic_unique_placeholder(self):
        self.member.anonymize()
        self.assertEqual(self.member.username, f'deleted-{self.member.pk}')

    def test_emails_are_cleared(self):
        self.member.anonymize()
        self.assertIsNone(self.member.email)
        self.assertIsNone(self.member.other_email)

    def test_contact_and_social_fields_are_blanked(self):
        self.member.anonymize()
        for field in ANONYMIZE_CHARFIELDS_BLANKED:
            self.assertEqual(getattr(self.member, field), '', f'{field} was not blanked')

    def test_json_fields_reset_to_empty(self):
        self.member.anonymize()
        self.assertEqual(self.member.majors, [])
        self.assertEqual(self.member.minors, [])
        self.assertEqual(self.member.concentrations, [])
        self.assertEqual(self.member.custom_socials, [])
        self.assertEqual(self.member.initiation_chapters, [])

    def test_role_number_is_cleared(self):
        self.member.anonymize()
        self.assertIsNone(self.member.role_number)

    def test_account_is_deactivated_and_marked_removed_and_anonymized(self):
        self.member.anonymize()
        self.assertFalse(self.member.is_active)
        self.assertEqual(self.member.member_status, 'Removed')
        self.assertTrue(self.member.is_anonymized)

    def test_admin_flag_is_revoked(self):
        self.member.is_admin = True
        self.member.save(update_fields=['is_admin'])
        self.member.anonymize()
        self.assertFalse(self.member.is_admin)

    def test_password_becomes_unusable(self):
        self.assertTrue(self.member.has_usable_password())
        self.member.anonymize()
        self.assertFalse(self.member.has_usable_password())

    def test_profile_picture_field_is_cleared(self):
        # No file attached in this fixture — just confirms the no-file path
        # doesn't raise (the `if self.profile_picture:` guard in anonymize()).
        self.member.anonymize()
        self.assertFalse(self.member.profile_picture)

    def test_row_is_not_deleted(self):
        pk = self.member.pk
        self.member.anonymize()
        self.assertTrue(ParliamentUser.objects.filter(pk=pk).exists())

    def test_kept_fields_survive_untouched(self):
        self.member.graduation_year = 2027
        self.member.pledge_class = 'Fall 2024'
        self.member.member_type = 'Member'
        self.member.save()
        self.member.anonymize()
        self.assertEqual(self.member.graduation_year, 2027)
        self.assertEqual(self.member.pledge_class, 'Fall 2024')
        self.assertEqual(self.member.member_type, 'Member')

    def test_is_idempotent(self):
        self.member.anonymize()
        first_username = self.member.username
        self.member.anonymize()  # must not raise (unique username collision, etc.)
        self.assertEqual(self.member.username, first_username)
        self.assertEqual(self.member.name, 'Deleted User')


class AnonymizePreservesInstitutionalRecordsTests(TestCase):
    """
    The whole point of anonymize() over a hard delete: everything with a
    foreign key to this member's user_id survives, untouched, because
    user_id never changes.
    """

    def setUp(self):
        self.author = _make_user('anon-author', name='Bill Author')
        self.member = _make_user('anon-voter', name='Vote Caster')
        self.legislation = Legislation.objects.create(
            title='Test Bill', description='d', posted_by=self.author,
            available_at=timezone.now(), vote_mode='percentage',
            required_percentage='50', status='passed',
        )
        self.vote = Vote.objects.create(
            legislation=self.legislation, user=self.member, vote_choice='yes',
        )

    def test_vote_row_survives_with_the_same_fk(self):
        pk = self.member.pk
        self.member.anonymize()
        self.vote.refresh_from_db()
        self.assertEqual(self.vote.user_id, pk)
        self.assertTrue(Vote.objects.filter(pk=self.vote.pk).exists())

    def test_legislation_authorship_survives_for_a_different_member(self):
        pk = self.author.pk
        self.author.anonymize()
        self.legislation.refresh_from_db()
        self.assertEqual(self.legislation.posted_by_id, pk)


class AnonymizeDeletesLoginCredentialsTests(TestCase):
    def setUp(self):
        self.member = _make_user('anon-creds', name='Cred Holder')

    def test_deletes_api_tokens(self):
        from src.models.api import APIToken
        APIToken.objects.create(user=self.member, name='tok', key='k1', scopes=[])
        self.member.anonymize()
        self.assertEqual(APIToken.objects.filter(user_id=self.member.pk).count(), 0)

    def test_deletes_push_subscriptions(self):
        from src.models.notifications import PushSubscription
        PushSubscription.objects.create(user=self.member, endpoint='https://x', p256dh='a', auth='b')
        self.member.anonymize()
        self.assertEqual(PushSubscription.objects.filter(user_id=self.member.pk).count(), 0)

    def test_deletes_calendar_subscription(self):
        from src.models_calendar_subscription import CalendarSubscription
        CalendarSubscription.get_or_create_for_user(self.member)
        self.member.anonymize()
        self.assertEqual(CalendarSubscription.objects.filter(user_id=self.member.pk).count(), 0)

    def test_deletes_two_factor_requirement(self):
        from src.models import TwoFactorRequirement
        TwoFactorRequirement.objects.create(user=self.member, requirement='required', set_by=self.member)
        self.member.anonymize()
        self.assertEqual(TwoFactorRequirement.objects.filter(user_id=self.member.pk).count(), 0)

    def test_deletes_otp_devices(self):
        from django_otp.plugins.otp_totp.models import TOTPDevice
        from django_otp.plugins.otp_static.models import StaticDevice
        TOTPDevice.objects.create(user=self.member, name='default', confirmed=True)
        StaticDevice.objects.create(user=self.member, name='backup', confirmed=True)
        self.member.anonymize()
        self.assertEqual(TOTPDevice.objects.filter(user_id=self.member.pk).count(), 0)
        self.assertEqual(StaticDevice.objects.filter(user_id=self.member.pk).count(), 0)

    def test_clears_current_roles(self):
        from src.models import Role
        role = Role.objects.create(name='Test Role', code='TESTROLE')
        self.member.roles.add(role)
        self.member.anonymize()
        self.assertEqual(self.member.roles.count(), 0)


@override_settings(REQUIRE_2FA_FOR_ADMINS=False, REQUIRE_2FA_FOR_OFFICERS=False)
class AnonymizeAdminActionTests(TestCase):
    def setUp(self):
        self.admin = _make_admin()
        self.member = _make_user('anon-target', name='Real Target Name')
        self.client.force_login(self.admin)

    def _post(self, extra=None):
        data = {'action': 'anonymize_members', '_selected_action': [self.member.pk]}
        data.update(extra or {})
        return self.client.post(reverse('admin:src_parliamentuser_changelist'), data)

    def test_first_click_shows_confirmation_and_does_not_act(self):
        response = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Real Target Name')
        self.member.refresh_from_db()
        self.assertEqual(self.member.name, 'Real Target Name')
        self.assertFalse(self.member.is_anonymized)

    def test_confirming_anonymizes_the_member(self):
        self._post({'post': 'yes'})
        self.member.refresh_from_db()
        self.assertEqual(self.member.name, 'Deleted User')
        self.assertTrue(self.member.is_anonymized)

    def test_confirming_writes_an_admin_action_log_with_the_original_name(self):
        self._post({'post': 'yes'})
        log = AdminActionLog.objects.get(action='member_anonymized')
        self.assertEqual(log.actor, self.admin)
        self.assertEqual(log.target_user_id, self.member.pk)
        self.assertIn('Real Target Name', log.target_repr)
        self.assertIn('Real Target Name', log.detail)

    def test_already_anonymized_member_is_skipped_not_re_logged(self):
        self.member.anonymize()
        self._post({'post': 'yes'})
        self.assertEqual(AdminActionLog.objects.filter(action='member_anonymized').count(), 0)

    def test_a_non_admin_cannot_reach_the_admin_action_at_all(self):
        plain_member = _make_user('anon-plain', name='Plain Member')
        client = self.client.__class__()
        client.force_login(plain_member)
        response = client.post(
            reverse('admin:src_parliamentuser_changelist'),
            {'action': 'anonymize_members', '_selected_action': [self.member.pk], 'post': 'yes'},
        )
        # Non-staff is redirected to the admin login page, not given the action.
        self.assertNotEqual(response.status_code, 200)
        self.member.refresh_from_db()
        self.assertEqual(self.member.name, 'Real Target Name')
