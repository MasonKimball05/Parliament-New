"""
Mobile plan, Phase 3 — chapter ballots from the app (v4.0.0), and the shared
ballot code the website now uses too (src/ballots.py).

What must hold: the app casts through the same rules as the web form
(attendance, membership, choice validation, one ballot per member, receipt),
re-auth can't be skipped or replayed, a passkey grant only works for the
device and member it was issued to, and password failures share the web's
limit. Passkey verification is mocked, as in test_passkey.py.

    python manage.py test src.tests.mobile.test_votes
"""
import base64
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from src.models import Attendance, Legislation, Vote
from src.models.mobile import MobileAuthTicket
from src.models.webauthn import WebAuthnCredential
from src.tests.mobile.sess_helpers import bearer, make_device, make_user
from src.tests.mobile.test_home_and_notifications import restrict, toggle
from src.utils.vote_receipts import verify_receipt

API = '/api/mobile/v1/'
CRED_ID = b'\x09vote-passkey'


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


@override_settings(ALLOWED_HOSTS=['testserver', 'localhost'])
class _Base(TestCase):

    def setUp(self):
        cache.clear()
        self.user = make_user('vote-1')
        self.device, self.access, _ = make_device(self.user)
        self.api = Client()
        Attendance.objects.create(user=self.user, status='present')
        self.bill = self.legislation('Open bill')

    def legislation(self, title, **kw):
        now = timezone.now()
        fields = dict(title=title, description='d', posted_by=self.user,
                      available_at=now - timedelta(hours=1), voting_starts_at=now - timedelta(hours=1))
        fields.update(kw)
        return Legislation.objects.create(**fields)

    def get(self, path, access=None):
        return self.api.get(API + path, **bearer(access or self.access))

    def post(self, path, body=None, access=None):
        return self.api.post(API + path, body or {}, content_type='application/json',
                             **bearer(access or self.access))

    def cast(self, bill=None, access=None, **body):
        body.setdefault('choices', ['yes'])
        if 'reauth_grant' not in body:
            body.setdefault('password', 'pw-vote-1')
        return self.post(f'votes/{(bill or self.bill).id}/cast/', body, access=access)


class ListAndDetailTests(_Base):

    def test_list_shows_the_ballot_and_my_state(self):
        self.legislation('Tabled', status='tabled')
        self.legislation('Closed', voting_closed=True)
        self.legislation('Later', voting_starts_at=timezone.now() + timedelta(days=1))
        self.legislation('Not yet available', available_at=timezone.now() + timedelta(days=1))
        body = self.get('votes/').json()
        self.assertEqual({v['title']: v['voting_open'] for v in body['votes']},
                         {'Open bill': True, 'Later': False})
        self.assertEqual(body['me'], {'can_vote': True, 'attendance_ok': True,
                                      'can_cast_now': True, 'has_passkeys': False})

    def test_detail_options(self):
        bill = self.legislation('Plural', vote_mode='plurality', plurality_options=['A', 'B', 'C'],
                                plurality_votes_allowed=2)
        vote = self.get(f'votes/{bill.id}/').json()['vote']
        self.assertEqual((vote['options'], vote['votes_allowed']), (['A', 'B', 'C'], 2))
        no_abstain = self.legislation('Strict', allow_abstain=False)
        self.assertEqual(self.get(f'votes/{no_abstain.id}/').json()['vote']['options'], ['yes', 'no'])

    def test_detail_404_off_the_ballot(self):
        tabled = self.legislation('Tabled', status='tabled')
        self.assertEqual(self.get(f'votes/{tabled.id}/').status_code, 404)

    def test_vote_page_toggle_off(self):
        toggle('vote', False)
        r = self.get('votes/')
        self.assertEqual((r.status_code, r.json()['error']), (403, 'page_disabled'))


