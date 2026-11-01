"""
The Apple App Review demo account (src/app_review.py,
src/management/commands/create_app_review_account.py).

    manage.py test src.tests.security.test_app_review_account

Proves: it signs in to the app with a password alone even when the chapter
requires 2FA (and nobody else does); it never sees another member's email or
phone in the app or on the website; it is left out of the directory, the house
map, attendance rosters and counts; its ballots are refused; and the command
refuses to make one that isn't configured.
"""
import csv
import io
import re
from datetime import timedelta

from django.core.cache import cache
from django.core.management import CommandError, call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src import ballots
from src.app_review import (
    exclude_review_accounts, is_2fa_exempt_review_account, is_app_review_account,
)
from src.middleware.two_factor import Enforce2FAMiddleware
from src.models import Event, ParliamentUser, TwoFactorRequirement
from src.models.mobile import MobileDevice
from src.models_feature_flags import SiteSetting
from src.tests.mobile.base import MobileTestCase, totp_device
from src.tests.mobile.base import make_user as _make_user
from src.tests.mobile.sess_helpers import bearer, make_device

REVIEW = override_settings(APP_REVIEW_USERNAMES=frozenset({'appreview'}))
API = '/api/mobile/v1/'


def make_user(uid, username=None, email=None, **kw):
    """The mobile fixture (which sets an email from the username), keeping ``email``."""
    user = _make_user(uid, username=username, **kw)
    if email:
        user.email = email
        user.save(update_fields=['email'])
    return user


def require_2fa_for_everyone():
    SiteSetting.objects.update_or_create(
        key='2fa_policy_mode', defaults={'value': 'all_members', 'setting_type': 'string'})


def make_reviewer(**kw):
    return make_user('rev1', username='appreview', name='App Review', **kw)


# ── the helper itself ────────────────────────────────────────────────────────

@REVIEW
class HelperTests(TestCase):

    def test_matches_only_configured_usernames(self):
        reviewer = make_reviewer()
        other = make_user('oth1')
        self.assertTrue(is_app_review_account(reviewer))
        self.assertFalse(is_app_review_account(other))
        self.assertFalse(is_app_review_account(None))

    def test_case_insensitive(self):
        self.assertTrue(is_app_review_account(make_user('rev2', username='AppReview')))

    @override_settings(APP_REVIEW_USERNAMES=frozenset())
    def test_empty_setting_matches_nobody_and_excludes_nothing(self):
        make_reviewer()
        self.assertFalse(is_app_review_account(ParliamentUser.objects.get(username='appreview')))
        qs = ParliamentUser.objects.all()
        self.assertEqual(exclude_review_accounts(qs).count(), qs.count())

    def test_exemption_needs_a_plain_member(self):
        reviewer = make_reviewer()
        self.assertTrue(is_2fa_exempt_review_account(reviewer))
        reviewer.member_type = 'Officer'
        self.assertFalse(is_2fa_exempt_review_account(reviewer))
        reviewer.member_type, reviewer.is_admin = 'Member', True
        self.assertFalse(is_2fa_exempt_review_account(reviewer))


# ── sign-in without a second factor ──────────────────────────────────────────

@REVIEW
class ReviewerSignInTests(MobileTestCase):

    def setUp(self):
        super().setUp()
        require_2fa_for_everyone()

    def test_reviewer_signs_in_without_2fa_when_everyone_needs_it(self):
        reviewer = make_reviewer()
        r = self.login('appreview')
        self.assertEqual(r.status_code, 200, r.content)
        body = r.json()
        self.assertFalse(body['mfa_required'])
        self.assertEqual(MobileDevice.objects.get(user=reviewer).verified_via, 'password')
        # The device stays valid on later requests (still_allowed re-asks the policy).
        self.assertEqual(self.get_me(body['access_token']).status_code, 200)

    def test_normal_member_still_cannot_skip_2fa(self):
        member = make_user('mem1')
        r = self.login(member.username)
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.json()['mfa_setup_required'])
        self.assertFalse(MobileDevice.objects.exists())

    def test_explicit_required_row_does_not_lock_the_reviewer_out(self):
        reviewer = make_reviewer()
        TwoFactorRequirement.objects.create(user=reviewer, requirement='required')
        self.assertEqual(self.login('appreview').status_code, 200)

    def test_promoted_review_account_needs_2fa_again(self):
        make_reviewer(member_type='Officer')
        r = self.login('appreview')
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.json()['mfa_setup_required'])

    @override_settings(APP_REVIEW_USERNAMES=frozenset())
    def test_unconfigured_account_needs_2fa(self):
        make_reviewer()
        self.assertEqual(self.login('appreview').status_code, 403)

    def test_an_enrolled_authenticator_is_still_asked_for(self):
        reviewer = make_reviewer()
        totp_device(reviewer)
        r = self.login('appreview')
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()['mfa_required'])

    def test_website_does_not_send_the_reviewer_to_2fa_setup(self):
        reviewer = make_reviewer()
        member = make_user('mem2')
        self.assertFalse(Enforce2FAMiddleware.user_requires_2fa(reviewer))
        self.assertTrue(Enforce2FAMiddleware.user_requires_2fa(member))
        client = Client()
        client.force_login(reviewer)
        r = client.get(reverse('member_directory'))
        self.assertEqual(r.status_code, 200)
        client.force_login(member)
        r = client.get(reverse('member_directory'))
        self.assertEqual(r.status_code, 302)
        self.assertIn('two-factor', r['Location'])


