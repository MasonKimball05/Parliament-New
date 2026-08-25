"""
Tests for the Parliament REST API (src/api/).

Covers: authentication gate, feature flag gate, all five viewsets
(members, events, legislation, committees, attendance), token management views,
and ownership/visibility enforcement.

All tests run against the real Postgres test DB — SQLite is incompatible with
ArrayField used on ParliamentUser.
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from src.models import (
    ParliamentUser, Committee, Legislation, Attendance,
)
from src.models.api import APIToken
from src.models.events import Event
from src.models_feature_flags import FeatureFlag


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(user_id, name, username, member_type='Member', is_active=True, member_status='Active'):
    user = ParliamentUser.objects.create_user(
        user_id=user_id,
        name=name,
        username=username,
        member_type=member_type,
    )
    user.is_active = is_active
    user.member_status = member_status
    user.save(update_fields=['is_active', 'member_status'])
    return user


def _make_token(user, scopes=None):
    """Create an active APIToken for the given user."""
    if scopes is None:
        scopes = APIToken.ALL_SCOPE_KEYS
    return APIToken.objects.create(
        user=user,
        key=APIToken.generate_key(),
        name='Test Token',
        status=APIToken.STATUS_ACTIVE,
        scopes=scopes,
    )


def _token_header(token):
    return {'HTTP_AUTHORIZATION': f'Token {token.key}'}


def _results(response):
    """
    Unwrap a list-endpoint response. Real settings use CursorPagination
    (list responses are {"next", "previous", "results": [...]}); the old
    ci_settings had no pagination, so tests used to see bare lists.
    (Updated 07-05-26 when CI moved onto Parliament.settings.)
    """
    data = response.json()
    return data['results'] if isinstance(data, dict) and 'results' in data else data



# ---------------------------------------------------------------------------
# Base class: creates the feature flag and a default member + token
# ---------------------------------------------------------------------------

class APITestBase(TestCase):

    def setUp(self):
        # Ensure the feature flag exists and is ON for all API tests
        self.flag, _ = FeatureFlag.objects.get_or_create(
            name='rest_api',
            defaults={'display_name': 'REST API', 'category': 'features', 'is_enabled': True},
        )
        self.flag.is_enabled = True
        self.flag.save(update_fields=['is_enabled'])

        self.user = _make_user('1001', 'Alice Member', 'alice')
        self.token = _make_token(self.user)

    def tearDown(self):
        self.flag.is_enabled = False
        self.flag.save(update_fields=['is_enabled'])


# ---------------------------------------------------------------------------
# Feature flag gate
# ---------------------------------------------------------------------------

class APIFlagGateTestCase(APITestBase):

    def test_api_disabled_when_flag_off(self):
        self.flag.is_enabled = False
        self.flag.save(update_fields=['is_enabled'])
        response = self.client.get('/api/v1/members/', **_token_header(self.token))
        self.assertEqual(response.status_code, 403)

    def test_api_enabled_when_flag_on(self):
        response = self.client.get('/api/v1/members/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)

    def test_unauthenticated_request_rejected(self):
        response = self.client.get('/api/v1/members/')
        self.assertEqual(response.status_code, 401)


# ---------------------------------------------------------------------------
# Token status gate — pending, revoked, rejected tokens must be refused
# ---------------------------------------------------------------------------

class APITokenStatusGateTestCase(APITestBase):

    def test_pending_token_rejected(self):
        pending = APIToken.objects.create(
            user=self.user,
            key=APIToken.generate_key(),
            name='Pending',
            status=APIToken.STATUS_PENDING,
            scopes=APIToken.ALL_SCOPE_KEYS,
        )
        response = self.client.get('/api/v1/members/', **_token_header(pending))
        self.assertEqual(response.status_code, 401)

    def test_revoked_token_rejected(self):
        revoked = APIToken.objects.create(
            user=self.user,
            key=APIToken.generate_key(),
            name='Revoked',
            status=APIToken.STATUS_REVOKED,
            scopes=APIToken.ALL_SCOPE_KEYS,
        )
        response = self.client.get('/api/v1/members/', **_token_header(revoked))
        self.assertEqual(response.status_code, 401)

    def test_expired_token_rejected(self):
        expired = APIToken.objects.create(
            user=self.user,
            key=APIToken.generate_key(),
            name='Expired',
            status=APIToken.STATUS_ACTIVE,
            scopes=APIToken.ALL_SCOPE_KEYS,
            expires_at=timezone.now() - timedelta(days=1),
        )
        response = self.client.get('/api/v1/members/', **_token_header(expired))
        self.assertEqual(response.status_code, 401)

    def test_wrong_scope_rejected(self):
        """A token without members:read cannot access /api/v1/members/."""
        no_scope_token = APIToken.objects.create(
            user=self.user,
            key=APIToken.generate_key(),
            name='No Scope',
            status=APIToken.STATUS_ACTIVE,
            scopes=['attendance:read'],  # deliberately missing members:read
        )
        response = self.client.get('/api/v1/members/', **_token_header(no_scope_token))
        self.assertEqual(response.status_code, 403)


# ---------------------------------------------------------------------------
# Token management views (request / revoke)
# ---------------------------------------------------------------------------

class APITokenManagementTestCase(TestCase):

    def setUp(self):
        self.client = Client()
        self.user = _make_user('2001', 'Bob Dev', 'bob')
        self.client.force_login(self.user)
        # Ensure auto-approve flag is OFF so tokens go to 'pending' by default
        self.auto_flag, _ = FeatureFlag.objects.get_or_create(
            name='api_token_auto_approve',
            defaults={'display_name': 'API Token Auto-Approve', 'category': 'admin', 'is_enabled': False},
        )
        self.auto_flag.is_enabled = False
        self.auto_flag.save(update_fields=['is_enabled'])

    def test_request_creates_pending_token(self):
        response = self.client.post(reverse('request_api_token'), {
            'name': 'My Token',
            'scopes': ['members:read'],
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get('pending'))
        self.assertTrue(APIToken.objects.filter(user=self.user, status='pending').exists())

    def test_request_auto_approve(self):
        self.auto_flag.is_enabled = True
        self.auto_flag.save(update_fields=['is_enabled'])
        response = self.client.post(reverse('request_api_token'), {
            'name': 'My Token',
            'scopes': ['members:read'],
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data.get('pending'))
        self.assertEqual(data.get('status'), 'active')
        self.assertTrue(APIToken.objects.filter(user=self.user, status='active').exists())

    def test_request_blocked_when_token_exists(self):
        _make_token(self.user)
        response = self.client.post(reverse('request_api_token'), {
            'name': 'Duplicate',
            'scopes': ['members:read'],
        })
        self.assertEqual(response.status_code, 400)

    def test_request_requires_name(self):
        response = self.client.post(reverse('request_api_token'), {
            'scopes': ['members:read'],
        })
        self.assertEqual(response.status_code, 400)

    def test_request_requires_valid_scope(self):
        response = self.client.post(reverse('request_api_token'), {
            'name': 'Bad',
            'scopes': ['invalid:scope'],
        })
        self.assertEqual(response.status_code, 400)

    def test_revoke_own_token(self):
        token = _make_token(self.user)
        response = self.client.post(reverse('revoke_api_token'), {'token_id': token.id})
        self.assertEqual(response.status_code, 200)
        token.refresh_from_db()
        self.assertEqual(token.status, 'revoked')

    def test_revoke_other_users_token_fails(self):
        other = _make_user('2002', 'Carol Other', 'carol')
        other_token = _make_token(other)
        response = self.client.post(reverse('revoke_api_token'), {'token_id': other_token.id})
        self.assertEqual(response.status_code, 404)

    def test_request_requires_login(self):
        self.client.logout()
        response = self.client.post(reverse('request_api_token'), {
            'name': 'Anon',
            'scopes': ['members:read'],
        })
        self.assertIn(response.status_code, [302, 403])

    def test_request_rejects_get(self):
        response = self.client.get(reverse('request_api_token'))
        self.assertEqual(response.status_code, 405)

    def test_generate_alias_still_works(self):
        """The old /api/token/generate/ URL alias still resolves after the rename."""
        response = self.client.post(reverse('generate_api_token'), {
            'name': 'Alias Token',
            'scopes': ['members:read'],
        })
        # Should succeed (200 or 400 if duplicate) — just not 404
        self.assertNotEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------

class MemberAPITestCase(APITestBase):

    def test_list_returns_active_members(self):
        _make_user('1002', 'Carol Active', 'carol')
        response = self.client.get('/api/v1/members/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        names = [m['name'] for m in _results(response)]
        self.assertIn('Alice Member', names)
        self.assertIn('Carol Active', names)

    def test_inactive_members_excluded(self):
        _make_user('1003', 'Dave Inactive', 'dave', is_active=False, member_status='Inactive')
        response = self.client.get('/api/v1/members/', **_token_header(self.token))
        names = [m['name'] for m in _results(response)]
        self.assertNotIn('Dave Inactive', names)

    def test_me_returns_own_record(self):
        response = self.client.get('/api/v1/members/me/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user_id'], '1001')

    def test_sensitive_fields_excluded(self):
        response = self.client.get('/api/v1/members/me/', **_token_header(self.token))
        data = response.json()
        for field in ('password', 'email', 'phone_number', 'is_admin', 'watch_flag', 'force_password_change'):
            self.assertNotIn(field, data, f"Sensitive field '{field}' exposed in API response")

    def test_retrieve_by_user_id(self):
        response = self.client.get('/api/v1/members/1001/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'Alice Member')


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class EventAPITestCase(APITestBase):

    def setUp(self):
        super().setUp()
        self.event = Event.objects.create(
            title='Chapter Meeting',
            description='Weekly meeting',
            date_time=timezone.now() + timedelta(days=3),
            location='Chapter House',
            created_by=self.user,
            visible_to=None,
            is_active=True,
            archived=False,
        )

    def test_list_returns_events(self):
        response = self.client.get('/api/v1/events/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        titles = [e['title'] for e in _results(response)]
        self.assertIn('Chapter Meeting', titles)

    def test_upcoming_filters_to_30_days(self):
        Event.objects.create(
            title='Far Future Event',
            description='Way out there',
            date_time=timezone.now() + timedelta(days=60),
            created_by=self.user,
            visible_to=None,
            is_active=True,
            archived=False,
        )
        response = self.client.get('/api/v1/events/upcoming/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        titles = [e['title'] for e in _results(response)]
        self.assertIn('Chapter Meeting', titles)
        self.assertNotIn('Far Future Event', titles)

    def test_retrieve_single_event(self):
        response = self.client.get(f'/api/v1/events/{self.event.id}/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['title'], 'Chapter Meeting')


# ---------------------------------------------------------------------------
# Legislation
# ---------------------------------------------------------------------------

class LegislationAPITestCase(APITestBase):

    def setUp(self):
        super().setUp()
        self.leg = Legislation.objects.create(
            title='Test Bill',
            description='A test bill',
            posted_by=self.user,
            available_at=timezone.now(),
            document='test.pdf',
            status='active',
            is_active=True,
        )
        self.removed_leg = Legislation.objects.create(
            title='Removed Bill',
            description='Should not appear',
            posted_by=self.user,
            available_at=timezone.now(),
            document='test.pdf',
            status='removed',
            is_active=True,
        )

    def test_list_excludes_removed(self):
        response = self.client.get('/api/v1/legislation/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        titles = [l['title'] for l in _results(response)]
        self.assertIn('Test Bill', titles)
        self.assertNotIn('Removed Bill', titles)

    def test_active_endpoint_returns_open_votes(self):
        response = self.client.get('/api/v1/legislation/active/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        titles = [l['title'] for l in _results(response)]
        self.assertIn('Test Bill', titles)

    def test_retrieve_single(self):
        response = self.client.get(f'/api/v1/legislation/{self.leg.id}/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['title'], 'Test Bill')


# ---------------------------------------------------------------------------
# Committees
# ---------------------------------------------------------------------------

class CommitteeAPITestCase(APITestBase):

    def setUp(self):
        super().setUp()
        self.committee = Committee.objects.create(
            code='BROTHER',
            name='Brotherhood Committee',
            is_active=True,
        )
        self.committee.members.add(self.user)

        self.other_user = _make_user('1010', 'Eve Other', 'eve')
        self.other_token = _make_token(self.other_user)

        # Slating committee — only visible to members
        self.slating = Committee.objects.create(
            code='SLATE',
            name='Slating Committee',
            is_active=True,
            is_slating_committee=True,
        )

    def test_list_returns_visible_committees(self):
        response = self.client.get('/api/v1/committees/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        names = [c['name'] for c in _results(response)]
        self.assertIn('Brotherhood Committee', names)

    def test_slating_hidden_from_non_member(self):
        response = self.client.get('/api/v1/committees/', **_token_header(self.other_token))
        names = [c['name'] for c in _results(response)]
        self.assertNotIn('Slating Committee', names)

    def test_mine_returns_only_own_committees(self):
        response = self.client.get('/api/v1/committees/mine/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        names = [c['name'] for c in _results(response)]
        self.assertIn('Brotherhood Committee', names)

    def test_mine_excludes_unjoined_committees(self):
        response = self.client.get('/api/v1/committees/mine/', **_token_header(self.other_token))
        names = [c['name'] for c in _results(response)]
        self.assertNotIn('Brotherhood Committee', names)

    def test_chairs_and_members_are_user_id_strings(self):
        response = self.client.get(f'/api/v1/committees/{self.committee.id}/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIsInstance(data['members'], list)
        self.assertIn('1001', data['members'])


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------

class AttendanceAPITestCase(APITestBase):

    def setUp(self):
        super().setUp()
        self.other_user = _make_user('1020', 'Frank Other', 'frank')
        self.other_token = _make_token(self.other_user)

        self.event = Event.objects.create(
            title='Test Event',
            date_time=timezone.now(),
            created_by=self.user,
            visible_to=None,
            is_active=True,
            archived=False,
        )
        self.record = Attendance.objects.create(
            user=self.user,
            event=self.event,
            attendance_type='event',
            status='present',
        )
        self.other_record = Attendance.objects.create(
            user=self.other_user,
            event=self.event,
            attendance_type='event',
            status='absent',
        )

    def test_list_returns_own_records_only(self):
        response = self.client.get('/api/v1/attendance/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        ids = [r['id'] for r in _results(response)]
        self.assertIn(self.record.id, ids)
        self.assertNotIn(self.other_record.id, ids)

    def test_cannot_retrieve_other_users_record(self):
        response = self.client.get(f'/api/v1/attendance/{self.other_record.id}/', **_token_header(self.token))
        self.assertEqual(response.status_code, 404)

    def test_type_filter(self):
        Attendance.objects.create(
            user=self.user,
            attendance_type='committee',
            status='present',
        )
        response = self.client.get('/api/v1/attendance/?type=committee', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        for r in _results(response):
            self.assertEqual(r['attendance_type'], 'committee')

    def test_year_filter(self):
        response = self.client.get(f'/api/v1/attendance/?year={timezone.now().year}', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        self.assertGreater(len(_results(response)), 0)

    def test_event_title_in_response(self):
        response = self.client.get(f'/api/v1/attendance/{self.record.id}/', **_token_header(self.token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['event_title'], 'Test Event')