class CastWithPasswordTests(_Base):

    def test_cast_records_one_ballot_and_returns_a_valid_receipt(self):
        r = self.cast()
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(Vote.objects.filter(user=self.user, legislation=self.bill).count(), 1)
        result = verify_receipt(r.json()['receipt'])
        self.assertTrue(result['valid'])
        self.assertEqual(result['voter_pk'], self.user.pk)
        r = self.cast()
        self.assertEqual((r.status_code, r.json()['error']), (409, 'already_voted'))

    def test_wrong_password_is_refused_and_records_nothing(self):
        r = self.cast(password='nope')
        self.assertEqual((r.status_code, r.json()['error']), (401, 'reauth_failed'))
        self.assertFalse(Vote.objects.exists())

    def test_reauth_is_required(self):
        r = self.post(f'votes/{self.bill.id}/cast/', {'choices': ['yes']})
        self.assertEqual((r.status_code, r.json()['error']), (401, 'reauth_required'))
        self.assertFalse(Vote.objects.exists())

    def test_password_failures_share_the_web_limit(self):
        cache.set(f'vote_pw_attempts_{self.user.pk}', 10, 900)   # the web's cap
        r = self.cast()   # right password, but locked
        self.assertEqual((r.status_code, r.json()['error']), (429, 'reauth_locked'))
        self.assertFalse(Vote.objects.exists())

    def test_not_present_is_refused_before_reauth(self):
        Attendance.objects.all().delete()
        r = self.cast(password='nope')
        self.assertEqual((r.status_code, r.json()['error']), (403, 'not_present'))
        # Eligibility came first: the wrong password wasn't counted.
        self.assertIsNone(cache.get(f'vote_pw_attempts_{self.user.pk}'))

    def test_pledge_cannot_vote(self):
        pledge = make_user('P-VOTE01', member_type='Pledge')
        Attendance.objects.create(user=pledge, status='present')
        _, access, _ = make_device(pledge)
        r = self.cast(access=access, password='pw-P-VOTE01')
        self.assertEqual((r.status_code, r.json()['error']), (403, 'not_eligible'))

    def test_invalid_choices(self):
        strict = self.legislation('Strict', allow_abstain=False)
        for body, bill in (({'choices': ['maybe']}, self.bill),
                           ({'choices': ['yes', 'no']}, self.bill),
                           ({'choices': ['abstain']}, strict),
                           ({'choices': 'yes'}, self.bill)):
            r = self.cast(bill=bill, **body)
            self.assertEqual((r.status_code, r.json()['error']), (400, 'invalid_choice'), body)
        self.assertFalse(Vote.objects.exists())

    def test_multi_select_plurality(self):
        bill = self.legislation('Plural', vote_mode='plurality', plurality_options=['A', 'B', 'C'],
                                plurality_votes_allowed=2)
        self.assertEqual(self.cast(bill=bill, choices=['A', 'A']).status_code, 400)
        r = self.cast(bill=bill, choices=['A', 'C'])
        self.assertEqual(r.json()['choices_count'], 2)

    def test_off_ballot_bills_refused_on_both_sides(self):
        # v4.0.0: the web form used to accept a tabled bill whose voting
        # wasn't marked closed.
        tabled = self.legislation('Tabled', status='tabled')
        r = self.cast(bill=tabled)
        self.assertEqual((r.status_code, r.json()['error']), (409, 'not_open'))
        web = Client()
        web.force_login(self.user)
        web.post(reverse('cast_vote'), {'legislation_id': tabled.id, 'vote_choice': 'yes',
                                        'password': 'pw-vote-1'})
        self.assertFalse(Vote.objects.filter(legislation=tabled).exists())

    def test_cast_vote_pledge_restriction_applies(self):
        # Pledges can't vote anyway; the restriction is still honoured first.
        pledge = make_user('P-VOTE02', member_type='Pledge')
        _, access, _ = make_device(pledge)
        restrict('cast_vote')
        r = self.cast(access=access)
        self.assertEqual(r.json()['error'], 'pledge_restricted')

    def test_web_form_still_casts(self):
        web = Client()
        web.force_login(self.user)
        web.post(reverse('cast_vote'), {'legislation_id': self.bill.id, 'vote_choice': 'no',
                                        'password': 'pw-vote-1'})
        self.assertEqual(Vote.objects.get(user=self.user, legislation=self.bill).vote_choice, 'no')