# ── contact details: the app ─────────────────────────────────────────────────

@REVIEW
@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class AppContactTests(TestCase):

    def setUp(self):
        cache.clear()
        self.reviewer = make_reviewer()
        self.member = make_user('mem3', name='Normal Member')
        self.other = make_user('oth3', name='Other Brother', email='other@parliament.test',
                               phone_number='2055550123', other_email='alt@parliament.test')
        _, self.rev_access, _ = make_device(self.reviewer)
        _, self.mem_access, _ = make_device(self.member)
        self.api = Client()

    def get(self, path, access):
        return self.api.get(API + path, **bearer(access))

    def test_directory_list(self):
        rows = {m['user_id']: m for m in self.get('members/', self.rev_access).json()['members']}
        self.assertEqual(rows['oth3']['email'], '')
        self.assertEqual(rows['oth3']['phone'], '')
        self.assertNotIn('rev1', rows)                      # not listed to itself either
        body = self.get('members/', self.rev_access).content.decode()
        self.assertNotIn('other@parliament.test', body)
        self.assertNotIn('2055550123', body)

        rows = {m['user_id']: m for m in self.get('members/', self.mem_access).json()['members']}
        self.assertEqual(rows['oth3']['email'], 'other@parliament.test')
        self.assertEqual(rows['oth3']['phone'], '2055550123')
        self.assertNotIn('rev1', rows)                      # members don't see it

    def test_member_card(self):
        card = self.get('members/oth3/', self.rev_access).json()['member']
        self.assertEqual((card['email'], card['other_email'], card['phone']), ('', '', ''))
        card = self.get('members/oth3/', self.mem_access).json()['member']
        self.assertEqual(card['email'], 'other@parliament.test')
        self.assertEqual(card['other_email'], 'alt@parliament.test')
        self.assertEqual(card['phone'], '2055550123')

    def test_own_card_is_not_blanked(self):
        self.reviewer.phone_number = '2055550999'
        self.reviewer.save()
        card = self.get('members/rev1/', self.rev_access).json()['member']
        self.assertEqual(card['phone'], '2055550999')

    def test_house_map_leaves_the_reviewer_off(self):
        from src.view.house_map import house_map_data
        _, all_members, unassigned = house_map_data()
        self.assertNotIn('rev1', [m.user_id for m in all_members])
        self.assertNotIn('rev1', [m.user_id for m in unassigned])


# ── contact details: the website ─────────────────────────────────────────────

@REVIEW
class WebContactTests(TestCase):

    def setUp(self):
        cache.clear()
        self.reviewer = make_reviewer()
        self.member = make_user('mem4', name='Normal Member')
        self.other = make_user('oth4', name='Other Brother', email='other4@parliament.test',
                               phone_number='2055550144', other_email='alt4@parliament.test')
        self.client = Client()

    def test_directory_page(self):
        self.client.force_login(self.reviewer)
        html = self.client.get(reverse('member_directory')).content.decode()
        self.assertIn('Other Brother', html)
        self.assertNotIn('other4@parliament.test', html)
        self.assertNotIn('2055550144', html)
        self.assertNotIn('205-555-0144', html)
        self.assertNotIn('name="include_email"', html)

        self.client.force_login(self.member)
        html = self.client.get(reverse('member_directory')).content.decode()
        self.assertIn('other4@parliament.test', html)
        self.assertNotIn('App Review', html)

    def test_profile_card_json(self):
        self.client.force_login(self.reviewer)
        card = self.client.get(reverse('profile_card', args=['oth4'])).json()
        self.assertEqual((card['email'], card['other_email'], card['phone']), ('', '', ''))
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse('profile_card', args=['oth4'])).json()['email'],
                         'other4@parliament.test')

    def _export(self, user):
        self.client.force_login(user)
        r = self.client.get(reverse('export_directory'), {
            'format': 'csv', '_export_submitted': '1',
            'include_email': '1', 'include_phone': '1'})
        self.assertEqual(r.status_code, 200)
        return list(csv.reader(io.StringIO(r.content.decode())))

    def test_export_drops_contact_columns_for_the_reviewer(self):
        rows = self._export(self.reviewer)
        self.assertNotIn('Email', rows[0])
        self.assertNotIn('Phone', rows[0])
        flat = '\n'.join(','.join(r) for r in rows)
        self.assertNotIn('other4@parliament.test', flat)
        self.assertNotIn('App Review', flat)

        rows = self._export(self.member)
        self.assertIn('Email', rows[0])
        flat = '\n'.join(','.join(r) for r in rows)
        self.assertIn('other4@parliament.test', flat)
        self.assertNotIn('App Review', flat)

    def test_people_search_never_runs_for_a_review_account(self):
        from src.view.global_search import search_everything
        officer_named_like_reviewer = make_user('rev5', username='appreview2', member_type='Officer')
        with override_settings(APP_REVIEW_USERNAMES=frozenset({'appreview', 'appreview2'})):
            results, _ = search_everything('Other', officer_named_like_reviewer)
        self.assertNotIn('users', results)
        officer = make_user('off5', member_type='Officer')
        results, _ = search_everything('Other', officer)
        self.assertIn('users', results)