class PasskeyReauthTests(_Base):

    def setUp(self):
        super().setUp()
        WebAuthnCredential.objects.create(user=self.user, credential_id=CRED_ID,
                                          public_key=b'pk', sign_count=1, name='Phone')

    def begin(self, access=None):
        r = self.post('votes/reauth/passkey/begin/', access=access)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def complete(self, challenge_id, cred_id=CRED_ID, access=None):
        return self.post('votes/reauth/passkey/complete/', {
            'challenge_id': challenge_id,
            'credential': {'id': b64url(cred_id), 'rawId': b64url(cred_id),
                           'type': 'public-key', 'response': {}},
        }, access=access)

    def test_begin_scopes_to_my_keys(self):
        opts = self.begin()['options']
        self.assertEqual(opts['userVerification'], 'required')
        self.assertEqual([c['id'] for c in opts['allowCredentials']], [b64url(CRED_ID)])

    @patch('webauthn.verify_authentication_response')
    def test_grant_casts_once(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=2)
        grant = self.complete(self.begin()['challenge_id']).json()['reauth_grant']
        self.assertTrue(verify.call_args.kwargs['require_user_verification'])
        r = self.cast(reauth_grant=grant)
        self.assertEqual(r.status_code, 200, r.content)
        other = self.legislation('Second bill')
        r = self.cast(bill=other, reauth_grant=grant)   # replay
        self.assertEqual((r.status_code, r.json()['error']), (401, 'reauth_failed'))

    @patch('webauthn.verify_authentication_response')
    def test_grant_is_bound_to_device_and_member(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=2)
        grant = self.complete(self.begin()['challenge_id']).json()['reauth_grant']
        _, second_access, _ = make_device(self.user, name='iPad')
        r = self.cast(reauth_grant=grant, access=second_access)
        self.assertEqual(r.json()['error'], 'reauth_failed')
        self.assertFalse(Vote.objects.exists())

    def test_password_and_grant_together_is_refused(self):
        r = self.cast(reauth_grant='x', password='pw-vote-1')
        self.assertEqual(r.json()['error'], 'reauth_required')

    @patch('webauthn.verify_authentication_response')
    def test_someone_elses_credential_is_refused(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=2)
        other = make_user('vote-2')
        WebAuthnCredential.objects.create(user=other, credential_id=b'theirs', public_key=b'pk',
                                          sign_count=1, name='Theirs')
        r = self.complete(self.begin()['challenge_id'], cred_id=b'theirs')
        self.assertEqual((r.status_code, r.json()['error']), (401, 'reauth_failed'))
        verify.assert_not_called()
        self.assertFalse(MobileAuthTicket.objects.filter(
            purpose=MobileAuthTicket.PURPOSE_VOTE_GRANT).exists())

    @patch('webauthn.verify_authentication_response')
    def test_challenge_from_another_device_is_refused(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=2)
        challenge = self.begin()['challenge_id']
        _, second_access, _ = make_device(self.user, name='iPad')
        r = self.complete(challenge, access=second_access)
        self.assertEqual(r.json()['error'], 'reauth_failed')

    @patch('webauthn.verify_authentication_response', side_effect=ValueError('bad sig'))
    def test_failures_count_toward_the_web_limit(self, verify):
        for _ in range(5):
            self.complete(self.begin()['challenge_id'])
        r = self.post('votes/reauth/passkey/begin/')
        self.assertEqual((r.status_code, r.json()['error']), (429, 'reauth_locked'))
        self.assertEqual(cache.get(f'passkey_reauth_attempts_{self.user.pk}'), 5)

    def test_no_passkeys(self):
        # A member who never had one (deleting a passkey would sign the
        # device out: Phase 1 revocation).
        plain = make_user('vote-3')
        _, access, _ = make_device(plain)
        r = self.post('votes/reauth/passkey/begin/', access=access)
        self.assertEqual((r.status_code, r.json()['error']), (400, 'no_passkeys'))


class CommitteeBallotTests(_Base):
    """committees/<code>/votes/ — voting members of an active committee only."""

    def setUp(self):
        super().setUp()
        from src.models import Committee, CommitteeLegislation
        self.committee = Committee.objects.create(name='Finance', code='FIN', is_active=True)
        self.committee.voting_members.add(self.user)
        self.other_committee = Committee.objects.create(name='Social', code='SOC', is_active=True)
        now = timezone.now()
        make = lambda c, title, **kw: CommitteeLegislation.objects.create(
            committee=c, title=title, description='d', posted_by=self.user,
            available_at=now - timedelta(hours=1), **kw)
        self.cbill = make(self.committee, 'Budget')
        self.removed = make(self.committee, 'Removed', status='removed')
        self.theirs = make(self.other_committee, 'Party')

    def ccast(self, code, bill, access=None, **body):
        body.setdefault('choices', ['yes'])
        body.setdefault('password', 'pw-vote-1')
        return self.post(f'committees/{code}/votes/{bill.id}/cast/', body, access=access)

    def test_votes_lists_my_committees_with_open_ballots(self):
        self.assertEqual(self.get('votes/').json()['committees'],
                         [{'code': 'FIN', 'name': 'Finance', 'open_count': 1}])

    def test_list_and_cast(self):
        from src.models import CommitteeVote
        body = self.get('committees/FIN/votes/').json()
        self.assertEqual([v['title'] for v in body['votes']], ['Budget'])
        r = self.ccast('FIN', self.cbill)
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(CommitteeVote.objects.filter(user=self.user, legislation=self.cbill).count(), 1)
        self.assertEqual(self.ccast('FIN', self.cbill).json()['error'], 'already_voted')

    def test_not_a_voting_member_sees_nothing(self):
        for path in ('committees/SOC/votes/', f'committees/SOC/votes/{self.theirs.id}/'):
            self.assertEqual(self.get(path).status_code, 404, path)
        self.assertEqual(self.ccast('SOC', self.theirs).status_code, 404)

    def test_another_committees_bill_through_my_committee_is_404(self):
        # The P0 IDOR shape: my committee's code, their bill's id.
        from src.models import CommitteeVote
        self.assertEqual(self.ccast('FIN', self.theirs).status_code, 404)
        self.assertFalse(CommitteeVote.objects.filter(legislation=self.theirs).exists())

    def test_removed_bill_is_refused_on_web_and_app(self):
        from src.models import CommitteeVote
        self.assertEqual(self.ccast('FIN', self.removed).status_code, 404)
        web = Client()
        web.force_login(self.user)
        web.post(reverse('vote', kwargs={'code': 'FIN'}),
                 {'action': 'cast_vote', 'legislation_id': self.removed.id,
                  'vote_choice': 'yes', 'password': 'pw-vote-1'})
        self.assertFalse(CommitteeVote.objects.filter(legislation=self.removed).exists())

    def test_web_committee_form_still_casts(self):
        from src.models import CommitteeVote
        web = Client()
        web.force_login(self.user)
        web.post(reverse('vote', kwargs={'code': 'FIN'}),
                 {'action': 'cast_vote', 'legislation_id': self.cbill.id,
                  'vote_choice': 'no', 'password': 'pw-vote-1'})
        self.assertEqual(CommitteeVote.objects.get(legislation=self.cbill).vote_choice, 'no')

    def test_not_present(self):
        Attendance.objects.all().delete()
        self.assertEqual(self.ccast('FIN', self.cbill).json()['error'], 'not_present')

    def test_flag_off(self):
        from src.tests.mobile.test_home_and_notifications import flag
        flag('committee_voting', False)
        r = self.get('committees/FIN/votes/')
        self.assertEqual((r.status_code, r.json()['error']), (403, 'feature_disabled'))
        self.assertEqual(self.get('votes/').json()['committees'], [])

    def test_inactive_committee_is_404(self):
        self.committee.is_active = False
        self.committee.save()
        self.assertEqual(self.get('committees/FIN/votes/').status_code, 404)

    @patch('webauthn.verify_authentication_response')
    def test_passkey_grant_works_for_committee_ballots(self, verify):
        verify.return_value = SimpleNamespace(new_sign_count=2)
        WebAuthnCredential.objects.create(user=self.user, credential_id=CRED_ID,
                                          public_key=b'pk', sign_count=1, name='Phone')
        begin = self.post('votes/reauth/passkey/begin/').json()
        grant = self.post('votes/reauth/passkey/complete/', {
            'challenge_id': begin['challenge_id'],
            'credential': {'id': b64url(CRED_ID), 'rawId': b64url(CRED_ID),
                           'type': 'public-key', 'response': {}},
        }).json()['reauth_grant']
        r = self.post(f'committees/FIN/votes/{self.cbill.id}/cast/',
                      {'choices': ['yes'], 'reauth_grant': grant})
        self.assertEqual(r.status_code, 200, r.content)