# ── counts and rosters ───────────────────────────────────────────────────────

@REVIEW
class CountTests(TestCase):

    def setUp(self):
        cache.clear()
        self.reviewer = make_reviewer()
        self.member = make_user('mem6')

    def test_event_roster_and_rate_denominator(self):
        event = Event.objects.create(
            title='Chapter', description='', date_time=timezone.now() + timedelta(days=1),
            requires_attendance=True, created_by=self.member)
        ids = set(event.required_members().values_list('pk', flat=True))
        self.assertIn('mem6', ids)
        self.assertNotIn('rev1', ids)
        # The bulk path the attendance pages use agrees with the slow one.
        self.assertEqual(event.get_attendance_stats()['total_members'], len(ids))
        (primed,) = Event.prime_attendance_stats([Event.objects.get(pk=event.pk)])
        self.assertEqual(primed._attendance_stats_cache['total_members'], len(ids))

    def test_home_member_count(self):
        self.client.force_login(self.member)
        r = self.client.get(reverse('home'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['total_active_members'],
                         ParliamentUser.objects.filter(member_status='Active').count() - 1)

    def test_ballots_are_refused(self):
        with self.assertRaises(ballots.BallotError) as ctx:
            ballots.check_eligible(self.reviewer)
        self.assertEqual(ctx.exception.code, 'not_eligible')
        self.assertIn('demo account', str(ctx.exception))
        # A normal member gets the usual reason (not marked present), not this one.
        with self.assertRaises(ballots.BallotError) as ctx:
            ballots.check_eligible(self.member)
        self.assertEqual(ctx.exception.code, 'not_present')


# ── the management command ───────────────────────────────────────────────────

class CommandTests(MobileTestCase):

    def run_command(self, *args):
        out = io.StringIO()
        call_command('create_app_review_account', *args, stdout=out, stderr=io.StringIO())
        return out.getvalue()

    @override_settings(APP_REVIEW_USERNAMES=frozenset())
    def test_refuses_an_unconfigured_username(self):
        with self.assertRaises(CommandError) as ctx:
            self.run_command()
        self.assertIn('APP_REVIEW_USERNAMES=appreview', str(ctx.exception))
        self.assertFalse(ParliamentUser.objects.filter(username='appreview').exists())

    @REVIEW
    def test_creates_a_plain_member_that_can_sign_in_and_be_disabled(self):
        require_2fa_for_everyone()
        out = self.run_command()
        password = re.search(r'Password: (\S+)', out).group(1)
        self.assertGreaterEqual(len(password), 20)
        self.assertIn('APP_REVIEW_USERNAMES=appreview', out)
        user = ParliamentUser.objects.get(username='appreview')
        self.assertEqual((user.name, user.member_type, user.member_status), ('App Review', 'Member', 'Active'))
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_admin)
        self.assertIsNone(user.email)
        self.assertTrue(user.onboarding_complete)
        self.assertFalse(user.force_password_change)
        self.assertFalse(user.roles.exists())

        r = self.login('appreview', password=password)
        self.assertEqual(r.status_code, 200, r.content)
        access = r.json()['access_token']

        # Re-running resets: new password, the old one and the old device stop working.
        out = self.run_command()
        new_password = re.search(r'Password: (\S+)', out).group(1)
        self.assertNotEqual(new_password, password)
        self.assertEqual(ParliamentUser.objects.filter(username='appreview').count(), 1)
        self.assertEqual(self.get_me(access).status_code, 401)
        self.assertEqual(self.login('appreview', password=password).status_code, 401)
        cache.clear()
        self.assertEqual(self.login('appreview', password=new_password).status_code, 200)

        self.run_command('--disable')
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertEqual(user.member_status, 'Inactive')
        self.assertFalse(MobileDevice.objects.filter(user=user, revoked_at__isnull=True).exists())
        self.assertNotEqual(self.login('appreview', password=new_password).status_code, 200)

    @REVIEW
    def test_reset_removes_an_enrolled_authenticator(self):
        self.run_command()
        user = ParliamentUser.objects.get(username='appreview')
        totp_device(user)
        out = self.run_command()
        password = re.search(r'Password: (\S+)', out).group(1)
        r = self.login('appreview', password=password)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()['mfa_required'])
